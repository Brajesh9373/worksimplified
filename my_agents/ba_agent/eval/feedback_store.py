"""Feedback store — Tester verdicts on BA output (pure, no LLM).

Taxonomy (tester may say):
wrong, incomplete, inconsistent, ambiguous, hallucinated,
misclassified, missing_requirement, bad_question, bad_prioritization,
poor_methodology, improvement_suggested.

Stored per-agent-version in state (project_context['ba_eval']) so feedback
survives across turns but never leaks client project facts into agent memory:
only failure patterns + related IDs, never full business content.
"""

from __future__ import annotations

from typing import Any

VERDICTS = (
    "wrong",
    "incomplete",
    "inconsistent",
    "ambiguous",
    "hallucinated",
    "misclassified",
    "missing_requirement",
    "bad_question",
    "bad_prioritization",
    "poor_methodology",
    "improvement_suggested",
)

EVAL_KEY = "ba_eval"


def _bucket(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    bucket = ctx.get(EVAL_KEY)
    if not isinstance(bucket, dict) or not isinstance(bucket.get("feedback"), list):
        bucket = {"feedback": []}
        ctx[EVAL_KEY] = bucket
        commit(state)
    return bucket


def capture(
    state: dict[str, Any],
    verdict: str,
    detail: str,
    agent_version: str = "v1.0",
    related_ids: str = "",
) -> dict:
    """Record one tester verdict. Returns {ok, id} or {ok: False, error}."""
    v = (verdict or "").strip().lower()
    if v not in VERDICTS:
        return {"ok": False, "error": f"verdict must be one of {VERDICTS}, got {verdict!r}"}
    if not (detail or "").strip():
        return {"ok": False, "error": "detail is required"}
    bucket = _bucket(state)
    # Project-memory guard: store pattern + IDs only, truncate detail.
    entry = {
        "id": f"FB-{len(bucket['feedback']) + 1:03d}",
        "verdict": v,
        "detail": detail.strip()[:1000],
        "agent_version": agent_version[:32],
        "related_ids": [s.strip() for s in related_ids.split(",") if s.strip()][:20],
        "status": "open",
    }
    bucket["feedback"].append(entry)
    del bucket["feedback"][:-200]
    try:
        from shared.project_context import commit

        commit(state)
    except Exception:
        pass
    return {"ok": True, "id": entry["id"]}


def list_open(state: dict[str, Any]) -> list[dict]:
    """All feedback with status == open."""
    return [f for f in _bucket(state).get("feedback", []) if f.get("status") == "open"]


def mark_addressed(state: dict[str, Any], feedback_id: str, version: str) -> dict:
    """Mark feedback addressed by a candidate version (human approval still required)."""
    for f in _bucket(state).get("feedback", []):
        if f.get("id") == feedback_id:
            f["status"] = f"addressed-by-{version}"
            try:
                from shared.project_context import commit

                commit(state)
            except Exception:
                pass
            return {"ok": True, "id": feedback_id}
    return {"ok": False, "error": f"unknown feedback {feedback_id!r}"}
