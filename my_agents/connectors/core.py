"""ChannelCore — the single intake path all channels share.

One inbound message from any channel becomes one turn of the existing
``delivery_pipeline``::

    submit(channel, external_id, text, send=...)   -> schedules a turn, pushes replies
    ask(channel, external_id, text)                -> awaits and returns the replies

Design notes:

- **Identity**: ``identity.identify`` turns (channel, external id) into a stable
  ADK ``(user_id, session_id)``. The first contact binds a project workspace into
  the session state, so one conversation is one project forever after.
- **Sessions** persist in SQLite, so a restart does not lose a conversation. The
  workspace on disk remains the shared source of truth with ``adk web``.
- **Human-in-the-loop**: when a turn pauses (the pipeline waiting for a human),
  the pending ``(invocation_id, interrupt_id)`` is remembered; the *next* message
  for that conversation is delivered as the ``adk_request_input`` response rather
  than as new text — the same protocol ``adk web`` uses.
- **Long turns**: generation can take minutes, so ``submit`` never blocks the
  caller; it acknowledges, runs in the background, and pushes each reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import config
from .identity import Identity, identify

log = logging.getLogger("connectors.core")

Send = Callable[[str], Awaitable[None]]


class _PendingStore:
    """Remembers a paused turn per session (session_id -> invocation/interrupt)."""

    def __init__(self, path: Path):
        self._path = path
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self._path.is_file():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = {k: v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            self._data = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def get(self, session_id: str) -> dict | None:
        return self._data.get(session_id)

    def set(self, session_id: str, value: dict) -> None:
        self._data[session_id] = value
        self._save()

    def clear(self, session_id: str) -> None:
        if self._data.pop(session_id, None) is not None:
            self._save()


def _text_of(event: Any) -> list[str]:
    """User-visible text carried by one ADK event."""
    out: list[str] = []
    try:
        content = getattr(event, "content", None)
        for part in (content.parts if content and content.parts else []):
            text = getattr(part, "text", None)
            if text and text.strip():
                out.append(text.strip())
    except Exception:
        pass
    if not out:
        try:
            message = getattr(event, "message", None)
            if isinstance(message, str) and message.strip():
                out.append(message.strip())
        except Exception:
            pass
    return out


class ChannelCore:
    """Drives the pipeline for every channel."""

    def __init__(self, *, app_name: str | None = None, session_service: Any = None,
                 runner: Any = None, agent: Any = None,
                 state_dir: Path | None = None):
        self._app_name = app_name or config.adk_app_name()
        self._session_service = session_service
        self._runner = runner
        self._agent = agent
        self._locks: dict[str, asyncio.Lock] = {}
        base = state_dir or config.state_dir()
        self._pending = _PendingStore(Path(base) / "pending.json")

    # ------------------------------------------------------------- wiring

    def _service(self):
        if self._session_service is None:
            from google.adk.sessions.sqlite_session_service import SqliteSessionService

            self._session_service = SqliteSessionService(str(config.session_db()))
        return self._session_service

    def _get_runner(self):
        if self._runner is None:
            from google.adk.runners import Runner

            if self._agent is None:
                from delivery_pipeline.agent import root_agent as pipeline

                self._agent = pipeline
            self._runner = Runner(agent=self._agent, app_name=self._app_name,
                                 session_service=self._service())
        return self._runner

    def _lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks[session_id] = asyncio.Lock()
        return lock

    # ------------------------------------------------------------- session

    async def _ensure_session(self, ident: Identity, first_text: str = "",
                              workspace_id: str = ""):
        svc = self._service()
        try:
            session = await svc.get_session(app_name=self._app_name,
                                            user_id=ident.user_id,
                                            session_id=ident.session_id)
        except Exception:
            session = None
        if session is not None:
            return session
        from shared.workspace import generate_project_id

        pid = workspace_id or generate_project_id(
            ((first_text or ident.channel or "project").strip().splitlines() or [""])[0][:60])
        state = {"project_workspace_id": pid}
        return await svc.create_session(app_name=self._app_name, user_id=ident.user_id,
                                        session_id=ident.session_id, state=state)

    async def ensure_session(self, channel: str, external_id: str, *, name: str = "",
                             workspace_id: str = "") -> Identity:
        """Create the conversation's session if absent (binding a workspace once).

        Used by the web intake so it can write structured fields and documents
        into the workspace *before* the first turn.
        """
        ident = identify(channel, external_id)
        await self._ensure_session(ident, name, workspace_id)
        return ident

    async def bound_workspace(self, channel: str, external_id: str) -> str:
        """The project workspace bound to this conversation (``""`` if unknown)."""
        from .identity import workspace_id as _ws

        ident = identify(channel, external_id)
        try:
            session = await self._service().get_session(
                app_name=self._app_name, user_id=ident.user_id,
                session_id=ident.session_id)
        except Exception:
            session = None
        return _ws(session.state) if session is not None else ""

    # --------------------------------------------------------------- turns

    def _new_message(self, text: str):
        from google.genai import types

        return types.Content(role="user", parts=[types.Part(text=text)])

    def _resume_message(self, interrupt_id: str, text: str):
        from google.genai import types

        return types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=interrupt_id, name="adk_request_input",
                response={"result": text}))])

    async def _run_turn(self, ident: Identity, text: str) -> list[str]:
        """Run one turn (or resume a paused one) and return the reply texts."""
        async with self._lock(ident.session_id):
            await self._ensure_session(ident, text)
            runner = self._get_runner()
            pending = self._pending.get(ident.session_id)
            if pending is not None:
                message = self._resume_message(str(pending.get("interrupt_id", "")), text)
                invocation_id = pending.get("invocation_id")
            else:
                message = self._new_message(text)
                invocation_id = None

            replies: list[str] = []
            interrupt: dict | None = None
            kwargs: dict[str, Any] = {"user_id": ident.user_id,
                                      "session_id": ident.session_id,
                                      "new_message": message}
            if invocation_id:
                kwargs["invocation_id"] = invocation_id
            async for event in runner.run_async(**kwargs):
                replies.extend(_text_of(event))
                if getattr(event, "long_running_tool_ids", None):
                    interrupt = {"invocation_id": getattr(event, "invocation_id", ""),
                                 "interrupt_id": ""}
                    for fc in event.get_function_calls():
                        interrupt["interrupt_id"] = fc.id or ""
            if interrupt and interrupt.get("interrupt_id"):
                self._pending.set(ident.session_id, interrupt)
            elif pending is not None:
                self._pending.clear(ident.session_id)
            return replies

    # ------------------------------------------------------------ public API

    async def ask(self, channel: str, external_id: str, text: str) -> list[str]:
        """Submit one message and wait for the reply texts (web page)."""
        ident = identify(channel, external_id)
        try:
            return await self._run_turn(ident, text)
        except Exception as e:  # a channel must never see a traceback
            log.exception("turn failed for %s", ident.user_id)
            return [f"Sorry — something went wrong handling that ({type(e).__name__})."]

    async def submit(self, channel: str, external_id: str, text: str,
                     send: Send | None = None,
                     attachments: list[tuple[str, bytes]] | None = None,
                     ack: bool = True) -> None:
        """Acknowledge, run the turn in the background, push replies via ``send``."""
        ident = identify(channel, external_id)
        if ack and send is not None:
            try:
                await send(config.ack_text())
            except Exception:
                log.exception("ack failed for %s", ident.user_id)

        async def _job() -> None:
            if attachments:
                try:
                    from .ingest import ingest_files

                    workspace = await self.bound_workspace(channel, external_id)
                    if workspace:
                        ingest_files(workspace, attachments)
                except Exception:
                    log.exception("ingest failed for %s", ident.user_id)
            replies = await self.ask(channel, external_id, text)
            if send is not None:
                for reply in replies:
                    try:
                        await send(reply)
                    except Exception:
                        log.exception("send failed for %s", ident.user_id)

        asyncio.create_task(_job())

    async def pending_interrupt(self, channel: str, external_id: str) -> dict | None:
        """The paused turn for a conversation, if any (for tests/diagnostics)."""
        return self._pending.get(identify(channel, external_id).session_id)
