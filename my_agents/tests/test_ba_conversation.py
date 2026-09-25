"""Conversational BA: discovery-first, explicit generation, no jargon.

Deterministic (no LLM): the BA stub answers a DISCOVERY prompt with plain prose
and behaves like the sectional writer for generation prompts. Knowledge capture
by the agent's tools is simulated between turns here; the tool itself is unit
tested (`test_record_knowledge_tool_writes_through_live_state`) and the capture
audit is exercised by asserting a `capture_gap` history entry.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from google.adk import Workflow  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from shared.orch_nodes import DeliveryOrchestrator  # noqa: E402
from shared.project_context import get_context, get_execution  # noqa: E402
from shared.workspace import ProjectWorkspace  # noqa: E402
from tests.ba_fixture import SECTION_BODIES  # noqa: E402
from tests.prod_fixtures import BRD, DESIGN, PLAN, SPEC  # noqa: E402
from tests.frappe_fixture import seed_ba_v2, seed_frappe  # noqa: E402
from tests.test_sectional import _sectional_stub_factory  # noqa: E402

WS = "convflow"

# Anything a non-technical owner must never see.
LEAK_RX = re.compile(
    r"(?i)\b(?:gates?|checks?|STAGE TASK|MoSCoW|NFRs?|blockers?|criteria|harness|"
    r"sections?)\b|\b(?:G|BN|OPT|BR|US|FR|UC|TECH|DEC|SM|OQ|EL|CT|AP|QB|PL)-\d+"
    r"|\bsection '")

REPLY = ("Got it — so approvals today happen over email and nobody can see what is "
         "committed. Who signs off the bigger trips?")


def _stub():
    """Generation stub: the sectional writer (discovery never calls an agent)."""
    return _sectional_stub_factory()


def _seed_knowledge(state) -> None:
    """Simulate what the agent's record_knowledge calls would have captured."""
    from ba_agent.managers import knowledge as K

    K.capture(state, "need", "control travel spend", "we lose track of committed spend")
    K.capture(state, "stakeholder", "finance director", "signs off anything above 1000 EUR")
    K.capture(state, "process", "approval", "manager approves, then finance checks receipts")
    K.capture(state, "rule", "cap", "hotel bookings must stay under 180 EUR a night")
    K.capture(state, "requirement", "submit request", "employees submit before booking")
    K.capture(state, "constraint", "audit", "auditors want evidence of every approval")
    K.capture(state, "risk", "receipts", "receipts get lost in email")
    K.capture(state, "dependency", "sso", "single sign-on must be available")


def _drive(pipe, turns, *, answers=None, seed_state=None, wid=WS, max_resumes=6,
           before_turn=None):
    """Run a chat: each turn, then answer any pause with the next scripted reply."""
    shutil.rmtree(MY_AGENTS / "projects" / wid, ignore_errors=True)
    ws = ProjectWorkspace(wid)
    answers = list(answers or [])
    seed = dict(seed_state or {})
    if isinstance(seed.get("project_context"), dict):
        # A resumed project reads its context from the workspace: seed both so
        # the load cannot clobber it.
        ws.update_context(seed["project_context"])
    seed["project_workspace_id"] = wid

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=seed)
        events: list = []
        for i, text in enumerate(turns):
            if before_turn is not None:
                before_turn(i, await _state_of(runner, sess))
            await _say(runner, sess, events, text)
            for _ in range(max_resumes):
                st = await _state_of(runner, sess)
                exc = get_execution(st)
                if exc.get("workflow_status") != "WAITING_FOR_HUMAN":
                    break
                inv, iid = _last_interrupt(events)
                if not (inv and iid):
                    break
                answer = answers.pop(0) if answers else "approve"
                await _answer(runner, sess, events, inv, iid, answer)
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return events, final

    return asyncio.run(go())


