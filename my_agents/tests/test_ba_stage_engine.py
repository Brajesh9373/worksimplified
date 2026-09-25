"""BA stage engine tests — assignments, auto-batch, package hook, contracts."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from tests.ba_fixture import ALL_SECTIONS, ba_state  # noqa: E402
from tests.prod_fixtures import BRD as _BRD  # noqa: E402

BRD_FULL = _BRD

DIAG = {"kind": "flow", "nodes": 19, "edges": 16,
        "mermaid": "flowchart TD\nA{OK?}-->B", "mmd": "ba_flow_t.mmd"}


def _state(brd="", sections=(), diagram=False, project="Engine Test", v2=True):
    from shared.project_context import get_context

    s: dict = {}
    if brd:
        s["brd"] = brd
    if diagram:
        s["ba_flow_diagram"] = dict(DIAG)
    ctx = get_context(s)
    ctx["sections_done_BA"] = list(sections)
    ctx["project"] = {"name": project}
    if v2 and brd:
        from tests.frappe_fixture import seed_ba_v2

        seed_ba_v2(s, brd)
    return s


def test_fragments_fully_owned_no_dead_contract():
    from ba_agent import prompts as P
    from ba_agent import stage_engine as E

    owned = {f for frags in E.STAGE_FRAGMENTS.values() for f in frags}
    all_frags = {k for k in vars(P) if k.startswith("FRAG_")}
    assert all_frags <= owned, f"orphan fragments: {all_frags - owned}"
    assert set(E.STAGE_FRAGMENTS) == set(E.STAGES)


def test_orchestrator_delegates_order():
    from ba_agent import orchestrator as O
    from ba_agent import stage_engine as E

    assert list(O.STAGE_ORDER) == list(E.STAGES)
    assert O.next_step("S3", missing=["x"])["action"] == "ask_human"
    assert O.next_step("S3", missing=[], confidence=0.9)["stage"] == "S4"


def test_assignment_names_exact_blockers():
    from ba_agent import stage_engine as E

    a = E.stage_assignment(_state(sections=["objectives"]))
    assert a.startswith("STAGE TASK S2")
    assert "scope" in a and "asis_tobe" in a  # exact missing sections
    a4 = E.stage_assignment(_state(brd=_BRD, sections=ALL_SECTIONS))
    assert a4.startswith("STAGE TASK S7")
    assert "gate" in a4 or "SM-" in a4  # validator substance, not boilerplate


def test_blockers_per_stage():
    from ba_agent import stage_engine as E

    assert E.blockers(_state(), "S1") == ["section 'objectives' not accepted"]
    assert E.blockers(_state(brd=BRD_FULL, sections=ALL_SECTIONS), "S6") == []
    b7 = E.blockers(_state(brd=BRD_FULL, sections=ALL_SECTIONS), "S7")
    assert any("flow diagram" in m for m in b7)  # gate substance


def test_auto_batch_once_per_stage_and_cr():
    from ba_agent import stage_engine as E
    from ba_agent.managers.question_manager import open_questions

    s = _state(sections=["objectives"])  # current S2
    r1 = E.auto_batch(s)
    assert r1["batched"] is True and r1["batch"]["stage"] == "S2"
    assert len(r1["batch"]["questions"]) <= 5
    assert E.auto_batch(s)["batched"] is False  # open batch exists
    # Answer everything -> still once per stage+CR.
    from ba_agent.managers.question_manager import answer

    for q in open_questions(s):
        answer(s, q["qid"], "noted")
    r2 = E.auto_batch(s)
    assert r2["batched"] is False and "already batched" in r2["reason"]
    # New change-request re-arms the stage.
    from shared.project_context import get_context

    get_context(s)["execution"]["active_change_request"] = "CR-001"
    r3 = E.auto_batch(s)
    assert r3["batched"] is True


def test_auto_batch_skips_complete_and_late_stages():
    from ba_agent import stage_engine as E

    s3 = _state(sections=["objectives", "scope", "asis_tobe"])  # S3 current
    assert E.auto_batch(s3)["batched"] is True
    # A complete BA stage needs no elicitation at any stage, including S7.
    full = _state(brd=BRD_FULL, sections=ALL_SECTIONS, diagram=True)
    assert E.auto_batch(full)["batched"] is False
    # Late stages DO elicit when their evidence is still incomplete (§8).
    late = _state(brd=BRD_FULL, sections=ALL_SECTIONS)  # S7 incomplete: no diagram
    r_late = E.auto_batch(late)
    assert r_late["batched"] is True and r_late["batch"]["stage"] == "S7"
    assert E.auto_batch(late)["batched"] is False  # one batch at a time


def test_brief_extras_combines_all_layers():
    from ba_agent import stage_engine as E

    x = E.brief_extras(_state(sections=["objectives"]))
    assert "BA stage progress" in x and "STAGE TASK S2" in x
    assert "QB-001" in x  # auto-batch note included
    assert E.brief_extras({}) != ""  # never empty, never raises


def test_package_hook_persists_artifact_and_snapshot():
    import shutil
    import uuid

    from ba_agent import stage_engine as E
    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace)

    pid = f"_test_pkghook_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s = _state(brd=BRD_FULL, sections=ALL_SECTIONS, diagram=True)
    bind_workspace(s, pid)
    r = E.on_ba_gate_passed(s)
    assert r["saved"] is True and r["path"].endswith("BA_PACKAGE_assembled.md")
    assert (ws.root / "artifacts" / "BA_PACKAGE_assembled.md").is_file()
    arts = get_context(s)["artifacts"]
    assert arts["BA"]["doc_key"] == "ba_package"
    assert any(h.get("type") == "artifact_created" for h in get_context(s)["history"])
    # Unbound state refuses honestly instead of writing globally.
    assert E.on_ba_gate_passed({})["saved"] is False
    shutil.rmtree(ws.root, ignore_errors=True)


def test_batched_marker_persists_across_resume():
    import shutil
    import uuid

    from ba_agent import stage_engine as E
    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace,
                                  load_project_state, persist_project_state)

    pid = f"_test_batchpersist_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s: dict = {}
    bind_workspace(s, pid)
    get_context(s)["sections_done_BA"] = ["objectives"]
    assert E.auto_batch(s)["batched"] is True
    persist_project_state(s, ws)

    resumed: dict = {}
    bind_workspace(resumed, pid)
    load_project_state(resumed, ws)
    # Marker survived: no duplicate batch after resume.
    assert E.auto_batch(resumed)["batched"] is False
    shutil.rmtree(ws.root, ignore_errors=True)
