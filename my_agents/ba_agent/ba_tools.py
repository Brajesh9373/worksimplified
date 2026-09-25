"""BA v2 tool surface — thin adapters over the managers (business case, plan,
evidence, decisions, assumptions, quality, change impact, business outcome).

Every tool returns a plain dict, never raises, and routes writes through the
LIVE session state via `eval.tools._state_of` (ADK `State` safe).
"""

from __future__ import annotations

from typing import Any

from .eval.tools import _state_of


def _brd(state: Any) -> str:
    try:
        from shared.traceability import doc_texts

        return doc_texts(state).get("brd", "") or ""
    except Exception:
        try:
            v = state.get("brd", "")
            return v if isinstance(v, str) else ""
        except Exception:
            return ""


def record_decision(tool_context: Any, decision: str, reason: str,
                    decision_maker: str, related_ids: str = "",
                    status: str = "approved") -> dict:
    """Record a DEC-n decision with its reason, maker and related requirement ids.

    Args:
        decision: what was decided (e.g. 'Approval requires the department manager').
        reason: why — the business policy or evidence behind it.
        decision_maker: named human or role who owns the decision.
        related_ids: comma-separated requirement ids, e.g. 'BR-004,FR-012'.
        status: proposed | approved | rejected.
    """
    from .managers import decision_manager as DM

    st = _state_of(tool_context)
    return DM.record_decision(st, decision, reason, decision_maker, related_ids,
                              status, brd=_brd(st))


def link_evidence(tool_context: Any, artifact_id: str, source_type: str,
                  source_ref: str = "", note: str = "") -> dict:
    """Link one requirement to where its information came from (§34).

    Args:
        artifact_id: the requirement/objective id, e.g. 'US-001', 'BR-003'.
        source_type: elicitation | upload | document | decision | human | system.
        source_ref: the citable reference — EL-n answer, DEC-n decision or the
            uploaded_* artifact name.
        note: optional context for the link.
    """
    from .managers import evidence as EV

    st = _state_of(tool_context)
    return EV.link(st, artifact_id, source_type, source_ref, note, brd=_brd(st))


def evidence_report(tool_context: Any) -> dict:
    """Evidence coverage: which US-/BR- ids still have no source (§34)."""
    from .managers import evidence as EV

    st = _state_of(tool_context)
    return EV.report(st, _brd(st))


def record_assumption(tool_context: Any, label: str, text: str = "") -> dict:
    """Register an assumption tag so it can be validated or rejected (§31).

    Args:
        label: the [ASSUMPTION:label] name used in the BRD.
        text: the assumption stated in one sentence.
    """
    from .managers import assumptions as ASM

    st = _state_of(tool_context)
    return ASM.record(st, label, text)


def set_assumption_status(tool_context: Any, label: str, status: str,
                          evidence_ref: str = "", decided_by: str = "") -> dict:
    """Confirm or reject an assumption (evidence + named decider required).

    Args:
        label: the [ASSUMPTION:label] name.
        status: unvalidated | confirmed | rejected.
        evidence_ref: the EL-n answer / DEC-n decision / upload that settles it.
        decided_by: the named human confirming it.
    """
    from .managers import assumptions as ASM

    return ASM.set_status(_state_of(tool_context), label, status, evidence_ref, decided_by)


def record_ba_plan(tool_context: Any, title: str, type: str = "activity",
                   stage: str = "", owner: str = "ba_agent",
                   status: str = "planned", note: str = "") -> dict:
    """Add a BA plan item (PL-n) — activity, deliverable, communication or review.

    Args:
        title: what the item is.
        type: approach | activity | deliverable | communication | review.
        stage: the stage it belongs to (S1..S7).
        owner: who does it.
        status: planned | in_progress | done | waived.
    """
    from .managers import ba_plan as BP

    return BP.record(_state_of(tool_context), title, type, stage, owner, status, note)


def update_ba_plan_item(tool_context: Any, item_id: str, status: str = "",
                        note: str = "") -> dict:
    """Advance a plan item (PL-n) — keeps plan and progress in agreement (§28)."""
    from .managers import ba_plan as BP

    return BP.update(_state_of(tool_context), item_id, status, note)


def quality_report(tool_context: Any) -> dict:
    """The 8 requirement-quality attributes with blocking findings (§15/§16)."""
    from .managers import quality_engine as QE

    st = _state_of(tool_context)
    return QE.report(_brd(st))


def resolve_contradiction(tool_context: Any, finding_id: str, resolution: str,
                          resolved_by: str) -> dict:
    """Close a stakeholder contradiction with a human resolution (§7 3.15).

    Args:
        finding_id: the CT-n finding from the brief.
        resolution: the agreed truth.
        resolved_by: the named human who adjudicated.
    """
    from .managers import quality_engine as QE

    return QE.resolve_contradiction(_state_of(tool_context), finding_id, resolution,
                                    resolved_by)


