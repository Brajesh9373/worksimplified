"""Orchestrator flow tests with stub stage agents (no LLM, no network).

Exercises the real DeliveryOrchestrator: linear SUCCESS path, CR-routed
iteration, and gate-failure → human-review interrupt.
"""

import asyncio
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from google.adk import Event, Workflow  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402

from shared.orch_nodes import DeliveryOrchestrator  # noqa: E402
from tests.ba_fixture import generation  # noqa: E402
from tests.frappe_fixture import seed_frappe  # noqa: E402

OUT = MY_AGENTS / "_outputs"

# production-grade fixtures (the standard the gates enforce)
from tests.prod_fixtures import BRD, DESIGN, PLAN, SPEC  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_test_workspaces():
    import shutil
    proots = MY_AGENTS / "projects"
    yield
    for p in proots.iterdir():
        if p.is_dir() and (p.name.startswith("flowtest") or p.name.startswith("test_project") or p.name.startswith("go_")):
            shutil.rmtree(p, ignore_errors=True)


_WS_SEQ = {"n": 0}


def _new_ws_id():
    _WS_SEQ["n"] += 1
    return f"flowtest{_WS_SEQ['n']}"


DIAGS = {
    "ba_flow_t_diagram": {"kind": "flow", "nodes": 19, "edges": 16, "mermaid": "flowchart TD\nA{OK?}-->B"},
    "project_gantt_t_diagram": {"kind": "gantt", "nodes": 17, "edges": 0, "mermaid": "gantt"},
    "functional_t_diagram": {"kind": "functional", "nodes": 9, "edges": 8, "mermaid": "flowchart LR"},
    "tech_seq_t_diagram": {"kind": "sequence", "nodes": 6, "edges": 5, "mermaid": "sequenceDiagram"},
    "tech_arch_t_diagram": {"kind": "architecture", "nodes": 8, "edges": 7, "mermaid": "flowchart TB"},
}


def _stubs():
    def stub_ba(node_input):
        return Event(state={"brd": BRD, **DIAGS})
    def stub_project(node_input):
        return Event(state={"project_plan": PLAN})
    def stub_functional(node_input):
        return Event(state={"functional_spec": SPEC})
    def stub_technical(node_input):
        return Event(state={"tech_design": DESIGN})
    def stub_frappe(node_input):
        return Event(state={"frappe_setup": "setup done", "frappe_project": "PROJ-TEST"})
    return {"BA": stub_ba, "PROJECT": stub_project, "FUNCTIONAL": stub_functional,
            "TECHNICAL": stub_technical, "FRAPPE": stub_frappe}


def _run(pipe, start_text="test project", initial_state=None, workspace_id=None,
         auto_approve_review=True, _max_resumes=3):
    """Run to completion, auto-approving BA final-review pauses.

    Reviews are real WAITING_FOR_HUMAN pauses answered with 'approve' via
    FunctionResponse — the same path as adk web resume. Set
    auto_approve_review=False to stop at the first pause.
    """
    from google.genai import types
    from shared.workspace import ProjectWorkspace
    wid = workspace_id or _new_ws_id()
    ProjectWorkspace(wid)  # ensure dirs exist
    base = dict(initial_state or {})
    base["project_workspace_id"] = wid  # pre-bind: no ad-hoc creation
    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=base)
        events = []
        msg = types.Content(role="user", parts=[types.Part(text=start_text)])
        inv = None
        for _ in range(_max_resumes + 1):
            inv_id = None
            interrupt_id = None
            kwargs = dict(user_id="u1", session_id=sess.id, new_message=msg)
            if inv:
                kwargs["invocation_id"] = inv
            async for ev in runner.run_async(**kwargs):
                events.append(ev)
                if getattr(ev, "long_running_tool_ids", None):
                    inv_id = ev.invocation_id
                    for fc in ev.get_function_calls():
                        interrupt_id = fc.id
            final = await runner.session_service.get_session(
                app_name="pipe", user_id="u1", session_id=sess.id)
            exc = final.state["project_context"]["execution"]
            if exc["workflow_status"] != "WAITING_FOR_HUMAN":
                await runner.close()
                return events, final
            if not (auto_approve_review and inv_id and interrupt_id
                    and final.state["project_context"].get("ba_final_review_pending")):
                await runner.close()
                return events, final
            msg = types.Content(role="user", parts=[types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id, name="adk_request_input",
                    response={"result": "approve"}) )])
            inv = inv_id
        await runner.close()
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        return events, final
    return asyncio.run(go())


def test_success_path(monkeypatch):
    monkeypatch.setenv("BA_CHAT_VERBOSITY", "quiet")  # assert the owner-facing register
    orch = DeliveryOrchestrator(name="orc", stage_agents=_stubs())
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    seed = generation(seed_frappe({}, SPEC, DESIGN, plan=PLAN, brd=BRD))
    events, final = _run(pipe, initial_state=seed)
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "SUCCESS", str(exc)
    # The completion is reported to the owner in plain words (no internal
    # tokens), and the machine outcome is recorded in history.
    msgs = [e.content.parts[0].text for e in events
            if e.content and e.content.parts and e.content.parts[0].text]
    assert any(m.strip() for m in msgs), msgs
    assert not any("gate" in m.lower() or "checks" in m.lower() for m in msgs), msgs
    hist_types = [h["type"] for h in final.state["project_context"]["history"]]
    for expected in ("pipeline_started", "agent_started", "gate_passed", "validation_passed",
                     "ba_final_review_requested", "ba_final_review_approved"):
        assert expected in hist_types, hist_types


