"""Guardrails: the instructions a section receives must satisfy the checks it is
judged by.

These are the anti-drift tests. The brief is generated from the same spec the
validator uses, so if a check tightens without the guardrail following, or a
shape template stops satisfying its own checks, these fail.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from shared import sections as SEC  # noqa: E402

BA_SHAPED = ["business_case", "stories", "rules", "solution_assessment"]


def _fill(shape: str) -> str:
    """Turn the template's <placeholders> into concrete text, as a model would."""
    return re.sub(r"<[^>]+>", "concrete value", shape)


@pytest.mark.parametrize("section_id", BA_SHAPED)
def test_shape_template_passes_its_own_section_check(section_id):
    """A model that copies the template verbatim must pass — that is the point."""
    shape = SEC.shape_for("BA", section_id)
    assert shape, f"no shape template for BA/{section_id}"
    result = SEC.check_section("BA", section_id, _fill(shape))
    assert result["passed"], f"{section_id} template fails its own checks: {result['missing']}"


def test_stories_template_satisfies_the_quality_engine():
    """The template must give every story two acceptance criteria — the rule the
    quality engine blocks on (`validate_requirements` is a whole-BRD check, so it
    is the per-story counts that matter for this section)."""
    from ba_agent.managers.requirement_engine import (MIN_CRITERIA_PER_STORY,
                                                      MIN_STORIES,
                                                      validate_requirements)

    result = validate_requirements(_fill(SEC.shape_for("BA", "stories")))
    assert len(result["us"]) >= MIN_STORIES, result
    assert result["per_story_counts"], result
    assert all(c >= MIN_CRITERIA_PER_STORY for c in result["per_story_counts"]), result
    assert not [m for m in result["missing"] if "criteria" in m], result["missing"]


@pytest.mark.parametrize("section_id", ["stories", "rules"])
def test_templates_carry_priority_tags(section_id):
    from ba_agent.managers.quality_engine import priorities_untagged

    untagged = priorities_untagged(_fill(SEC.shape_for("BA", section_id)))
    assert not untagged, f"{section_id} template has untagged ids: {untagged}"


def test_guardrails_track_the_validator():
    """Every machine-checkable requirement is stated in the guardrails text."""
    for stage in ("BA", "PROJECT", "FUNCTIONAL", "TECHNICAL"):
        for section in SEC.sections_for(stage):
            text = " ".join(SEC.guardrails(stage, section)).lower()
            for group in section.get("need") or []:
                assert any(a in text for a in group), (stage, section["id"], group)
            for rx, minimum in (section.get("ids") or []):
                assert f"at least {minimum} id" in text, (stage, section["id"], rx)
            for rx, minimum in (section.get("rows") or []):
                assert f"at least {minimum} row" in text, (stage, section["id"], rx)
                assert SEC._sample_id(rx).lower() in text, (stage, section["id"], rx)


def test_every_section_has_guardrails():
    for stage in ("BA", "PROJECT", "FUNCTIONAL", "TECHNICAL"):
        for section in SEC.sections_for(stage):
            checks = SEC.guardrails(stage, section)
            assert checks, f"{stage}/{section['id']} has no guardrails"
            assert any("200 characters" in c for c in checks)


def test_assignment_states_shape_and_checks_as_gates():
    sections = {s["id"]: s for s in SEC.sections_for("BA")}
    out = SEC.section_assignment("BA", sections["business_case"], [], {},
                                 {"OPT": ["OPT-001", "OPT-002"]})
    assert "REQUIRED SHAPE" in out
    assert "AUTOMATED CHECKS" in out
    assert "rejected and rewritten" in out          # framed as a gate, not advice
    assert "| Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended |" in out
    assert "OPT-001" in out and "OPT-002" in out    # allocated ids carried through
    assert "Risk of doing nothing" in out


def test_wbs_shape_template_satisfies_every_per_row_wbs_rule():
    """The PROJECT wbs template must satisfy the per-row checks; only the row COUNT
    (>=5) is left for the agent to extend. A wrong template here starves FRAPPE,
    which parses this table to create the Frappe project's tasks."""
    shape = SEC.shape_for("PROJECT", "wbs")
    assert shape, "no shape template for PROJECT/wbs"
    filled = _fill(shape)
    assert SEC._wbs_detail(filled) == [], SEC._wbs_detail(filled)
    res = SEC.check_section("PROJECT", "wbs", filled)
    assert not [m for m in res["missing"] if "lacks" in m], res["missing"]


def test_trace_id_vocabulary_is_shared_by_checker_and_gate():
    """One vocabulary for 'traces upstream'. These disagreed, so a row could pass
    the WBS detail check and fail the gate's trace fraction (or vice versa)."""
    assert SEC.TRACE_ID_RX is not None
    for ident in ("US-001", "BR-002", "FR-003", "UC-004", "TECH-005", "PMO-006"):
        assert re.search(SEC.TRACE_ID_RX, ident), ident
    # the gate must use the same regex, not its own copy
    gate_src = (MY_AGENTS / "shared" / "gates.py").read_text()
    assert "TRACE_ID_RX" in gate_src, "the PROJECT gate no longer shares the trace vocabulary"


def test_use_case_cross_reference_in_a_table_row_is_not_a_use_case_block():
    """A UC id inside the traceability matrix must not be judged as a use case —
    the gate demanded pre/post conditions from a section that cannot supply them."""
    matrix = ("| Use case | Requirement | Note |\n|---|---|---|\n"
              "| UC-001 | FR-001 | Actor submits the request and sees it saved |\n")
    assert SEC._use_cases_detail(matrix) == [] or all(
        "UC-001" not in m for m in SEC._use_cases_detail(matrix)), SEC._use_cases_detail(matrix)


