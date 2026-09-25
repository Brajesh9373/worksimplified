"""Question manager — human collaboration tracking (state only, no new HITL node).

Rules: <=5 questions per batch; never re-ask logged elicitation Q&A or
already-pending batch questions; every assumption tagged [ASSUMPTION:xxx]
mirrored as OQ-xxx. Actual human I/O stays on adk web chat +
DeliveryOrchestrator RequestInput pauses + create_change_request; this module
prepares batches, tracks answers, and renders the pending fragment that rides
the BA brief.

State keys (persisted — see shared/workspace allowlist):
  ba_question_batches (list of {id, stage, questions[{qid, text, topic,
  status}], status}), elicitation log (shared, written on answer).

All entry points are total functions: they never raise.
"""

from __future__ import annotations

import re
from typing import Any

MAX_PER_TURN = 5

BATCHES_KEY = "ba_question_batches"

# Stage -> elicitation topics (happy path first, then alternates/exceptions).
STAGE_TOPICS: dict[str, tuple[str, ...]] = {
    "S1": ("problem", "goals", "initial scope", "stakeholders"),
    "S2": ("as-is pain", "business volume", "systems involved", "constraints"),
    "S3": ("happy path", "alternate flow", "exception flow", "business rule owner"),
    "S4": ("acceptance detail", "priority (MoSCoW)", "glossary term"),
    "S5": ("validation gap", "ambiguity to resolve"),
    "S6": ("trace gap", "change impact"),
    "S7": ("readiness gap", "transition need"),
}

_ASSUMPTION_RX = re.compile(r"\[ASSUMPTION:([^\]]+)\]", re.IGNORECASE)
_OQ_RX = re.compile(r"\bOQ-(\d+)\b")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def filter_unasked(candidates: list[str], state: dict) -> list[str]:
    """Drop candidates already answered in the elicitation log."""
    try:
        from shared.project_context import get_context

        log = (get_context(state).get("elicitation") or {}).get("log", [])
    except Exception:
        log = []
    asked = {_norm(e.get("q", "")) for e in log if isinstance(e, dict)}
    return [q for q in candidates if _norm(q) not in asked][:MAX_PER_TURN]


def needs_human(missing: list[str], confidence: float) -> bool:
    """Trigger human when info is missing or confidence is low.

    The trigger is 'cannot confidently proceed', not a stage number.
    """
    return bool(missing) or confidence < 0.7


def assumption_tag(text: str) -> str:
    """Ensure an assumption carries [ASSUMPTION:xxx]; add placeholder if absent."""
    if "[ASSUMPTION:" in text:
        return text
    return f"{text} [ASSUMPTION:pending]"


# ---------------- batches (persisted, resume-safe) ----------------


def _batches(state: dict[str, Any]) -> list:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    batches = ctx.get(BATCHES_KEY)
    if not isinstance(batches, list):
        batches = []
        ctx[BATCHES_KEY] = batches
        commit(state)
    return batches


def _pending_texts(state: dict[str, Any]) -> set[str]:
    out = set()
    try:
        for b in _batches(state):
            if not isinstance(b, dict):
                continue
            for q in b.get("questions", []) or []:
                if isinstance(q, dict) and q.get("status") != "answered":
                    out.add(_norm(q.get("text", "")))
    except Exception:
        pass
    return out