def test_cr_iteration_then_success():
    """Pre-seeded OPEN CR (TECHNICAL→FUNCTIONAL) with prior docs present:
    orchestrator must route back to FUNCTIONAL, resolve, and reach SUCCESS."""
    from shared import project_context as PC
    from shared.change_requests import create_cr, open_crs
    seed = generation({"brd": BRD, "project_plan": PLAN, "functional_spec": SPEC,
                       "tech_design": DESIGN, **DIAGS})
    PC.get_context(seed)
    seed_frappe(seed, SPEC, DESIGN, plan=PLAN, brd=BRD)
    cr = create_cr(seed, "TECHNICAL", "FUNCTIONAL", "FR-008 ambiguous", ["FR-008"])
    assert open_crs(seed)[0]["target_agent"] == "FUNCTIONAL"
    orch = DeliveryOrchestrator(name="orc", stage_agents=_stubs())
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    events, final = _run(pipe, initial_state=seed)
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "SUCCESS", str(exc)
    assert open_crs(final.state) == []
    hist_types = [h["type"] for h in final.state["project_context"]["history"]]
    assert "iteration_routed" in hist_types and "change_request_resolved" in hist_types


def test_gate_failure_requests_human():
    def stub_empty(node_input):
        return Event(state={"brd": "lorem ipsum " * 300})
    stubs = _stubs()
    stubs["BA"] = stub_empty
    orch = DeliveryOrchestrator(name="orc", stage_agents=stubs, max_revisions_per_stage=0)
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    events, final = _run(pipe, initial_state=generation({}))
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "WAITING_FOR_HUMAN", str(exc)
    assert any(getattr(e, "long_running_tool_ids", None) for e in events)
    hist_types = [h["type"] for h in final.state["project_context"]["history"]]
    assert "section_failed" in hist_types  # sectional BA: empty stub fails section checks


def test_human_resume_abort():
    """AGENT → HUMAN → AGENT: persisted WAITING state resumes and honors 'abort'."""
    from google.genai import types

    def stub_empty(node_input):
        return Event(state={"brd": "lorem ipsum " * 300})
    stubs = _stubs()
    stubs["BA"] = stub_empty
    orch = DeliveryOrchestrator(name="orc", stage_agents=stubs, max_revisions_per_stage=0)
    pipe = Workflow(name="pipe", edges=[("START", orch)])

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        # This test exercises the pipeline/human-pause machinery, so the project
        # declares generation mode (conversation-first is the product default).
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1",
            state={"conversation": {"mode": "generation"}})
        inv_id = None
        interrupt_id = None
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text="go")])):
            if getattr(ev, "long_running_tool_ids", None):
                inv_id = ev.invocation_id
                for fc in ev.get_function_calls():
                    interrupt_id = fc.id
        assert inv_id and interrupt_id, "expected human-review interrupt with function call"
        mid = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        # persistence across the pause: gate failure + waiting flag survived
        assert mid.state["project_context"]["execution"]["workflow_status"] == "WAITING_FOR_HUMAN"
        assert any(h["type"] == "section_failed" for h in mid.state["project_context"]["history"])
        # answer exactly like adk web / `adk run` resume: FunctionResponse
        resume_msg = types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=interrupt_id, name="adk_request_input",
                response={"result": "abort"}) )])
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id, invocation_id=inv_id,
                new_message=resume_msg):
            pass
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return final
    final = asyncio.run(go())
    assert final.state["project_context"]["execution"]["workflow_status"] == "ABORTED"


def _run_until_pause(pipe, initial_state=None):
    """Run once without auto-approval; return (runner, sess, inv_id, interrupt_id, final)."""
    import asyncio as _aio

    from google.genai import types
    from shared.workspace import ProjectWorkspace
    wid = _new_ws_id()
    ProjectWorkspace(wid)
    base = dict(initial_state or {})
    base["project_workspace_id"] = wid
    runner = InMemoryRunner(agent=pipe, app_name="pipe")

    async def go():
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=base)
        inv_id = interrupt_id = None
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text="test project")])):
            if getattr(ev, "long_running_tool_ids", None):
                inv_id = ev.invocation_id
                for fc in ev.get_function_calls():
                    interrupt_id = fc.id
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        return sess, inv_id, interrupt_id, final
    out = _aio.run(go())
    return (runner,) + out


