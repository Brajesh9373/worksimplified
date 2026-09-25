"""Every gate failure must be repairable by some section.

When a gate check fails and no section owns it, the repair loop resets nothing,
rewrites nothing, and after two revisions the stage escalates to a human with
nothing attempted. That is exactly how `fr_coverage` behaved before it was routed.

The messages below are the real ones the gates emit (several copied verbatim from
actual run output). Length checks are exempt: they follow from the sections and are
deliberately skipped by `_gate_missing_to_sections`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from shared.orch_nodes import _gate_missing_to_sections  # noqa: E402
from shared.sections import sections_for  # noqa: E402

EXEMPT = ("brd_exists", "plan_exists", "spec_exists", "design_exists")

MESSAGES: dict[str, list[str]] = {
    "BA": [
        "quality_attributes: requirement quality: complete: US US-001: 0 criteria (need >=2)",
        "quality_attributes: requirement quality: unambiguous: vague term 'fast' is not quantified",
        "quality_attributes: requirement quality: modifiable: BR-001 joins two obligations",
        "priorities_tagged: requirements without a priority tag: US-002, US-003",
        "evidence_coverage: requirements with no evidence link: BR-001, US-001",
        "assumptions_registered: assumption ledger: unregistered: some_hypothesis",
        "outcome_metrics: no SM-n success metric with baseline, target, method and review point",
        "ba_plan: BA plan: stage S1 is complete but this item is planned",
        "solution_assessment: no production-grade solution assessment: missing from the BRD",
        "business_case: no production-grade business case: OPT-001 lacks a pro and a con",
        "contradictions_resolved: open contradictions: CT-001",
        "decisions_recorded: approved requests with no DEC-n decision: CR-001",
        "flow_diagram: flow diagram nodes=0 (need >=6)",
        "user_stories: US ids found: [] (need >=3)",
        "business_rules: BR ids found: [] (need >=3)",
    ],
    "PROJECT": [
        "task_trace: 5/27 rows trace to US/BR/TECH/PMO (need >=half)",
        "wbs_detail: WBS production detail - section 'wbs' row T-001 lacks a priority",
        "schedule_detail: schedule production detail - 0 dated milestones (need >=3)",
        "raid_detail: RAID production detail - no RAID table",
        "critical_path: no critical path named",
        "linked_to_brd: plan does not reference the BRD",
        "dependencies: no dependency rows",
        "milestones: no milestones",
        "raci: no RACI table",
        "raid: no RAID log",
        "sprints: no sprint phasing",
        "wbs: WBS rows 0 (need >=5 with T-xxx ids)",
        "gantt_diagram: no gantt bundle",
    ],
    "FUNCTIONAL": [
        "use_cases_detail: use_cases: section 'use_cases' UC-001 lacks precondition",
        "fr_trace: FR-003 does not trace to a US/BR",
        "fr_priority: no priorities on FRs",
        "fr_ids: 2 FR ids (need >=5)",
        "val_validations_detail: validations: fewer than 3 error codes",
        "alternate_flows: no alternate flows",
        "validations: no validation section",
        "data_model: no data model",
        "trace_matrix: no traceability matrix",
        "br_coverage: BR-004 has no FR",
        "use_cases: no UC ids",
        "func_diagram: no functional diagram bundle",
    ],
    "TECHNICAL": [
        "apis_detail: apis: section 'apis' has 1 endpoint (need >=3)",
        "data_detail: data: schema has 1 field type (need >=3)",
        "fr_coverage: FRs with no implemented object: FR-003, FR-007",
        "architecture: no architecture content",
        "decisions: no decisions recorded",
        "apis: no endpoints",
        "data_schema: no data schema",
        "security: no security content",
        "nfrs: no NFRs",
        "deployment: no deployment content",
        "sequence_diagram: no sequence diagram",
        "arch_diagram: no architecture diagram",
        "tech_tasks: no TECH- ids",
    ],
}


@pytest.mark.parametrize("stage", sorted(MESSAGES))
def test_every_gate_failure_routes_to_a_repairable_section(stage):
    plan = {s["id"] for s in sections_for(stage)}
    assert plan, f"{stage} has no sections"
    unroutable = []
    for msg in MESSAGES[stage]:
        if msg.split(":", 1)[0] in EXEMPT:
            continue
        routed = _gate_missing_to_sections(stage, [msg])
        if not routed:
            unroutable.append(msg)
        else:
            assert set(routed) <= (plan | {"__diagram__"}), (msg, routed)
    assert not unroutable, f"{stage}: nothing can repair {unroutable}"


@pytest.mark.parametrize("stage", ["BA", "PROJECT", "FUNCTIONAL", "TECHNICAL"])
def test_length_checks_stay_exempt(stage):
    """A length failure is resolved by writing the pending sections, so it must not
    be turned into a section reset of its own."""
    assert _gate_missing_to_sections(stage, ["plan_exists: length 0 (need >1500)"]) == []
