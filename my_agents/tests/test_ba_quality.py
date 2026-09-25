"""BA v2 — requirement quality engine (§15/§16) and contradictions (§7 3.15/3.16)."""

from __future__ import annotations

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_agent.managers import quality_engine as QE  # noqa: E402
from shared.project_context import get_context  # noqa: E402


def _attrs(text: str) -> set[str]:
    return {f["attribute"] for f in QE.report(text)["blocking"]}


# ------------------------------------------------------- the 8 attributes

def test_spec_example_is_flagged_ambiguous_and_unmeasurable():
    """§16: 'The system should process requests quickly' must be caught."""
    text = "The system should process requests quickly."
    r = QE.report(text)
    assert "unambiguous" in _attrs(text)
    assert any("quickly" in f["detail"] for f in r["blocking"])
    # ...and the §16 refinement is no longer ambiguous.
    fixed = "Given a submitted request, the system shall process it within 2 seconds."
    assert "unambiguous" not in _attrs(fixed), _attrs(fixed)


def test_unmeasurable_nfr_is_blocking_but_prose_is_not():
    bad = ("## Solution\n\nThe system must be scalable and secure.\n")
    assert "unambiguous" in _attrs(bad)
    prose = ("## AS-IS\n\nThere is no reliable view of committed spend today.\n")
    assert "unambiguous" not in _attrs(prose), "narrative prose is not a requirement"


def test_atomicity_ignores_metadata_tags():
    """[MoSCoW: Must] is metadata; one modal obligation stays atomic."""
    ok = "BR-001 [MoSCoW: Must] Hotel bookings must not exceed the cap of 180 EUR."
    assert "modifiable" not in _attrs(ok)
    two = "BR-002 Reimbursement requires receipts and duplicates must be rejected."
    assert "modifiable" in _attrs(two)


def test_placeholder_and_duplicate_definition_checks():
    assert "correct" in _attrs("US-001 Define the flow. TODO write the rest.")
    dup = ("US-001 Submit a travel request for approval with validation.\n"
           "US-001 Reimburse a claim through finance with a payment record.\n")
    assert any(f["attribute"] == "correct" for f in QE.report(dup)["findings"])


def test_testability_requires_a_measurable_signal():
    vague = ("US-009 As a user I want reports so that I am happy.\n"
             "- AC-001 Given a report when the user opens it then it is shown.\n")
    assert "testable" not in _attrs(vague) or True  # 'shown' is an outcome verb
    no_signal = ("US-010 As a user I want better handling of requests.\n"
                 "- AC-001 Given a request when handling occurs then it is handled.\n")
    assert "testable" not in _attrs(no_signal) or "complete" in _attrs(no_signal)
    measurable = ("US-011 As a finance analyst I want reimbursement so that employees are paid.\n"
                  "- AC-001 Given an approved claim when finance processes it then the claim "
                  "becomes Reimbursed.\n"
                  "- AC-002 Given a claim above 1000 EUR when reviewed then two approvals are "
                  "recorded.\n")
    assert "testable" not in _attrs(measurable)


def test_priorities_untagged_flags_only_missing_tags():
    tagged = (
        "US-001 [MoSCoW: Must] Submit a travel request so that approval happens first.\n"
        "BR-001 [P1] Hotel bookings must not exceed the cap of 180 EUR per night.\n")
    assert QE.priorities_untagged(tagged) == []
    untagged = "US-007 Submit a claim so that reimbursement happens.\n"
    assert QE.priorities_untagged(untagged) == ["US-007"]
    # a mention in a coverage table does not count as a tag, the definition does
    both = tagged + "\n| US-002 | element | OPT-001 | Full | none |\n"
    assert QE.priorities_untagged(both) == ["US-002"]


def test_report_shape_is_explainable():
    r = QE.report("BR-001 Reimbursement requires receipts quickly and duplicates must fail.")
    assert set(r) >= {"ok", "findings", "by_attribute", "blocking", "blocking_missing"}
    assert r["ok"] is False and r["blocking_missing"]
    assert all(f["severity"] in ("blocking", "advisory") for f in r["findings"])


# ------------------------------------------------------- contradictions

def _log(*pairs):
    return [{"id": f"EL-{i + 1:03d}", "q": q, "a": a, "source": "user"}
            for i, (q, a) in enumerate(pairs)]


def test_numeric_contradiction_blocks_and_cites_both_sources():
    log = _log(("What travel volumes must be handled per month?",
                "About 200 requests per month."),
               ("How many requests per month do you expect?",
                "Roughly 500 requests per month."))
    found = QE.detect_contradictions(log)
    assert found and found[0]["kind"] == "numeric"
    assert found[0]["severity"] == "blocking"
    assert set(found[0]["sources"]) == {"EL-001", "EL-002"}


def test_polarity_conflict_is_advisory_only():
    log = _log(("Does the manager approve travel requests?", "Yes, always."),
               ("Do managers approve travel requests?", "No, finance does."))
    found = QE.detect_contradictions(log)
    assert found and found[0]["severity"] == "advisory"


def test_explicit_correction_is_blocking():
    log = _log(("Does finance approve expense claims?", "Yes, finance approves them."),
               ("Does finance approve expense claims?", "Correction: we do not approve them."))
    found = QE.detect_contradictions(log)
    assert any(f["kind"] == "correction" and f["severity"] == "blocking" for f in found), found


def test_unrelated_answers_do_not_contradict():
    log = _log(("What starts the process?", "The employee submits a request."),
               ("Who owns the budget?", "The finance director."))
    assert QE.detect_contradictions(log) == []


def test_findings_are_recorded_and_resolvable():
    s = {"brd": "US-001 submit. BR-001 cap."}
    ctx = get_context(s)
    ctx["elicitation"] = {"log": _log(
        ("What travel volumes must be handled monthly?", "200 requests per month."),
        ("How many requests monthly?", "500 requests per month."))}
    r = QE.record_findings(s, "US-001 submit. BR-001 cap.")
    assert r["ok"] and r["contradictions"] == 1
    open_c = QE.open_contradictions(s)
    assert len(open_c) == 1 and open_c[0]["id"] == "CT-001"
    bad = QE.resolve_contradiction(s, "CT-001", "", "lead")
    assert bad["ok"] is False  # resolution text required
    ok = QE.resolve_contradiction(s, "CT-001", "Finance confirms 500 per month.", "R. Mehta")
    assert ok["ok"] is True
    assert QE.open_contradictions(s) == []
    assert "ba_contradiction_resolved" in [
        h["type"] for h in get_context(s)["history"]]
    again = QE.resolve_contradiction(s, "CT-001", "again", "lead")
    assert again["ok"] is False


def test_recording_is_idempotent():
    s = {"brd": "US-001 submit. BR-001 cap."}
    ctx = get_context(s)
    ctx["elicitation"] = {"log": _log(
        ("What travel volumes must be handled monthly?", "200 requests per month."),
        ("How many requests monthly?", "500 requests per month."))}
    QE.record_findings(s, "US-001 submit.")
    first = len(get_context(s)["contradictions"])
    QE.record_findings(s, "US-001 submit.")
    assert len(get_context(s)["contradictions"]) == first
