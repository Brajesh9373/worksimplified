"""Decision manager — decisions + approval requests (persisted, resume-safe).

Decisions (§33) are first-class `DEC-n` records carrying the decision, its
reason, the decision maker and the related requirement ids — free-form strings
are kept only for backward compatibility. Approvals: request_approval() opens
AP-xxx (pending); decide_approval() records approve/reject with a named
approver, and an approval automatically records the linked DEC entry (an
approval is a decision — the gate refuses to certify otherwise).

State keys: ba_approvals, decision_log (see shared/workspace allowlist).
Total functions: never raise.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

APPROVALS_KEY = "ba_approvals"
DECISIONS_KEY = "decision_log"
STATUSES = ("proposed", "approved", "rejected")
_RELATED_RX = re.compile(r"^(?:G|BN|OPT|BR|US|FR|UC|T|TECH|SM)-\d+$", re.IGNORECASE)


def record(state: dict[str, Any], key: str, decision: str) -> dict:
    from shared.project_context import get_context

    ctx = get_context(state)
    if "decisions" not in ctx or not isinstance(ctx["decisions"], dict):
        ctx["decisions"] = {}
    ctx["decisions"][key] = (decision or "")[:1000]
    return {"ok": True, "key": key}


def all_decisions(state: dict[str, Any]) -> dict:
    from shared.project_context import get_context

    dec = get_context(state).get("decisions", {})
    return dec if isinstance(dec, dict) else {}


# ------------------------------------------------------------ decision records


def _log(state: dict[str, Any]) -> list[dict]:
    from shared.project_context import get_context

    ctx = get_context(state)
    log = ctx.get(DECISIONS_KEY)
    if not isinstance(log, list):
        log = []
        ctx[DECISIONS_KEY] = log
    return log


def record_decision(state: dict[str, Any], decision: str, reason: str,
                    decision_maker: str, related_ids: str = "",
                    status: str = "approved", approval_id: str = "",
                    brd: str = "") -> dict:
    """Record one DEC-n decision with reason, maker and related ids."""
    try:
        from shared.project_context import append_history, commit

        if not (decision or "").strip():
            return {"ok": False, "error": "decision is required"}
        if not (reason or "").strip():
            return {"ok": False, "error": "reason is required (why this decision)"}
        if not (decision_maker or "").strip():
            return {"ok": False, "error": "decision_maker is required (named human/role)"}
        if status not in STATUSES:
            return {"ok": False, "error": f"status must be one of {list(STATUSES)}"}
        rel = [r.strip().upper() for r in re.split(r"[,\s]+", related_ids or "") if r.strip()]
        bad = [r for r in rel if not _RELATED_RX.match(r)]
        if bad:
            return {"ok": False, "error": f"related_ids must be requirement ids, got {bad}"}
        if rel and brd:
            try:
                from shared.traceability import extract_ids

                known = {i for ids in extract_ids(brd).values() for i in ids}
                unknown = [r for r in rel if known and r not in known]
                if unknown:
                    return {"ok": False, "error": f"related ids not found in the BRD: {unknown}"}
            except Exception:
                pass
        log = _log(state)
        rec = {"id": f"DEC-{len(log) + 1:03d}", "decision": decision.strip()[:500],
               "reason": reason.strip()[:500],
               "decision_maker": decision_maker.strip()[:120], "related_ids": rel,
               "status": status, "approval_id": (approval_id or "").strip(),
               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        log.append(rec)
        del log[:-200]
        commit(state)
        try:
            append_history(state, "ba_decision", rec["decision_maker"],
                           f"{rec['id']} {rec['status']}: {rec['decision'][:140]}", rec["id"])
        except Exception:
            pass
        return {"ok": True, "decision": dict(rec)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def decisions(state: dict[str, Any]) -> list[dict]:
    """All DEC-n records (copies)."""
    try:
        return [dict(d) for d in _log(state) if isinstance(d, dict)]
    except Exception:
        return []


def decisions_missing_for_approvals(state: dict[str, Any]) -> list[str]:
    """Approved AP-xxx that have no DEC-n record (gate-facing)."""
    try:
        approved = {a.get("id") for a in _approvals(state)
                    if isinstance(a, dict) and a.get("status") == "approved"}
        recorded = {d.get("approval_id") for d in decisions(state) if d.get("approval_id")}
        return sorted(x for x in approved if x and x not in recorded)
    except Exception:
        return []


def render(state: dict[str, Any]) -> str:
    """Package rendering: structured decisions, then legacy free-form entries."""
    try:
        lines = [f"- **{d['id']}** [{d.get('status')}] {d.get('decision')} — reason: "
                 f"{d.get('reason')} (by {d.get('decision_maker')}; "
                 f"related: {', '.join(d.get('related_ids') or []) or '-'})"
                 for d in decisions(state)]
        legacy = all_decisions(state)
        lines += [f"- {k}: {v}" for k, v in legacy.items()]
        return "\n".join(lines)
    except Exception:
        return ""


def _approvals(state: dict[str, Any]) -> list:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    items = ctx.get(APPROVALS_KEY)
    if not isinstance(items, list):
        items = []
        ctx[APPROVALS_KEY] = items
        commit(state)
    return items


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_approval(state: dict[str, Any], subject: str, detail: str = "",
                     requester: str = "ba_agent") -> dict:
    """Open an approval request. Returns {ok, approval} or {ok: False, error}."""
    try:
        from shared.project_context import commit

        if not (subject or "").strip():
            return {"ok": False, "error": "subject is required"}
        items = _approvals(state)
        ap = {"id": f"AP-{len(items) + 1:03d}", "subject": subject.strip()[:200],
              "detail": (detail or "").strip()[:1000],
              "requester": (requester or "ba_agent")[:120],
              "status": "pending", "requested_at": _utcnow()}
        items.append(ap)
        del items[:-50]
        commit(state)
        return {"ok": True, "approval": dict(ap)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def decide_approval(state: dict[str, Any], approval_id: str, verdict: str,
                    approver: str) -> dict:
    """Approve/reject a pending request (named approver required, audited)."""
    try:
        from shared.project_context import append_history, commit

        v = (verdict or "").strip().lower()
        if v not in ("approve", "reject"):
            return {"ok": False, "error": "verdict must be approve or reject"}
        if not (approver or "").strip():
            return {"ok": False, "error": "approver is required"}
        for ap in _approvals(state):
            if isinstance(ap, dict) and ap.get("id") == approval_id:
                if ap.get("status") != "pending":
                    return {"ok": False,
                            "error": f"{approval_id} already {ap.get('status')}"}
                ap["status"] = "approved" if v == "approve" else "rejected"
                ap["approver"] = approver.strip()[:120]
                ap["decided_at"] = _utcnow()
                commit(state)
                try:
                    append_history(state, "ba_approval", ap["approver"],
                                   f"{ap['status']} {approval_id}: {ap['subject']}",
                                   approval_id)
                except Exception:
                    pass
                # An approval IS a decision (§33): record the linked DEC-n so the
                # reasoning behind the approval is never lost.
                if v == "approve":
                    record_decision(
                        state,
                        decision=f"{ap['subject']} approved",
                        reason=ap.get("detail") or "Approved at the BA approval gate.",
                        decision_maker=ap["approver"],
                        status="approved",
                        approval_id=approval_id,
                    )
                return {"ok": True, "approval": dict(ap)}
        return {"ok": False, "error": f"unknown approval {approval_id!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def pending_approvals(state: dict[str, Any]) -> list[dict]:
    """All pending approval requests (copies)."""
    try:
        return [dict(a) for a in _approvals(state)
                if isinstance(a, dict) and a.get("status") == "pending"]
    except Exception:
        return []
