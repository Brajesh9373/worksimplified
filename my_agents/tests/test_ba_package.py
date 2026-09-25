"""BA Package assembler tests — no LLM, no network."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from tests.prod_fixtures import BRD as _BRD

# Prod BRD lacks only [ASSUMPTION:] tags and carries 1 AC on US-002/US-003;
# top both up so the golden state meets the >=2-criteria contract.
BRD = (_BRD
       .replace(
           "then the state becomes Approved and the employee is notified.",
           "then the state becomes Approved and the employee is notified.\n"
           "- AC-002 Given a pending request when the manager rejects then the state "
           "becomes Rejected with a reason recorded.")
       .replace(
           "then the claim becomes Reimbursed and a payment record is created.",
           "then the claim becomes Reimbursed and a payment record is created.\n"
           "- AC-002 Given a duplicate claim when finance reviews then it is rejected "
           "as a duplicate with the original claim referenced.")
       + "\nAssumption: grade caps reviewed yearly. [ASSUMPTION:grade-cap-review]\n")


def _rich_state():
    from shared.project_context import get_context

    s = {
        "brd": BRD,
        "ba_flow_diagram": {"kind": "flow", "nodes": 19, "edges": 16,
                            "mermaid": "flowchart TD\nA{OK?}-->B",
                            "mmd": "ba_flow_t_20260918.mmd"},
    }
    ctx = get_context(s)
    ctx["project"] = {"name": "Travel Expense"}
    ctx["business"] = {"need": "control travel spend", "problem": "manual approvals",
                       "drivers": "audit findings", "objectives": "cut cycle to 10 days",
                       "goals": "100% policy-checked"}
    ctx["stakeholders"] = {"sponsor": "CFO", "users": "employees"}
    ctx["actors"] = {"Employee": "submits requests"}
    ctx["processes"] = {"travel_request": "submit -> approve -> book"}
    ctx["requirements"] = {"REQ-1": "policy-checked requests"}
    ctx["business_rules"] = {"BR-001": "manager approval above threshold"}
    ctx["functional_requirements"] = {"FR-001": "submit travel request"}
    ctx["use_cases"] = {"UC-001": "submit request"}
    ctx["risks"] = {"R-1": "adoption delay"}
    ctx["dependencies"] = {"D-1": "SSO availability"}
    ctx["delivery"] = {"transition": "pilot with finance team"}
    ctx["open_questions"] = {"OQ-001": "grade cap value?"}
    ctx["decisions"] = {"approval_threshold": "1000 EUR"}
    ctx["history"] = [{"ts": "t", "type": "elicitation", "actor": "ba_agent",
                       "summary": "volumes 200/mo", "ref": ""}]
    ctx["change_requests"] = [{"id": "CR-001", "status": "OPEN",
                               "reason": "add per-diem rule"}]
    from tests.frappe_fixture import seed_ba_v2

    seed_ba_v2(s, BRD)
    from ba_agent.managers import impact as IM

    IM.analyze(s, "CR-001", ["BR-001"], "per-diem rule requested", BRD)
    return s


def test_package_full_has_all_items_and_no_missing():
    from ba_agent.managers.package_assembler import PACKAGE_ITEMS, assemble

    pkg = assemble(_rich_state(), "travel_expense")
    assert pkg["items"] == 34 == len(PACKAGE_ITEMS)
    assert pkg["missing"] == [], f"unexpected gaps: {pkg['missing']}"
    assert pkg["ok"] is True
    for _, title in PACKAGE_ITEMS:
        assert f"## {title}" in pkg["markdown"], f"header missing: {title}"
    # Traceability + baseline carry real inventories, not placeholders.
    assert "US-001" in pkg["markdown"] and "BR-001" in pkg["markdown"]
    assert "ba_flow_diagram" in pkg["markdown"]
    assert "[ASSUMPTION:grade-cap-review]" in pkg["markdown"]


def test_package_sparse_reports_gaps_without_inventing():
    from ba_agent.managers.package_assembler import assemble

    pkg = assemble({"brd": "short draft"}, "sparse")
    assert pkg["ok"] is False and pkg["missing"]
    assert "_No data recorded" in pkg["markdown"]
    # Nothing invented: no IDs, no diagram names, no decisions.
    for invented in ("US-001", "BR-001", "ba_flow_diagram", "CFO"):
        assert invented not in pkg["markdown"], f"hallucinated {invented}"


def test_package_deterministic():
    from ba_agent.managers.package_assembler import assemble

    s = _rich_state()
    assert assemble(s, "p")["markdown"] == assemble(s, "p")["markdown"]


def test_handoff_brief_has_9_points_and_unresolved():
    from ba_agent.managers.package_assembler import HANDOFF_POINTS, handoff_brief

    hb = handoff_brief(_rich_state(), "travel_expense")
    assert len(HANDOFF_POINTS) == 9
    for p in HANDOFF_POINTS:
        assert f"## {p.capitalize()}" in hb["markdown"]
    assert hb["unresolved"] == []
    hb_sparse = handoff_brief({"brd": "short"}, "sparse")
    assert hb_sparse["unresolved"]  # gaps surfaced, not papered over


def test_assemble_tool_saves_package_and_handoff():
    import shutil

    from ba_agent.eval import tools as T
    from shared.workspace import ProjectWorkspace

    class FakeTC:
        def __init__(self, sid="testpkg"):
            self.state = _rich_state()
            self.session = type("S", (), {"id": sid})()

    tc = FakeTC()
    r = T.assemble_ba_package(tc, "travel_expense")
    assert r["ok"] is True and r["items"] == 34
    assert r["missing"] == [] and r["unresolved_handoff"] == []
    assert r["package"]["path"].endswith(".md") and r["handoff"]["path"].endswith(".md")
    # Session-scoped isolation: files land under projects/<pid>/artifacts.
    from pathlib import Path as _P

    pkg_path = _P(r["package"]["path"])
    assert "BA_PACKAGE_travel_expense" in pkg_path.name
    assert pkg_path.parent.name == "artifacts" and pkg_path.is_file()
    ws = ProjectWorkspace(pkg_path.parent.parent.name, create=False)
    assert ws.exists()
    shutil.rmtree(ws.root, ignore_errors=True)


def test_agent_wires_package_tool():
    from ba_agent import agent as BA

    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    assert "assemble_ba_package" in names


def test_upload_tool_roundtrip_and_refusals():
    import shutil
    from pathlib import Path as _P

    from shared.eng_tools import upload_document

    class FakeTC:
        def __init__(self, sid="testupload"):
            self.state: dict = {}
            self.session = type("S", (), {"id": sid})()

    tc = FakeTC()
    assert upload_document("notes", "   ", tc)["ok"] is False
    r = upload_document("interview notes (cfo)", "# Notes\nVolume 200/mo.", tc)
    assert r["ok"] is True and r["chars"] > 0
    p = _P(r["path"])
    assert p.name.startswith("uploaded_interview_notes_cfo_") and p.is_file()
    # Same-project read-back via the pull path.
    from shared.workspace import ProjectWorkspace

    ws = ProjectWorkspace(p.parent.parent.name, create=False)
    assert "Volume 200/mo." in (ws.root / "artifacts" / p.name).read_text()
    shutil.rmtree(ws.root, ignore_errors=True)


def test_agent_wires_upload_tool():
    from ba_agent import agent as BA

    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    assert "upload_document" in names


def test_g_bn_trace_head():
    from shared.traceability import chain_for, coverage_report, extract_ids

    doc = ("G-001 Control spend. BN-001 Policy-checked travel. "
           "US-001 submit request. BR-001 approval above threshold. "
           "US-002 approve. BR-002 grade caps.")
    ids = extract_ids(doc)
    assert ids["G"] == ["G-001"] and ids["BN"] == ["BN-001"]
    cov = coverage_report({"brd": doc})
    assert cov["counts"]["G"] == 1 and cov["counts"]["BN"] == 1
    assert cov["uncovered_G"] == []  # G-001 aligns with US-001/BR-001
    assert coverage_report({"brd": "G-009 Distant goal."})["uncovered_G"] == ["G-009"]
    chain = chain_for({"brd": doc}, "BN-001")
    assert chain["chain"]["G"] == ["G-001"]
    assert chain["chain"]["US"] == ["US-001"]
    assert chain_for({"brd": doc}, "G-001")["chain"]["BN"] == ["BN-001"]
    assert "error" in chain_for({"brd": doc}, "XYZ-1")


def _rich_package_state():
    from shared.project_context import get_context

    s = {"brd": BRD,
         "ba_flow_diagram": {"kind": "flow", "nodes": 7, "edges": 6,
                             "mermaid": "flowchart TD\nA[Start]-->B{C?}\nC-->D[E]"}}
    ctx = get_context(s)
    ctx["business"] = {"need": "control spend", "objectives": "10-day cycle"}
    ctx["stakeholders"] = {"sponsor": "CFO"}
    ctx["actors"] = {"Employee": "submits"}
    ctx["processes"] = {"travel": "submit->approve"}
    ctx["requirements"] = {"REQ-1": "policy checks"}
    ctx["business_rules"] = {"BR-001": "approval rule"}
    ctx["functional_requirements"] = {"FR-001": "submit"}
    ctx["use_cases"] = {"UC-001": "submit request"}
    ctx["risks"] = {"R-1": "adoption"}
    ctx["dependencies"] = {"D-1": "SSO"}
    ctx["delivery"] = {"transition": "pilot"}
    ctx["open_questions"] = {"OQ-001": "cap value?"}
    ctx["decisions"] = {"threshold": "1000 EUR"}
    return s


def test_audience_views_share_sources():
    from ba_agent.managers.package_assembler import audience_views

    v = audience_views(_rich_package_state(), "travel")
    assert "Business Owner View" in v["business_owner"]
    assert "Project View" in v["project"]
    # The five §22 consumer cuts all render from the same sources.
    for cut in ("business_owner", "project", "functional", "technical", "frappe"):
        assert v[cut].strip(), f"empty cut: {cut}"
    assert "US-001" in v["project"]  # build-ready cut carries IDs
    assert "US-001" not in v["business_owner"] or "User Stories" not in v["business_owner"]
    assert "CFO" in v["business_owner"]
    # One cut can be requested on its own, and legacy aliases still work.
    one = audience_views(_rich_package_state(), "travel", "frappe")
    assert "Frappe Implementation View" in one["frappe"] and "Project View" not in str(one)
    assert "Business Owner View" in audience_views(_rich_package_state(), "t", "stakeholder")[
        "business_owner"]
    # Same sources: a decision edited in state appears in the project cut once.
    assert v["project"].count("1000 EUR") <= 2


def test_reuse_report_tracks_stability():
    from ba_agent.eval import improvement as IM
    from ba_agent.eval import version_manager as VM

    s = {"brd": "US-001 a. BR-001 x. US-002 b. BR-002 y. US-003 c. BR-003 z."}
    assert IM.propose_candidate(s, "v1.0-c", "prompt", "base", [])["ok"]
    IM.set_regression(s, "v1.0-c", True)
    IM.approve_candidate(s, "v1.0-c", "lead")
    assert VM.deploy(s, "v1.0-c")["ok"] is True
    s["brd"] += " US-004 d. BR-004 w."
    assert IM.propose_candidate(s, "v1.1-c", "prompt", "add", [])["ok"]
    IM.set_regression(s, "v1.1-c", True)
    IM.approve_candidate(s, "v1.1-c", "lead")
    assert VM.deploy(s, "v1.1-c")["ok"] is True
    rep = VM.reuse_report(s)
    assert rep["ok"] and rep["snapshots"] == 2
    pair = rep["pairs"][0]
    assert pair["from"] == "v1.0-c" and pair["to"] == "v1.1-c"
    assert "US-001" in pair["carried"] and "US-004" in pair["added"]
    assert pair["dropped"] == [] and 0 < pair["stability"] <= 1.0


def test_views_and_reuse_tools_wired():
    from ba_agent import agent as BA

    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    assert "build_audience_views" in names and "reuse_report_tool" in names


def test_package_works_on_adk_state_without_items():
    """Regression: producers must not assume plain-dict state.

    ADK State exposes get/setitem/to_dict but no .items(); assembly and the
    gate-pass hook run against live session state in production.
    """
    import shutil
    import uuid

    from google.adk.sessions.state import State
    from shared.workspace import ProjectWorkspace

    pid = f"_test_adkstate_{uuid.uuid4().hex[:8]}"
    ProjectWorkspace(pid)
    val = {"project_workspace_id": pid, "brd": BRD,
           "ba_flow_demo_diagram": {"kind": "flow", "nodes": 7, "edges": 6,
                                    "mermaid": "flowchart TD\nA-->B{C}\nC-->D"}}
    st = State(dict(val), {})
    from ba_agent.managers.package_assembler import assemble
    from ba_agent.stage_engine import on_ba_gate_passed

    pkg = assemble(st)
    assert pkg["items"] == 34
    assert "ba_flow_demo_diagram" in pkg["markdown"]
    r = on_ba_gate_passed(st)
    assert r["saved"] is True
    shutil.rmtree(ProjectWorkspace(pid, create=False).root, ignore_errors=True)
