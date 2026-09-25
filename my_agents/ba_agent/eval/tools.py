"""ADK tool wrappers for the BA eval loop (production path).

Thin adapters over feedback_store / root_cause / improvement (store-backed) /
regression_runner / version_manager. Never raise — return {ok, ...} dicts so
the agent can report failures. All state lives in project_context and is
persisted to the project workspace (see shared/workspace.persist allowlist).
"""

from __future__ import annotations

from typing import Any


def _state_of(tool_context: Any) -> Any:
    """Return the LIVE session state — never a copy.

    ADK's `State` exposes get/__setitem__/to_dict but no keys()/items(), so a
    `dict(state)` copy silently yields {} and would drop every write made by a
    tool (production bug: unit tests pass plain dicts, live sessions do not).
    The managers and shared.project_context handle both shapes, so the live
    object is handed through unchanged.
    """
    s = getattr(tool_context, "state", None) if tool_context is not None else None
    if s is None:
        return {}
    if isinstance(s, dict) or (hasattr(s, "get") and hasattr(s, "__setitem__")):
        return s
    try:
        return s.to_dict()
    except Exception:
        return {}


def capture_ba_feedback(
    tool_context: Any,
    verdict: str,
    detail: str,
    agent_version: str = "",
    related_ids: str = "",
) -> dict:
    """Record one tester verdict on BA output.

    Args:
        verdict: one of wrong/incomplete/inconsistent/ambiguous/hallucinated/
            misclassified/missing_requirement/bad_question/bad_prioritization/
            poor_methodology/improvement_suggested.
        detail: failure pattern (business facts truncated, max 1000 chars).
        agent_version: version under test (defaults to current).
        related_ids: comma-separated IDs, e.g. 'FR-007,BR-003'.
    """
    from .feedback_store import capture
    from .version_manager import current

    try:
        st = _state_of(tool_context)
        ver = (agent_version or "").strip() or current(st)
        return capture(st, verdict, detail, ver, related_ids)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def analyze_ba_feedback(tool_context: Any, feedback_id: str) -> dict:
    """Classify one feedback entry into an improvement target."""
    from .feedback_store import _bucket
    from .root_cause import analyze

    try:
        st = _state_of(tool_context)
        items = _bucket(st).get("feedback", [])
        fb = next((f for f in items if f.get("id") == feedback_id), None)
        if fb is None:
            return {"ok": False, "error": f"unknown feedback {feedback_id!r}"}
        out = analyze(fb)
        return {"ok": True, "feedback_id": feedback_id, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def propose_ba_candidate(
    tool_context: Any,
    version: str,
    target: str,
    change: str,
    feedback_ids: str = "",
    proposer: str = "tester",
) -> dict:
    """Propose a new BA candidate version (stored, status=proposed)."""
    from .improvement import propose_candidate

    try:
        st = _state_of(tool_context)
        ids = [s.strip() for s in (feedback_ids or "").split(",") if s.strip()]
        return propose_candidate(st, version, target, change, ids, proposer)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def set_ba_regression(
    tool_context: Any,
    version: str,
    passed: bool,
    failures: str = "",
) -> dict:
    """Record a regression outcome on the stored candidate."""
    from .improvement import set_regression

    try:
        st = _state_of(tool_context)
        fails = [s.strip() for s in (failures or "").split(";") if s.strip()]
        return set_regression(st, version, bool(passed), fails)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def run_ba_regression(tool_context: Any, version: str) -> dict:
    """Run deterministic regression (gate + minima + anchors) AND record it.

    The instruction under test is always the live BA_INSTRUCTION so a
    candidate cannot pass by submitting a weakened prompt copy.
    """
    from .improvement import set_regression
    from .regression_runner import run

    try:
        st = _state_of(tool_context)
        from ba_agent.prompts import BA_INSTRUCTION

        res = run(st, BA_INSTRUCTION)
        set_regression(st, version, res["passed"], res.get("failures", []))
        return {"ok": True, "version": version, **res}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def approve_ba_candidate(tool_context: Any, version: str, approver: str) -> dict:
    """Human approval — only regression-pass candidates qualify."""
    from .improvement import approve_candidate

    try:
        return approve_candidate(_state_of(tool_context), version, approver)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def deploy_ba_version(tool_context: Any, version: str) -> dict:
    """Deploy a stored human-approved candidate (hard gate enforced)."""
    from .version_manager import deploy

    try:
        return deploy(_state_of(tool_context), version)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def assemble_ba_package(tool_context: Any, project: str = "") -> dict:
    """Assemble the full BA Package + Project handoff and save both.

    Args:
        project: project slug for doc names (defaults to bound workspace id).

    Saves BA_PACKAGE_<project> (full package) and BA_HANDOFF_<project>
    (9-point Project Agent brief) via save_doc. Items without source data
    render placeholders and are reported in missing[] — never invented.
    """
    try:
        from shared.eng_tools import save_doc
        from shared.project_context import append_history
        from shared.workspace import bound_workspace_id

        st = _state_of(tool_context)
        slug = (project or "").strip()
        if not slug:
            try:
                slug = bound_workspace_id(st) or "project"
            except Exception:
                slug = "project"
        from ba_agent.managers.package_assembler import assemble, handoff_brief

        pkg = assemble(st, slug)
        saved_pkg = save_doc(f"BA_PACKAGE_{slug}", pkg["markdown"], tool_context)
        hb = handoff_brief(st, slug)
        saved_hb = save_doc(f"BA_HANDOFF_{slug}", hb["markdown"], tool_context)
        try:
            append_history(st, "ba_eval", "ba_agent",
                           f"assembled BA package ({pkg['items']} items, "
                           f"{len(pkg['missing'])} missing)", slug)
        except Exception:
            pass
        return {"ok": pkg["ok"], "items": pkg["items"], "missing": pkg["missing"],
                "unresolved_handoff": hb["unresolved"],
                "package": saved_pkg, "handoff": saved_hb,
                "package_version": pkg["package_version"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def ba_eval_status(tool_context: Any) -> dict:
    """Current version, open feedback, candidates, deployed ledger."""
    from .feedback_store import _bucket
    from .version_manager import current, history

    try:
        st = _state_of(tool_context)
        from shared.project_context import get_context

        ctx = get_context(st)
        store = ctx.get("ba_candidates")
        cands = ({k: {"status": v.get("status"), "target": v.get("target")}
                  for k, v in store.items()} if isinstance(store, dict) else {})
        opens = [f.get("id") for f in _bucket(st).get("feedback", [])
                 if f.get("status") == "open"]
        return {"ok": True, "current": current(st), "open_feedback": opens,
                "candidates": cands, "deployed": history(st)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ---------------- HITL collaboration tools (prod) ----------------


def propose_question_batch(tool_context: Any, stage: str,
                           questions: str = "") -> dict:
    """Propose one <=5-question stakeholder batch (tracked, no-repeat).

    Args:
        stage: S1..S7 focus stage for topic tagging.
        questions: newline- or ';'-separated candidate questions.
    """
    try:
        from ba_agent.managers.question_manager import build_batch

        st = _state_of(tool_context)
        cands = [q.strip() for part in (questions or "").split("\n")
                 for q in part.split(";") if q.strip()]
        return build_batch(st, (stage or "S3").strip().upper(), cands)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def log_stakeholder_answer(tool_context: Any, qid: str, answer: str,
                           source: str = "user") -> dict:
    """Log one stakeholder answer (elicitation log + batch tracking)."""
    try:
        from ba_agent.managers import question_manager as QM

        return QM.answer(_state_of(tool_context), qid, answer, source)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def request_ba_approval(tool_context: Any, subject: str, detail: str = "",
                        requester: str = "ba_agent") -> dict:
    """Open an approval request (AP-xxx, pending)."""
    try:
        from ba_agent.managers.decision_manager import request_approval

        return request_approval(_state_of(tool_context), subject, detail,
                                requester)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def record_approval_decision(tool_context: Any, approval_id: str,
                             verdict: str, approver: str) -> dict:
    """Record a human approve/reject (named approver required, audited)."""
    try:
        from ba_agent.managers.decision_manager import decide_approval

        return decide_approval(_state_of(tool_context), approval_id, verdict,
                               approver)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def hitl_status(tool_context: Any) -> dict:
    """Open batch questions, pending approvals, assumption gaps."""
    try:
        from ba_agent.managers import decision_manager as DM
        from ba_agent.managers.question_manager import (assumption_gaps,
                                                        open_questions)
        from shared.traceability import doc_texts

        st = _state_of(tool_context)
        brd = doc_texts(st).get("brd", "")
        return {"ok": True,
                "open_questions": open_questions(st),
                "pending_approvals": DM.pending_approvals(st),
                "assumption_gaps": assumption_gaps(brd)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# ---------------- ops tools (prod monitoring + sampling) ----------------


def ba_ops_snapshot(tool_context: Any) -> dict:
    """One queryable health summary: stage, version, queues, package, gate."""
    try:
        from ba_agent.ops import ops_snapshot

        return ops_snapshot(_state_of(tool_context))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def sampling_review_queue(tool_context: Any, limit: int = 25) -> dict:
    """Feedback entries needing human labels, low-confidence first."""
    try:
        from ba_agent.eval.sampling import review_queue

        return review_queue(_state_of(tool_context), int(limit))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def record_field_label(tool_context: Any, feedback_id: str, true_target: str,
                       labeler: str) -> dict:
    """Store a tester's true root-cause target for one feedback entry."""
    try:
        from ba_agent.eval.sampling import record_label

        return record_label(_state_of(tool_context), feedback_id, true_target,
                            labeler)
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def field_accuracy_report(tool_context: Any) -> dict:
    """Queue depth + rolling field accuracy in one call."""
    try:
        from ba_agent.eval.sampling import report

        return report(_state_of(tool_context))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def build_audience_views(tool_context: Any, project: str = "", audience: str = "") -> dict:
    """Render + save the BA Package audience cuts (§22).

    Args:
        project: slug for the saved file names (defaults to the workspace id).
        audience: one cut — business_owner | project | functional | technical |
            frappe (legacy aliases stakeholder/delivery accepted). Empty renders
            all five cuts.
    """
    try:
        from shared.eng_tools import save_doc
        from shared.workspace import bound_workspace_id

        st = _state_of(tool_context)
        slug = (project or "").strip()
        if not slug:
            try:
                slug = bound_workspace_id(st) or "project"
            except Exception:
                slug = "project"
        from ba_agent.managers.package_assembler import AUDIENCE_VIEW_ITEMS, audience_views

        views = audience_views(st, slug, audience)
        if views.get("error"):
            return {"ok": False, "error": views["error"]}
        saved: dict[str, Any] = {}
        names = [a for a in AUDIENCE_VIEW_ITEMS if isinstance(views.get(a), str)]
        for name in names:
            saved[name] = save_doc(f"BA_VIEW_{name.upper()}_{slug}", views[name], tool_context)
        out: dict[str, Any] = {"ok": True, "missing": views.get("missing", []),
                               "audiences": names, "saved": saved}
        # legacy keys used by the runbook and earlier callers
        if "business_owner" in views:
            out["stakeholder"] = saved.get("business_owner", {})
        if "project" in views:
            out["delivery"] = saved.get("project", {})
        return out
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def reuse_report_tool(tool_context: Any) -> dict:
    """Carried/added/dropped requirement IDs between BA versions."""
    try:
        from ba_agent.eval.version_manager import reuse_report

        return reuse_report(_state_of(tool_context))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def improvement_memory_report(tool_context: Any) -> dict:
    """Validated improvement lessons from deployed versions, by target."""
    try:
        from ba_agent.eval.version_manager import memory_report

        return memory_report(_state_of(tool_context))
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}
