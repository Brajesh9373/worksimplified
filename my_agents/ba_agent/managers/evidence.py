"""Evidence manager (§34) — where each requirement's information came from.

A link is `artifact_id -> source`. Validation is deterministic: the artifact id
must be a real requirement/objective/question id and, where the source type has
a registry (elicitation answers, decisions, uploaded documents), the reference
must exist. Coverage (§34 auditing) is computed per US/BR id; ids with no link
are reported, not silently accepted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

EVIDENCE_KEY = "evidence_links"
SOURCE_TYPES = ("elicitation", "upload", "document", "decision", "human", "system")
_ARTIFACT_RX = re.compile(r"^(?:G|BN|OPT|BR|US|FR|UC|T|TECH|DEC|OQ|SM)-\d+$|^ASSUMPTION:[^\s\]]+$")
_EL_RX = re.compile(r"\bEL-\d+\b")
_ASSUMPTION_RX = re.compile(r"\[ASSUMPTION:([^\]]+)\]", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import get_context

    return get_context(state)


def _log(state: dict[str, Any]) -> list[dict]:
    log = (_ctx(state).get("elicitation") or {}).get("log")
    return [e for e in log if isinstance(e, dict)] if isinstance(log, list) else []


def _known_artifacts(state: dict[str, Any], brd: str) -> set[str]:
    """Every citable artifact id known to the project."""
    known: set[str] = set()
    try:
        from shared.traceability import extract_ids

        for ids in extract_ids(brd or "").values():
            known.update(ids)
    except Exception:
        pass
    ctx = _ctx(state)
    for fam in ("requirements", "business_rules", "functional_requirements",
                "use_cases", "open_questions", "risks", "dependencies"):
        val = ctx.get(fam)
        if isinstance(val, dict):
            known.update(str(k) for k in val)
    for d in ctx.get("decision_log", []) or []:
        if isinstance(d, dict) and d.get("id"):
            known.add(str(d["id"]))
    for m in _ASSUMPTION_RX.findall(brd or ""):
        known.add(f"ASSUMPTION:{m.strip()}")
    for m in re.findall(r"\bOQ-\d+\b", brd or ""):
        known.add(m)
    return known


def _source_ok(state: dict[str, Any], source_type: str, source_ref: str) -> tuple[bool, str]:
    """Validate a source reference against its registry where one exists."""
    if source_type == "elicitation":
        ids = {e.get("id") for e in _log(state)}
        if source_ref and source_ref in ids:
            return True, ""
        return False, f"elicitation ref {source_ref!r} not in the log"
    if source_type == "decision":
        ids = {d.get("id") for d in (_ctx(state).get("decision_log") or [])
               if isinstance(d, dict)}
        if source_ref and source_ref in ids:
            return True, ""
        return False, f"decision ref {source_ref!r} is not recorded"
    if source_type == "upload":
        if not source_ref:
            return False, "upload ref is required (the uploaded_* artifact name)"
        try:
            from shared.workspace import bound_workspace_id, ProjectWorkspace

            pid = bound_workspace_id(_ctx(state))
            if pid:
                ws = ProjectWorkspace(pid, create=False)
                if ws.exists() and (ws.root / "artifacts" / source_ref).is_file():
                    return True, ""
        except Exception:
            pass
        if source_ref.startswith("uploaded_"):
            return True, ""
        return False, f"upload ref {source_ref!r} is not a project artifact"
    return bool(source_ref), ("" if source_ref else "source_ref is required")


def link(state: dict[str, Any], artifact_id: str, source_type: str,
         source_ref: str = "", note: str = "", brd: str = "") -> dict:
    """Record one evidence link. Returns {ok, link} or {ok: False, error}."""
    try:
        from shared.project_context import commit

        aid = (artifact_id or "").strip().upper().replace(" ", "")
        if not _ARTIFACT_RX.match(aid):
            return {"ok": False, "error": "artifact_id must look like US-001, BR-003, "
                                          "OQ-002, DEC-001 or ASSUMPTION:label"}
        stype = (source_type or "").strip().lower()
        if stype not in SOURCE_TYPES:
            return {"ok": False, "error": f"source_type must be one of {sorted(SOURCE_TYPES)}"}
        ok, err = _source_ok(state, stype, (source_ref or "").strip())
        if not ok:
            return {"ok": False, "error": err}
        if brd:
            known = _known_artifacts(state, brd)
            if known and aid not in known:
                return {"ok": False, "error": f"{aid} is not a known project artifact"}
        ctx = _ctx(state)
        links = ctx.get(EVIDENCE_KEY)
        if not isinstance(links, list):
            links = []
            ctx[EVIDENCE_KEY] = links
        key = (aid, stype, (source_ref or "").strip()[:200], (note or "").strip()[:300])
        for existing in links:
            if not isinstance(existing, dict):
                continue
            if (existing.get("artifact_id"), existing.get("source_type"),
                    existing.get("source_ref"), existing.get("note")) == key:
                return {"ok": True, "link": dict(existing), "duplicate": True}
        rec = {"artifact_id": aid, "source_type": stype,
               "source_ref": (source_ref or "").strip()[:200],
               "note": (note or "").strip()[:300], "at": _now()}
        links.append(rec)
        del links[:-500]
        commit(state)
        return {"ok": True, "link": rec}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def links(state: dict[str, Any]) -> list[dict]:
    """All evidence links (copies)."""
    try:
        return [dict(x) for x in (_ctx(state).get(EVIDENCE_KEY) or [])
                if isinstance(x, dict)]
    except Exception:
        return []


def report(state: dict[str, Any], brd: str = "") -> dict:
    """Coverage: which US/BR ids carry evidence, grouped by source type."""
    try:
        from shared.traceability import extract_ids

        ids = extract_ids(brd or "")
        target = sorted(set(ids.get("US", [])) | set(ids.get("BR", [])))
        have: dict[str, set[str]] = {}
        by_source: dict[str, int] = {}
        for l in links(state):
            have.setdefault(l["artifact_id"], set()).add(l.get("source_type", "?"))
            by_source[l.get("source_type", "?")] = by_source.get(l.get("source_type", "?"), 0) + 1
        uncovered: list[str] = []
        for rid in target:
            if rid in have:
                continue
            # an explicit assumption/OQ tag next to the id is acceptable evidence
            block = ""
            m = re.search(rf"\b{re.escape(rid)}\b", brd or "")
            if m:
                block = (brd or "")[m.start():m.start() + 400]
            if _ASSUMPTION_RX.search(block) or re.search(r"\bOQ-\d+\b", block):
                continue
            uncovered.append(rid)
        return {"ok": not uncovered, "total": len(target), "covered": len(target) - len(uncovered),
                "uncovered": uncovered, "by_source": by_source, "links": len(links(state))}
    except Exception as e:
        return {"ok": False, "total": 0, "covered": 0, "uncovered": [],
                "by_source": {}, "links": 0, "error": str(e)[:200]}


def uncovered(state: dict[str, Any], brd: str = "") -> list[str]:
    """Gate-facing list of requirement ids lacking evidence."""
    try:
        return list(report(state, brd).get("uncovered", []))
    except Exception:
        return []
