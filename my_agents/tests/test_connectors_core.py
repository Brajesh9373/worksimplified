"""ChannelCore: one inbound message -> replies, workspace reuse, HITL resume.

A stub session service and a stub Runner stand in for ADK, so these run offline
with no LLM and no network.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from connectors.core import ChannelCore  # noqa: E402

STATE_DIR = MY_AGENTS / "_outputs" / "connector_state_test"


class FakeSession:
    def __init__(self, state: dict):
        self.state = state


class FakeSessionService:
    def __init__(self):
        self.sessions: dict[tuple, FakeSession] = {}

    async def get_session(self, *, app_name, user_id, session_id):
        return self.sessions.get((app_name, user_id, session_id))

    async def create_session(self, *, app_name, user_id, session_id=None, state=None):
        session = FakeSession(dict(state or {}))
        self.sessions[(app_name, user_id, session_id)] = session
        return session


class FakeEvent:
    def __init__(self, text: str = "", interrupt: str = ""):
        self.content = (SimpleNamespace(parts=[SimpleNamespace(text=text)])
                        if text else None)
        self.invocation_id = "inv-1"
        self.long_running_tool_ids = ["x"] if interrupt else None
        self._interrupt = interrupt

    def get_function_calls(self):
        return [SimpleNamespace(id=self._interrupt)] if self._interrupt else []


class FakeRunner:
    """Yields a canned batch per call and records what it was sent."""

    def __init__(self, batches: list[list[FakeEvent]]):
        self._batches = list(batches)
        self.calls: list[dict] = []

    async def run_async(self, **kwargs):
        self.calls.append(kwargs)
        batch = self._batches.pop(0) if self._batches else []
        for event in batch:
            yield event


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(STATE_DIR, ignore_errors=True)
    yield
    shutil.rmtree(STATE_DIR, ignore_errors=True)


def _core(runner: FakeRunner, service: FakeSessionService) -> ChannelCore:
    return ChannelCore(app_name="delivery_pipeline", session_service=service,
                       runner=runner, state_dir=STATE_DIR)


def test_one_message_yields_replies_and_binds_a_workspace():
    service, runner = FakeSessionService(), FakeRunner([[FakeEvent("Hello there")]])
    core = _core(runner, service)

    replies = asyncio.run(core.ask("web", "abc", "I want an ERP"))

    assert replies == ["Hello there"]
    session = service.sessions[("delivery_pipeline", "web:abc", "web-abc")]
    assert session.state["project_workspace_id"]
    sent = runner.calls[0]
    assert sent["user_id"] == "web:abc" and sent["session_id"] == "web-abc"
    assert sent["new_message"].parts[0].text == "I want an ERP"


def test_second_message_reuses_the_same_workspace():
    service, runner = FakeSessionService(), FakeRunner([[FakeEvent("a")], [FakeEvent("b")]])
    core = _core(runner, service)

    asyncio.run(core.ask("telegram", "42", "first"))
    first = service.sessions[("delivery_pipeline", "telegram:42", "telegram-42")].state["project_workspace_id"]
    asyncio.run(core.ask("telegram", "42", "second"))
    second = service.sessions[("delivery_pipeline", "telegram:42", "telegram-42")].state["project_workspace_id"]

    assert first and first == second
    assert len(runner.calls) == 2  # no extra session, no extra project


def test_pause_is_remembered_and_next_message_resumes():
    pause = [FakeEvent("Ready for review — say approve.", interrupt="intr-9")]
    resume = [FakeEvent("Approved, continuing.")]
    service = FakeSessionService()
    runner = FakeRunner([pause, resume])
    core = _core(runner, service)

    asyncio.run(core.ask("whatsapp", "9198", "generate my BRD"))
    pending = asyncio.run(core.pending_interrupt("whatsapp", "9198"))
    assert pending and pending["interrupt_id"] == "intr-9"
    assert pending["invocation_id"] == "inv-1"

    asyncio.run(core.ask("whatsapp", "9198", "approve"))
    resumed = runner.calls[1]
    assert resumed["invocation_id"] == "inv-1"
    part = resumed["new_message"].parts[0]
    assert part.function_response.name == "adk_request_input"
    assert part.function_response.id == "intr-9"
    assert part.function_response.response["result"] == "approve"
    # the pause is consumed
    assert asyncio.run(core.pending_interrupt("whatsapp", "9198")) is None


def test_errors_are_reported_not_raised():
    service = FakeSessionService()

    class Boom(FakeRunner):
        async def run_async(self, **kwargs):
            raise RuntimeError("model exploded")
            yield  # pragma: no cover

    core = _core(Boom([]), service)
    replies = asyncio.run(core.ask("web", "abc", "hi"))
    assert len(replies) == 1 and "RuntimeError" in replies[0]


def test_submit_acks_then_pushes_replies():
    service, runner = FakeSessionService(), FakeRunner([[FakeEvent("the answer")]])
    core = _core(runner, service)
    pushed: list[str] = []

    async def main():
        await core.submit("telegram", "7", "hello", send=lambda t: _push(pushed, t))
        for _ in range(50):                      # let the background task run
            if pushed and pushed[-1] == "the answer":
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert pushed[0] != "the answer"             # an ack first
    assert pushed[-1] == "the answer"            # then the reply


async def _push(sink: list[str], text: str) -> None:
    sink.append(text)


def test_ensure_session_can_pre_bind_a_workspace():
    service, runner = FakeSessionService(), FakeRunner([])
    core = _core(runner, service)

    ident = asyncio.run(core.ensure_session("web", "zzz", name="Acme",
                                            workspace_id="acme_20260101"))

    assert ident.session_id == "web-zzz"
    assert asyncio.run(core.bound_workspace("web", "zzz")) == "acme_20260101"
