"""BA v2 — planning & monitoring (§28), change impact (§20), outcome (§27),
evidence/decisions/assumptions contracts (§31/§33/§34)."""

from __future__ import annotations

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_agent.managers import assumptions as ASM  # noqa: E402
from ba_agent.managers import ba_plan as BP  # noqa: E402
from ba_agent.managers import decision_manager as DM  # noqa: E402
from ba_agent.managers import evidence as EV  # noqa: E402
from ba_agent.managers import impact as IM  # noqa: E402
from ba_agent.managers import outcome as OC  # noqa: E402
from shared.project_context import get_context  # noqa: E402
from tests.ba_fixture import ALL_SECTIONS, ba_state  # noqa: E402
from tests.prod_fixtures import BRD  # noqa: E402


# ------------------------------------------------------------------ §28 plan

def test_plan_seeds_required_items_and_records_work():
    s = {"brd": BRD}
    assert BP.seed(s)["added"] >= 5
    assert BP.seed(s)["added"] == 0  # idempotent
    assert BP.required_present(s) == []
    r = BP.record(s, "Prepare the stakeholder briefing pack", "deliverable", "S3", "ba_agent")
    assert r["ok"] and r["item"]["id"].startswith("PL-")
    bad = BP.record(s, "x", "not-a-type")
    assert bad["ok"] is False
    assert BP.update(s, r["item"]["id"], status="done")["ok"] is True
    assert BP.update(s, "PL-999", status="done")["ok"] is False


def test_plan_variance_detects_drift_against_completed_sections():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)  # S1/S2/S3 sections accepted
    items = BP.items(s)
    first = items[0]["id"]
    BP.update(s, first, status="in_progress")
    var = BP.variance(s)
    assert var and var[0]["id"] == first
    assert "in_progress" in var[0]["reason"]
    BP.update(s, first, status="done")
    assert BP.variance(s) == []
    rep = BP.report(s)
    assert rep["ok"] is True and rep["items"] >= 5


def test_plan_sync_closes_items_when_their_stage_completes():
    """The harness owns stage completion, so it closes its own plan items —
    otherwise every real run blocks the gate on variance the model never clears.
    """
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    for i in BP.items(s):
        BP.update(s, i["id"], status="planned")      # as a real run leaves them
    assert BP.variance(s), "there must be drift before the sync"
    assert BP.sync(s) == len(BP.items(s))
    assert BP.variance(s) == []
    assert all(i["status"] == "done" for i in BP.items(s))
    assert BP.sync(s) == 0                            # idempotent


def test_plan_sync_leaves_waived_items_alone():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    first = BP.items(s)[0]["id"]
    BP.update(s, first, status="waived")
    BP.sync(s)
    assert next(i for i in BP.items(s) if i["id"] == first)["status"] == "waived"


def test_plan_sync_does_not_close_items_for_incomplete_stages():
    s = ba_state(sections=["objectives"], diagram=True)      # S1 accepted only
    for i in BP.items(s):
        BP.update(s, i["id"], status="planned")
    BP.sync(s)
    by_stage = {i["stage"]: i["status"] for i in BP.items(s)}
    assert by_stage["S1"] == "done"
    assert by_stage["S3"] == "planned" and by_stage["S4"] == "planned"
    assert BP.variance(s) == []      # not drift — their stages are not complete


def test_plan_sync_leaves_unstaged_commitments_alone():
    """An item's `stage` is the binding. Without one it is a free-standing
    commitment, so `sync()` must leave it to the agent to close."""
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    added = BP.record(s, "Prepare the stakeholder briefing pack", "deliverable", "")
    BP.sync(s)
    item = next(i for i in BP.items(s) if i["id"] == added["item"]["id"])
    assert item["status"] == "planned"


def test_plan_sync_closes_a_stage_bound_item_the_agent_left_open():
    """Regression: the model adds its own plan item for a stage and leaves it
    in_progress, which blocked a real run at the gate."""
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    added = BP.record(s, "Prepare the stakeholder briefing pack", "deliverable", "S3")
    assert BP.sync(s) >= 1
    item = next(i for i in BP.items(s) if i["id"] == added["item"]["id"])
    assert item["status"] == "done"
    assert BP.variance(s) == []


def test_ba_gate_plan_check_passes_on_a_just_seeded_plan():
    """Regression: `ba_fixture` closes the plan by hand, so the gate test passed
    while a real run — where the model never ticks its own plan — blocked on it.
    """
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    for i in BP.items(s):
        BP.update(s, i["id"], status="planned")
    g = ba_gate(s)
    assert not [m for m in g["missing"] if m.startswith("ba_plan")], g["missing"]


