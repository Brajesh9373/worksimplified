"""Controlled iteration: structured Change Requests.

Every iteration carries source/target/reason/affected items/status/timestamps
and a stable ID (CR-001...). Guards prevent uncontrolled loops:
  - MAX_ITERATIONS_PER_CR: re-runs of one CR before human escalation.
  - Only one ACTIVE CR at a time (execution.active_change_request).

Pure dict API over PROJECT_CONTEXT + ToolContext tool wrappers.
"""

from __future__ import annotations

from typing import Any

from .project_context import append_history, get_context, utcnow

MAX_ITERATIONS_PER_CR = 3

VALID_STAGES = ("BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE", "VALIDATION")
VALID_STATUS = ("OPEN", "IN_PROGRESS", "RESOLVED", "REJECTED", "ESCALATED")


def _crs(state: dict[str, Any]) -> list:
    return get_context(state)["change_requests"]


def create_cr(
    state: dict[str, Any],
    source_agent: str,
    target_agent: str,
    reason: str,
    affected_requirements: list[str] | None = None,
    affected_tasks: list[str] | None = None,
    affected_artifacts: list[str] | None = None,
    impact: str = "",
) -> dict[str, Any]:
    src = source_agent.upper()
    tgt = target_agent.upper()
    if src not in VALID_STAGES or tgt not in VALID_STAGES:
        raise ValueError(f"CR stages must be in {VALID_STAGES}")
    if not reason or not reason.strip():
        raise ValueError("CR reason is required")
    open_same = [
        c for c in _crs(state)
        if c["status"] in ("OPEN", "IN_PROGRESS")
        and c["source_agent"] == src
        and c["target_agent"] == tgt
        and c["reason"] == reason.strip()
    ]
    if open_same:
        return open_same[0]  # idempotent: don't duplicate identical open CR
    ctx = get_context(state)
    ctx["cr_counter"] += 1
    cr = {
        "id": f"CR-{ctx['cr_counter']:03d}",
        "source_agent": src,
        "target_agent": tgt,
        "reason": reason.strip()[:2000],
        "affected_requirements": list(affected_requirements or []),
        "affected_tasks": list(affected_tasks or []),
        "affected_artifacts": list(affected_artifacts or []),
        "impact": impact[:1000],
        "status": "OPEN",
        "iterations": 0,
        "created_at": utcnow(),
        "resolved_at": None,
    }
    _crs(state).append(cr)
    ctx["execution"]["active_change_request"] = cr["id"]
    append_history(state, "change_request_created", src,
                   f"{cr['id']}: {src}→{tgt}: {reason.strip()[:200]}", cr["id"])
    return dict(cr)


def get_cr(state: dict[str, Any], cr_id: str) -> dict[str, Any] | None:
    for c in _crs(state):
        if c["id"] == cr_id:
            return dict(c)
    return None


def open_crs(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(c) for c in _crs(state) if c["status"] in ("OPEN", "IN_PROGRESS")]


def note_iteration(state: dict[str, Any], cr_id: str) -> dict[str, Any]:
    """Record one re-run pass of a CR. Returns {'cr', 'budget_exceeded'}."""
    for c in _crs(state):
        if c["id"] == cr_id:
            c["iterations"] += 1
            if c["status"] == "OPEN":
                c["status"] = "IN_PROGRESS"
            exceeded = c["iterations"] >= MAX_ITERATIONS_PER_CR
            append_history(state, "change_request_iteration", c["target_agent"],
                           f"{cr_id} pass {c['iterations']}", cr_id)
            return {"cr": dict(c), "budget_exceeded": exceeded}
    raise ValueError(f"Unknown change request: {cr_id}")


def resolve_cr(state: dict[str, Any], cr_id: str, resolution: str = "") -> dict[str, Any]:
    for c in _crs(state):
        if c["id"] == cr_id:
            c["status"] = "RESOLVED"
            c["resolved_at"] = utcnow()
            if resolution:
                c["resolution"] = resolution[:1000]
            ctx = get_context(state)
            if ctx["execution"].get("active_change_request") == cr_id:
                ctx["execution"]["active_change_request"] = None
            append_history(state, "change_request_resolved", c["target_agent"],
                           f"{cr_id} resolved: {resolution[:200]}", cr_id)
            return dict(c)
    raise ValueError(f"Unknown change request: {cr_id}")


def escalate_cr(state: dict[str, Any], cr_id: str, reason: str = "") -> dict[str, Any]:
    for c in _crs(state):
        if c["id"] == cr_id:
            c["status"] = "ESCALATED"
            append_history(state, "change_request_escalated", "orchestrator",
                           f"{cr_id} escalated to human: {reason[:200]}", cr_id)
            return dict(c)
    raise ValueError(f"Unknown change request: {cr_id}")


# ---- tool wrappers ----

def tool_create_cr(
    tool_context: Any,
    source_agent: str,
    target_agent: str,
    reason: str,
    affected_requirements_json: list[str] | None = None,
    affected_tasks_json: list[str] | None = None,
    affected_artifacts_json: list[str] | None = None,
    impact: str = "",
) -> dict:
    """File a structured Change Request sending work back to an earlier stage.

    Args:
        source_agent: stage discovering the problem (BA/PROJECT/FUNCTIONAL/TECHNICAL/FRAPPE).
        target_agent: stage that must revise.
        reason: what is wrong/missing/contradictory.
        affected_requirements_json: IDs like ["FR-014"].
        affected_tasks_json: IDs like ["T-023"].
        affected_artifacts_json: names like ["FunctionalSpec"].
        impact: downstream impact note.
    """
    try:
        state = tool_context.state
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
    cr = create_cr(state, source_agent, target_agent, reason,
                   affected_requirements_json, affected_tasks_json,
                   affected_artifacts_json, impact)
    return {"ok": True, "cr": cr}
