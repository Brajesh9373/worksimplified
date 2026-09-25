"""Tool wrappers for orchestration infra (safe to attach to any agent).

These expose PROJECT_CONTEXT capabilities as ADK function tools WITHOUT
touching any agent system prompt. Pure logic lives in sibling modules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Whole documents are already in the handoff brief, so a tool read only needs to
# return enough to locate/quote from it. Unbounded reads wreck the tool loop.
_READ_CAP = 12000
# A customer-supplied upload is input material, not a duplicate of the brief
_UPLOAD_READ_CAP = 40000


def _ws_of(tool_context: Any):
    from .workspace import current_workspace
    state = None
    try:
        state = tool_context.state if tool_context is not None else None
    except Exception:
        state = None
    return current_workspace(state=state, tool_context=tool_context)


def get_project_status(tool_context: Any) -> dict:
    """Show ONE-project status: stage, gates, CRs, blockers, frappe state."""
    from .handoffs import status_summary
    try:
        return {"ok": True, "status": status_summary(tool_context.state)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def get_stage_brief(tool_context: Any, stage: str = "") -> dict:
    """Get the shared-context handoff brief for a stage (BA/PROJECT/FUNCTIONAL/TECHNICAL/FRAPPE)."""
    from .handoffs import build_brief
    from .project_context import get_execution
    try:
        st = stage.upper() or get_execution(tool_context.state)["current_stage"]
        return {"ok": True, "stage": st, "brief": build_brief(tool_context.state, st)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def create_change_request(
    tool_context: Any,
    target_agent: str,
    reason: str,
    affected_requirements: str = "",
    affected_tasks: str = "",
    affected_artifacts: str = "",
    impact: str = "",
) -> dict:
    """Send work back to an earlier stage (bounded: max 3 passes, then human).

    Args:
        target_agent: stage that must revise (BA/PROJECT/FUNCTIONAL/TECHNICAL).
        reason: contradiction/ambiguity/gap found.
        affected_requirements: comma-separated IDs (e.g. "FR-014,BR-003").
        affected_tasks: comma-separated IDs (e.g. "T-023").
        affected_artifacts: comma-separated names.
        impact: downstream impact note.
    """
    from .change_requests import create_cr, note_iteration
    from .project_context import get_execution
    try:
        src = get_execution(tool_context.state)["current_stage"]
        cr = create_cr(
            tool_context.state, src, target_agent, reason,
            [s.strip() for s in affected_requirements.split(",") if s.strip()],
            [s.strip() for s in affected_tasks.split(",") if s.strip()],
            [s.strip() for s in affected_artifacts.split(",") if s.strip()],
            impact)
        return {"ok": True, "cr": cr,
                "note": f"Filed {cr['id']}. Orchestrator will route it (max 3 passes before human review)."}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def record_history_event(tool_context: Any, event_type: str, summary: str, ref: str = "") -> dict:
    """Append a lightweight history entry (references only, no doc dumps)."""
    from .project_context import append_history, get_execution
    try:
        actor = get_execution(tool_context.state)["current_agent"]
        return {"ok": True, "entry": append_history(tool_context.state, event_type, actor, summary, ref)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def read_output_file(tool_context: Any, filename: str) -> dict:
    """Read a file from the CURRENT project workspace (never other projects).

    Capped: the stage documents already ride the handoff brief in full, so returning
    them again is pure duplication. Unbounded reads doubled the conversation on every
    tool round of a FRAPPE run (76k -> 154k -> 200k chars across six calls) until the
    gateway stalled.

    A document the customer uploaded is the exception — it is input material rather
    than a copy of the brief, so clipping it to the stage-document budget would hide
    most of what they handed over. It gets a larger cap, and says so when it clips.
    """
    try:
        ws = _ws_of(tool_context)
        name = Path(filename).name
        cap = _UPLOAD_READ_CAP if name.startswith("uploaded_") else _READ_CAP
        content = ws.read_artifact(name) or ""
        total = len(content)
        if total > cap:
            content = (content[:cap]
                       + f"\n\n[... clipped {total - cap} of {total} chars."
                       + (" Read again with a narrower question if you need a later part."
                          if cap == _UPLOAD_READ_CAP else
                          " The full document is already in your handoff brief — work "
                          "from that and do not re-read this file.")
                       + " ...]")
        return {"ok": True, "file": name, "project_id": ws.project_id,
                "chars": total, "content": content}
    except (FileNotFoundError, ValueError) as e:
        return {"ok": False, "error": f"{e} (current project only)"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def record_elicitation(tool_context: Any, question: str, answer: str, source: str = "user") -> dict:
    """Log one elicitation Q&A pair so answers survive across turns/stages.

    Call after each stakeholder answer during discovery. The log is rendered
    into every BA brief, so revision passes never lose what was learned.
    """
    from .project_context import commit, get_context
    try:
        ctx = get_context(tool_context.state)
        log = ctx.get("elicitation")
        if not isinstance(log, dict) or not isinstance(log.get("log"), list):
            log = {"log": []}
            ctx["elicitation"] = log
        log["log"].append({"q": question[:500], "a": answer[:2000], "source": source[:80]})
        del log["log"][:-50]
        commit(tool_context.state)
        return {"ok": True, "entries": len(log["log"])}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def record_decision(tool_context: Any, key: str, decision: str) -> dict:
    """Record a project decision in shared context (survives all stages)."""
    from .project_context import get_context
    try:
        ctx = get_context(tool_context.state)
        ctx["decisions"][key] = decision[:1000]
        return {"ok": True, "key": key}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
