"""BA v2 — stage 2 / stage 7 artefacts, fail-closed gate, package and tools."""

from __future__ import annotations

import re
import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_agent.managers.package_assembler import (  # noqa: E402
    PACKAGE_ITEMS,
    assemble,
    audience_views,
    handoff_brief,
)
from shared.gates import ba_gate  # noqa: E402
from shared.harness import extract_section_text  # noqa: E402
from shared.project_context import get_context  # noqa: E402
from shared.sections import check_section, sections_for  # noqa: E402
from tests.ba_fixture import ALL_SECTIONS, ba_state  # noqa: E402
from tests.prod_fixtures import BRD  # noqa: E402


def _section(state: dict, section_id: str) -> str:
    title = next(s["title"] for s in sections_for("BA") if s["id"] == section_id)
    return extract_section_text(state["brd"], title)


# ------------------------------------------------- stage 2: business case

def test_business_case_section_contract():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    body = _section(s, "business_case")
    assert check_section("BA", "business_case", body)["passed"], \
        check_section("BA", "business_case", body)["missing"]
    # loose prose is rejected with the specific missing pieces
    thin = ("## Business Case and Solution Options\n\n"
            "We will build it and it will be good for the business. " * 6)
    missing = check_section("BA", "business_case", thin)["missing"]
    joined = " | ".join(missing)
    assert "OPT-" in joined and "risk of doing nothing" in joined


def test_solution_assessment_section_contract():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    body = _section(s, "solution_assessment")
    assert check_section("BA", "solution_assessment", body)["passed"]
    thin = ("## Solution Assessment, Readiness and Gaps\n\n"
            "The solution covers the requirements and the business accepts it. " * 6)
    missing = check_section("BA", "solution_assessment", thin)
    joined = " | ".join(missing["missing"] + missing["advisory"])
    assert "people" in joined and "SM-" in joined


# ------------------------------------------------ fail-closed gate enforcement

def test_gate_blocks_without_the_stage_2_artefact():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    assert ba_gate(s)["passed"] is True
    get_context(s)["sections_done_BA"] = [x for x in ALL_SECTIONS if x != "business_case"]
    # remove the section text so the gate re-check sees the real state
    s["brd"] = re.sub(r"## Business Case and Solution Options.*?(?=## Data Needs)",
                      "", s["brd"], flags=re.S)
    g = ba_gate(s)
    assert g["passed"] is False
    assert any(m.startswith("business_case") for m in g["missing"]), g["missing"]


def test_gate_blocks_without_the_stage_7_artefact():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    s["brd"] = re.sub(r"## Solution Assessment, Readiness and Gaps.*", "", s["brd"], flags=re.S)
    g = ba_gate(s)
    assert g["passed"] is False
    assert any(m.startswith(("solution_assessment", "readiness")) for m in g["missing"])


def test_gate_reports_untagged_priorities_and_open_contradictions():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    s["brd"] = s["brd"].replace("US-003 [MoSCoW: Should]", "US-003")
    g = ba_gate(s)
    assert any(m.startswith("priorities_tagged") and "US-003" in m for m in g["warnings"])
    s2 = ba_state(sections=ALL_SECTIONS, diagram=True)
    get_context(s2)["contradictions"] = [
        {"id": "CT-001", "status": "open", "severity": "blocking", "topic": "volume",
         "detail": "conflicting 'requests' values 200 vs 500", "sources": ["EL-001", "EL-002"]}]
    assert any(m.startswith("contradictions_resolved") for m in ba_gate(s2)["warnings"])


def test_gate_reports_the_full_v2_contract():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    g = ba_gate(s)
    names = [c for c in ("business_case", "solution_assessment", "readiness",
                         "quality_attributes", "priorities_tagged", "evidence_coverage",
                         "assumptions_registered", "decisions_recorded", "ba_plan",
                         "outcome_metrics", "cr_impact_recorded",
                         "contradictions_resolved") if any(
        m.startswith(c) for m in g["missing"])]
    assert names == [], f"unexpected failing v2 checks: {names}"
    assert g["checked"] >= 25


