"""Version manager — BA agent version ledger (in-state, persisted to workspace).

Hard deploy gate (prod, by construction):
- deploy(state, version) takes a VERSION ID, never a candidate dict, and loads
  the stored candidate from project_context['ba_candidates'].
- Deploy succeeds only when stored status == 'human-approved' with a named
  approver. Forged copies, direct dict injection, or skipping regression/
  approval all fail because the stored copy is authoritative.
- Idempotent: redeploying the current/deployed version returns ok with
  already=True instead of duplicating the ledger.
- Every deploy appends a ba_eval history entry (audit trail).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

VERSION_KEY = "ba_versions"
CURRENT_KEY = "ba_current_version"
SNAPSHOTS_KEY = "ba_baseline_snapshots"
MEMORY_KEY = "ba_improvement_memory"
_SNAPSHOT_CAP = 20
_MEMORY_CAP = 100


def _ledger(state: dict[str, Any]) -> list:
    from shared.project_context import commit, get_context

    ctx = get_context(state)
    led = ctx.get(VERSION_KEY)
    if not isinstance(led, list):
        led = []
        ctx[VERSION_KEY] = led
        commit(state)
    return led


def current(state: dict[str, Any]) -> str:
    try:
        from shared.project_context import get_context

        return get_context(state).get(CURRENT_KEY, "v1.0") or "v1.0"
    except Exception:
        return "v1.0"


def history(state: dict[str, Any]) -> list[dict]:
    """Deployed version ledger (copies)."""
    return [dict(e) for e in _ledger(state) if isinstance(e, dict)]


def deploy(state: dict[str, Any], version: str) -> dict:
    """Deploy a stored human-approved candidate as the new current version.

    Args:
        state: session state (candidates + ledger live in project_context).
        version: candidate version id, e.g. 'v1.1-candidate'.

    Returns:
        {ok: True, version, already} on success;
        {ok: False, error} when unknown / not approved / tampered.
    """
    from shared.project_context import append_history, commit, get_context

    v = (version or "").strip()
    if not v:
        return {"ok": False, "error": "version is required"}
    ctx = get_context(state)
    store = ctx.get("ba_candidates")
    stored = store.get(v) if isinstance(store, dict) else None
    if not isinstance(stored, dict):
        return {"ok": False, "error": f"unknown candidate {v!r} (propose it first)"}
    # Idempotency first: already-deployed versions short-circuit (the stored
    # status is 'deployed' after the first deploy, which must not error).
    led = _ledger(state)
    if current(state) == v or any(e.get("version") == v for e in led if isinstance(e, dict)):
        return {"ok": True, "version": v, "already": True}
    # Hard gate: stored status is authoritative — caller-supplied dicts ignored.
    if stored.get("status") != "human-approved":
        return {"ok": False,
                "error": f"candidate {v!r} must be human-approved before deploy "
                         f"(status={stored.get('status')})"}
    if not str(stored.get("approver", "")).strip():
        return {"ok": False, "error": f"candidate {v!r} has no recorded approver"}
    entry = {
        "version": v,
        "target": stored.get("target"),
        "change": stored.get("change"),
        "feedback_ids": list(stored.get("feedback_ids", []) or [])[:50],
        "approver": stored.get("approver", ""),
        "deployed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    led.append(entry)
    get_context(state)[CURRENT_KEY] = v
    stored["status"] = "deployed"
    stored["deployed_at"] = entry["deployed_at"]
    _snapshot_baseline(state, v)
    _remember_patterns(state, stored, entry["deployed_at"])
    commit(state)
    try:
        append_history(state, "ba_eval", str(stored.get("approver", "lead")),
                       f"deployed BA version {v}", v)
    except Exception:
        pass
    return {"ok": True, "version": v, "already": False}


def _baseline_ids(state: dict[str, Any]) -> dict[str, list[str]]:
    """Current ID inventory across tracked docs (never raises)."""
    try:
        from shared.traceability import doc_texts, extract_ids

        texts = doc_texts(state)
        merged: dict[str, set[str]] = {}
        for text in texts.values():
            for fam, ids in extract_ids(text or "").items():
                merged.setdefault(fam, set()).update(ids)
        return {fam: sorted(ids) for fam, ids in merged.items() if ids}
    except Exception:
        return {}


def _snapshot_baseline(state: dict[str, Any], version: str) -> None:
    """Freeze this version's baseline for reuse tracking (S6 reuse)."""
    try:
        from shared.project_context import commit, get_context

        ctx = get_context(state)
        snaps = ctx.get(SNAPSHOTS_KEY)
        if not isinstance(snaps, list):
            snaps = []
            ctx[SNAPSHOTS_KEY] = snaps
        snaps[:] = [s for s in snaps
                    if isinstance(s, dict) and s.get("version") != version]
        snaps.append({"version": version, "ids": _baseline_ids(state),
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        del snaps[:-_SNAPSHOT_CAP]
        commit(state)
    except Exception:
        pass


def reuse_report(state: dict[str, Any]) -> dict:
    """Carried/added/dropped IDs between consecutive baselines (never raises).

    Reuse within the project boundary: stable IDs are reusable assets,
    churn is flagged. Returns {ok, pairs[], error?}.
    """
    try:
        from shared.project_context import get_context

        snaps = get_context(state).get(SNAPSHOTS_KEY, [])
        snaps = [s for s in snaps if isinstance(s, dict)]
        pairs = []
        for prev, cur in zip(snaps, snaps[1:]):
            pids = {i for ids in prev.get("ids", {}).values() for i in ids}
            cids = {i for ids in cur.get("ids", {}).values() for i in ids}
            carried, added, dropped = pids & cids, cids - pids, pids - cids
            denom = len(pids | cids)
            pairs.append({
                "from": prev.get("version"), "to": cur.get("version"),
                "carried": sorted(carried), "added": sorted(added),
                "dropped": sorted(dropped),
                "stability": round(len(carried) / denom, 3) if denom else 1.0,
            })
        return {"ok": True, "pairs": pairs, "snapshots": len(snaps)}
    except Exception as e:
        return {"ok": False, "pairs": [], "error": str(e)[:200]}


def _remember_patterns(state: dict[str, Any], stored: dict, deployed_at: str) -> None:
    """Promote a deployed candidate's validated lesson to improvement memory.

    Agent improvement memory (§18): general lessons (failure pattern +
    validated fix by target area), never client facts — only the change
    description, target, and feedback ids are kept. Org-wide promotion beyond
    the project boundary stays a manual curation step (see RUNBOOK).
    """
    try:
        from shared.project_context import commit, get_context

        ctx = get_context(state)
        mem = ctx.get(MEMORY_KEY)
        if not isinstance(mem, list):
            mem = []
            ctx[MEMORY_KEY] = mem
        mem[:] = [m for m in mem
                  if isinstance(m, dict) and m.get("version") != stored.get("version")]
        mem.append({"version": stored.get("version"), "target": stored.get("target"),
                    "lesson": (stored.get("change", "") or "")[:500],
                    "feedback_ids": list(stored.get("feedback_ids", []) or [])[:20],
                    "approver": stored.get("approver", ""),
                    "validated_at": deployed_at})
        del mem[:-_MEMORY_CAP]
        commit(state)
    except Exception:
        pass


def memory_report(state: dict[str, Any]) -> dict:
    """Validated improvement lessons, grouped by target (never raises)."""
    try:
        from shared.project_context import get_context

        mem = get_context(state).get(MEMORY_KEY, [])
        mem = [m for m in mem if isinstance(m, dict)]
        by_target: dict[str, int] = {}
        for m in mem:
            by_target[m.get("target", "?")] = by_target.get(m.get("target", "?"), 0) + 1
        return {"ok": True, "patterns": len(mem), "by_target": by_target,
                "lessons": mem[-10:]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