async def _say(runner, sess, events, text):
    async for ev in runner.run_async(
            user_id="u1", session_id=sess.id,
            new_message=types.Content(role="user", parts=[types.Part(text=text)])):
        events.append(ev)


async def _state_of(runner, sess):
    s = await runner.session_service.get_session(
        app_name="pipe", user_id="u1", session_id=sess.id)
    return s.state


def _last_interrupt(events):
    inv = iid = None
    for ev in events:
        if getattr(ev, "long_running_tool_ids", None):
            inv = ev.invocation_id
            for fc in ev.get_function_calls():
                iid = fc.id
    return inv, iid


async def _answer(runner, sess, events, inv, iid, text):
    msg = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=iid, name="adk_request_input", response={"result": text}))])
    async for ev in runner.run_async(user_id="u1", session_id=sess.id,
                                     invocation_id=inv, new_message=msg):
        events.append(ev)


def _messages(events) -> list[str]:
    return [e.content.parts[0].text for e in events
            if e.content and e.content.parts and e.content.parts[0].text]


def _pipe(stub=None, **kw):
    agents = {s: (stub or _stub()) for s in ("BA", "PROJECT", "FUNCTIONAL",
                                             "TECHNICAL", "FRAPPE")}
    return Workflow(name="pipe", edges=[("START", DeliveryOrchestrator(
        name="orc", stage_agents=agents, **kw))])


@pytest.fixture(autouse=True)
def _clean():
    yield
    shutil.rmtree(MY_AGENTS / "projects" / WS, ignore_errors=True)


@pytest.fixture(autouse=True)
def _owner_register(monkeypatch):
    """These tests assert the owner-facing register; pin it explicitly."""
    monkeypatch.setenv("BA_CHAT_VERBOSITY", "quiet")


@pytest.fixture(autouse=True)
def _fake_discovery(monkeypatch):
    """Discovery is one model turn owned by the orchestrator (no agent stream).

    The turn is stubbed here so the tests stay deterministic; by default it
    captures nothing, which also keeps the capture-audit expectations honest.
    """
    from shared import discovery

    def fake(state, user_text, source_ref=""):
        return {"reply": REPLY, "tools_used": [], "steps": 1}

    monkeypatch.setattr(discovery, "run_turn", fake)


# --------------------------------------------------------------- discovery

def test_new_project_starts_as_a_conversation():
    events, final = _drive(_pipe(), ["We're a 40-person logistics company and travel "
                                     "expenses are a mess."])
    ctx = get_context(final.state)
    exc = get_execution(final.state)
    assert ctx["conversation"]["mode"] == "discovery"
    assert ctx["conversation"]["turn"] == 1
    assert not (ctx.get("brd") or "").strip()          # nothing generated
    assert not (ctx.get("sections_done_BA") or [])     # no section work
    assert exc["workflow_status"] == "RUNNING"
    types_seen = [h["type"] for h in ctx["history"]]
    assert "conversation_started" in types_seen and "discovery_turn" in types_seen
    assert not any(t.startswith("gate_") for t in types_seen)
    assert REPLY in "\n".join(_messages(events))        # the agent answers, in words


def test_discovery_never_produces_documents_or_gate_noise():
    events, final = _drive(_pipe(), ["Our managers approve by email.",
                                     "We tried spreadsheets last year, it failed.",
                                     "Next year we want a portal for the team."])
    ctx = get_context(final.state)
    assert ctx["conversation"]["turn"] == 3
    assert ctx["conversation"]["mode"] == "discovery"
    assert not (ctx.get("brd") or "").strip()
    hist = [h["type"] for h in ctx["history"]]
    assert not any("gate" in h for h in hist), hist


def test_no_internal_vocabulary_reaches_the_owner():
    events, _ = _drive(_pipe(), ["We're a 40-person logistics company.",
                                 "Approvals happen over email today.",
                                 "we'll need a BRD at some point I suppose"])
    leaked = [m for m in _messages(events) if LEAK_RX.search(m)]
    assert leaked == [], leaked


