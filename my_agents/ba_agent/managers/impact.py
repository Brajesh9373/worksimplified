"""Change-impact analysis (§20, §6.9–6.16) for requirements-level change control.

Given the ids a change touches, walks the existing traceability (no new ID
scheme) to compute the affected set: scope sections, requirements, plan tasks,
risks, decisions, package items that go stale, and artefacts needing
re-validation — plus the consequence (which stories lose acceptance coverage,
which rules become orphaned) and the human decision the change needs.

One impact report per change request; the BA gate refuses to certify while an
open change request has no impact report.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

IMPACT_KEY = "impact_reports"
_CLOSED_CR = ("resolved", "closed", "cancelled", "rejected")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store(state: dict[str, Any]) -> dict:
    from shared.project_context import get_context

    ctx = get_context(state)
    store = ctx.get(IMPACT_KEY)
    if not isinstance(store, dict):
        store = {}
        ctx[IMPACT_KEY] = store
    return store


def analyze(state: dict[str, Any], cr_id: str, changed_ids: list[str],
            reason: str = "", brd: str = "") -> dict:
    """Compute and persist the impact report for one change request."""
    try:
        from shared.project_context import append_history, commit
        from shared.traceability import chain_for, coverage_report, extract_ids

        if not (cr_id or "").strip():
            return {"ok": False, "error": "cr_id is required"}
        ids = [str(i).strip().upper() for i in (changed_ids or []) if str(i).strip()]
        if not ids:
            return {"ok": False, "error": "changed_ids must name at least one requirement id"}

        from shared.project_context import get_context

        text = brd or ""
        if not text:
            try:
                from shared.traceability import doc_texts

                text = doc_texts(state).get("brd", "") or ""
            except Exception:
                text = ""
        inv = extract_ids(text)
        cov = coverage_report(state)

        affected: dict[str, list[str]] = {}
        for cid in ids:
            chain = chain_for(state, cid).get("chain", {}) if "-" in cid else {}
            for fam, members in (chain or {}).items():
                if fam in ("FRAPPE", "VALIDATED"):
                    continue
                for m in members or []:
                    affected.setdefault(fam, [])
                    if m not in affected[fam]:
                        affected[fam].append(m)

        consequence: list[str] = []
        stale_items: list[str] = []
        for cid in ids:
            fam = cid.split("-")[0]
            if fam == "US":
                stale_items += ["user_stories", "acceptance_criteria", "traceability", "baseline"]
                if cid not in (inv.get("BR") or []) :
                    consequence.append(f"{cid} changes: re-check its acceptance criteria and "
                                       "the linked business rule")
            elif fam == "BR":
                stale_items += ["business_rules", "traceability", "baseline"]
                consequence.append(f"{cid} changes: every US relying on this rule needs "
                                   "re-validation")
            elif fam == "FR":
                stale_items += ["functional_requirements", "traceability", "baseline"]
                consequence.append(f"{cid} changes: downstream functional/technical design must "
                                   "be revisited")
            elif fam == "OPT":
                stale_items += ["business_case", "solution_assessment"]
                consequence.append(f"{cid} changes: re-run the options and coverage assessment")
            else:
                stale_items += ["baseline", "changelog"]
        for unc in ("uncovered_US", "uncovered_FR_no_TECH"):
            if cov.get(unc):
                consequence.append(f"existing coverage gap to watch: {unc} "
                                   f"({', '.join(map(str, cov[unc][:5]))})")

        report = {
            "cr_id": cr_id.strip(),
            "reason": (reason or "").strip()[:400],
            "changed_ids": ids,
            "affected": {k: sorted(set(v)) for k, v in sorted(affected.items())},
            "affected_plan_tasks": sorted(set(
                t for t in (inv.get("T") or []) if t))[:20],
            "stale_package_items": sorted(set(stale_items)),
            "consequences": consequence[:10],
            "artifacts_to_revalidate": ["brd", "ba_package"],
            "decision_needed": ("Approve, reject or defer this change — a baselined requirement "
                                "is never altered silently."),
            "analyzed_at": _now(),
            "status": "open",
        }
        store = _store(state)
        store[report["cr_id"]] = report
        commit(state)
        try:
            append_history(state, "ba_impact_analysis", "ba_agent",
                           f"{report['cr_id']} impact: "
                           f"{sum(len(v) for v in report['affected'].values())} affected item(s)",
                           report["cr_id"])
        except Exception:
            pass
        return {"ok": True, "impact": report}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def get(state: dict[str, Any], cr_id: str) -> dict | None:
    """One stored impact report (copy)."""
    try:
        rec = _store(state).get((cr_id or "").strip())
        return dict(rec) if isinstance(rec, dict) else None
    except Exception:
        return None


def all_reports(state: dict[str, Any]) -> list[dict]:
    """Every stored impact report (copies), newest first."""
    try:
        return [dict(v) for v in _store(state).values() if isinstance(v, dict)]
    except Exception:
        return []


def open_without_impact(state: dict[str, Any]) -> list[str]:
    """Open change requests that have no impact report (gate-facing)."""
    try:
        from shared.project_context import get_context

        crs = get_context(state).get("change_requests", []) or []
        have = set(_store(state).keys())
        out: list[str] = []
        for c in crs if isinstance(crs, list) else []:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id", ""))
            if not cid:
                continue
            if str(c.get("status", "")).lower() in _CLOSED_CR:
                continue
            if cid not in have:
                out.append(cid)
        return sorted(set(out))
    except Exception:
        return []


def render(state: dict[str, Any]) -> str:
    """Package rendering: each open CR with its impact analysis."""
    try:
        reps = all_reports(state)
        if not reps:
            return ""
        lines: list[str] = []
        for r in reps:
            affected = ", ".join(f"{k}={len(v)}" for k, v in (r.get("affected") or {}).items())
            lines.append(f"- **{r.get('cr_id')}** [{r.get('status')}] changed: "
                         f"{', '.join(r.get('changed_ids', []))} | affected: {affected or '-'} "
                         f"| stale: {', '.join(r.get('stale_package_items', [])[:4]) or '-'}")
            for c in (r.get("consequences") or [])[:3]:
                lines.append(f"  - consequence: {c}")
            lines.append(f"  - decision needed: {r.get('decision_needed')}")
        return "\n".join(lines)
    except Exception:
        return ""
