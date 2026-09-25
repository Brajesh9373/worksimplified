"""Knowledge capture — turning ordinary conversation into project knowledge.

The owner talks; the agent listens and records what it hears. Every capture is a
ledger entry (`K-nnn`, with its source) and, where a project section exists, a
mirror into that section so the BA Package and the understanding digest are
populated before any document exists.

Rules
- Empty values are refused: nothing is invented on the owner's behalf.
- Re-capturing the same (kind, key) updates the value and keeps the previous one
  in `revisions` — truth changes are auditable, never silent.
- Sources are recorded (`EL-nn` conversation turn, an uploaded document, or a
  free note) so a later requirement can be traced back to what was said.
- A captured "assumption" goes through the assumption registry (status
  unvalidated); a captured "decision" goes through the decision log when a
  reason and a decider are available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LEDGER_KEY = "knowledge"
MAX_LEDGER = 500

# kind -> (project section, fixed key or None for key-as-name)
KINDS: dict[str, tuple[str, str | None]] = {
    "need": ("business", "need"),
    "problem": ("business", "problem"),
    "objective": ("business", "objectives"),
    "goal": ("business", "goals"),
    "metric": ("business", "success_criteria"),
    "driver": ("business", "drivers"),
    "stakeholder": ("stakeholders", None),
    "actor": ("actors", None),
    "process": ("processes", None),
    "rule": ("business_rules", None),
    "requirement": ("requirements", None),
    "use_case": ("use_cases", None),
    "risk": ("risks", None),
    "dependency": ("dependencies", None),
    "integration": ("integrations", None),
    "transition": ("delivery", None),
    "question": ("open_questions", None),
    "constraint": ("", None),
    "decision": ("", None),
    "assumption": ("", None),
}

# Plain-language gaps used by the readiness pre-check (never internal names).
GAP_PHRASES: dict[str, str] = {
    "business": "what the project is meant to achieve and how you will know it worked",
    "stakeholders": "who is involved and who decides",
    "actors": "who actually uses it day to day",
    "processes": "how the work happens today, step by step",
    "business_rules": "the rules that must always be respected",
    "requirements": "what the solution must do",
    "risks": "what could go wrong",
    "dependencies": "anything this depends on (systems, teams, approvals)",
    "constraint": "budget, timing or compliance limits",
}

_GAP_ORDER = ("business", "stakeholders", "processes", "business_rules", "requirements",
              "constraint", "risks", "dependencies")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ctx(state: Any) -> dict[str, Any]:
    from shared.project_context import get_context

    return get_context(state)


def _commit(state: Any) -> None:
    try:
        from shared.project_context import commit

        commit(state)
    except Exception:
        pass


def ledger(state: Any) -> list[dict]:
    """All captured knowledge entries (copies)."""
    try:
        items = _ctx(state).get(LEDGER_KEY)
        return [dict(e) for e in items if isinstance(e, dict)] if isinstance(items, list) else []
    except Exception:
        return []


def count(state: Any) -> int:
    """How many entries have been captured (used by the capture audit)."""
    try:
        return len(ledger(state))
    except Exception:
        return 0


def capture(state: Any, kind: str, key: str, value: str, source_ref: str = "",
            note: str = "") -> dict:
    """Record one piece of knowledge the owner shared."""
    try:
        k = (kind or "").strip().lower().replace(" ", "_")
        if k not in KINDS:
            return {"ok": False, "error": f"kind must be one of {sorted(KINDS)}"}
        val = (value or "").strip()
        if not val:
            return {"ok": False, "error": "value is required (nothing is captured from nothing)"}
        name = (key or "").strip() or k
        ctx = _ctx(state)
        entries = ctx.get(LEDGER_KEY)
        if not isinstance(entries, list):
            entries = []
            ctx[LEDGER_KEY] = entries

        prior = next((e for e in entries if isinstance(e, dict)
                      and e.get("kind") == k and e.get("key") == name), None)
        if prior is not None:
            if (prior.get("value") or "").strip() == val:
                return {"ok": True, "entry": dict(prior), "duplicate": True}
            prior.setdefault("revisions", [])
            prior["revisions"].append({"value": prior.get("value", ""), "at": prior.get("at")})
            del prior["revisions"][:-20]
            prior["value"] = val
            prior["at"] = _now()
            if source_ref:
                prior["source_ref"] = source_ref.strip()[:200]
            _mirror(ctx, k, name, val)
            _commit(state)
            return {"ok": True, "entry": dict(prior), "updated": True}

        entry = {"id": f"K-{len(entries) + 1:03d}", "kind": k, "key": name,
                 "value": val, "source_ref": (source_ref or "").strip()[:200],
                 "note": (note or "").strip()[:300], "at": _now(), "revisions": []}
        entries.append(entry)
        del entries[:-MAX_LEDGER]
        _mirror(ctx, k, name, val)
        if k == "assumption":
            try:
                from ba_agent.managers import assumptions as ASM

                ASM.record(state, name, val)
            except Exception:
                pass
        if k == "decision":
            try:
                from ba_agent.managers import decision_manager as DM

                DM.record_decision(state, decision=val, reason=(note or source_ref or
                                                               "Agreed in conversation"),
                                   decision_maker=(key or "owner"), status="approved")
            except Exception:
                pass
        _commit(state)
        return {"ok": True, "entry": dict(entry)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _mirror(ctx: dict[str, Any], kind: str, name: str, value: str) -> None:
    """Mirror captured knowledge into the project section the package reads."""
    section, fixed_key = KINDS.get(kind, ("", None))
    if not section:
        return
    bucket = ctx.get(section)
    if not isinstance(bucket, dict):
        bucket = {}
        ctx[section] = bucket
    bucket[fixed_key or name] = value[:1000]


def digest(state: Any) -> dict:
    """Structured understanding: what is known per area and what is thin."""
    ctx = _ctx(state)
    known: dict[str, list[str]] = {}
    for kind, (section, fixed_key) in KINDS.items():
        if not section:
            continue
        bucket = ctx.get(section)
        if isinstance(bucket, dict) and bucket:
            known.setdefault(section, [])
            for k2 in bucket:
                if k2 not in known[section]:
                    known[section].append(str(k2))
    for kind in ("constraint", "decision", "assumption"):
        items = [e for e in ledger(state) if e.get("kind") == kind]
        if items:
            known[kind] = [e.get("key", "?") for e in items[:20]]
    open_qs = list((ctx.get("open_questions") or {}).keys()) if isinstance(
        ctx.get("open_questions"), dict) else []
    try:
        from ba_agent.managers import assumptions as ASM

        unvalidated = [a.get("label") for a in ASM.assumptions(state)
                       if a.get("status") == "unvalidated"]
    except Exception:
        unvalidated = []
    return {"known": known, "open_questions": open_qs, "unvalidated_assumptions": unvalidated,
            "entries": count(state)}


def thin(state: Any) -> list[str]:
    """Plain-language list of what is missing before a document can be written."""
    ctx = _ctx(state)
    out: list[str] = []
    for area in _GAP_ORDER:
        if area == "constraint":
            have = any(e.get("kind") == "constraint" for e in ledger(state))
        else:
            bucket = ctx.get(area)
            have = bool(bucket) if isinstance(bucket, dict) else False
        if not have and area in GAP_PHRASES:
            out.append(GAP_PHRASES[area])
    return out


def digest_text(state: Any) -> str:
    """Prompt-friendly rendering of what has been captured."""
    d = digest(state)
    lines: list[str] = []
    for area, keys in d["known"].items():
        lines.append(f"- {area}: " + ", ".join(keys[:8]))
    if d["open_questions"]:
        lines.append("- open questions: " + ", ".join(str(q) for q in d["open_questions"][:8]))
    if d["unvalidated_assumptions"]:
        lines.append("- assumptions to confirm: "
                     + ", ".join(str(a) for a in d["unvalidated_assumptions"][:8]))
    if not lines:
        return ""
    return "\n".join(lines) + f"\n({d['entries']} things captured so far)"


def status(state: Any) -> dict:
    """Plain-language 'what I have / what's missing' (for the tool + playback)."""
    d = digest(state)
    return {"ok": True, "captured": d["entries"], "areas": sorted(d["known"]),
            "open_questions": d["open_questions"],
            "assumptions_to_confirm": d["unvalidated_assumptions"],
            "still_missing": thin(state)}
