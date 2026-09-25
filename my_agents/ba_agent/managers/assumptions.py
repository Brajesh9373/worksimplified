"""Assumption lifecycle (§31) — never quietly convert an assumption into a fact.

The BRD carries `[ASSUMPTION:label]` tags; this registry tracks each one's
status. Invariants enforced for the gate:
  - every tag in the BRD has a registry entry;
  - `confirmed` requires an evidence reference and a named confirmer;
  - a `rejected` assumption must no longer appear in the BRD text.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

ASSUMPTIONS_KEY = "assumptions"
STATUSES = ("unvalidated", "confirmed", "rejected")
_TAG_RX = re.compile(r"\[ASSUMPTION:([^\]]+)\]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _label(label: str) -> str:
    return re.sub(r"\s+", "-", (label or "").strip().strip("[]").strip()).lower()


def _store(state: dict[str, Any]) -> list[dict]:
    from shared.project_context import get_context

    ctx = get_context(state)
    items = ctx.get(ASSUMPTIONS_KEY)
    if not isinstance(items, list):
        items = []
        ctx[ASSUMPTIONS_KEY] = items
    return items


def record(state: dict[str, Any], label: str, text: str = "",
           status: str = "unvalidated", evidence_ref: str = "") -> dict:
    """Register one assumption (idempotent by label)."""
    try:
        from shared.project_context import commit

        lab = _label(label)
        if not lab:
            return {"ok": False, "error": "label is required"}
        if status not in STATUSES:
            return {"ok": False, "error": f"status must be one of {list(STATUSES)}"}
        items = _store(state)
        for a in items:
            if a.get("label") == lab:
                if text:
                    a["text"] = text.strip()[:500]
                commit(state)
                return {"ok": True, "assumption": dict(a), "duplicate": True}
        rec = {"label": lab, "text": (text or "").strip()[:500], "status": status,
               "evidence_ref": (evidence_ref or "").strip()[:200], "decided_by": "",
               "at": _now()}
        items.append(rec)
        del items[:-100]
        commit(state)
        return {"ok": True, "assumption": dict(rec)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def set_status(state: dict[str, Any], label: str, status: str,
               evidence_ref: str = "", decided_by: str = "") -> dict:
    """Move one assumption to confirmed/rejected with evidence and a confirmer."""
    try:
        from shared.project_context import append_history, commit

        lab = _label(label)
        if status not in STATUSES:
            return {"ok": False, "error": f"status must be one of {list(STATUSES)}"}
        for a in _store(state):
            if a.get("label") != lab:
                continue
            if status in ("confirmed", "rejected"):
                if not (evidence_ref or a.get("evidence_ref")):
                    return {"ok": False, "error": f"{status} requires evidence_ref"}
                if not (decided_by or "").strip():
                    return {"ok": False, "error": f"{status} requires decided_by (named human)"}
            a["status"] = status
            if evidence_ref:
                a["evidence_ref"] = evidence_ref.strip()[:200]
            a["decided_by"] = (decided_by or a.get("decided_by") or "").strip()[:120]
            a["decided_at"] = _now()
            commit(state)
            try:
                append_history(state, "ba_assumption_status", a["decided_by"] or "ba_agent",
                               f"ASSUMPTION:{lab} -> {status}", f"ASSUMPTION:{lab}")
            except Exception:
                pass
            return {"ok": True, "assumption": dict(a)}
        return {"ok": False, "error": f"unknown assumption {label!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def assumptions(state: dict[str, Any]) -> list[dict]:
    """Registered assumptions (copies)."""
    try:
        return [dict(a) for a in _store(state) if isinstance(a, dict)]
    except Exception:
        return []


def gaps(state: dict[str, Any], brd: str = "") -> dict:
    """Gate-facing invariants for the assumption ledger."""
    try:
        tags = sorted({_label(t) for t in _TAG_RX.findall(brd or "")})
        registered = {a.get("label") for a in assumptions(state)}
        missing = [t for t in tags if t not in registered]
        invalid_confirm: list[str] = []
        rejected_in_doc: list[str] = []
        low = (brd or "").lower()
        for a in assumptions(state):
            if a.get("status") == "confirmed" and not (
                    a.get("evidence_ref") and a.get("decided_by")):
                invalid_confirm.append(a.get("label", "?"))
            if a.get("status") == "rejected" and f"[assumption:{a.get('label')}]" in low:
                rejected_in_doc.append(a.get("label", "?"))
        return {"tags": tags, "registered": sorted(x for x in registered if x),
                "missing": missing, "invalid_confirm": invalid_confirm,
                "rejected_in_doc": rejected_in_doc,
                "ok": not (missing or invalid_confirm or rejected_in_doc)}
    except Exception as e:
        return {"tags": [], "registered": [], "missing": [], "invalid_confirm": [],
                "rejected_in_doc": [], "ok": False, "error": str(e)[:200]}


def render(state: dict[str, Any], brd: str = "") -> str:
    """Package lines: one per assumption with its status and confirmation."""
    try:
        items = assumptions(state)
        tags = sorted({_label(t) for t in _TAG_RX.findall(brd or "")})
        if not items and not tags:
            return ""
        lines: list[str] = []
        seen = set()
        for a in items:
            seen.add(a.get("label"))
            conf = ""
            if a.get("status") != "unvalidated":
                conf = f" (evidence: {a.get('evidence_ref') or '-'}; by {a.get('decided_by') or '-'})"
            lines.append(f"- **ASSUMPTION:{a.get('label')}** — [{a.get('status')}]{conf} "
                         f"{a.get('text', '')}".rstrip())
        for t in tags:
            if t not in seen:
                lines.append(f"- **ASSUMPTION:{t}** — [unregistered] (no status recorded)")
        return "\n".join(lines)
    except Exception:
        return ""
