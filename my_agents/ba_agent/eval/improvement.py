"""Improvement engine — feedback + root cause -> stored candidate proposal.

Hard-gate design (prod):
- Candidates live ONLY in state (project_context['ba_candidates']), keyed by
  version. All transitions mutate the stored copy via get_context()+commit().
- Callers can never inject a dict with a forged status: deploy/approve/
  regression always load the stored candidate by version id and validate its
  stored status. A tampered copy passed by the caller is ignored.
- Status flow: proposed -> regression-pass -> human-approved -> deployed;
  regression-fail -> needs-fix (re-run regression to leave fail state).

Pure builders (propose/mark_regression/approve) are kept for unit tests;
the state-backed wrappers below are the only path production code uses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

FLOW = ("proposed", "regression-pass", "regression-fail", "human-approved", "deployed")

CANDIDATES_KEY = "ba_candidates"


def propose(
    version: str,
    target: str,
    change: str,
    feedback_ids: list[str],
) -> dict:
    """Build a candidate proposal dict (pure, no I/O)."""
    if not version.strip():
        return {"ok": False, "error": "version is required (e.g. v1.1-candidate)"}
    if not change.strip():
        return {"ok": False, "error": "change description is required"}
    return {
        "ok": True,
        "candidate": {
            "version": version.strip()[:64],
            "target": target,
            "change": change.strip()[:2000],
            "feedback_ids": list(feedback_ids)[:50],
            "status": "proposed",
        },
    }


def mark_regression(candidate: dict, passed: bool) -> dict:
    """Advance candidate after regression run."""
    candidate["status"] = "regression-pass" if passed else "regression-fail"
    return candidate


def approve(candidate: dict, approver: str) -> dict:
    """Human approval gate — only regression-pass candidates may be approved."""
    if candidate.get("status") != "regression-pass":
        return {"ok": False, "error": "only regression-pass candidates can be approved"}
    if not approver.strip():
        return {"ok": False, "error": "approver is required"}
    candidate["status"] = "human-approved"
    candidate["approver"] = approver.strip()[:120]
    return {"ok": True, "candidate": candidate}


# ---------------- state-backed store (production path) ----------------


def _store(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    store = ctx.get(CANDIDATES_KEY)
    if not isinstance(store, dict):
        store = {}
        ctx[CANDIDATES_KEY] = store
        commit(state)
    return store


def _audit(state: dict[str, Any], actor: str, summary: str, ref: str = "") -> None:
    try:
        from shared.project_context import append_history

        append_history(state, "ba_eval", actor, summary, ref)
    except Exception:
        pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_candidate(state: dict[str, Any], version: str) -> dict | None:
    """Return the stored candidate (a copy) or None."""
    c = _store(state).get((version or "").strip())
    return dict(c) if isinstance(c, dict) else None


def propose_candidate(
    state: dict[str, Any],
    version: str,
    target: str,
    change: str,
    feedback_ids: list[str],
    proposer: str = "tester",
) -> dict:
    """Create + persist a candidate. Fails if version already exists."""
    from shared.project_context import commit

    v = (version or "").strip()[:64]
    if not v:
        return {"ok": False, "error": "version is required (e.g. v1.1-candidate)"}
    if not (change or "").strip():
        return {"ok": False, "error": "change description is required"}
    store = _store(state)
    if v in store:
        return {"ok": False, "error": f"candidate {v!r} already exists (status={store[v].get('status')})"}
    built = propose(v, target, change, feedback_ids)
    if not built.get("ok"):
        return built
    c = built["candidate"]
    c["proposer"] = (proposer or "tester")[:120]
    c["proposed_at"] = _utcnow()
    store[v] = c
    commit(state)
    _audit(state, c["proposer"], f"proposed BA candidate {v} targeting {target}", v)
    return {"ok": True, "candidate": dict(c)}


def set_regression(
    state: dict[str, Any],
    version: str,
    passed: bool,
    failures: list[str] | None = None,
) -> dict:
    """Record a regression outcome on the stored candidate (by version id)."""
    from shared.project_context import commit

    v = (version or "").strip()
    store = _store(state)
    c = store.get(v)
    if not isinstance(c, dict):
        return {"ok": False, "error": f"unknown candidate {v!r}"}
    if c.get("status") == "deployed":
        return {"ok": False, "error": f"candidate {v!r} already deployed (immutable)"}
    mark_regression(c, passed)
    c["regression_at"] = _utcnow()
    c["regression_failures"] = list(failures or [])[:50]
    commit(state)
    _audit(state, "regression-runner",
           f"regression {'PASS' if passed else 'FAIL'} for BA candidate {v}", v)
    return {"ok": True, "candidate": dict(c)}


def approve_candidate(state: dict[str, Any], version: str, approver: str) -> dict:
    """Human approval on the stored candidate. Only regression-pass qualifies."""
    from shared.project_context import commit

    v = (version or "").strip()
    if not (approver or "").strip():
        return {"ok": False, "error": "approver is required"}
    store = _store(state)
    c = store.get(v)
    if not isinstance(c, dict):
        return {"ok": False, "error": f"unknown candidate {v!r}"}
    if c.get("status") != "regression-pass":
        return {"ok": False,
                "error": f"only regression-pass candidates can be approved (status={c.get('status')})"}
    c["status"] = "human-approved"
    c["approver"] = approver.strip()[:120]
    c["approved_at"] = _utcnow()
    commit(state)
    # Mark linked feedback addressed (approval still recorded per item).
    try:
        from .feedback_store import mark_addressed

        for fid in c.get("feedback_ids", []) or []:
            mark_addressed(state, fid, v)
    except Exception:
        pass
    _audit(state, c["approver"], f"approved BA candidate {v}", v)
    return {"ok": True, "candidate": dict(c)}