def test_discussing_documents_does_not_start_generation():
    _, final = _drive(_pipe(), ["we'll need a BRD at some point",
                                "maybe an SRS later too"])
    assert get_context(final.state)["conversation"]["mode"] == "discovery"
    assert not (get_context(final.state).get("brd") or "").strip()


def test_capture_audit_flags_a_turn_that_captured_nothing():
    long_turn = ("Our finance director signs off anything above 1000 EUR, and audit want "
                 "evidence for every single approval we give, including the ones we "
                 "reject, because last year they found gaps in the records.")
    _, final = _drive(_pipe(), [long_turn])
    hist = [h["type"] for h in get_context(final.state)["history"]]
    assert "capture_gap" in hist                # nothing invented on the owner's behalf


def test_each_discovery_turn_speaks_exactly_once():
    """The owner-facing channel has one writer: no model scratchpad, no echo."""
    events, final = _drive(_pipe(), ["We're a 40-person logistics company.",
                                     "Approvals happen over email today."])
    replies = [m for m in _messages(events) if m.strip() == REPLY]
    assert len(replies) == 2, replies          # one per turn, and nothing else
    ctx = get_context(final.state)
    assert ctx["conversation"]["turn"] == 2
    assert len(ctx["conversation"]["transcript"]) == 4   # 2 owner + 2 agent


def test_discovery_tools_write_through_the_orchestrator():
    """The turn's tool calls land in the project, not in a discarded copy."""
    import shared.discovery as D
    from ba_agent.managers import knowledge as K

    s = {}
    assert D._dispatch(s, "record_knowledge",
                       {"kind": "rule", "key": "cap", "value": "hotel cap 180 EUR"})["ok"]
    assert D._dispatch(s, "ask_user", {"question": "Who signs off the big trips?"})["ok"]
    assert D._dispatch(s, "propose_generation", {"artifacts": "brd"})["ok"]
    assert D._dispatch(s, "nonsense", {})["ok"] is False
    assert K.count(s) == 2                      # rule + question
    assert get_context(s)["business_rules"]["cap"].startswith("hotel cap")
    assert get_context(s)["conversation"]["pending_proposal"] is True
    assert K.status(s)["captured"] == 2


# --------------------------------------------------------------- the trigger

def test_explicit_ask_switches_to_generation_and_offers_to_continue():
    seed: dict = {}
    seed_frappe(seed, SPEC, DESIGN, plan=PLAN, brd=BRD)
    _seed_knowledge(seed)
    events, final = _drive(
        _pipe(), ["We struggle with travel expense control.", "ok, write my BRD now"],
        answers=["approve", "yes"], seed_state=seed)
    ctx = get_context(final.state)
    hist = [h["type"] for h in ctx["history"]]
    assert ctx["conversation"]["mode"] == "generation"
    assert "generation_requested" in hist
    assert "ba_final_review_requested" in hist      # the artefacts were produced
    assert ctx["conversation"].get("awaiting_continue") is False
    assert "continue_confirmed" in hist or get_execution(final.state)[
        "workflow_status"] == "SUCCESS"
    msgs = "\n".join(_messages(events))
    assert LEAK_RX.search(msgs) is None, [m for m in _messages(events)
                                          if LEAK_RX.search(m)]
    assert "ready for review" in msgs.lower()
    assert "shall I continue" in msgs.lower() or "continue" in msgs.lower()


def test_thin_knowledge_ask_is_a_plain_question_not_a_gate_dump():
    events, final = _drive(_pipe(), ["write my BRD please"])
    ctx = get_context(final.state)
    exc = get_execution(final.state)
    assert ctx["conversation"]["mode"] == "discovery"        # not started
    assert "generation_blocked_thin" in [h["type"] for h in ctx["history"]]
    assert exc["workflow_status"] == "RUNNING"               # a chat ask, not a pause
    msgs = "\n".join(_messages(events))
    assert "before i write this up" in msgs.lower()
    for token in ("gate", "check", "section", "US-", "BR-"):
        assert token.lower() not in msgs.lower()


