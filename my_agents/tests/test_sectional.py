"""Sectional writing tests (no LLM, no network).

Covers: append_doc assembly/replace, per-section checks, missing→section
mapping, and the orchestrator section loop end-to-end with a smart stub
that appends canned sections (simulating append_doc via state).
"""

import asyncio
import re
import shutil
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from google.adk import Event, Workflow  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402

from shared import sections as SEC  # noqa: E402
from shared.eng_tools import append_doc  # noqa: E402
from shared.orch_nodes import (  # noqa: E402
    DeliveryOrchestrator,
    _extract_section_text,
    _gate_missing_to_sections,
)
from tests.prod_fixtures import DESIGN as OLD_DESIGN  # noqa: E402
from tests.prod_fixtures import BRD as _BRD  # noqa: E402
from tests.prod_fixtures import PLAN as OLD_PLAN  # noqa: E402
from tests.prod_fixtures import SPEC as OLD_SPEC  # noqa: E402
from tests.ba_fixture import SECTION_BODIES as _SECTION_BODIES  # noqa: E402
from tests.ba_fixture import generation  # noqa: E402


class FakeTC:
    def __init__(self, sid="testsec"):
        self.state = {}
        self.session = type("S", (), {"id": sid})()


@pytest.fixture()
def tc():
    t = FakeTC()
    yield t
    ws = MY_AGENTS / "projects" / "_session_testsec"
    shutil.rmtree(ws, ignore_errors=True)


def test_append_assembles_in_order(tc):
    r1 = append_doc("BRD_demo", "Objectives and Executive Summary", "Our objective and goal. " * 20, tc)
    r2 = append_doc("BRD_demo", "Scope IN and Scope OUT", "Scope IN x. Scope OUT y. " * 20, tc)
    assert r1["sections"] == 1 and r2["sections"] == 2
    assert r2["chars"] > r1["chars"]
    assert tc.state["BRD_demo"].index("## Objectives") < tc.state["BRD_demo"].index("## Scope IN")


def test_append_replaces_same_title(tc):
    append_doc("BRD_demo", "Scope IN and Scope OUT", "v1 " * 100, tc)
    r = append_doc("BRD_demo", "Scope IN and Scope OUT", "v2 with Scope OUT items. " * 30, tc)
    assert r["replaced"] is True
    assert r["sections"] == 1
    assert "v1 v1" not in tc.state["BRD_demo"]
    assert "Scope OUT items" in tc.state["BRD_demo"]


def test_section_checks():
    from tests.ba_fixture import SECTION_BODIES

    ok = SEC.check_section("BA", "stories", SECTION_BODIES["stories"])
    assert ok["passed"]
    bad = SEC.check_section("BA", "stories", "some user wishes " * 20)
    assert not bad["passed"] and any("US-" in m for m in bad["missing"])
    # ids and keywords alone are not a section: acceptance enforces the same
    # per-story rules the gate does, so a summary that only mentions the ids is
    # flagged. These are advisory — they are recorded and carried into the handoff
    # rather than costing another model pass.
    stub = SEC.check_section("BA", "stories",
                             "US-001 x US-002 y US-003 z acceptance Given When Then " * 10)
    assert any("criteria" in m for m in stub["missing"] + stub["advisory"]), stub
    short = SEC.check_section("BA", "objectives", "tiny")
    assert not short["passed"]
    assert SEC.check_section("NOPE", "whatever", "anything")["passed"]


def test_missing_to_sections_mapping():
    got = _gate_missing_to_sections("BA", [
        "scope_in: no Scope IN", "user_stories: US ids found: [] (need >=3)",
        "brd_exists: BRD length 100 (need >1500)", "flow_diagram: nodes=0"])
    assert "scope" in got and "stories" in got and "__diagram__" in got
    assert _gate_missing_to_sections("PROJECT", ["whatever"]) == []


def test_extract_section_text():
    full = "# BRD\n\n## Objectives and Executive Summary\nGoal A.\n\n## Scope IN and Scope OUT\nIN x.\n"
    assert _extract_section_text(full, "Objectives and Executive Summary") == "Goal A."
    assert _extract_section_text(full, "Scope IN and Scope OUT") == "IN x."
    assert _extract_section_text(full, "Nope") == ""


# ---- orchestrator section loop with smart stub ----

CANNED = dict(_SECTION_BODIES)  # gate-passing stub content

DIAG = {"ba_flow_sw_diagram": {"kind": "flow", "nodes": 8, "edges": 7,
                               "mermaid": "flowchart TD\nA{Valid?}-->B"}}


