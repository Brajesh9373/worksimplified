"""Field-accuracy sampling harness — measure heuristics on real labels (prod).

Golden sets prove consistency; field labels prove accuracy. This module:
- review_queue(): feedback entries worth human labeling (low-confidence
  predictions first, then unlabeled remainder), capped for review workload.
- record_label(): store a tester's true target for a feedback entry.
- field_accuracy(): rolling accuracy over labeled entries.
- report(): queue depth + field stats in one call.

Labels live in project_context['ba_field_labels'] (persisted — see
shared/workspace allowlist), capped at 200. Total functions: never raise.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LABELS_KEY = "ba_field_labels"
LOW_CONFIDENCE = 0.35
QUEUE_CAP = 25
_LABEL_CAP = 200


def _labels(state: dict[str, Any]) -> list:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    items = ctx.get(LABELS_KEY)
    if not isinstance(items, list):
        items = []
        ctx[LABELS_KEY] = items
        commit(state)
    return items


def _feedback_entries(state: dict[str, Any]) -> list[dict]:
    try:
        from ba_agent.eval.feedback_store import _bucket

        return [f for f in _bucket(state).get("feedback", []) if isinstance(f, dict)]
    except Exception:
        return []


def review_queue(state: dict[str, Any], limit: int = QUEUE_CAP) -> dict:
    """Feedback entries needing labels, low-confidence first (never raises)."""
    try:
        from ba_agent.eval.root_cause import analyze

        labeled = {lb.get("feedback_id") for lb in _labels(state)
                   if isinstance(lb, dict)}
        scored = []
        for fb in _feedback_entries(state):
            if fb.get("id") in labeled:
                continue
            a = analyze(fb)
            scored.append({"feedback_id": fb.get("id"), "verdict": fb.get("verdict"),
                           "detail": (fb.get("detail", "") or "")[:200],
                           "predicted": a["target"], "confidence": a["confidence"],
                           "runner_up": a["runner_up"]})
        scored.sort(key=lambda e: (e["confidence"], e["feedback_id"] or ""))
        return {"ok": True, "queue": scored[: max(limit, 0)], "unlabeled": len(scored)}
    except Exception as e:
        return {"ok": False, "queue": [], "error": str(e)[:200]}


def record_label(state: dict[str, Any], feedback_id: str, true_target: str,
                 labeler: str) -> dict:
    """Store a tester's true target (idempotent per feedback id)."""
    try:
        from ba_agent.eval.root_cause import TARGETS, analyze
        from shared.project_context import commit

        if true_target not in TARGETS:
            return {"ok": False, "error": f"true_target must be one of {TARGETS}"}
        if not (labeler or "").strip():
            return {"ok": False, "error": "labeler is required"}
        entries = {f.get("id"): f for f in _feedback_entries(state)}
        if feedback_id not in entries:
            return {"ok": False, "error": f"unknown feedback {feedback_id!r}"}
        predicted = analyze(entries[feedback_id])["target"]
        items = _labels(state)
        for lb in items:
            if isinstance(lb, dict) and lb.get("feedback_id") == feedback_id:
                lb.update({"true": true_target, "predicted": predicted,
                           "correct": predicted == true_target,
                           "labeler": labeler.strip()[:120],
                           "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
                commit(state)
                return {"ok": True, "updated": True, "correct": lb["correct"]}
        items.append({"feedback_id": feedback_id, "true": true_target,
                      "predicted": predicted, "correct": predicted == true_target,
                      "labeler": labeler.strip()[:120],
                      "labeled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        del items[:-_LABEL_CAP]
        commit(state)
        return {"ok": True, "updated": False,
                "correct": predicted == true_target}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def field_accuracy(state: dict[str, Any]) -> dict:
    """Rolling accuracy over labeled entries (never raises)."""
    try:
        items = [lb for lb in _labels(state) if isinstance(lb, dict)]
        if not items:
            return {"ok": True, "labeled": 0, "correct": 0, "accuracy": None}
        correct = sum(1 for lb in items if lb.get("correct"))
        return {"ok": True, "labeled": len(items), "correct": correct,
                "accuracy": round(correct / len(items), 3)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def report(state: dict[str, Any]) -> dict:
    """Queue depth + field stats in one call (never raises)."""
    try:
        q = review_queue(state, limit=0)
        acc = field_accuracy(state)
        return {"ok": True, "unlabeled": q.get("unlabeled", 0),
                "labeled": acc.get("labeled", 0),
                "field_accuracy": acc.get("accuracy")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