def test_unmeasurable_nfr_is_rejected_at_acceptance():
    """The gate's `unambiguous` attribute has TWO halves. Only the vague-word half
    was checked at acceptance, so an unmeasurable NFR was accepted, then blocked the
    gate on every rewrite until the revision budget ran out."""
    bad = ("- [ASSUMPTION:x] the system performance baselines are supplied verbally\n"
           + "x" * 220)
    problems = SEC.detail_missing("BA", "objectives", bad)
    assert any("not measurable" in p for p in problems), problems

    ok = "- [ASSUMPTION:x] the system must respond within 2 seconds\n" + "x" * 220
    assert not [p for p in SEC.detail_missing("BA", "objectives", ok)
                if "not measurable" in p]


def test_non_atomic_rule_is_rejected_at_acceptance():
    """Regression from the full-chain run: the gate blocks on `modifiable` but
    acceptance did not check it, so a non-atomic rule was accepted, blocked the
    gate, was rewritten, failed again — and the repeated re-run hung the workflow's
    replay scheduler ('Timed out waiting for sequence key')."""
    bad = ("BR-001 [MoSCoW: Must] An approver must verify the receipt and record the "
           "decision.\n" + "x" * 240)
    problems = SEC.detail_missing("BA", "rules", bad)
    assert any("not atomic" in p for p in problems), problems

    ok = "BR-001 [MoSCoW: Must] An approver must verify the receipt.\n" + "x" * 240
    assert not [p for p in SEC.detail_missing("BA", "rules", ok) if "not atomic" in p]


def test_vague_term_is_rejected_at_acceptance_not_only_at_the_gate():
    """Regression: a run reached the gate and was blocked by the single unquantified
    word 'fast' in a section whose guardrails never mentioned vagueness."""
    bad = ("The system must be fast so that operators save time. " + "x" * 220)
    problems = SEC.detail_missing("BA", "objectives", bad)
    assert any("'fast'" in p for p in problems), problems

    ok = ("The system must respond within 2 seconds so that operators save time. "
          + "x" * 220)
    assert not [p for p in SEC.detail_missing("BA", "objectives", ok) if "fast" in p]


def test_every_ba_section_states_the_vagueness_rule():
    """The gate checks it document-wide, so every section must warn about it."""
    for section in SEC.sections_for("BA"):
        assert "vague" in " ".join(SEC.guardrails("BA", section)), section["id"]


def test_assignment_batches_tool_calls_into_one_turn():
    """Measured: phrasing the ledger calls as "after append_doc, ..." made the model
    spend one turn per call (4-5 model calls a pass). The brief must ask for one turn,
    and must still list every ledger call that turn has to carry.
    """
    sections = {s["id"]: s for s in SEC.sections_for("BA")}
    out = SEC.section_assignment("BA", sections["stories"], [], {},
                                 {"US": ["US-001", "US-002", "US-003"]})
    assert "ONE TURN" in out and "SINGLE turn" in out
    assert "after append_doc" not in out
    assert "append_doc" in out
    assert "link_evidence" in out and "record_assumption" in out


# --- the validator must not reject a section that satisfies it ---------------

def _business_case(gap_table: str, with_pro_con: bool) -> str:
    pro_con = ("OPT-001 pro: one originating record makes stock a fact.\n"
               "OPT-001 con: the first quarter costs owner time.\n"
               "OPT-002 pro: no licence and no migration.\n"
               "OPT-002 con: the Must objectives stay open.\n") if with_pro_con else ""
    return (
        f"Capability gap: the business cannot see committed stock.\n\n{gap_table}\n\n"
        "| Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended |\n"
        "|---|---|---|---|---|---|\n"
        "| OPT-001 | integrated system | stock accuracy rises to 98% | one quarter | feasible | yes |\n"
        "| OPT-002 | spreadsheets only | no licence cost | 3 days | viable | no |\n\n"
        f"{pro_con}\n"
        "Risk of doing nothing: stock accuracy stays at 85%.\n"
        "Decision factors: cost, audit evidence and time to first release.\n")


def test_option_cited_early_does_not_hide_its_pro_and_con():
    """Regression: a capability-gap table commonly cites OPT-001 rows before the
    option's own pro/con lines appear. The option must still count as having both.
    """
    gap = ("| Business need | Can we do this today? | Where the gap is closed |\n"
           "|---|---|---|\n" + "\n".join(
               f"| BN-{i:03d} — need {i} | No, handled on paper | OPT-001: S-IN-{i:02d}, "
               f"T-{i:02d}, D-{i:02d} |"
               for i in range(1, 26)))
    text = _business_case(gap, with_pro_con=True)

    # the fixture must reproduce the real shape: pro/con lines well past the 1200
    # characters the old check searched from the *first* mention of the option
    delta = text.lower().index("opt-001 pro:") - text.lower().index("opt-001")
    assert delta > 1200, f"fixture does not reproduce the bug (delta={delta} chars)"
    assert SEC._business_case_detail(text) == []


def test_a_business_case_without_pro_and_con_is_still_rejected():
    gap = ("| Business need | Where the gap is closed |\n|---|---|\n"
           "| BN-001 | OPT-001: S-IN-01 |")
    problems = SEC._business_case_detail(_business_case(gap, with_pro_con=False))
    assert any("lacks a pro and a con" in p for p in problems), problems