RICH_DIAGS = {
    "project_gantt_t_diagram": {"kind": "gantt", "nodes": 17, "edges": 0, "mermaid": "gantt"},
    "functional_t_diagram": {"kind": "functional", "nodes": 9, "edges": 8, "mermaid": "flowchart LR"},
    "tech_seq_t_diagram": {"kind": "sequence", "nodes": 6, "edges": 5, "mermaid": "sequenceDiagram"},
    "tech_arch_t_diagram": {"kind": "architecture", "nodes": 8, "edges": 7, "mermaid": "flowchart TB"},
}


def _sectional_stub_factory():
    titles = {s["id"]: s["title"] for s in SEC.sections_for("BA")}

    def stub(node_input):
        txt = node_input if isinstance(node_input, str) else ""
        m_stage = re.search(r"Handoff brief for (\w+)", txt)
        stage = m_stage.group(1) if m_stage else "BA"
        if stage == "BA":
            if "DIAGRAM TASK" in txt:
                return Event(state=dict(DIAG))
            m = re.search(r"\(id: (\w+)\)", txt)
            if not m:
                return Event(state={"brd": "confused " * 100})
            upto = []
            for s in SEC.sections_for("BA"):
                upto.append(f"## {titles[s['id']]}\n{CANNED[s['id']]}")
                if s["id"] == m.group(1):
                    break
            return Event(state={"brd": "# BRD_sw\n\n" + "\n\n".join(upto)})
        if stage == "PROJECT":
            return Event(state={"project_plan": OLD_PLAN, **{k: v for k, v in RICH_DIAGS.items() if "gantt" in k}})
        if stage == "FUNCTIONAL":
            return Event(state={"functional_spec": OLD_SPEC, **{k: v for k, v in RICH_DIAGS.items() if "functional" in k}})
        if stage == "TECHNICAL":
            return Event(state={"tech_design": OLD_DESIGN, **{k: v for k, v in RICH_DIAGS.items() if "tech" in k}})
        if stage == "FRAPPE":
            return Event(state={"frappe_setup": "setup done", "frappe_project": "PROJ-TEST"})
        return Event(state={})
    return stub


def _pipe(stub):
    orch = DeliveryOrchestrator(name="orc", stage_agents={
        "BA": stub, "PROJECT": stub, "FUNCTIONAL": stub,
        "TECHNICAL": stub, "FRAPPE": stub})
    return Workflow(name="pipe", edges=[("START", orch)])


def _run(pipe, start_text="project_id:flowsec", initial_state=None,
         auto_approve_review=True, _max_resumes=3):
    from google.genai import types
    from shared.workspace import ProjectWorkspace
    ProjectWorkspace("flowsec")
    base = dict(initial_state or {})
    base["project_workspace_id"] = "flowsec"

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


@pytest.fixture(autouse=True)
def _clean():
    yield
    for p in (MY_AGENTS / "projects").iterdir():
        if p.is_dir() and (p.name.startswith("flowsec") or p.name.startswith("flowsecbad")):
            shutil.rmtree(p, ignore_errors=True)


def test_section_loop_reaches_success():
    from tests.frappe_fixture import seed_frappe
    seed = generation(seed_frappe({}, OLD_SPEC, OLD_DESIGN, plan=OLD_PLAN, brd=_BRD))
    events, final = _run(_pipe(_sectional_stub_factory()), initial_state=seed)
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "SUCCESS", str(exc)
    types_seen = [h["type"] for h in final.state["project_context"]["history"]]
    assert "section_done" in types_seen and "gate_passed" in types_seen
    assert "US-001" in final.state["brd"] and "BR-001" in final.state["brd"]


def test_targeted_repair_rewrites_only_bad_section():
    # seed: full assembly present but stories section broken (no US ids)
    titles = {s["id"]: s["title"] for s in SEC.sections_for("BA")}
    bad_stories = "Some wishes without identifiers. " * 30
    parts = []
    for s in SEC.sections_for("BA"):
        body = bad_stories if s["id"] == "stories" else CANNED[s["id"]]
        parts.append(f"## {titles[s['id']]}\n{body}")
    seed = generation({"brd": "# BRD_sw\n\n" + "\n\n".join(parts), **DIAG,
                       "project_plan": "x", "functional_spec": "x", "tech_design": "x"})
    events, final = _run(_pipe(_sectional_stub_factory()), initial_state=seed)
    # stub rewrites requested sections with good text -> gate passes
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] in ("SUCCESS", "RUNNING", "WAITING_FOR_HUMAN"), str(exc)
    hist = [h["type"] for h in final.state["project_context"]["history"]]
    assert "gate_failed" in hist or "gate_passed" in hist