def test_ops_snapshot_reports_plan_quality_evidence_and_outcome():
    from ba_agent.ops import ops_snapshot

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    # a free-standing commitment (no stage) stays open and must still be reported;
    # stage-bound drift is reconciled by the gate's `BP.sync` before the snapshot,
    # and the drift rule itself is covered by the variance test above.
    BP.record(s, "Prepare the stakeholder briefing pack", "deliverable", "")
    snap = ops_snapshot(s)
    assert snap["ok"] is True
    assert snap["plan"]["items"] >= 8
    assert snap["plan"]["by_status"].get("planned", 0) >= 1
    assert snap["quality"]["blocking"] == 0
    assert snap["evidence"]["covered"] == snap["evidence"]["total"]
    assert snap["outcome"]["metrics"] == 1


# ------------------------------------------------------- §34 evidence links

def test_evidence_links_validate_sources_and_report_coverage():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    assert EV.report(s, BRD)["uncovered"] == []
    bad = EV.link(s, "US-999", "elicitation", "EL-001", brd=BRD)
    assert bad["ok"] is False  # unknown artifact
    bad2 = EV.link(s, "US-001", "elicitation", "EL-404", brd=BRD)
    assert bad2["ok"] is False  # unknown elicitation id
    bad3 = EV.link(s, "US-001", "telegraph", "EL-001", brd=BRD)
    assert bad3["ok"] is False  # unknown source type
    # identical link (same artifact/source/ref/note) dedupes; a different note does not
    first = [l for l in EV.links(s) if l["artifact_id"] == "US-001"][0]
    dup = EV.link(s, "US-001", first["source_type"], first["source_ref"],
                  first["note"], brd=BRD)
    assert dup["ok"] is True and dup.get("duplicate") is True
    extra = EV.link(s, "US-001", "human", "stakeholder workshop", "confirmed on site",
                    brd=BRD)
    assert extra["ok"] is True and not extra.get("duplicate")
    assert len([l for l in EV.links(s) if l["artifact_id"] == "US-001"]) == 2


def test_evidence_coverage_is_reported_without_blocking_the_handoff():
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    get_context(s)["evidence_links"] = []
    assert EV.uncovered(s, BRD) == ["BR-001", "BR-002", "BR-003", "BR-004",
                                    "US-001", "US-002", "US-003"]
    g = ba_gate(s)
    # still detected and carried forward, but polish must not hold up the handoff
    assert g["passed"] is True
    assert any(m.startswith("evidence_coverage") for m in g["warnings"])


def test_assumption_tag_or_oq_is_acceptable_evidence():
    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    get_context(s)["evidence_links"] = []
    tagged = BRD.replace(
        "US-001 [MoSCoW: Must] As an employee",
        "US-001 [MoSCoW: Must] [ASSUMPTION:portal-access] As an employee")
    assert "US-001" not in EV.uncovered(s, tagged)


# ------------------------------------------------------- §33 decisions

def test_decisions_are_structured_and_validated():
    s = {}
    assert DM.record_decision(s, "", "reason", "CFO")["ok"] is False
    assert DM.record_decision(s, "Approve cap", "", "CFO")["ok"] is False
    assert DM.record_decision(s, "Approve cap", "policy", "")["ok"] is False
    bad = DM.record_decision(s, "Approve cap", "policy", "CFO", "NOT-AN-ID")
    assert bad["ok"] is False
    r = DM.record_decision(s, "Hotel cap is 180 EUR", "finance policy",
                           "R. Mehta", "BR-002,US-002")
    assert r["ok"] and r["decision"]["id"] == "DEC-001"
    assert r["decision"]["related_ids"] == ["BR-002", "US-002"]
    assert "DEC-001" in DM.render(s)
    assert "ba_decision" in [h["type"] for h in get_context(s)["history"]]


def test_approval_requires_a_linked_decision():
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    DM.request_approval(s, "Release the BA package", "final review")
    assert DM.decisions_missing_for_approvals(s) == []  # still pending
    DM.decide_approval(s, "AP-001", "approve", "R. Mehta")
    assert DM.decisions_missing_for_approvals(s) == []  # auto-recorded
    assert DM.decisions(s)[0]["approval_id"] == "AP-001"
    # A hand-recorded approval without a decision is caught by the gate.
    get_context(s)["decision_log"] = []
    assert DM.decisions_missing_for_approvals(s) == ["AP-001"]
    assert any(m.startswith("decisions_recorded") for m in ba_gate(s)["warnings"])


# ------------------------------------------------------- §31 assumptions

def test_assumption_lifecycle_invariants():
    s = {}
    assert ASM.record(s, "", "text")["ok"] is False
    r = ASM.record(s, "hotel-cap-per-grade", "Caps are reviewed yearly")
    assert r["ok"] and r["assumption"]["status"] == "unvalidated"
    assert ASM.record(s, "hotel-cap-per-grade", "caps reviewed yearly")["duplicate"] is True
    # confirming needs evidence + a named decider
    assert ASM.set_status(s, "hotel-cap-per-grade", "confirmed")["ok"] is False
    assert ASM.set_status(s, "hotel-cap-per-grade", "confirmed",
                          "EL-001", "")["ok"] is False
    ok = ASM.set_status(s, "hotel-cap-per-grade", "confirmed", "EL-001", "R. Mehta")
    assert ok["ok"] and ok["assumption"]["decided_by"] == "R. Mehta"
    assert ASM.set_status(s, "nope", "confirmed", "EL-001", "x")["ok"] is False


