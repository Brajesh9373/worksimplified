"""BA stage tracker tests — no LLM, no network."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from tests.ba_fixture import ALL_SECTIONS, WEAK_BRD, ba_state  # noqa: E402
from tests.prod_fixtures import BRD as _BRD  # noqa: E402

BRD_FULL = _BRD

DIAG = {"kind": "flow", "nodes": 19, "edges": 16,
        "mermaid": "flowchart TD\nA{OK?}-->B", "mmd": "ba_flow_t.mmd"}


def _state(brd="", sections=(), diagram=False):
    from shared.project_context import get_context

    s = {}
    if brd:
        s["brd"] = brd
    if diagram:
        s["ba_flow_diagram"] = dict(DIAG)
    ctx = get_context(s)
    ctx["sections_done_BA"] = list(sections)
    return s


def test_empty_starts_at_s1():
    from ba_agent import stage_tracker as ST

    v = ST.sync(_state())
    assert v["current"] == "S1" and v["done"] == []
    assert v["pending"] == list(ST.STAGES)
    assert "S1" in ST.brief_fragment(_state())


def test_evidence_stages_follow_sections():
    from ba_agent import stage_tracker as ST

    assert ST.sync(_state(sections=["objectives"]))["done"] == ["S1"]
    # S2 owns scope, AS-IS/TO-BE *and* the business case
    v = ST.sync(_state(sections=["objectives", "scope", "asis_tobe"]))
    assert v["done"] == ["S1"] and v["current"] == "S2"
    v = ST.sync(_state(sections=["objectives", "scope", "asis_tobe", "business_case"]))
    assert v["done"] == ["S1", "S2"] and v["current"] == "S3"
    v = ST.sync(_state(sections=["objectives", "scope", "asis_tobe", "business_case",
                                 "stakeholders"]))
    assert v["done"] == ["S1", "S2", "S3"] and v["current"] == "S4"


def test_s4_needs_both_stories_and_rules():
    from ba_agent import stage_tracker as ST

    v = ST.sync(ba_state(sections=["objectives", "scope", "asis_tobe", "business_case",
                                   "stakeholders", "stories"]))
    assert "S4" not in v["done"]  # rules missing


def test_s5_requires_strict_self_check():
    from ba_agent import stage_tracker as ST

    # Ambiguous + unmeasurable requirement: S4 is done, S5 must not be.
    v = ST.sync(ba_state(brd=WEAK_BRD, sections=ALL_SECTIONS))
    assert "S4" in v["done"] and "S5" not in v["done"]
    assert v["current"] == "S5"
    # The production-grade fixture satisfies all eight quality attributes.
    v = ST.sync(ba_state(sections=ALL_SECTIONS))
    assert "S5" in v["done"]


def test_s6_cooccurrence_and_s7_gate():
    from ba_agent import stage_tracker as ST

    v = ST.sync(ba_state(sections=ALL_SECTIONS))
    assert "S6" in v["done"]  # ids + evidence + decisions + assumptions + priorities
    assert "S7" not in v["done"]  # no diagram yet -> gate fails
    v = ST.sync(ba_state(sections=ALL_SECTIONS, diagram=True))
    assert v["done"] == list(ST.STAGES) and v["current"] == "S7"


def test_reset_regresses_with_rework_log():
    from ba_agent import stage_tracker as ST

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    assert ST.sync(s)["done"] == list(ST.STAGES)
    # Gate-fail targeted repair drops stories/rules (as orch_nodes does).
    from shared.project_context import get_context

    done = get_context(s)["sections_done_BA"]
    get_context(s)["sections_done_BA"] = [d for d in done if d not in ("stories", "rules")]
    v = ST.sync(s)
    assert "S4" not in v["done"] and "S5" not in v["done"]
    assert v["current"] == "S4"
    assert any("rework" in e for e in v["transitions"])
    assert any("rework" in e for e in ST.status(s)["log"])


def test_sync_deterministic_no_duplicate_log():
    from ba_agent import stage_tracker as ST

    s = _state(sections=["objectives"])
    first = ST.sync(s)
    assert first["transitions"] == ["advanced to S1 (Understand Business)"]
    assert ST.sync(s)["transitions"] == []
    assert ST.sync(s)["done"] == ["S1"]


def test_never_raises_on_garbage():
    from ba_agent import stage_tracker as ST

    assert ST.sync({})["current"] == "S1"
    assert ST.sync({"brd": None, "sections_done_BA": "junk"})["current"] == "S1"
    assert isinstance(ST.brief_fragment({}), str)
    assert isinstance(ST.status({})["pending"], list)


def test_brief_fragment_guides_current_focus():
    from ba_agent import stage_tracker as ST

    frag = ST.brief_fragment(_state(sections=["objectives"]))
    assert "S1✓" in frag and "S2" in frag and "Scope IN/OUT" in frag


def test_stage_keys_persist_across_resume():
    import shutil
    import uuid

    from ba_agent import stage_tracker as ST
    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace,
                                  load_project_state, persist_project_state)

    pid = f"_test_stagetrack_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s: dict = {}
    bind_workspace(s, pid)
    get_context(s)["sections_done_BA"] = ["objectives", "scope"]
    ST.sync(s)
    persist_project_state(s, ws)

    resumed: dict = {}
    bind_workspace(resumed, pid)
    load_project_state(resumed, ws)
    st = ST.status(resumed)
    assert "S1" in st["done"] and resumed.get("project_context", {}).get("ba_stage_current") == "S2"
    shutil.rmtree(ws.root, ignore_errors=True)


def test_pipeline_hooks_present_and_guarded():
    import pathlib

    src = pathlib.Path(MY_AGENTS, "shared", "orch_nodes.py").read_text()
    assert "ba_agent.stage_engine" in src
    assert "ba_agent.stage_tracker" in src
    assert 'stage == "BA"' in src
    # Engine entry points are lazy + guarded: pipeline never breaks without BA.
    assert "brief_extras" in src and "on_ba_gate_passed" in src
