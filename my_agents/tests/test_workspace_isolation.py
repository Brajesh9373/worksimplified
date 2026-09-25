"""Project-workspace isolation tests (no LLM, no network).

Proves: new projects cannot see older projects' artifacts/history/CRs/frappe
state; IDs restart per project; interleaved execution stays separate; resume
restores the right workspace; the exact ShopFloor → Service Management
regression from the bug report.
"""

import shutil
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from shared import change_requests as CR  # noqa: E402
from shared import gates  # noqa: E402
from shared import project_context as PC  # noqa: E402
from shared import traceability as TR  # noqa: E402
from shared.workspace import (  # noqa: E402
    ProjectWorkspace,
    bind_workspace,
    bound_workspace_id,
    current_workspace,
    generate_project_id,
)

OLD_WS = MY_AGENTS / "projects" / "shopfloor_o2c_20260917" / "artifacts"


def _old_brd():
    cands = [p for p in OLD_WS.glob("BRD_ShopFloor*.md") if p.stat().st_size > 200]
    assert cands, "migrated ShopFloor workspace missing"
    return max(cands, key=lambda p: p.stat().st_size).read_text(encoding="utf-8")


@pytest.fixture()
def ws_a():
    ws = ProjectWorkspace("_test_alpha")
    yield ws
    shutil.rmtree(ws.root, ignore_errors=True)


@pytest.fixture()
def ws_b():
    ws = ProjectWorkspace("_test_beta")
    yield ws
    shutil.rmtree(ws.root, ignore_errors=True)


def _bind(docs: dict | None = None) -> dict:
    """Fresh session state bound to its own workspace, optionally with docs."""
    import uuid
    s = {}
    ws = ProjectWorkspace(f"_test_{uuid.uuid4().hex[:8]}")
    bind_workspace(s, ws.project_id)
    for name, content in (docs or {}).items():
        ws.save_artifact(name, content)
        if isinstance(content, str) and len(content) > 200:
            for key, prefix in (("brd", "BRD_"), ("project_plan", "ProjectPlan_"),
                                ("functional_spec", "FunctionalSpec_"),
                                ("tech_design", "TechDesign_")):
                if name.startswith(prefix):
                    s[key] = content
    s["_ws_obj"] = ws
    return s


@pytest.fixture()
def _cleanup_dynamic():
    yield
    for p in (MY_AGENTS / "projects").iterdir():
        if p.is_dir() and p.name.startswith("_test_") and len(p.name) == 13 + 1:
            shutil.rmtree(p, ignore_errors=True)


# 1. new project creates a new workspace
def test_new_project_creates_workspace():
    pid = generate_project_id("Service Management System")
    assert pid.startswith("service_management_system_")
    ws = ProjectWorkspace(pid)
    try:
        assert ws.exists()
        for sub in ("context", "artifacts", "traceability", "change_requests",
                    "history", "handoffs", "execution", "frappe"):
            assert (ws.root / sub).is_dir()
    finally:
        shutil.rmtree(ws.root, ignore_errors=True)


# 2. new project starts with zero traceability
def test_new_project_zero_traceability():
    s = _bind()
    try:
        rep = TR.coverage_report(s)
        assert rep["counts"] == {"G": 0, "BN": 0, "BR": 0, "US": 0, "FR": 0,
                                 "UC": 0, "T": 0, "TECH": 0, "FRAPPE_PROJECT": 0}
    finally:
        shutil.rmtree(s["_ws_obj"].root, ignore_errors=True)


# 3-6. cannot discover another project's docs
@pytest.mark.parametrize("prefix,key", [
    ("BRD_", "brd"), ("ProjectPlan_", "project_plan"),
    ("FunctionalSpec_", "functional_spec"), ("TechDesign_", "tech_design")])
def test_cannot_discover_other_project_docs(prefix, key):
    s = _bind()  # brand-new workspace, empty session
    try:
        assert TR.doc_texts(s)[key] == ""
    finally:
        shutil.rmtree(s["_ws_obj"].root, ignore_errors=True)


# 7-9. cannot read history / CRs / frappe state
def test_cannot_read_other_history_crs_frappe():
    s = _bind()
    try:
        ctx = PC.get_context(s)
        assert ctx["history"] == [] and ctx["change_requests"] == []
        assert CR.open_crs(s) == []
        ws = s["_ws_obj"]
        assert ws.get_history() == [] and ws.get_change_requests() == []
        assert ws.get_frappe_state() == {}
    finally:
        shutil.rmtree(s["_ws_obj"].root, ignore_errors=True)