# ------------------------------------------------------------- package contract

def test_package_carries_the_v2_items_with_real_content():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    pkg = assemble(s, "travel")
    assert pkg["items"] == 34 == len(PACKAGE_ITEMS)
    assert pkg["missing"] == [], pkg["missing"]
    for item_id in ("business_case", "solution_assessment", "readiness", "priorities",
                    "evidence_sources", "outcome_metrics", "ba_plan", "ba_summary"):
        title = dict(PACKAGE_ITEMS)[item_id]
        assert f"## {title}" in pkg["markdown"]
    md = pkg["markdown"]
    assert "OPT-001" in md and "Recommended" in md          # business case content
    assert "Training" in md and "Not ready" in md           # readiness dimensions
    assert "SM-001" in md                                   # outcome metric
    assert "EL-001" in md                                   # evidence source
    assert "PL-001" in md                                   # BA plan item
    assert "Stage progress" in md                           # BA summary


def test_package_reports_missing_v2_items_instead_of_inventing():
    s = {"brd": "short draft"}
    pkg = assemble(s, "sparse")
    assert pkg["ok"] is False
    for item_id in ("business_case", "solution_assessment", "evidence_sources",
                    "outcome_metrics", "ba_summary"):
        assert item_id in " | ".join(pkg["missing"])
    assert "OPT-001" not in pkg["markdown"]  # nothing invented


def test_handoff_brief_resolves_every_point_when_the_ba_is_complete():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    hb = handoff_brief(s, "travel")
    assert hb["unresolved"] == [], hb["unresolved"]
    assert "OPT-001" in hb["markdown"]  # "why it wants it" carries the case
    assert "SM-001" in hb["markdown"]   # success measurement is contractual


def test_audience_cuts_are_consistent_and_consumer_specific():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    v = audience_views(s, "travel")
    for cut in ("business_owner", "project", "functional", "technical", "frappe"):
        assert v[cut].strip()
    assert "US-001" not in v["business_owner"]      # non-technical cut
    assert "US-001" in v["frappe"]                  # build-ready cut
    assert "NF" in v["technical"] or "NFR" in v["technical"] or v["technical"]


# --------------------------------------------------------------- tool surface

def test_v2_tools_are_wired_on_the_agent():
    from ba_agent import agent as BA

    names = {getattr(t, "__name__", str(t)) for t in BA.root_agent.tools}
    for tool in ("record_decision", "link_evidence", "evidence_report",
                 "record_assumption", "set_assumption_status", "record_ba_plan",
                 "update_ba_plan_item", "ba_plan_status", "quality_report",
                 "resolve_contradiction", "analyze_change_impact", "impact_report",
                 "record_success_metric", "record_outcome_measurement", "outcome_report"):
        assert tool in names, f"missing tool: {tool}"


def test_v2_tools_work_through_a_live_adk_state():
    """The tool layer must write to ADK session state, not a throwaway copy."""
    from google.adk.sessions.state import State

    from ba_agent import ba_tools as T

    class TC:
        pass

    st = State({"brd": BRD, "project_workspace_id": "baadk"}, {})
    tc = TC()
    tc.state = st
    assert T.record_decision(tc, "Cap is 180 EUR", "finance policy", "R. Mehta",
                             "BR-002")["ok"] is True
    assert T.link_evidence(tc, "US-001", "human", "kickoff workshop")["ok"] is True
    assert T.record_success_metric(tc, "Cycle time", "10 days", "2 days",
                                   "audit trail", "30 days post go-live")["ok"] is True
    ctx = st.get("project_context")
    assert ctx["decision_log"][0]["id"] == "DEC-001"
    assert ctx["evidence_links"][0]["artifact_id"] == "US-001"
    assert ctx["success_metrics"][0]["id"] == "SM-001"
