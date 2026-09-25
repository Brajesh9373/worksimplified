"""BA ops snapshot — one queryable health summary (prod monitoring).

Aggregates existing state (no new writes except none): stage progress,
agent version, eval queue, HITL queue, package readiness, formal gate, and
history depth. Powers dashboards and the runbook's triage section.
Total function: never raises; failed probes report "unavailable".
"""

from __future__ import annotations

from typing import Any


def _probe(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        return {"_error": str(e)[:200]}


def ops_snapshot(state: dict[str, Any]) -> dict:
    """Return the full ops snapshot (pure read)."""
    snap: dict[str, Any] = {}
    try:
        from shared.project_context import get_context

        ctx = get_context(state)
    except Exception:
        return {"ok": False, "error": "unreadable state"}

    try:
        from ba_agent import stage_tracker as ST

        st = ST.status(state)
        snap["stage"] = {"current": st["current"], "done": st["done"],
                         "pending": st["pending"]}
    except Exception:
        snap["stage"] = "unavailable"

    try:
        from ba_agent.eval import version_manager as VM

        snap["version"] = {"current": VM.current(state),
                           "deployed": len(VM.history(state))}
    except Exception:
        snap["version"] = "unavailable"

    try:
        from ba_agent.eval.feedback_store import _bucket
        from ba_agent.eval import improvement as IM

        fb = _bucket(state).get("feedback", [])
        store = ctx.get("ba_candidates", {})
        by_status: dict[str, int] = {}
        if isinstance(store, dict):
            for c in store.values():
                if isinstance(c, dict):
                    by_status[c.get("status", "?")] = by_status.get(c.get("status", "?"), 0) + 1
        snap["eval"] = {"open_feedback": sum(1 for f in fb if f.get("status") == "open"),
                        "feedback_total": len(fb),
                        "candidates_by_status": by_status}
    except Exception:
        snap["eval"] = "unavailable"

    try:
        from ba_agent.managers.question_manager import open_questions
        from ba_agent.managers import decision_manager as DM

        snap["hitl"] = {"open_questions": len(open_questions(state)),
                        "pending_approvals": len(DM.pending_approvals(state))}
    except Exception:
        snap["hitl"] = "unavailable"

    try:
        from ba_agent.managers.package_assembler import assemble

        pkg = assemble(state)
        snap["package"] = {"ok": pkg["ok"], "missing_count": len(pkg["missing"]),
                           "missing": pkg["missing"][:10]}
    except Exception:
        snap["package"] = "unavailable"

    try:
        from ba_agent.managers.package_assembler import assemble

        pkg = assemble(state)
        snap["package"] = {"ok": pkg["ok"], "missing_count": len(pkg["missing"]),
                           "missing": pkg["missing"][:10]}
    except Exception:
        snap["package"] = "unavailable"

    # §28 monitoring: plan vs progress
    try:
        from ba_agent.managers import ba_plan as BP

        rep = BP.report(state)
        snap["plan"] = {"items": rep["items"], "by_status": rep["by_status"],
                        "variance": rep["variance"][:10],
                        "missing_required": rep["missing"],
                        "corrective": rep["corrective"]}
    except Exception:
        snap["plan"] = "unavailable"

    # §15/§16 + §7 3.15/3.16: quality findings and open contradictions
    try:
        from ba_agent.managers import quality_engine as QE
        from shared.traceability import doc_texts

        brd = doc_texts(state).get("brd", "") or ""
        q = QE.report(brd)
        snap["quality"] = {"blocking": len(q["blocking"]),
                           "by_attribute": q["by_attribute"],
                           "open_contradictions": len(QE.open_contradictions(state))}
    except Exception:
        snap["quality"] = "unavailable"

    # §34 evidence coverage and §27 outcome status
    try:
        from ba_agent.managers import evidence as EV
        from ba_agent.managers import outcome as OC
        from shared.traceability import doc_texts

        brd = doc_texts(state).get("brd", "") or ""
        rep = EV.report(state, brd)
        snap["evidence"] = {"links": rep["links"], "covered": rep["covered"],
                            "total": rep["total"], "uncovered": rep["uncovered"][:10]}
        orep = OC.report(state)
        snap["outcome"] = {"metrics": len(orep["metrics"]), "measured": orep["measured"],
                           "gaps": len(orep["gaps"])}
    except Exception:
        snap["evidence"] = "unavailable"
        snap["outcome"] = "unavailable"

    try:
        snap["history_depth"] = len(ctx.get("history", []) or [])
        snap["suggested_action"] = _suggest(snap)
        snap["ok"] = True
    except Exception:
        snap["ok"] = False
    return snap


def _suggest(snap: dict[str, Any]) -> str:
    """Single next action for on-call triage (best effort)."""
    try:
        hitl = snap.get("hitl", {})
        if isinstance(hitl, dict) and hitl.get("pending_approvals"):
            return f"decide {hitl['pending_approvals']} pending approval(s)"
        if isinstance(hitl, dict) and hitl.get("open_questions"):
            return f"answer {hitl['open_questions']} open stakeholder question(s)"
        plan = snap.get("plan", {})
        if isinstance(plan, dict) and plan.get("variance"):
            return f"close plan variance: {plan['variance'][0].get('reason', 'item open')}"
        if isinstance(plan, dict) and plan.get("missing_required"):
            return f"add required plan item: {plan['missing_required'][0]}"
        quality = snap.get("quality", {})
        if isinstance(quality, dict) and quality.get("open_contradictions"):
            return f"resolve {quality['open_contradictions']} stakeholder contradiction(s)"
        if isinstance(quality, dict) and quality.get("blocking"):
            return f"fix {quality['blocking']} blocking quality finding(s)"
        evidence = snap.get("evidence", {})
        if isinstance(evidence, dict) and evidence.get("uncovered"):
            return f"link evidence for {', '.join(evidence['uncovered'][:3])}"
        gate = snap.get("gate", {})
        if isinstance(gate, dict) and not gate.get("passed", True):
            missing = gate.get("missing", [])
            return f"fix gate: {missing[0]}" if missing else "fix gate failures"
        pkg = snap.get("package", {})
        if isinstance(pkg, dict) and not pkg.get("ok", True):
            missing = pkg.get("missing", [])
            return f"close package gap: {missing[0]}" if missing else "close package gaps"
        outcome = snap.get("outcome", {})
        if isinstance(outcome, dict) and outcome.get("gaps"):
            return f"investigate {outcome['gaps']} business outcome gap(s)"
        ev = snap.get("eval", {})
        if isinstance(ev, dict) and ev.get("open_feedback"):
            return f"triage {ev['open_feedback']} open feedback item(s)"
        return "steady state — no action required"
    except Exception:
        return "triage unavailable"
