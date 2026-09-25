"""Telegram channel — Bot API long polling.

Long polling (``getUpdates``) rather than a webhook, so nothing needs to be
publicly reachable: the bot dials out from wherever the service runs. Plain
``httpx`` — no ``python-telegram-bot`` dependency — which also keeps it fully
testable offline with an ``httpx.MockTransport``.

With no token configured the worker idles instead of crashing, so the connectors
service starts before the bot exists.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from . import config
from .core import ChannelCore

log = logging.getLogger("connectors.telegram")


def chunk(text: str, limit: int | None = None) -> list[str]:
    """Split a reply into <= limit-character messages, preferring paragraph breaks."""
    limit = limit or config.message_chunk()
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n\n"), window.rfind("\n"), window.rfind(" "))
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        parts.append(rest)
    return [p for p in parts if p]


class TelegramBot:
    """One bot: polls updates, forwards text to the core, sends replies back."""

    name = "telegram"

    def __init__(self, core: ChannelCore, *, token: str = "", api_base: str = "",
                 client: httpx.AsyncClient | None = None,
                 offset_path: Path | None = None):
        self._core = core
        self._token = token or config.telegram_token()
        self._api_base = (api_base or config.telegram_api_base()).rstrip("/")
        self._client = client
        base = offset_path or (config.state_dir() / "telegram_offset.json")
        self._offset_path = Path(base)
        self._offset = self._load_offset()
        self._stop = asyncio.Event()

    # --------------------------------------------------------------- state

    def _load_offset(self) -> int:
        try:
            if self._offset_path.is_file():
                return int(json.loads(self._offset_path.read_text(encoding="utf-8"))
                           .get("offset", 0))
        except Exception:
            pass
        return 0

    def _save_offset(self) -> None:
        try:
            self._offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._offset_path.write_text(json.dumps({"offset": self._offset}),
                                         encoding="utf-8")
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    # ---------------------------------------------------------------- HTTP

    async def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=config.telegram_poll_timeout() + 15)
        return self._client

    async def _call(self, method: str, payload: dict[str, Any]) -> dict:
        client = await self._client_or_new()
        url = f"{self._api_base}/bot{self._token}/{method}"
        try:
            resp = await client.post(url, json=payload)
            data = resp.json() if resp.content else {}
            return data if isinstance(data, dict) else {}
        except Exception as e:
            log.warning("telegram %s failed: %s", method, e)
            return {}

    async def get_updates(self, timeout: int | None = None) -> list[dict]:
        data = await self._call("getUpdates", {
            "offset": self._offset,
            "timeout": timeout if timeout is not None else config.telegram_poll_timeout(),
            "allowed_updates": ["message"],
        })
        updates = data.get("result")
        return [u for u in updates if isinstance(u, dict)] if isinstance(updates, list) else []

    async def send_message(self, chat_id: Any, text: str) -> None:
        for part in chunk(text):
            await self._call("sendMessage", {"chat_id": chat_id, "text": part})

    async def get_me(self) -> dict:
        """Identify this bot to the Bot API — proves a token works, or says why not.

        Returns ``{"ok", "username", "name", "error"}``. The token itself is never
        part of the answer, so a caller can identify the bot without holding the
        secret.
        """
        if not self.enabled:
            return {"ok": False, "username": "", "name": "",
                    "error": "no token configured"}
        data = await self._call("getMe", {})
        result = data.get("result") or {}
        if not isinstance(result, dict) or not result:
            return {"ok": False, "username": "", "name": "",
                    "error": str(data.get("description") or "no response from Telegram")}
        return {"ok": True,
                "username": str(result.get("username") or ""),
                "name": str(result.get("first_name") or ""),
                "error": ""}

    # -------------------------------------------------------------- inbound

    async def handle_update(self, update: dict) -> bool:
        """Forward one update to the core. Returns True when a message was handled."""
        message = update.get("message") or update.get("edited_message") or {}
        if not isinstance(message, dict):
            return False
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not text or chat_id is None:
            return False

        async def send(reply: str) -> None:
            await self.send_message(chat_id, reply)

        await self._core.submit(self.name, str(chat_id), text, send=send)
        return True

    async def poll_once(self, timeout: int | None = None) -> int:
        """Fetch and dispatch one batch. Returns how many messages were handled."""
        updates = await self.get_updates(timeout=timeout)
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset, update_id + 1)
            self._save_offset()
            if await self.handle_update(update):
                handled += 1
        return handled

    async def run_forever(self) -> None:
        """Poll until stopped. Idles quietly when no token is configured."""
        if not self.enabled:
            log.info("telegram: no TELEGRAM_BOT_TOKEN — worker idle")
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=60)
                except asyncio.TimeoutError:
                    continue
            return
        log.info("telegram: polling as configured")
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram poll failed")
                await asyncio.sleep(3)

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
