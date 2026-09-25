"""Shared PROJECT_CONTEXT state layer.

Single source of continuity for the delivery pipeline. Everything lives in
ADK session state under the ``project_context`` key (one dict — no new DB),
while existing artifact keys (brd, project_plan, ...) are left untouched and
only *referenced* from here.

Sections (per architecture spec):
  project, business, stakeholders, actors, processes, requirements,
  business_rules, functional_requirements, use_cases, architecture,
  data_model, integrations, delivery, tasks, dependencies, risks,
  decisions, technical_design, frappe_state, validation, open_questions,
  change_requests (+ cr_counter), history, artifacts, execution.

All helpers are pure functions over a plain dict so they are unit-testable
without ADK. Thin ToolContext adapters are provided for use as tools.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

CONTEXT_KEY = "project_context"

SECTIONS = (
    "project",
    "business",
    "stakeholders",
    "actors",
    "processes",
    "requirements",
    "business_rules",
    "functional_requirements",
    "use_cases",
    "architecture",
    "data_model",
    "integrations",
    "delivery",
    "tasks",
    "dependencies",
    "risks",
    "decisions",
    "technical_design",
    "frappe_state",
    "validation",
    "open_questions",
    "elicitation",
)

STAGES = ("BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE", "VALIDATION", "DONE")

MAX_HISTORY = 200


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_dict(state: Any) -> dict[str, Any]:
    """Best-effort plain-dict view of session state.

    ADK's State exposes .to_dict() (no .items()/.keys()); plain dicts pass
    through. Never raises — returns {} when unreadable.
    """
    if isinstance(state, dict):
        return state
    to_dict = getattr(state, "to_dict", None)
    if callable(to_dict):
        try:
            d = to_dict()
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}
    try:
        return dict(state)
    except Exception:
        return {}


def blank_context(project_name: str = "") -> dict[str, Any]:
    ctx: dict[str, Any] = {s: {} for s in SECTIONS}
    ctx["project"] = {"name": project_name} if project_name else {}
    ctx["change_requests"] = []
    ctx["cr_counter"] = 0
    ctx["history"] = []
    ctx["artifacts"] = {}  # stage -> {doc_key, path, ts, summary}
    ctx["execution"] = {
        "current_stage": "BA",
        "current_agent": "ba_agent",
        "current_artifact": None,
        "current_gate": None,
        "workflow_status": "NOT_STARTED",
        "iteration_count": 0,
        "active_change_request": None,
        "next_stage": "BA",
        "interrupt_seq": 0,
        "pending_interrupt_id": None,
    }
    return ctx


def get_context(state: dict[str, Any]) -> dict[str, Any]:
    """Return the live project_context dict, initializing if absent."""
    ctx = state.get(CONTEXT_KEY)
    if not isinstance(ctx, dict) or not ctx:
        ctx = blank_context()
        try:
            state[CONTEXT_KEY] = ctx
        except Exception:
            pass
    # tolerate contexts created before a section existed
    for s in SECTIONS:
        ctx.setdefault(s, {})
    ctx.setdefault("change_requests", [])
    ctx.setdefault("cr_counter", 0)
    ctx.setdefault("history", [])
    ctx.setdefault("artifacts", {})
    ctx.setdefault("execution", blank_context()["execution"])
    for k, v in blank_context()["execution"].items():  # backfill new fields
        ctx["execution"].setdefault(k, v)
    # CRITICAL (ADK semantics): nested in-place mutations are NOT committed —
    # only top-level state[key] = value sets land in the event delta and
    # survive turns/resumes. Re-assign every touch so ALL helpers that go
    # through get_context() persist. Cost: context re-sent in deltas (KBs).
    try:
        state[CONTEXT_KEY] = ctx
    except Exception:
        pass
    return ctx


def commit(state: Any) -> None:
    """Force-commit nested mutations (call after direct context edits).

    ADK only persists top-level sets; call this after any code path that
    mutates the context WITHOUT going through a helper below.
    """
    try:
        ctx = state.get(CONTEXT_KEY)
        if isinstance(ctx, dict):
            state[CONTEXT_KEY] = ctx
    except Exception:
        pass


def get_section(state: dict[str, Any], section: str) -> Any:
    return deepcopy(get_context(state).get(section))


def set_section(state: dict[str, Any], section: str, value: Any) -> None:
    if section not in SECTIONS:
        raise ValueError(f"Unknown PROJECT_CONTEXT section: {section!r}")
    get_context(state)[section] = deepcopy(value)
    commit(state)


def update_section(state: dict[str, Any], section: str, patch: dict[str, Any]) -> None:
    if section not in SECTIONS:
        raise ValueError(f"Unknown PROJECT_CONTEXT section: {section!r}")
    cur = get_context(state)[section]
    if not isinstance(cur, dict):
        cur = {}
        get_context(state)[section] = cur
    cur.update(deepcopy(patch))
    commit(state)


def append_history(
    state: dict[str, Any],
    event_type: str,
    actor: str,
    summary: str,
    ref: str = "",
) -> dict[str, Any]:
    """Lightweight history: references only, never full documents."""
    entry = {
        "ts": utcnow(),
        "type": event_type,
        "actor": actor,
        "summary": summary[:500],
        "ref": ref[:200],
    }
    hist = get_context(state)["history"]
    hist.append(entry)
    del hist[:-MAX_HISTORY]
    commit(state)
    return entry


def snapshot_artifact(
    state: dict[str, Any],
    stage: str,
    doc_key: str,
    path: str = "",
    summary: str = "",
) -> None:
    get_context(state)["artifacts"][stage] = {
        "doc_key": doc_key,
        "path": path,
        "ts": utcnow(),
        "summary": summary[:500],
    }
    commit(state)


def set_execution(state: dict[str, Any], **fields: Any) -> dict[str, Any]:
    exc = get_context(state)["execution"]
    for k, v in fields.items():
        if k not in exc:
            raise ValueError(f"Unknown execution field: {k!r}")
        exc[k] = v
    commit(state)
    return dict(exc)


def get_execution(state: dict[str, Any]) -> dict[str, Any]:
    return dict(get_context(state)["execution"])


# ---- ToolContext adapters (import ToolContext lazily to keep module light) ----

def _tc_state(tool_context: Any) -> dict[str, Any]:
    try:
        return tool_context.state  # State is dict-like and live
    except Exception:
        return {}


def tc_get_section(tool_context: Any, section: str) -> Any:
    """Get a PROJECT_CONTEXT section (tool-friendly)."""
    return get_section(_tc_state(tool_context), section)


def tc_update_section(tool_context: Any, section: str, patch_json: dict[str, Any]) -> dict:
    """Merge patch_json into a PROJECT_CONTEXT section."""
    update_section(_tc_state(tool_context), section, patch_json)
    return {"ok": True, "section": section}
