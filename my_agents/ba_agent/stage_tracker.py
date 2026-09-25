"""BA stage tracker — S1..S7 progress derived from pipeline truth (prod).

OBSERVE + GUIDE, never block. The formal BA gate stays authoritative.
Evidence computation is single-sourced in ba_agent.stage_engine (the
executing engine); this module keeps the stable observer API
(sync/status/brief_fragment) backed by it.
"""

from __future__ import annotations

from typing import Any

from ba_agent.stage_engine import (  # noqa: E402  (single source of truth)
    FOCUS,
    SECTION_GROUPS,
    STAGES,
    TITLES,
    evidence as _engine_evidence,
)

DONE_KEY = "ba_stage_done"
CURRENT_KEY = "ba_stage_current"
LOG_KEY = "ba_stage_log"
_LOG_CAP = 50


def _evidence_ok(state: dict[str, Any]) -> dict[str, bool]:
    return _engine_evidence(state)


def _store(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    if not isinstance(ctx.get(DONE_KEY), list):
        ctx[DONE_KEY] = []
    if not isinstance(ctx.get(CURRENT_KEY), str):
        ctx[CURRENT_KEY] = "S1"
    if not isinstance(ctx.get(LOG_KEY), list):
        ctx[LOG_KEY] = []
    commit(state)
    return ctx


def sync(state: dict[str, Any]) -> dict:
    """Recompute S1..S7 from pipeline truth; log transitions; never raises.

    Returns {current, done[], pending[], transitions[]}.
    """
    try:
        ok = _evidence_ok(state)
        done = [s for s in STAGES if ok.get(s)]
        current = next((s for s in STAGES if not ok.get(s)), "S7")
        ctx = _store(state)
        prev_done = set(ctx[DONE_KEY]) if isinstance(ctx[DONE_KEY], list) else set()
        new_done, transitions = set(done), []
        for s in STAGES:
            if s in new_done and s not in prev_done:
                transitions.append(f"advanced to {s} ({TITLES[s]})")
            elif s not in new_done and s in prev_done:
                transitions.append(f"rework: {s} ({TITLES[s]}) invalidated")
        if transitions:
            log = ctx[LOG_KEY]
            log.extend(transitions)
            del log[:-_LOG_CAP]
        ctx[DONE_KEY] = done
        ctx[CURRENT_KEY] = current
        try:
            from shared.project_context import commit

            commit(state)
        except Exception:
            pass
        return {"current": current, "done": done,
                "pending": [s for s in STAGES if s not in done],
                "transitions": transitions}
    except Exception as e:
        return {"current": "S1", "done": [], "pending": list(STAGES),
                "transitions": [], "error": str(e)[:200]}


def status(state: dict[str, Any]) -> dict:
    """Read-only view (no writes, no validator runs beyond cached state)."""
    try:
        from shared.project_context import get_context

        ctx = get_context(state)
        done = ctx.get(DONE_KEY, [])
        current = ctx.get(CURRENT_KEY, "S1")
        if not isinstance(done, list):
            done = []
        return {"current": current if current in STAGES else "S1",
                "done": [s for s in done if s in STAGES],
                "pending": [s for s in STAGES if s not in done],
                "log": list(ctx.get(LOG_KEY, []) or [])[-10:]}
    except Exception:
        return {"current": "S1", "done": [], "pending": list(STAGES), "log": []}


def brief_fragment(state: dict[str, Any]) -> str:
    """One-paragraph stage guidance for the BA brief (never raises)."""
    try:
        view = sync(state)
        marks = " ".join(f"{s}{'✓' if s in view['done'] else '○'}" for s in STAGES)
        cur = view["current"]
        return (f"BA stage progress: {marks}. Current focus: {cur} ({TITLES[cur]}) — "
                f"{FOCUS[cur]}.")
    except Exception:
        return "BA stage progress unavailable; proceed with the assigned section."