def _resume(runner, sess, inv_id, interrupt_id, text):
    import asyncio as _aio

    from google.genai import types
    msg = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=interrupt_id, name="adk_request_input",
            response={"result": text}) )])

    async def go():
        async for _ev in runner.run_async(
                user_id="u1", session_id=sess.id, invocation_id=inv_id,
                new_message=msg):
            pass
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return final
    return _aio.run(go())


def test_final_review_pause_requests_approval():
    orch = DeliveryOrchestrator(name="orc", stage_agents=_stubs())
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    seed = generation(seed_frappe({}, SPEC, DESIGN, plan=PLAN, brd=BRD))
    runner, _sess, inv_id, interrupt_id, final = _run_until_pause(pipe, seed)
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "WAITING_FOR_HUMAN", str(exc)
    assert exc["next_stage"] == "PROJECT"
    assert final.state["project_context"].get("ba_final_review_pending") is True
    hist = [h["type"] for h in final.state["project_context"]["history"]]
    assert "ba_final_review_requested" in hist and "gate_passed" in hist
    await_close = runner.close()
    import asyncio as _aio
    _aio.run(await_close)


def test_final_review_revision_returns_to_ba():
    orch = DeliveryOrchestrator(name="orc", stage_agents=_stubs())
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    seed = generation(seed_frappe({}, SPEC, DESIGN, plan=PLAN, brd=BRD))
    runner, sess, inv_id, interrupt_id, _mid = _run_until_pause(pipe, seed)
    final = _resume(runner, sess, inv_id, interrupt_id,
                    "Add a per-diem rule before release")
    # Revision loops BA -> gate -> review pause again (no silent advance).
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "WAITING_FOR_HUMAN", str(exc)
    assert final.state["project_context"].get("ba_final_review_pending") is True
    hist = [h["type"] for h in final.state["project_context"]["history"]]
    assert hist.count("gate_passed") >= 2  # BA re-ran and re-passed
    assert hist.count("ba_final_review_requested") >= 2
    assert "ba_final_review_changes_requested" in hist


def test_stage_gate_exhaustion_advances_with_advisories():
    """A stage whose gate keeps failing must not re-run forever. The old code reset
    the revision counter at the escalation pause, so every human answer granted a
    fresh budget — one FRAPPE stage ran 21 times with 18 gate failures and cost 30
    minutes. It now advances once, carrying the gaps as advisories."""
    from shared import project_context as PC
    from shared.orch_nodes import MAX_REVISIONS_PER_STAGE

    stubs = _stubs()

    def stub_thin_frappe(node_input):
        return Event(state={})

    stubs["FRAPPE"] = stub_thin_frappe
    orch = DeliveryOrchestrator(name="orc", stage_agents=stubs)
    pipe = Workflow(name="pipe", edges=[("START", orch)])
    seed = generation(seed_frappe({}, SPEC, DESIGN, plan=PLAN, brd=BRD))
    # strip the fixture's passing Frappe evidence so FRAPPE's gate really fails, and
    # start it already at the revision cap (deterministic, no section-timing luck)
    seed.pop("frappe_project", None)
    PC.get_context(seed)["frappe_state"] = {}
    PC.get_context(seed)["revisions_FRAPPE"] = MAX_REVISIONS_PER_STAGE
    _events, final = _run(pipe, initial_state=seed, _max_resumes=8)
    ctx = final.state["project_context"]

    carried = [h for h in ctx["history"] if h["type"] == "stage_carried_forward"]
    assert carried, [h["type"] for h in ctx["history"]][-16:]
    assert "FRAPPE" in str(carried[0].get("summary", "")), carried
    # bounded attempts, then advanced rather than re-run forever
    failures = [h for h in ctx["history"]
                if h["type"] == "gate_failed" and "FRAPPE" in str(h.get("summary", ""))]
    assert len(failures) <= MAX_REVISIONS_PER_STAGE + 1, len(failures)
    # the unreached checks ride forward instead of vanishing
    assert ctx.get("gate_warnings", {}).get("FRAPPE"), ctx.get("gate_warnings")


def test_final_review_approval_cues_are_word_safe():
    from shared.orch_nodes import _is_final_approval

    assert _is_final_approval("approve") and _is_final_approval("Looks good, release it")
    assert _is_final_approval("yes") and _is_final_approval("OK")
    assert _is_final_approval("proceed") and _is_final_approval("LGTM")
    # benign dismissals still approve
    assert _is_final_approval("approve, no changes needed")
    assert _is_final_approval("no objections, go ahead and proceed")
    assert not _is_final_approval("This is broken, fix the totals")
    assert not _is_final_approval("Add a per-diem rule before release")


def test_final_review_never_releases_on_negated_approval():
    """Negated/qualified replies must not release the package (silent advance)."""
    from shared.orch_nodes import _is_final_approval

    for text in ("disapprove", "I disapprove of this scope", "unapproved",
                 "not confirmed yet", "unconfirmed", "do not approve",
                 "cannot approve this", "reject", "hold on", "wait",
                 "please revise the scope", "revise then approve",
                 "yes, but fix the totals", "looks good, add a per-diem rule",
                 "approve nothing yet"):
        assert not _is_final_approval(text), f"{text!r} must not approve"