def build_batch(state: dict[str, Any], stage: str, candidates: list[str]) -> dict:
    """Build one <=5-question batch for a stage; never repeats log/pending.

    Returns {ok, batch} or {ok: False, error} when nothing new to ask.
    """
    try:
        from shared.project_context import commit

        fresh = filter_unasked(candidates, state)
        pending = _pending_texts(state)
        fresh = [q for q in fresh if _norm(q) not in pending][:MAX_PER_TURN]
        if not fresh:
            return {"ok": False, "error": "nothing new to ask (all logged or pending)"}
        batches = _batches(state)
        bid = f"QB-{len(batches) + 1:03d}"
        topics = STAGE_TOPICS.get(stage, ())
        batch = {
            "id": bid,
            "stage": stage,
            "questions": [
                {"qid": f"{bid}-Q{i + 1}", "text": q,
                 "topic": topics[i % len(topics)] if topics else "general",
                 "status": "open"}
                for i, q in enumerate(fresh)
            ],
            "status": "open",
        }
        batches.append(batch)
        del batches[:-50]
        commit(state)
        return {"ok": True, "batch": batch}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def answer(state: dict[str, Any], qid: str, answer_text: str,
           source: str = "user") -> dict:
    """Log one answer: elicitation log entry + question/batch status.

    Returns {ok, qid} or {ok: False, error} for unknown qid / empty answer.
    """
    try:
        from shared.project_context import commit, get_context

        if not (answer_text or "").strip():
            return {"ok": False, "error": "answer text is required"}
        for b in _batches(state):
            if not isinstance(b, dict):
                continue
            for q in b.get("questions", []) or []:
                if isinstance(q, dict) and q.get("qid") == qid:
                    if q.get("status") == "answered":
                        return {"ok": False, "error": f"{qid} already answered"}
                    ctx = get_context(state)
                    log = ctx.get("elicitation")
                    if not isinstance(log, dict) or not isinstance(log.get("log"), list):
                        log = {"log": []}
                        ctx["elicitation"] = log
                    log["log"].append({"id": f"EL-{len(log['log']) + 1:03d}",
                                        "q": q["text"],
                                        "a": answer_text.strip()[:2000],
                                        "source": (source or "user")[:80],
                                        "qid": qid})
                    del log["log"][:-50]
                    q["status"] = "answered"
                    if all(x.get("status") == "answered"
                           for x in b["questions"] if isinstance(x, dict)):
                        b["status"] = "complete"
                    commit(state)
                    return {"ok": True, "qid": qid}
        return {"ok": False, "error": f"unknown question {qid!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def open_questions(state: dict[str, Any]) -> list[dict]:
    """All unanswered batch questions (copies)."""
    out = []
    try:
        for b in _batches(state):
            if not isinstance(b, dict):
                continue
            for q in b.get("questions", []) or []:
                if isinstance(q, dict) and q.get("status") != "answered":
                    out.append({**q, "batch": b.get("id"), "stage": b.get("stage")})
    except Exception:
        pass
    return out


# ---------------- assumptions / OQ pairing ----------------


def assumption_gaps(brd: str, open_questions: Any = None) -> dict:
    """Every [ASSUMPTION:xxx] must mirror as an OQ-xxx open question.

    Returns {tags[], oqs[], unmirrored[]} — unmirrored tags need an OQ entry.
    Pure, never raises.
    """
    try:
        tags = sorted(set(_ASSUMPTION_RX.findall(brd or "")))
        oqs = sorted(set(_OQ_RX.findall(brd or "")))
        if isinstance(open_questions, dict):
            oqs += [m.group(1) for k in open_questions
                    for m in [_OQ_RX.search(str(k))] if m]
            oqs = sorted(set(oqs))
        # A tag mirrors when its label appears as (or alongside) an OQ id or
        # when any OQ exists for 'pending' placeholders.
        unmirrored = [t for t in tags
                      if t not in oqs and not (t == "pending" and oqs)]
        return {"tags": tags, "oqs": oqs, "unmirrored": unmirrored}
    except Exception:
        return {"tags": [], "oqs": [], "unmirrored": []}


def pending_fragment(state: dict[str, Any]) -> str:
    """Pending human items for the BA brief: open questions + approvals."""
    try:
        from ba_agent.managers import decision_manager as DM

        qs = open_questions(state)[:MAX_PER_TURN]
        parts = []
        if qs:
            lines = "\n".join(f"- [{q['qid']}] {q['text']}" for q in qs)
            parts.append(f"Awaiting stakeholder answers ({len(qs)} shown):\n{lines}")
        pend = DM.pending_approvals(state)
        if pend:
            lines = "\n".join(f"- [{a['id']}] {a['subject']}" for a in pend[:5])
            parts.append(f"Awaiting approvals ({len(pend)}):\n{lines}")
        return "\n\n".join(parts)
    except Exception:
        return ""
