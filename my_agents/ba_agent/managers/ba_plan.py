"""§28 BA Planning & Monitoring as a cross-cutting capability (not an 8th stage).

The plan is a small, persisted registry: approach / stakeholder strategy /
elicitation plan / traceability plan / prioritisation approach / review plan /
communication plan, plus any number of activities, deliverables and
communications the BA adds. Defaults are seeded deterministically so a run
cannot start without a plan.

Monitoring = variance: a plan item whose stage is already complete but which is
still `planned`/`in_progress` is drift, and it drives the ops snapshot's
suggested action. `variance()` never raises.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

PLAN_KEY = "ba_plan"
TYPES = ("approach", "activity", "deliverable", "communication", "review")
STATUSES = ("planned", "in_progress", "done", "waived")
STAGES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7")

# Validator stages own no sections (see SECTION_GROUPS): they are computed over
# the requirement set, so they count as complete when the stage they govern is.
_VALIDATOR_STAGE_DEPS: dict[str, str] = {"S5": "S4", "S6": "S4"}

# (type, title, stage, required?) — seeded once per project.
DEFAULTS: tuple[tuple[str, str, str, bool], ...] = (
    ("approach", "BA approach and scope of analysis", "S1", True),
    ("approach", "Stakeholder analysis and engagement strategy", "S1", True),
    ("activity", "Elicitation plan (techniques per information gap)", "S3", False),
    ("activity", "Traceability and requirements-management plan", "S6", False),
    ("approach", "Prioritisation approach (MoSCoW + business value)", "S4", False),
    ("review", "Review and approval plan (gate + human final review)", "S1", True),
    ("communication", "Communication plan (audience views and cadence)", "S6", False),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store(state: dict[str, Any]) -> list[dict]:
    from shared.project_context import get_context

    ctx = get_context(state)
    items = ctx.get(PLAN_KEY)
    if not isinstance(items, list):
        items = []
        ctx[PLAN_KEY] = items
    return items


def seed(state: dict[str, Any]) -> dict:
    """Create the default plan items once (idempotent by title)."""
    try:
        from shared.project_context import commit

        items = _store(state)
        have = {i.get("title") for i in items if isinstance(i, dict)}
        added = 0
        for _t, title, stage, _req in DEFAULTS:
            if title in have:
                continue
            items.append({"id": f"PL-{len(items) + 1:03d}", "type": _t, "title": title,
                          "stage": stage, "owner": "ba_agent", "status": "planned",
                          "note": "", "at": _now()})
            added += 1
        if added:
            commit(state)
        return {"ok": True, "added": added, "items": len(items)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "added": 0}


def record(state: dict[str, Any], title: str, type: str = "activity",
           stage: str = "", owner: str = "ba_agent", status: str = "planned",
           note: str = "") -> dict:
    """Add one plan item (PL-n)."""
    try:
        from shared.project_context import commit

        if not (title or "").strip():
            return {"ok": False, "error": "title is required"}
        if type not in TYPES:
            return {"ok": False, "error": f"type must be one of {list(TYPES)}"}
        if status not in STATUSES:
            return {"ok": False, "error": f"status must be one of {list(STATUSES)}"}
        st = (stage or "").strip().upper()
        if st and st not in STAGES:
            return {"ok": False, "error": f"stage must be one of {list(STAGES)}"}
        items = _store(state)
        rec = {"id": f"PL-{len(items) + 1:03d}", "type": type, "title": title.strip()[:200],
               "stage": st, "owner": (owner or "ba_agent").strip()[:120],
               "status": status, "note": (note or "").strip()[:300], "at": _now()}
        items.append(rec)
        del items[:-100]
        commit(state)
        return {"ok": True, "item": dict(rec)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def update(state: dict[str, Any], item_id: str, status: str = "",
           note: str = "") -> dict:
    """Advance one plan item (status and/or note)."""
    try:
        from shared.project_context import commit

        if status and status not in STATUSES:
            return {"ok": False, "error": f"status must be one of {list(STATUSES)}"}
        for i in _store(state):
            if isinstance(i, dict) and i.get("id") == item_id:
                if status:
                    i["status"] = status
                if note:
                    i["note"] = note.strip()[:300]
                i["updated_at"] = _now()
                commit(state)
                return {"ok": True, "item": dict(i)}
        return {"ok": False, "error": f"unknown plan item {item_id!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def items(state: dict[str, Any]) -> list[dict]:
    """Plan items (copies)."""
    try:
        return [dict(i) for i in _store(state) if isinstance(i, dict)]
    except Exception:
        return []


def _stage_done(state: dict[str, Any]) -> set[str]:
    """Stages whose accepted sections are complete.

    Deliberately section-based (not the full gate/evidence computation): plan
    monitoring must stay cheap and cycle-free — the gate itself calls
    `report()`/`variance()`, so calling the gate from here would recurse.

    S5 and S6 own no sections: they are computed validators over the requirement
    set, so their planning items close once the requirements they govern (S4) are
    accepted. The gate still enforces traceability and quality separately — the
    plan tracks planning work, the gate enforces artefacts.
    """
    try:
        from shared.project_context import get_context

        from ba_agent.stage_engine import SECTION_GROUPS

        done = set(get_context(state).get("sections_done_BA") or [])
        complete = {s for s, secs in SECTION_GROUPS.items() if secs <= done}
        for vstage, dep in _VALIDATOR_STAGE_DEPS.items():
            if dep in complete:
                complete.add(vstage)
        return complete
    except Exception:
        return set()


def sync(state: dict[str, Any]) -> int:
    """Close plan items bound to a completed stage. Idempotent.

    An item's `stage` is the binding: once every section of that stage is accepted
    the work the item describes is done — a fact the harness owns, so closing it
    must not depend on the model remembering to tick its own checklist. Items with
    no stage are free-standing commitments and are left to the agent, as are items
    it explicitly `waived`.
    """
    try:
        from shared.project_context import commit

        done = _stage_done(state)
        if not done:
            return 0
        closed = 0
        for i in _store(state):
            if not isinstance(i, dict):
                continue
            st = i.get("stage") or ""
            if not st:
                continue
            if st in done and i.get("status") in ("planned", "in_progress"):
                i["status"] = "done"
                i["note"] = f"auto-closed: stage {st} complete (its sections were accepted)"
                i["updated_at"] = _now()
                closed += 1
        if closed:
            commit(state)
        return closed
    except Exception:
        return 0


def variance(state: dict[str, Any]) -> list[dict]:
    """Plan drift: items whose stage is complete but work is not closed."""
    try:
        done = _stage_done(state)
        out: list[dict] = []
        for i in items(state):
            st = i.get("stage") or ""
            if st and st in done and i.get("status") not in ("done", "waived"):
                out.append({"id": i.get("id"), "title": i.get("title"), "stage": st,
                            "status": i.get("status"),
                            "reason": f"stage {st} is complete but this item is "
                                      f"{i.get('status')}"})
        return out
    except Exception:
        return []


def required_present(state: dict[str, Any]) -> list[str]:
    """Missing *required* plan items (gate-facing)."""
    try:
        have = {(i.get("type"), i.get("title")) for i in items(state)}
        return [f"plan item '{title}' ({t}) is missing"
                for t, title, _s, req in DEFAULTS if req and (t, title) not in have]
    except Exception:
        return ["plan unreadable"]


def report(state: dict[str, Any]) -> dict:
    """Full plan view for the package and ops snapshot."""
    try:
        all_items = items(state)
        by_status: dict[str, int] = {}
        for i in all_items:
            by_status[i.get("status", "?")] = by_status.get(i.get("status", "?"), 0) + 1
        var = variance(state)
        missing = required_present(state)
        return {"ok": not (var or missing), "items": len(all_items),
                "by_status": by_status, "variance": var, "missing": missing,
                "corrective": (var[0]["reason"] if var else
                               (missing[0] if missing else ""))}
    except Exception as e:
        return {"ok": False, "items": 0, "by_status": {}, "variance": [], "missing": [],
                "corrective": "", "error": str(e)[:200]}


def render(state: dict[str, Any]) -> str:
    """Package rendering of the plan + monitoring variance."""
    try:
        rows = items(state)
        if not rows:
            return ""
        lines = [f"| {i.get('id')} | {i.get('type')} | {i.get('title')} | "
                 f"{i.get('stage') or '-'} | {i.get('status')} |" for i in rows]
        body = "| ID | Type | Item | Stage | Status |\n|---|---|---|---|---|\n" + "\n".join(lines)
        var = variance(state)
        if var:
            body += "\n\n**Monitoring variance**\n" + "\n".join(
                f"- {v['id']} {v['title']}: {v['reason']}" for v in var)
        else:
            body += "\n\n**Monitoring variance**: none — plan and progress agree."
        return body
    except Exception:
        return ""
