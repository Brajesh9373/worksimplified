"""Agent handoffs: each stage receives what it needs from PROJECT_CONTEXT.

Never only the previous agent's output — briefs assemble the full relevant
slice (docs + decisions + CRs + trace + history). Token budgets keep briefs
inside model limits; full documents stay pullable via read_output_file.
"""

from __future__ import annotations

from typing import Any

from .project_context import get_context, get_execution
from .traceability import chain_for, coverage_report, doc_texts

# No clipping: full prior documents flow into briefs so output matches
# human-generated depth. None = unlimited (no char budgets anywhere).
BRIEF_BUDGETS = {
    "BA": {"brd": None, "project_plan": None, "functional_spec": None, "tech_design": None},
    "PROJECT": {"brd": None, "project_plan": None, "functional_spec": None, "tech_design": None},
    "FUNCTIONAL": {"brd": None, "project_plan": None, "functional_spec": None, "tech_design": None},
    "TECHNICAL": {"brd": None, "project_plan": None, "functional_spec": None, "tech_design": None},
    "FRAPPE": {"brd": None, "project_plan": None, "functional_spec": None, "tech_design": None},
}

# which stage's gate warnings ride the brief for a given stage
HANDOFF_SOURCE = {"PROJECT": "BA", "FUNCTIONAL": "PROJECT",
                  "TECHNICAL": "FUNCTIONAL", "FRAPPE": "TECHNICAL"}

STAGE_DOC = {"BA": "brd", "PROJECT": "project_plan", "FUNCTIONAL": "functional_spec",
             "TECHNICAL": "tech_design", "FRAPPE": "frappe_setup"}