def test_assumption_gaps_and_gate_enforcement():
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    assert ASM.gaps(s, BRD)["ok"] is True
    # a tag with no registry entry blocks the gate
    extra = BRD + "\nAssumption: caps reviewed yearly. [ASSUMPTION:cap-review]\n"
    assert ASM.gaps(s, extra)["missing"] == ["cap-review"]
    # the same state, but the document now carries an unregistered tag
    s["brd"] = extra
    assert any(m.startswith("assumptions_registered") for m in ba_gate(s)["warnings"])
    # rejected assumptions must leave the document
    ASM.record(s, "hotel-cap-per-grade", "old assumption")
    ASM.set_status(s, "hotel-cap-per-grade", "rejected", "EL-001", "R. Mehta")
    assert ASM.gaps(s, BRD)["rejected_in_doc"] == ["hotel-cap-per-grade"]


# ------------------------------------------------------- §20 change impact

def test_change_impact_maps_affected_set_and_is_reported():
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    get_context(s)["change_requests"] = [{"id": "CR-001", "status": "open",
                                          "reason": "add per-diem rule"}]
    assert IM.open_without_impact(s) == ["CR-001"]
    assert any(m.startswith("cr_impact_recorded") for m in ba_gate(s)["warnings"])
    r = IM.analyze(s, "CR-001", ["BR-002"], "per-diem policy change", BRD)
    assert r["ok"] is True
    rep = r["impact"]
    assert rep["changed_ids"] == ["BR-002"]
    assert rep["affected"], rep
    assert "business_rules" in rep["stale_package_items"]
    assert rep["consequences"] and rep["decision_needed"]
    assert "CR-001" in IM.render(s)
    assert IM.open_without_impact(s) == []
    assert ba_gate(s)["passed"] is True
    assert IM.get(s, "CR-002") is None


# ------------------------------------------------------------- §27 outcome

def _metric(state):
    return OC.record_metric(state, "Reimbursement cycle time", "10 business days",
                            "2 business days", "claim audit trail",
                            "30 days after go-live")


def test_metric_registry_validates_required_fields():
    s = {}
    assert OC.record_metric(s, "", "1", "2", "m", "r")["ok"] is False
    assert OC.record_metric(s, "cycle", "1", "", "m", "r")["ok"] is False
    assert OC.record_metric(s, "cycle", "1", "2", "", "r")["ok"] is False
    assert OC.record_metric(s, "cycle", "1", "2", "m", "")["ok"] is False
    r = _metric(s)
    assert r["ok"] and r["metric"]["id"] == "SM-001"
    assert OC.record_metric(s, "cycle", "0", "0", "m", "r", metric_id="bad")["ok"] is False


def test_outcome_gap_both_directions_and_feedback_loop():
    s = {}
    _metric(s)  # target 2 below baseline 10 -> "at most"
    rep = OC.report(s)
    assert rep["metrics"][0]["status"] == "pending" and rep["gaps"] == []
    assert OC.record_measurement(s, "SM-999", "3")["ok"] is False
    OC.record_measurement(s, "SM-001", "3 business days")
    rep = OC.report(s)
    assert rep["metrics"][0]["status"] == "missed"
    # baseline 10 -> target 2 is "at most 2", so 3 is above the allowed maximum
    assert rep["gaps"][0]["direction"] == "above target"
    OC.record_measurement(s, "SM-001", "2 business days")
    assert OC.report(s)["metrics"][0]["status"] == "met"
    OC.record_measurement(s, "SM-001", "4 business days")
    filed = OC.file_gap_feedback(s)
    assert filed["ok"] and filed["filed"], filed
    assert "SM-001" in get_context(s).setdefault("ba_eval", {}).get("feedback", [{}])[0].get(
        "related_ids", "SM-001")


def test_outcome_metric_is_reported_by_the_gate():
    from shared.gates import ba_gate

    s = ba_state(sections=ALL_SECTIONS, diagram=True)
    assert ba_gate(s)["passed"] is True
    get_context(s)["success_metrics"] = []
    assert any(m.startswith("outcome_metrics") for m in ba_gate(s)["warnings"])


def test_outcome_render_shows_table_and_gaps():
    s = {}
    _metric(s)
    OC.record_measurement(s, "SM-001", "7 business days")
    body = OC.render(s)
    assert "SM-001" in body and "Outcome gaps" in body
