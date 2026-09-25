"""BA orchestrator — internal stage selection (pipeline untouched).

Thin stateless facade over ba_agent.stage_engine (the executing engine):
same decision contract, stage order single-sourced from the engine. For
state-backed progress see stage_tracker.sync; for per-pass directives see
stage_engine.stage_assignment / brief_extras.
"""

from __future__ import annotations

from ba_agent.stage_engine import STAGES as STAGE_ORDER  # noqa: E402

# Controlled iteration: requirements-management change re-enters analysis.
BACK_EDGES = {"S6": "S4"}


def next_step(
    current: str,
    missing: list[str] | None = None,
    confidence: float = 1.0,
    change_detected: bool = False,
) -> dict:
    """Pure decision: {action: continue|ask_human|rework, stage, reason}."""
    missing = missing or []
    if change_detected and current == "S6":
        return {
            "action": "rework",
            "stage": BACK_EDGES["S6"],
            "reason": "S6 change detected -> re-analyze (S4) then revalidate (S5)",
        }
    if missing or confidence < 0.7:
        return {
            "action": "ask_human",
            "stage": current,
            "reason": f"cannot confidently proceed: missing={missing or 'low-confidence'}",
        }
    if current not in STAGE_ORDER:
        return {"action": "continue", "stage": "S1", "reason": "start at S1"}
    idx = STAGE_ORDER.index(current)
    if idx >= len(STAGE_ORDER) - 1:
        return {"action": "continue", "stage": "S7", "reason": "terminal: assess + handoff"}
    return {"action": "continue", "stage": STAGE_ORDER[idx + 1], "reason": "stage complete"}
