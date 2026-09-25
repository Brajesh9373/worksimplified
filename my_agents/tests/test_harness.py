"""Harness + guardrail tests (no LLM, no network).

Covers the deterministic harness introduced for weak-model reliability:
prose ingest, snapshot/recompose assembly, domain anchoring, harness-owned
ID allocation/verification, monotonic restore, and human-section ingestion
on resume.
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

from shared import harness as H  # noqa: E402
from shared.orch_nodes import DeliveryOrchestrator  # noqa: E402
from shared.workspace import ProjectWorkspace, bind_workspace  # noqa: E402
from tests.prod_fixtures import BRD as OLD_BRD  # noqa: E402
from tests.prod_fixtures import DESIGN as OLD_DESIGN  # noqa: E402
from tests.prod_fixtures import PLAN as OLD_PLAN  # noqa: E402
from tests.prod_fixtures import SPEC as OLD_SPEC  # noqa: E402
from tests.ba_fixture import SECTION_BODIES as _SECTION_BODIES  # noqa: E402
from tests.ba_fixture import generation  # noqa: E402

WS = "harnessws"
WS_FLOW = "harnessflow"

CANNED = dict(_SECTION_BODIES)  # gate-passing stub content

DIAG = {"ba_flow_harness_diagram": {"kind": "flow", "nodes": 8, "edges": 7,
                                    "mermaid": "flowchart TD\nA{Valid?}-->B"}}

RICH_DIAGS = {
    "project_gantt_h_diagram": {"kind": "gantt", "nodes": 17, "edges": 0, "mermaid": "gantt"},
    "functional_h_diagram": {"kind": "functional", "nodes": 9, "edges": 8, "mermaid": "flowchart LR"},
    "tech_seq_h_diagram": {"kind": "sequence", "nodes": 6, "edges": 5, "mermaid": "sequenceDiagram"},
    "tech_arch_h_diagram": {"kind": "architecture", "nodes": 8, "edges": 7, "mermaid": "flowchart TB"},
}


@pytest.fixture(autouse=True)
def _clean():
    yield
    for name in (WS, WS_FLOW):
        shutil.rmtree(MY_AGENTS / "projects" / name, ignore_errors=True)


def _state(goal=""):
    from shared.project_context import get_context
    st = {}
    bind_workspace(st, WS)
    ctx = get_context(st)
    ctx["project"]["name"] = goal or "employee travel expense management"
    if goal:
        ctx["project"]["goal"] = goal
    ProjectWorkspace(WS).save_artifact("brd_assembled.md", "# seed\n")
    return st


def _section(stage, sid):
    from shared.sections import sections_for
    return {s["id"]: s for s in sections_for(stage)}[sid]


# ---------------------------------------------------------------- text utils

def test_ingest_prose_reply_variants():
    body = "Objective: travel expense control.\n" * 8
    assert body.strip() in H.ingest_prose_reply(
        f"## Objectives and Executive Summary\n\n{body}", "Objectives and Executive Summary")
    fenced = f"Here is the section:\n```markdown\n## Scope IN and Scope OUT\n\nScope IN: x. Scope OUT: y.\n```"
    got = H.ingest_prose_reply(fenced, "Scope IN and Scope OUT")
    assert got.startswith("Scope IN") and "```" not in got
    # headers present but none match -> not this section
    assert H.ingest_prose_reply("## Business Rules\n\nBR-001 x.\n", "Objectives and Executive Summary") == ""
    # bare body is accepted as-is
    assert H.ingest_prose_reply("Just a body about travel.", "Objectives and Executive Summary") == "Just a body about travel."


def test_title_match_normalizes_punctuation():
    assert H.title_match("User Stories & Acceptance Criteria", "User Stories and Acceptance Criteria")
    assert not H.title_match("Scope IN and Scope OUT", "Business Rules")


# ------------------------------------------------------------ acceptance API

def test_accept_section_snapshots_and_recomposes():
    st = _state()
    sec_o = _section("BA", "objectives")
    sec_s = _section("BA", "scope")
    r1 = H.accept_section(st, "BA", sec_o, CANNED["objectives"], "brd")
    assert r1["passed"], r1
    r2 = H.accept_section(st, "BA", sec_s, CANNED["scope"], "brd")
    assert r2["passed"], r2
    doc = st["brd"]
    assert doc.index("## Objectives") < doc.index("## Scope IN")
    assert "Scope IN" in doc and doc.count("## ") == 2
    # re-accepting an earlier section replaces it in place (no duplicates)
    r3 = H.accept_section(st, "BA", sec_o, CANNED["objectives"] + "Updated objective detail. " * 4, "brd")
    assert r3["passed"] and st["brd"].count("## Objectives") == 1
    assert st["brd"].index("## Objectives") < st["brd"].index("## Scope IN")


def test_accept_section_fails_closed():
    st = _state()
    sec = _section("BA", "objectives")
    short = H.accept_section(st, "BA", sec, "tiny", "brd")
    assert not short["passed"] and any("too short" in m for m in short["missing"])


def test_wbs_section_requires_table_rows():
    from shared.sections import check_section
    bullets = ("T-001 discover. T-002 design. T-003 build. T-004 test. T-005 deploy. "
               "task breakdown and sprint phasing. ") * 6
    res = check_section("PROJECT", "wbs", bullets)
    assert not res["passed"]
    assert any("table rows" in m or "no T-xxx table rows" in m for m in res["missing"]), res
    rows = ("| ID | Task | Owner | Priority | Estimate | Start | End | Dependencies | Trace |\n"
            "|----|------|-------|----------|----------|-------|-----|--------------|-------|\n"
            "| T-001 | Travel request form | Analyst | P0 | 3d | 2026-09-21 | 2026-09-23 | - | US-001 |\n"
            "| T-002 | Approval workflow | Engineer | P0 | 2d | 2026-09-24 | 2026-09-25 | T-001 | US-002 |\n"
            "| T-003 | Expense claim form | Engineer | P1 | 4d | 2026-09-28 | 2026-09-30 | T-002 | US-003 |\n"
            "| T-004 | Receipt upload | Engineer | P1 | 2d | 2026-10-01 | 2026-10-02 | T-003 | US-004 |\n"
            "| T-005 | Reimbursement run | Analyst | P2 | 3d | 2026-10-05 | 2026-10-07 | T-004 | US-005 |\n"
            ) + "WBS task breakdown, sprint phasing. " * 6
    ok = check_section("PROJECT", "wbs", rows)
    assert ok["passed"], ok


def test_domain_anchor_rejects_drift():
    st = _state(goal="Build an employee travel expense management system")
    sec = _section("BA", "objectives")
    drift = ("Our objective is to streamline employee onboarding, leave balance "
             "tracking and performance appraisal. " * 6)
    res = H.accept_section(st, "BA", sec, drift, "brd")
    assert not res["passed"]
    assert any("domain_drift" in m for m in res["missing"]), res
    good = ("Objective: streamline travel expense claims and reimbursements. "
            "Executive summary and goal. " * 6)
    assert H.accept_section(st, "BA", sec, good, "brd")["passed"]


def test_id_registry_allocates_and_verifies():
    st = _state()
    sec = _section("BA", "stories")
    alloc = H.allocate_ids(st, "BA", sec, "brd")
    assert alloc == {"US": ["US-001", "US-002", "US-003"]}, alloc
    # allocation is stable across calls (retries reuse the same IDs)
    assert H.allocate_ids(st, "BA", sec, "brd") == alloc
    text_no_ids = "User stories with acceptance criteria and Given When Then. " * 6
    res = H.accept_section(st, "BA", sec, text_no_ids, "brd")
    assert not res["passed"] and any("allocated ids" in m for m in res["missing"]), res
    from tests.ba_fixture import SECTION_BODIES

    assert H.accept_section(st, "BA", sec, SECTION_BODIES["stories"], "brd")["passed"]
    # registry advanced past the accepted IDs
    from shared.project_context import get_context
    assert get_context(st)["id_registry"]["US"] == 4


def test_restore_is_monotonic():
    st = _state()
    H.accept_section(st, "BA", _section("BA", "objectives"), CANNED["objectives"], "brd")
    H.accept_section(st, "BA", _section("BA", "scope"), CANNED["scope"], "brd")
    good = st["brd"]
    # a model rewrite clobbers both state and the canonical file
    st["brd"] = "# Rewritten\n\n## Scope IN and Scope OUT\n\nScope IN: replaced. Scope OUT: replaced.\n"
    ProjectWorkspace(WS).save_artifact("brd_assembled.md", st["brd"])
    assert H.sync_canonical(st, "BA", "brd") == len(good)
    assert "## Objectives" in st["brd"] and "## Scope IN" in st["brd"]
    assert st["brd"].index("## Objectives") < st["brd"].index("## Scope IN")


def test_drop_snapshots_forces_rewrite():
    st = _state()
    H.accept_section(st, "BA", _section("BA", "objectives"), CANNED["objectives"], "brd")
    H.accept_section(st, "BA", _section("BA", "scope"), CANNED["scope"], "brd")
    assert H.drop_snapshots(st, "BA", ids=["scope"], doc_key="brd") == 1
    assert "## Scope IN" not in st["brd"]
    assert "## Objectives" in st["brd"]


def test_sync_prefers_canonical_only_when_harness_owns_stage():
    from shared.orch_nodes import _sync_assembly_to_state
    st = _state()
    legacy = "# Legacy BRD\n\n" + ("Scope IN x. Scope OUT y. " * 60)
    ProjectWorkspace(WS).save_artifact("BRD_legacy_thing_assembled.md", legacy)
    st.pop("brd", None)
    # no snapshots yet: a stale canonical leftover must not shadow the artifact
    assert _sync_assembly_to_state(st, "BA") == len(legacy)
    assert "Legacy BRD" in st["brd"]
    # once the harness owns the stage, the canonical doc is authoritative
    H.accept_section(st, "BA", _section("BA", "objectives"), CANNED["objectives"], "brd")
    _sync_assembly_to_state(st, "BA")
    assert "## Objectives" in st["brd"] and "Legacy BRD" not in st["brd"]


# ------------------------------------------------- production-grade detail

_WBS_HEAD = ("| ID | Task | Owner Role | Priority | Estimate | Start | End | "
             "Dependencies | Trace |\n"
             "|----|------|-----------|----------|----------|-------|-----|--------------|-------|\n")


def _wbs_row(tid, owner="Analyst", pri="P0", est="3d", start="2026-09-21",
             end="2026-09-23", dep="-", trace="US-001"):
    return (f"| {tid} | Task {tid} | {owner} | {pri} | {est} | {start} | {end} | "
            f"{dep} | {trace} |\n")


def _wbs_text():
    return ("WBS task breakdown and sprint phasing for delivery. " * 6 + "\n"
            + _WBS_HEAD + "".join(_wbs_row(f"T-{i:03d}") for i in range(1, 6)))


def test_wbs_detail_rules():
    from shared.sections import check_section
    text = _wbs_text()
    assert check_section("PROJECT", "wbs", text)["passed"], check_section("PROJECT", "wbs", text)["missing"][:3]
    bad = text.replace("| P0 |", "|  |", 1)
    assert any("lacks a priority" in m for m in check_section("PROJECT", "wbs", bad)["advisory"])
    bad = text.replace("| 2026-09-21 | 2026-09-23 |", "| soon | later |", 1)
    assert any("start/end dates" in m for m in check_section("PROJECT", "wbs", bad)["advisory"])
    bad = text.replace("| 2026-09-21 | 2026-09-23 |", "| 2026-09-23 | 2026-09-21 |", 1)
    assert any("before start" in m for m in check_section("PROJECT", "wbs", bad)["advisory"])
    bad = text.replace("| - | US-001 |", "|  | US-001 |", 1)
    assert any("lacks dependencies" in m for m in check_section("PROJECT", "wbs", bad)["advisory"])
    bad = text.replace("| - | US-001 |", "| - |  |", 1)
    assert any("lacks a trace id" in m for m in check_section("PROJECT", "wbs", bad)["advisory"])
    # a dependency table's T-xxx rows are not WBS tasks (regression)
    deps = ("\n### Dependencies\n\n| Predecessor | Successor | Reason |\n|---|---|---|\n"
            "| T-001 | T-002 | needs the form |\n| T-002 | T-003 | flow first |\n")
    assert check_section("PROJECT", "wbs", text + deps)["passed"]


def test_schedule_and_raid_detail_rules():
    from shared.sections import check_section
    ms = ("### Milestones\n\n| Milestone | Date | Description |\n|---|---|---|\n"
          "| M1 Sign-off | 2026-09-19 | approved |\n| M2 Build | 2026-09-30 | built |\n"
          "| M3 Go-live | 2026-10-13 | live |\n\n"
          "### Dependencies\n\n| Predecessor | Successor | Reason |\n|---|---|---|\n"
          "| T-001 | T-002 | workflow binds |\n\n"
          "Critical path: T-001 -> T-002 (milestone and dependency planning). ")
    text = "Milestones, dependencies and critical path planning. " * 5 + "\n" + ms
    assert check_section("PROJECT", "schedule", text)["passed"], check_section("PROJECT", "schedule", text)["missing"][:3]
    bad = ms.replace("| M2 Build | 2026-09-30 |", "| M2 Build | soon |")
    assert any("no date" in m for m in check_section("PROJECT", "schedule", text.replace(ms, bad))["advisory"])
    raci = ("### RACI Matrix\n\n| Role / Request | Approval |\n|---|---|\n"
            "| Employee | **R** | – |\n| Manager | C | **A / R** |\n\n")
    raci_alt = ("### RACI Matrix\n\n| Task | R (Responsible) | A (Accountable) |\n|---|---|---|\n"
                "| T-001 | Business Analyst | Project Lead |\n| T-002 | Developer | Finance Manager |\n\n")
    raid = ("### RAID Log\n\n| ID | Type | Description | Severity | Owner | Mitigation | Review Date |\n"
            "|---|---|---|---|---|---|---|\n"
            "| R-001 | Risk | Receipt fraud | High | Finance | Duplicate detection | 2026-10-01 |\n"
            "| A-001 | Assumption | Hotel cap | Medium | PM | Confirm policy | 2026-09-22 |\n")
    rtext = "RACI matrix and RAID log for governance. " * 5 + "\n" + raci + raid
    assert check_section("PROJECT", "raci_raid", rtext)["passed"], check_section("PROJECT", "raci_raid", rtext)["missing"][:3]
    # the other valid RACI layout (columns R/A, cells = role names) also passes
    alt = "RACI matrix and RAID log for governance. " * 5 + "\n" + raci_alt + raid
    assert check_section("PROJECT", "raci_raid", alt)["passed"], check_section("PROJECT", "raci_raid", alt)["missing"][:3]
    bad = raid.replace("| High |", "|  |")
    assert any("severity/impact" in m for m in
               check_section("PROJECT", "raci_raid", rtext.replace(raid, bad))["advisory"])
    bad = raid.replace("| 2026-10-01 |", "|  |")
    assert any("review date" in m for m in
               check_section("PROJECT", "raci_raid", rtext.replace(raid, bad))["advisory"])


def test_functional_and_technical_detail_rules():
    from shared.sections import check_section
    uc = ("### UC-001 Submit Request\nActor: Employee. Precondition: logged in. "
          "Postcondition: request saved as Pending.\nPrimary flow: fill and submit. "
          "Alternate flow: missing field shows an error.\n")
    text = "Use case detail with alternate flows and actors. " * 6 + "\n" + uc
    assert check_section("FUNCTIONAL", "use_cases", text)["passed"]
    bad = uc.replace("Precondition:", "")
    assert any("precondition" in m for m in
               check_section("FUNCTIONAL", "use_cases", text.replace(uc, bad))["advisory"])
    val = ("Validation rules with error codes: E-TR-001, E-AP-002, E-CL-003. Entities and "
           "attributes: travel_request table with fields. " * 3)
    assert check_section("FUNCTIONAL", "validations_data", val)["passed"]
    bad_val = "Validation rules listed without codes. Entities: a table with fields. " * 4
    assert any("error codes" in m for m in
               check_section("FUNCTIONAL", "validations_data", bad_val)["advisory"])
    apis = ("| Endpoint | Method | Codes |\n|---|---|---|\n"
            "| POST /api/resource/Travel Request | POST | 200, 400 |\n"
            "| GET /api/resource/Travel Request | GET | 200 |\n"
            "| PUT /api/resource/Travel Request/{name} | PUT | 200, 404 |\n")
    atext = "API contracts and endpoint behaviour. " * 8 + "\n" + apis
    assert check_section("TECHNICAL", "apis", atext)["passed"]
    bad_apis = apis.replace("| 200, 400 |", "| ok |")
    assert any("response code" in m for m in
               check_section("TECHNICAL", "apis", atext.replace(apis, bad_apis))["advisory"])
    data = ("Data schema: travel_request table with name varchar primary key, amount decimal, "
            "start_date date, receipt_no varchar unique index. " * 3)
    assert check_section("TECHNICAL", "data", data)["passed"]
    bad_data = "Data schema: travel_request table with columns and rows listed. " * 4
    assert any("field types" in m for m in
               check_section("TECHNICAL", "data", bad_data)["advisory"])


def test_gate_rejects_vague_plan():
    from shared import gates as G
    filler = "The project delivers a travel and expense capability with clear scope. " * 20
    vague = ("# Plan\n\n" + filler + "\n\n## Work Breakdown Structure\n\n"
             "| ID | Task | Owner |\n|---|---|---|\n"
             + "".join(f"| T-{i:03d} | do the thing | dev |\n" for i in range(1, 7))
             + "\nMilestone and dependency and critical path sections follow. " * 6)
    res = G.project_gate({"project_plan": vague})
    assert not res["passed"]
    # detail gaps are advisories now; a structural failure is what blocks
    assert any(m.startswith("wbs_detail") for m in res["warnings"]), res["warnings"][:4]
    assert any(m.startswith("task_trace") for m in res["missing"]), res["missing"][:4]


# ----------------------------------------------------- orchestrator ladder

def _pipe(stub, workspace_id=WS_FLOW):
    orch = DeliveryOrchestrator(name="orc", stage_agents={
        "BA": stub, "PROJECT": stub, "FUNCTIONAL": stub,
        "TECHNICAL": stub, "FRAPPE": stub})
    return Workflow(name="pipe", edges=[("START", orch)])


def _run(pipe, start_text="project_id:harnessflow", initial_state=None, resume=None):
    from google.genai import types
    from shared.workspace import ProjectWorkspace
    wid = WS_FLOW
    ProjectWorkspace(wid)
    base = dict(initial_state or {})
    base["project_workspace_id"] = wid

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=base)
        events = []
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text=start_text)])):
            events.append(ev)
        if resume is not None:
            inv_id, interrupt_id, answer = resume
            async for ev in runner.run_async(
                    user_id="u1", session_id=sess.id, invocation_id=inv_id,
                    new_message=types.Content(role="user", parts=[types.Part(
                        function_response=types.FunctionResponse(
                            id=interrupt_id, name="adk_request_input",
                            response={"result": answer}))])):
                events.append(ev)
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return events, final
    return asyncio.run(go())


def _prose_stub():
    """Tool pass returns nothing useful; the prose reply carries the section."""
    def stub(node_input):
        txt = node_input if isinstance(node_input, str) else ""
        stage_m = re.search(r"Handoff brief for (\w+)", txt)
        stage = stage_m.group(1) if stage_m else "BA"
        if stage == "BA":
            if "DIAGRAM TASK" in txt:
                return Event(state=dict(DIAG))
            m = re.search(r"\(id: (\w+)\)", txt)
            if not m:
                return Event(state={"brd": ""})
            if "PROSE TASK" in txt:
                # plain string reply — captured by the orchestrator, ingested
                sec = m.group(1)
                from shared.sections import sections_for
                title = {s["id"]: s["title"] for s in sections_for("BA")}[sec]
                return f"## {title}\n\n{CANNED[sec]}"
            return Event(state={"brd": "narration only, no section written"})
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


def test_prose_ladder_reaches_success():
    from google.genai import types

    from tests.frappe_fixture import seed_frappe
    seed = generation(seed_frappe({}, OLD_SPEC, OLD_DESIGN, plan=OLD_PLAN, brd=OLD_BRD))
    pipe = _pipe(_prose_stub())

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1",
            state={**seed, "project_workspace_id": WS_FLOW})
        from shared.workspace import ProjectWorkspace
        ProjectWorkspace(WS_FLOW)
        events = []
        msg = types.Content(role="user", parts=[types.Part(text="project_id:harnessflow")])
        inv = None
        for _ in range(3):
            inv_id = interrupt_id = None
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
            # Only the BA final review is auto-approved; other pauses fail loud.
            assert final.state["project_context"].get("ba_final_review_pending"), str(exc)
            msg = types.Content(role="user", parts=[types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id, name="adk_request_input",
                    response={"result": "approve"}) )])
            inv = inv_id
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return events, final
    events, final = asyncio.run(go())
    exc = final.state["project_context"]["execution"]
    assert exc["workflow_status"] == "SUCCESS", str(exc)
    hist = final.state["project_context"]["history"]
    assert any(h["type"] == "section_done" and "prose" in h["summary"] for h in hist), hist[-8:]
    assert "US-001" in final.state["brd"] and "BR-001" in final.state["brd"]


def _failing_stub():
    def stub(node_input):
        return Event(state={"brd": "lorem ipsum dolor sit amet " * 30})
    return stub


def _pause_and_answer(pipe, answer):
    """Run to the first human pause, then resume with `answer`."""
    import types as _types
    from google.genai import types

    def _answer_content(ans):
        return types.Content(role="user", parts=[types.Part(text=ans)])

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1",
            state=generation({"project_workspace_id": WS_FLOW}))
        inv_id = interrupt_id = None
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text="project_id:harnessflow")])):
            if getattr(ev, "long_running_tool_ids", None):
                inv_id = ev.invocation_id
                for fc in ev.get_function_calls():
                    interrupt_id = fc.id
        mid = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        if interrupt_id:
            async for ev in runner.run_async(
                    user_id="u1", session_id=sess.id, invocation_id=inv_id,
                    new_message=types.Content(role="user", parts=[types.Part(
                        function_response=types.FunctionResponse(
                            id=interrupt_id, name="adk_request_input",
                            response={"result": answer}))])):
                pass
        final = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return mid, final
    return asyncio.run(go())


def test_human_section_text_is_ingested():
    ProjectWorkspace(WS_FLOW)
    pipe = _pipe(_failing_stub())
    good = "Objective: control travel expense. Executive summary and goal. " * 8
    mid, final = _pause_and_answer(
        pipe, f"## Objectives and Executive Summary\n\n{good}")
    hist = [h["type"] for h in final.state["project_context"]["history"]]
    assert "human_section_ingested" in hist, hist
    pc = final.state["project_context"]
    assert "objectives" in (pc.get("sections_done_BA") or []), pc.get("sections_done_BA")


def test_resume_without_answer_keeps_waiting():
    import types as _types
    from google.genai import types
    from shared.workspace import ProjectWorkspace as PW

    PW(WS_FLOW)

    async def go():
        runner = InMemoryRunner(agent=_pipe(_failing_stub()), app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=generation({"project_workspace_id": WS_FLOW}))
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text="project_id:harnessflow")])):
            pass
        mid = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        mid_exc = dict(mid.state["project_context"]["execution"])
        # a second run WITHOUT a resume answer must not clear the pause
        async for ev in runner.run_async(
                user_id="u1", session_id=sess.id,
                new_message=types.Content(role="user", parts=[types.Part(text="hello?")])):
            pass
        after = await runner.session_service.get_session(
            app_name="pipe", user_id="u1", session_id=sess.id)
        await runner.close()
        return mid_exc, dict(after.state["project_context"]["execution"])
    before, after = asyncio.run(go())
    assert before["workflow_status"] == "WAITING_FOR_HUMAN"
    assert after["workflow_status"] == "WAITING_FOR_HUMAN"
    assert after["iteration_count"] == before["iteration_count"]