def analyze_change_impact(tool_context: Any, cr_id: str, changed_ids: str,
                          reason: str = "") -> dict:
    """Impact analysis for a change request (§20): affected scope, requirements,
    tasks, decisions and stale package items.

    Args:
        cr_id: the change request id (e.g. CR-001).
        changed_ids: comma-separated ids the change touches, e.g. 'US-002,BR-004'.
        reason: why the change is being considered.
    """
    from .managers import impact as IM

    st = _state_of(tool_context)
    ids = [x.strip() for x in (changed_ids or "").replace(";", ",").split(",") if x.strip()]
    return IM.analyze(st, cr_id, ids, reason, _brd(st))


def impact_report(tool_context: Any, cr_id: str = "") -> dict:
    """Stored impact report(s) — one CR, or all of them when cr_id is omitted."""
    from .managers import impact as IM

    st = _state_of(tool_context)
    if cr_id:
        rep = IM.get(st, cr_id)
        return {"ok": bool(rep), "impact": rep} if rep else {
            "ok": False, "error": f"no impact report for {cr_id!r}"}
    return {"ok": True, "reports": IM.all_reports(st),
            "open_without_impact": IM.open_without_impact(st)}


def record_success_metric(tool_context: Any, name: str, baseline: str, target: str,
                          method: str, review_point: str, unit: str = "",
                          metric_id: str = "") -> dict:
    """Define a measurable business-outcome metric (SM-n) (§27).

    Args:
        name: the outcome being measured, e.g. 'Travel approval cycle time'.
        baseline: today's value (e.g. '5 days').
        target: the expected value (e.g. '2 days').
        method: how it is measured, e.g. 'average DocType cycle time report'.
        review_point: when it is measured, e.g. '30 days after go-live'.
        unit: optional unit label.
        metric_id: optional explicit SM-n id.
    """
    from .managers import outcome as OC

    return OC.record_metric(_state_of(tool_context), name, baseline, target, method,
                            review_point, unit, metric_id)


def record_outcome_measurement(tool_context: Any, metric_id: str, actual: str,
                               note: str = "") -> dict:
    """Record an observed value for a metric; the report computes the gap (§27)."""
    from .managers import outcome as OC

    return OC.record_measurement(_state_of(tool_context), metric_id, actual, note)


def outcome_report(tool_context: Any, file_feedback: bool = False) -> dict:
    """Expected vs actual per metric, with outcome gaps (§27).

    Args:
        file_feedback: when True, each missed outcome is filed into the BA
            improvement-feedback loop so the gap is tracked, not lost.
    """
    from .managers import outcome as OC

    st = _state_of(tool_context)
    rep = OC.report(st)
    if file_feedback:
        rep["filed"] = OC.file_gap_feedback(st)
    return rep


def ba_plan_status(tool_context: Any) -> dict:
    """Plan vs progress: item statuses plus monitoring variance (§28)."""
    from .managers import ba_plan as BP

    return BP.report(_state_of(tool_context))


# ------------------------------------------------- conversation / knowledge

def record_knowledge(tool_context: Any, kind: str, key: str, value: str,
                     note: str = "") -> dict:
    """Capture one thing the owner told you, in their words.

    Use this during conversation, as soon as you hear something that matters —
    no document is being written yet, this is just what you now know.

    Args:
        kind: need | problem | objective | goal | metric | driver | stakeholder |
            actor | process | rule | requirement | use_case | risk | dependency |
            integration | transition | question | constraint | decision | assumption.
        key: a short name for it (e.g. the stakeholder's role, the rule's subject).
        value: what they said, faithfully — never invent or embellish.
        note: optional context, e.g. why it matters or where it came from.
    """
    from .managers import knowledge as K

    return K.capture(_state_of(tool_context), kind, key, value, note=note)


def knowledge_status(tool_context: Any) -> dict:
    """What has been captured so far and what is still missing (plain words)."""
    from .managers import knowledge as K

    return K.status(_state_of(tool_context))


def ask_user(tool_context: Any, question: str, why: str = "") -> dict:
    """Track a question you want to ask the owner, in your own words.

    Recording it means an unanswered question is never lost and never asked
    twice by accident. Ask in whatever way fits the conversation; there is no
    required number of questions and no required moment.

    Args:
        question: the question as you would say it to the owner.
        why: what it changes (used later when it matters).
    """
    from .managers import knowledge as K

    st = _state_of(tool_context)
    r = K.capture(st, "question", (question or "")[:120], question,
                  note=why or "asked in conversation")
    if r.get("ok"):
        r["ask_now"] = True
    return r


def propose_generation(tool_context: Any, artifacts: str = "", reason: str = "") -> dict:
    """Offer to write the documents up (the owner decides).

    Use when you believe there is enough to produce a good document — the owner
    still has to say yes.

    Args:
        artifacts: what you propose to produce, e.g. 'brd, diagrams, package'.
        reason: one line on why now.
    """
    state = _state_of(tool_context)
    try:
        from shared.conversation import conversation

        block = conversation(get_context_for(state))
        block["pending_proposal"] = True
        block["artifacts"] = [a.strip() for a in (artifacts or "brd").split(",") if a.strip()]
        return {"ok": True, "proposed": block["artifacts"], "reason": reason[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def get_context_for(state: Any) -> dict:
    """Small local helper: the live project_context for a state."""
    from shared.project_context import get_context

    return get_context(state)