def test_readiness_flips_once_knowledge_is_captured():
    """The plain question is answered by capturing knowledge, then it proceeds.

    (Mid-run capture cannot be driven by a stub agent, so the capture side is
    unit tested here and the tool's own write path is asserted in
    test_record_knowledge_tool_writes_through_live_state.)
    """
    from ba_agent.managers import knowledge as K
    from shared import conversation as C

    s = {}
    assert C.readiness(s)["ok"] is False            # nothing captured: ask
    for kind, key, val in (("need", "control spend", "we lose track of spend"),
                           ("stakeholder", "finance director", "signs off above 1000"),
                           ("process", "approval", "manager then finance"),
                           ("rule", "cap", "hotel cap 180 EUR"),
                           ("requirement", "submit", "submit before booking"),
                           ("constraint", "audit", "auditors want evidence"),
                           ("risk", "receipts", "receipts get lost"),
                           ("dependency", "sso", "SSO must be available")):
        assert K.capture(s, kind, key, val)["ok"] is True
    assert C.readiness(s)["ok"] is True             # enough to write from


def test_proposal_then_yes_triggers_generation():
    seed = {"conversation": {"pending_proposal": True, "artifacts": ["brd", "package"]}}
    seed_frappe(seed, SPEC, DESIGN, plan=PLAN, brd=BRD)
    _seed_knowledge(seed)
    _, final = _drive(_pipe(), ["yes, go ahead"], answers=["approve", "yes"],
                      seed_state=seed)
    assert get_context(final.state)["conversation"]["mode"] == "generation"


def test_proposal_then_no_stays_conversational():
    seed = {"conversation": {"pending_proposal": True, "artifacts": ["brd"]}}
    _, final = _drive(_pipe(), ["not yet, there's more to tell you"], seed_state=seed)
    ctx = get_context(final.state)
    assert ctx["conversation"]["mode"] == "discovery"
    assert ctx["conversation"]["pending_proposal"] is False


# ------------------------------------------------------------- persistence

def test_mode_and_transcript_survive_a_workspace_reload():
    _, final = _drive(_pipe(), ["We're a 40-person logistics company."])
    ws = ProjectWorkspace(WS, create=False)
    ctx = ws.get_context()
    assert ctx.get("conversation", {}).get("mode") == "discovery", ctx.get("conversation")
    assert ctx["conversation"]["turn"] == 1
    assert ctx["conversation"]["transcript"], ctx["conversation"]


# ---------------------------------------------------------------- the tool

def test_record_knowledge_tool_writes_through_live_state():
    from google.adk.sessions.state import State

    from ba_agent import ba_tools as T

    class TC:
        pass

    st = State({"project_workspace_id": "convadk"}, {})
    tc = TC()
    tc.state = st
    assert T.record_knowledge(tc, "rule", "cap",
                              "hotel bookings must stay under 180 EUR a night")["ok"] is True
    assert T.record_knowledge(tc, "rule", "cap", "")["ok"] is False       # no invention
    assert T.record_knowledge(tc, "nonsense", "x", "y")["ok"] is False
    ctx = st.get("project_context")
    assert ctx["business_rules"]["cap"].startswith("hotel bookings")
    assert ctx["knowledge"][0]["id"] == "K-001"
    assert ctx["knowledge"][0]["kind"] == "rule"
    assert T.ask_user(tc, "Who signs off the bigger trips?")["ok"] is True
    assert any(k.startswith("Who signs") for k in ctx["open_questions"])
    status = T.knowledge_status(tc)
    assert status["ok"] and status["captured"] >= 2
    assert T.propose_generation(tc, "brd, diagrams")["ok"] is True
    assert ctx["conversation"]["pending_proposal"] is True