# 10. independent BR-001 numbering
def test_independent_numbering(ws_a, ws_b):
    ws_a.save_artifact("BRD_A_20260918.md", "BR-001 scope A. " + "detail " * 60)
    ws_b.save_artifact("BRD_B_20260918.md", "BR-001 scope B. " + "detail " * 60)
    sa, sb = {"project_workspace_id": ws_a.project_id}, {"project_workspace_id": ws_b.project_id}
    assert TR.extract_ids(TR.doc_texts(sa)["brd"])["BR"] == ["BR-001"]
    assert TR.extract_ids(TR.doc_texts(sb)["brd"])["BR"] == ["BR-001"]
    assert TR.doc_texts(sa)["brd"] != TR.doc_texts(sb)["brd"]


# 11-12. workspace preserved across stages (A and B)
@pytest.mark.parametrize("tag", ["A", "B"])
def test_workspace_preserved_across_stages(tag, ws_a):
    ws = ws_a
    s = {"project_workspace_id": ws.project_id}
    PC.get_context(s)
    ws.save_artifact(f"BRD_{tag}_x.md", "BR-001 hello " + "x" * 500)
    s["brd"] = "BR-001 hello " + "x" * 500
    assert bound_workspace_id(s) == ws.project_id
    assert gates.ba_gate(s)["passed"] is False  # docs partial by design
    assert bound_workspace_id(s) == ws.project_id  # gate didn't switch ws
    assert current_workspace(state=s).project_id == ws.project_id


# 13. interleaved execution does not contaminate
def test_interleaved_no_contamination(ws_a, ws_b):
    sa = {"project_workspace_id": ws_a.project_id}
    sb = {"project_workspace_id": ws_b.project_id}
    PC.update_section(sa, "risks", {"R-01": "alpha risk"})
    PC.update_section(sb, "risks", {"R-01": "beta risk"})
    CR.create_cr(sa, "TECHNICAL", "BA", "alpha issue")
    assert PC.get_section(sb, "risks") == {"R-01": "beta risk"}
    assert CR.open_crs(sb) == []
    assert CR.open_crs(sa)[0]["id"] == "CR-001"
    cra = CR.create_cr(sb, "TECHNICAL", "BA", "beta issue")
    assert cra["id"] == "CR-001"  # numbering restarted per project


# 14/15. resume restores A; starting B does not restore A
def test_resume_and_new_project():
    from shared.workspace import load_project_state, persist_project_state
    sa = {}
    ws_a = ProjectWorkspace("_test_resume_a")
    try:
        bind_workspace(sa, ws_a.project_id)
        PC.update_section(sa, "risks", {"R-09": "kept"})
        PC.append_history(sa, "gate_passed", "orchestrator", "BA gate")
        persist_project_state(sa, ws_a)
        # resume A in a fresh session
        resumed = {}
        bind_workspace(resumed, "_test_resume_a")
        load_project_state(resumed, ws_a)
        assert PC.get_section(resumed, "risks") == {"R-09": "kept"}
        assert resumed["project_context"]["history"][-1]["type"] == "gate_passed"
        # starting B gives empty context, not A's
        sb = {}
        ws_b = ProjectWorkspace("_test_resume_b")
        bind_workspace(sb, ws_b.project_id)
        assert PC.get_section(sb, "risks") == {}
        assert PC.get_context(sb)["history"] == []
    finally:
        shutil.rmtree(ws_a.root, ignore_errors=True)
        shutil.rmtree((MY_AGENTS / "projects" / "_test_resume_b"), ignore_errors=True)


# EXACT BUG REGRESSION: ShopFloor BRD must be invisible to Service Management BA
def test_regression_shopfloor_invisible_to_service_management():
    old = _old_brd()
    assert "BR-" in old  # old project genuinely has requirements
    s = _bind()  # brand-new Service Management session + workspace
    try:
        texts = TR.doc_texts(s)
        assert texts["brd"] == ""
        rep = TR.coverage_report(s)
        assert (rep["counts"]["BR"], rep["counts"]["US"], rep["counts"]["FR"],
                rep["counts"]["UC"], rep["counts"]["T"], rep["counts"]["TECH"]) == (0, 0, 0, 0, 0, 0)
        gate = gates.ba_gate(s)
        assert not gate["passed"]
        assert any(m.startswith("brd_exists:") for m in gate["missing"])
        # and the BA brief for the new project contains zero old-project content
        from shared.handoffs import build_brief
        brief = build_brief(s, "BA")
        assert "ShopFloor" not in brief and "O2C" not in brief
    finally:
        shutil.rmtree(s["_ws_obj"].root, ignore_errors=True)