def _clip(text: str, budget: int | None) -> str:
    if not text:
        return ""
    if budget is None or budget <= 0:
        return text  # None = unlimited: full document, human-grade depth
    if len(text) <= budget:
        return text
    head = budget * 3 // 4
    return text[:head] + f"\n\n[... clipped {len(text) - budget} chars; use read_output_file for full text ...]\n\n" + text[-budget // 4:]


def build_brief(state: dict[str, Any], stage: str, extra: str = "") -> str:
    """Assemble the handoff brief for a stage from shared context.

    The brief is ALSO saved to the current project workspace (handoffs/)
    so it is auditable per project. Only current-workspace artifacts are
    ever referenced.
    """
    stage = stage.upper()
    ctx = get_context(state)
    texts = doc_texts(state)
    budgets = BRIEF_BUDGETS.get(stage, {})
    cov = coverage_report(state)
    open_crs = [c for c in ctx["change_requests"] if c["status"] in ("OPEN", "IN_PROGRESS")]
    relevant_crs = [c for c in open_crs if c["target_agent"] == stage] or open_crs[:3]
    hist = ctx["history"][-8:]

    parts = [f"# Handoff brief for {stage} (from shared PROJECT_CONTEXT)"]
    if extra:
        parts.append(f"## Orchestrator note (revision requested)\n{extra[:3000]}")
    try:
        from .sections import pending_sections, sections_for
        plan = sections_for(stage)
        if plan:
            done = list(ctx.get(f"sections_done_{stage}", []) or [])
            pend = [s["title"] for s in pending_sections(stage, done)]
            parts.append("## Sectional writing progress\n"
                         f"Done: {done or 'none'} | Pending: {pend or 'none'}\n"
                         "Write ONLY the section assigned in the orchestrator note, via append_doc.")
    except Exception:
        pass
    if relevant_crs:
        parts.append("## Open change requests you must address")
        for c in relevant_crs:
            parts.append(
                f"- {c['id']} [{c['status']}] {c['source_agent']}→{c['target_agent']}: "
                f"{c['reason'][:300]} (reqs={c['affected_requirements']}, tasks={c['affected_tasks']})")
    # The documents the customer handed over are the input material. They are stored
    # in the workspace and recorded as sources, but nothing else tells the agent they
    # exist — without this the upload is stored and then ignored.
    sources = [d for d in ((ctx.get("business") or {}).get("source_documents") or [])
               if isinstance(d, dict)]
    if sources:
        lines = ["## Documents you were given",
                 "These are the customer's own documents, stored in this workspace. "
                 "Read each one with read_output_file before you write — they are "
                 "source material, not summaries."]
        for d in sources[-12:]:
            fname = str(d.get("file") or "")
            if not fname:
                continue
            label = str(d.get("name") or fname)
            chars = int(d.get("chars") or 0)
            lines.append(f"- {fname} ({chars:,} chars)" + (f" — from {label}"
                                                           if label != fname else ""))
        if stage == "BA":
            lines.append('Where one supports a requirement, cite it: '
                         'link_evidence(source_type="upload", source_ref="<file name above>").')
        parts.append("\n".join(lines))
    for key in ("brd", "project_plan", "functional_spec", "tech_design"):
        doc = _clip(texts.get(key, ""), budgets.get(key, 0))
        if doc:
            parts.append(f"## Prior artifact: {key}\n{doc}")
    decisions = ctx.get("decisions", {})
    if decisions:
        parts.append(f"## Recorded decisions\n{str(decisions)[:2000]}")
    risks = ctx.get("risks", {})
    if risks:
        parts.append(f"## Known risks\n{str(risks)[:1500]}")
    oqs = ctx.get("open_questions", {})
    if oqs:
        parts.append(f"## Open questions (do not silently resolve business ambiguity)\n{str(oqs)[:2000]}")
    elic = ctx.get("elicitation", {})
    if isinstance(elic, dict) and elic.get("log"):
        qas = "\n".join(f"Q: {e.get('q', '')[:200]}\nA: {e.get('a', '')[:500]}"
                        for e in elic["log"][-10:])
        parts.append(f"## Elicitation log (already learned — do not re-ask)\n{qas[:3000]}")
    parts.append(
        f"## Traceability counts\nBR={cov['counts']['BR']} US={cov['counts']['US']} "
        f"FR={cov['counts']['FR']} UC={cov['counts']['UC']} T={cov['counts']['T']} "
        f"TECH={cov['counts']['TECH']}")
    if stage == "PROJECT":
        # Downstream intake: the BA Package + unresolved handoff points ride
        # the brief as must-resolve inputs (BA-owned, lazy; brief unchanged
        # when no BA evidence exists).
        try:
            from ba_agent.managers.package_assembler import project_intake
            intake = project_intake(state)
            if intake.get("has_content"):
                parts.append(intake["markdown"])
        except Exception:
            pass
    if hist:
        parts.append("## Recent project history\n" + "\n".join(
            f"- {h['ts']} [{h['type']}] {h['actor']}: {h['summary'][:160]}" for h in hist))
    # Polish and bookkeeping gaps are advisory so the next stage starts immediately
    # rather than looping the previous one — but they must not vanish silently.
    carried: list[str] = []
    for sec_key, items in (ctx.get("section_advisories") or {}).items():
        for it in list(items or [])[:4]:
            carried.append(f"- [{sec_key}] {str(it)[:220]}")
    prev = HANDOFF_SOURCE.get(stage)
    if prev:
        for w in (ctx.get("gate_warnings") or {}).get(prev, [])[:10]:
            carried.append(f"- [{prev} gate, advisory] {str(w)[:200]}")
    if carried:
        parts.append("## Carried-forward gaps (advisory — do not block; address if cheap)\n"
                     + "\n".join(carried[:24]))
    parts.append(
        "## Tools available\n- read_output_file(filename): pull any full document from THIS project workspace\n"
        "  (the prior artifacts below are already complete — do NOT re-read them; "
        "use this only for a file not included above)\n"
        "- append_doc(doc_name, section_title, content_markdown): add ONE section (sectional stages)\n"
        "- get_project_status(): stages, gates, CRs, blockers\n"
        "- create_change_request(...): send work back on contradiction/ambiguity (bounded, max 3 passes)")
    try:
        from .workspace import bound_workspace_id, ProjectWorkspace
        pid = bound_workspace_id(state)
        parts.insert(1, f"Project workspace: `{pid or 'unbound'}` — all files below belong to this project only.")
        if pid:
            ProjectWorkspace(pid).save_handoff(stage, "\n\n".join(parts))
    except Exception:
        pass
    return "\n\n".join(parts)


def status_summary(state: dict[str, Any]) -> dict[str, Any]:
    """User-facing project snapshot: stages, gates, CRs, blockers — no internals dump."""
    from .gates import GATES  # lazy: avoid import cycle
    ctx = get_context(state)
    gate_status: dict[str, Any] = {}
    for name, fn in GATES.items():
        try:
            g = fn(state)
            gate_status[name] = {"passed": g["passed"], "missing_count": len(g["missing"])}
        except Exception as e:
            gate_status[name] = {"passed": False, "missing_count": -1, "error": str(e)[:200]}
    open_crs = [c for c in ctx["change_requests"] if c["status"] in ("OPEN", "IN_PROGRESS")]
    arts = ctx["artifacts"]
    return {
        "execution": get_execution(state),
        "completed_stages": [s for s, a in arts.items() if a.get("path") or a.get("doc_key")],
        "current_artifact": arts.get(get_execution(state)["current_stage"], {}),
        "gates": gate_status,
        "open_change_requests": [
            {"id": c["id"], "route": f"{c['source_agent']}→{c['target_agent']}",
             "status": c["status"], "reason": c["reason"][:200]} for c in open_crs],
        "open_questions": ctx.get("open_questions", {}),
        "frappe_state": ctx.get("frappe_state", {}),
        "history_tail": ctx["history"][-5:],
    }
