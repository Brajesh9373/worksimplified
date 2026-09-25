"""BA Package assembler — full 25+1-item package from accumulated state (prod).

The package is an INDEX over knowledge the BA already recorded (project
context, BRD text, diagrams, traceability, decisions, history, CRs) — never
invented prose. Items without source data render an explicit placeholder and
are reported in `missing[]` instead of hallucinated.

Sources per item:
- Business Need/Context/Objectives .... ctx business/project
- Stakeholder Analysis ................ ctx stakeholders/actors
- Current/Future State ................ ctx processes + BRD AS-IS/TO-BE cue
- Solution Scope ...................... BRD Scope IN/OUT cue
- Business/Stakeholder/Functional reqs . ctx requirements/functional_requirements
- NFRs ................................ BRD NFR cue
- Business Rules / User Stories ....... BRD ID inventories (BR-xxx/US-xxx)
- Use Cases ........................... ctx use_cases
- Models .............................. state *_diagram entries + artifacts
- Acceptance Criteria ................. requirement_engine per-story counts
- Assumptions ......................... [ASSUMPTION:xxx] tags in BRD
- Constraints ......................... BRD constraint cue
- Risks / Dependencies ................ ctx risks/dependencies
- Transition Requirements ............. ctx delivery
- Open Questions ...................... ctx open_questions + OQ-xxx in BRD
- Decision Log ........................ ctx decisions
- Requirement Baseline ................ ID inventories + ba_current_version
- Traceability Matrix ................. traceability.coverage_report
- Change Log .......................... ctx history + change_requests

Pure functions (no LLM, no I/O). Deterministic: no timestamps in the body.
"""

from __future__ import annotations

import re
from typing import Any

# (item_id, title)
PACKAGE_ITEMS: tuple[tuple[str, str], ...] = (
    ("business_need", "Business Need"),
    ("business_context", "Business Context"),
    ("business_objectives", "Business Objectives"),
    ("stakeholders", "Stakeholder Analysis"),
    ("current_state", "Current State (AS-IS)"),
    ("future_state", "Future State (TO-BE)"),
    ("business_case", "Business Case and Solution Options"),
    ("solution_scope", "Solution Scope"),
    ("business_requirements", "Business Requirements"),
    ("stakeholder_requirements", "Stakeholder Requirements"),
    ("functional_requirements", "Functional Requirements"),
    ("nfrs", "Non-Functional Requirements"),
    ("business_rules", "Business Rules"),
    ("user_stories", "User Stories"),
    ("use_cases", "Use Cases"),
    ("models", "Models & Diagrams"),
    ("acceptance_criteria", "Acceptance Criteria"),
    ("priorities", "Priorities"),
    ("assumptions", "Assumptions"),
    ("constraints", "Constraints"),
    ("risks", "Risks"),
    ("dependencies", "Dependencies"),
    ("transition", "Transition Requirements"),
    ("open_questions", "Open Questions"),
    ("decisions", "Decision Log"),
    ("baseline", "Requirement Baseline"),
    ("traceability", "Traceability Matrix"),
    ("evidence_sources", "Evidence Sources"),
    ("solution_assessment", "Solution Assessment and Gaps"),
    ("readiness", "Organisational Readiness"),
    ("outcome_metrics", "Business Outcome Metrics"),
    ("ba_plan", "BA Plan and Monitoring"),
    ("changelog", "Change Log"),
    ("ba_summary", "BA Summary"),
)

_NO_DATA = "_No data recorded — see Open Questions._"

_CUE_SCOPE_IN = re.compile(r"scope\s*in|in[-\s]scope", re.IGNORECASE)
_CUE_SCOPE_OUT = re.compile(r"scope\s*out|out[-\s]of\s*scope", re.IGNORECASE)
_CUE_ASIS = re.compile(r"as-is|asis|current process", re.IGNORECASE)
_CUE_TOBE = re.compile(r"to-be|tobe|proposed|future state", re.IGNORECASE)
_CUE_NFR = re.compile(r"\bnfr\b|non-functional|performance|secur|availab", re.IGNORECASE)
_CUE_CONSTRAINT = re.compile(r"constraint|compliance|deadline|budget", re.IGNORECASE)
_ASSUMPTION_RX = re.compile(r"\[ASSUMPTION:[^\]]+\]", re.IGNORECASE)
_OQ_RX = re.compile(r"\bOQ-\d+\b")


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import get_context

    return get_context(state)


def _brd(state: dict[str, Any]) -> str:
    try:
        from shared.traceability import doc_texts

        return doc_texts(state).get("brd", "") or ""
    except Exception:
        return state.get("brd", "") if isinstance(state.get("brd"), str) else ""


def _has_cue(brd: str, rx: re.Pattern) -> bool:
    return bool(rx.search(brd or ""))


def _section_body(state: dict[str, Any], section_id: str, brd: str = "") -> tuple[str, bool]:
    """Real BRD section text for a package item (never a keyword cue).

    New artefacts (business case, solution assessment) are documents, so the
    package carries the accepted content itself and reports a gap when it is
    absent instead of inventing a summary.
    """
    try:
        from shared.harness import extract_section_text
        from shared.sections import sections_for

        title = next((s["title"] for s in sections_for("BA") if s["id"] == section_id), "")
        body = _brd(state) if not brd else brd
        text = extract_section_text(body, title) if title else ""
        text = (text or "").strip()
        if len(text) < 80:
            return _NO_DATA, False
        return text[:6000], True
    except Exception:
        return _NO_DATA, False


def _id_lines(text: str, rx: str, limit: int = 12) -> list[str]:
    """Lines of an accepted section that define the given ids."""
    import re as _re

    out: list[str] = []
    for line in (text or "").splitlines():
        if _re.search(rx, line) and line.strip():
            out.append(f"- {line.strip()[:220]}")
        if len(out) >= limit:
            break
    return out


# Items whose authoritative source may be the accepted BRD text rather than the
# state channel: an empty state section must not hide requirements the BA did
# write (the package is an index over recorded knowledge, and the BRD is
# recorded knowledge).
_BRD_FALLBACK = {
    "business_need", "business_context", "business_objectives", "stakeholders",
    "business_requirements", "stakeholder_requirements", "functional_requirements",
    "use_cases", "risks", "dependencies", "transition", "decisions", "changelog",
}

_FALLBACK_SECTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "business_need": (("objectives",), "From the accepted objectives section."),
    "business_context": (("objectives",), "From the accepted objectives section."),
    "business_objectives": (("objectives",), "From the accepted objectives section."),
    "stakeholders": (("stakeholders",), "From the accepted stakeholders section."),
    "risks": (("datarisks",), "From the accepted data/risks section."),
    "dependencies": (("datarisks",), "From the accepted data/risks section."),
}


def _brd_fallback(item_id: str, state: dict[str, Any], brd: str) -> tuple[str, bool]:
    """Derive one package item from accepted BRD content (never invents)."""
    if item_id in _FALLBACK_SECTIONS:
        sections, label = _FALLBACK_SECTIONS[item_id]
        bodies = []
        for sid in sections:
            body, ok = _section_body(state, sid, brd)
            if ok:
                bodies.append(body)
        if bodies:
            return f"_{label}_\n\n" + "\n\n".join(bodies), True
        return _NO_DATA, False

    if item_id == "business_requirements":
        body, ok = _section_body(state, "rules", brd)
        if ok:
            lines = _id_lines(body, r"\bBR-\d+\b")
            if lines:
                return ("_Business requirements, from the accepted rules section._\n\n"
                        + "\n".join(lines)), True
        return _NO_DATA, False

    if item_id == "stakeholder_requirements":
        body, ok = _section_body(state, "stories", brd)
        if ok:
            lines = _id_lines(body, r"\bUS-\d+\b")
            if lines:
                return ("_Stakeholder requirements, from the accepted user stories._\n\n"
                        + "\n".join(lines)), True
        return _NO_DATA, False

    if item_id == "functional_requirements":
        body, ok = _section_body(state, "stories", brd)
        if ok:
            lines = _id_lines(body, r"\bUS-\d+\b")
            if lines:
                return ("_Capabilities the solution must provide, derived from the accepted "
                        "user stories. Detailed FR-xxx catalogues are owned by the Functional "
                        "stage._\n\n" + "\n".join(lines)), True
        return _NO_DATA, False

    if item_id == "use_cases":
        body, ok = _section_body(state, "stories", brd)
        if ok:
            lines = _id_lines(body, r"\bUS-\d+\b", limit=8)
            if lines:
                return ("_Use cases implied by the accepted user stories and the process "
                        "flow._\n\n" + "\n".join(lines)), True
        return _NO_DATA, False

    if item_id == "transition":
        body, ok = _section_body(state, "solution_assessment", brd)
        text = body if ok else ""
        if not text:
            body2, ok2 = _section_body(state, "datarisks", brd)
            text = body2 if ok2 else ""
        if text:
            lines = [f"- {ln.strip()[:220]}" for ln in text.splitlines()
                     if re.search(r"transition|migrat|training|readiness|pilot|go-live",
                                  ln, re.IGNORECASE)]
            if lines:
                return ("_Transition, migration and training needs from the accepted "
                        "assessment._\n\n" + "\n".join(lines[:10])), True
        return _NO_DATA, False

    if item_id == "decisions":
        body, ok = _section_body(state, "business_case", brd)
        if ok:
            lines = [f"- {ln.strip()[:220]}" for ln in body.splitlines()
                     if re.search(r"decision factor|decid", ln, re.IGNORECASE)]
            if lines:
                return ("_Decision factors recorded; formal DEC-n decisions are logged "
                        "separately as they are taken._\n\n" + "\n".join(lines[:8])), True
        return _NO_DATA, False

    if item_id == "changelog":
        try:
            from ba_agent.eval.version_manager import history as _vhist

            ledger = _vhist(state)
        except Exception:
            ledger = []
        try:
            from ba_agent.managers.impact import all_reports

            impacts = all_reports(state)
        except Exception:
            impacts = []
        lines = [f"- BA version deploy {e.get('version', '?')} "
                 f"({e.get('deployed_at', '?')})" for e in ledger if isinstance(e, dict)]
        lines += [f"- impact analysis {r.get('cr_id')}: "
                  f"{', '.join(r.get('changed_ids', []))}" for r in impacts]
        return ("\n".join(lines), True) if lines else (_NO_DATA, False)

    return _NO_DATA, False


def _bullets(mapping: Any, max_items: int = 20) -> tuple[str, bool]:
    """Render a ctx section (dict or list) as bullets. Returns (md, has_data)."""
    if isinstance(mapping, dict):
        items = [(k, v) for k, v in mapping.items() if v not in (None, "", {}, [])]
        if not items:
            return _NO_DATA, False
        lines = [f"- **{k}**: {v if not isinstance(v, (dict, list)) else str(v)[:200]}"
                 for k, v in items[:max_items]]
        if len(items) > max_items:
            lines.append(f"- _…and {len(items) - max_items} more_")
        return "\n".join(lines), True
    if isinstance(mapping, list):
        items = [x for x in mapping if x not in (None, "", {}, [])]
        if not items:
            return _NO_DATA, False
        lines = [f"- {x if not isinstance(x, (dict, list)) else str(x)[:200]}"
                 for x in items[:max_items]]
        if len(items) > max_items:
            lines.append(f"- _…and {len(items) - max_items} more_")
        return "\n".join(lines), True
    if isinstance(mapping, str) and mapping.strip():
        return mapping.strip()[:2000], True
    return _NO_DATA, False


def _render_item(item_id: str, state: dict[str, Any], ctx: dict, brd: str) -> tuple[str, bool]:
    """Render one item: recorded state first, accepted BRD content second."""
    body, ok = _render_item_state(item_id, state, ctx, brd)
    if ok or item_id not in _BRD_FALLBACK:
        return body, ok
    return _brd_fallback(item_id, state, brd)


def _render_item_state(item_id: str, state: dict[str, Any], ctx: dict, brd: str) -> tuple[str, bool]:
    """Render one item body. Returns (markdown, has_data)."""
    if item_id == "business_need":
        return _bullets({k: ctx.get("business", {}).get(k) for k in ("need", "problem", "drivers")
                         if isinstance(ctx.get("business"), dict)} or ctx.get("business", {}))
    if item_id == "business_context":
        return _bullets(ctx.get("business", {}))
    if item_id == "business_objectives":
        b = ctx.get("business", {}) if isinstance(ctx.get("business"), dict) else {}
        return _bullets({k: b.get(k) for k in ("objectives", "goals", "success_criteria") if b.get(k)}
                        or ({"objectives": b} if b else {}))
    if item_id == "stakeholders":
        s, ok1 = _bullets(ctx.get("stakeholders", {}))
        a, ok2 = _bullets(ctx.get("actors", {}))
        return f"**Stakeholders**\n{s}\n\n**Actors**\n{a}", ok1 or ok2
    if item_id == "current_state":
        body, ok = _section_body(state, "asis_tobe", brd)
        if ok:
            part = re.split(r"\bTO-?BE\b", body, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if len(part) >= 40:
                return part[:3000], True
        proc, ok2 = _bullets(ctx.get("processes", {}))
        return (proc, True) if ok2 else (_NO_DATA, False)
    if item_id == "future_state":
        body, ok = _section_body(state, "asis_tobe", brd)
        if ok:
            parts = re.split(r"\bTO-?BE\b", body, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) > 1 and len(parts[1].strip()) >= 40:
                return parts[1].strip()[:3000], True
        return _NO_DATA, False
    if item_id == "solution_scope":
        ok = _has_cue(brd, _CUE_SCOPE_IN) and _has_cue(brd, _CUE_SCOPE_OUT)
        return ("Scope IN documented." if _has_cue(brd, _CUE_SCOPE_IN) else "Scope IN missing."
                + "\n\n" +
                ("Scope OUT documented." if _has_cue(brd, _CUE_SCOPE_OUT) else "Scope OUT missing."), ok)
    if item_id == "business_requirements":
        return _bullets(ctx.get("requirements", {}))
    if item_id == "stakeholder_requirements":
        req = ctx.get("requirements", {})
        return _bullets(req if req else {})
    if item_id == "functional_requirements":
        return _bullets(ctx.get("functional_requirements", {}))
    if item_id == "nfrs":
        # Paragraph-based: wrapped requirement text is kept whole.
        paras = [p.strip() for p in re.split(r"\n\s*\n", brd or "") if p.strip()]
        lines = [f"- {re.sub(r'\s+', ' ', p)[:400]}"
                 for p in paras if _CUE_NFR.search(p)]
        if lines:
            return ("_Non-functional requirements and constraints recorded in the BRD._\n\n"
                    + "\n".join(lines[:8])), True
        return _NO_DATA, False
    if item_id == "business_rules":
        from shared.traceability import extract_ids

        body, sec_ok = _section_body(state, "rules", brd)
        lines = _id_lines(body, r"\bBR-\d+\b") if sec_ok else []
        brs = extract_ids(brd).get("BR", [])
        if lines:
            return ("_Business rules from the accepted rules section._\n\n"
                    + "\n".join(lines)), True
        ctx_body, ok_ctx = _bullets(ctx.get("business_rules", {}))
        if ok_ctx:
            return ctx_body, True
        ids = ", ".join(brs)
        return (f"**BR IDs in BRD**: {ids}" if brs else _NO_DATA), bool(brs)
    if item_id == "user_stories":
        from shared.traceability import extract_ids

        uss = extract_ids(brd).get("US", [])
        ok = len(uss) >= 3
        return f"**US IDs in BRD ({len(uss)})**: {', '.join(uss) or 'none'}", ok
    if item_id == "use_cases":
        return _bullets(ctx.get("use_cases", {}))
    if item_id == "models":
        from shared.project_context import state_dict

        diags = {k: v for k, v in state_dict(state).items()
                 if isinstance(k, str) and k.endswith("_diagram") and isinstance(v, dict)}
        if not diags:
            return _NO_DATA, False
        lines = [f"- **{k}** ({v.get('kind', '?')}): {v.get('nodes', '?')} nodes — `{v.get('mmd', '')}`"
                 for k, v in sorted(diags.items())]
        return "\n".join(lines), True
    if item_id == "acceptance_criteria":
        from ba_agent.managers.requirement_engine import validate_requirements

        r = validate_requirements(brd)
        if not r["us"]:
            return _NO_DATA, False
        lines = [f"- **{u}**: {c} criteria" for u, c in zip(r["us"], r["per_story_counts"])]
        return "\n".join(lines), r["ok"]
    if item_id == "assumptions":
        # v2: the registry carries each assumption's lifecycle status; BRD tags
        # are also surfaced so an unregistered tag is visible, never hidden.
        try:
            from ba_agent.managers import assumptions as ASM

            body = ASM.render(state, brd)
            if body:
                return body, True
        except Exception:
            pass
        tags = sorted(set(_ASSUMPTION_RX.findall(brd)))
        return ("Assumption tags: " + ", ".join(tags) if tags else _NO_DATA), bool(tags)
    if item_id == "constraints":
        ok = _has_cue(brd, _CUE_CONSTRAINT)
        return ("Constraints documented in BRD." if ok else _NO_DATA), ok
    if item_id == "risks":
        return _bullets(ctx.get("risks", {}))
    if item_id == "dependencies":
        return _bullets(ctx.get("dependencies", {}))
    if item_id == "transition":
        return _bullets(ctx.get("delivery", {}))
    if item_id == "open_questions":
        oqs = sorted(set(_OQ_RX.findall(brd)))
        body, ok_ctx = _bullets(ctx.get("open_questions", {}))
        ok = ok_ctx or bool(oqs) or "none" in brd.lower()
        extra = f"\n\nOQ IDs in BRD: {', '.join(oqs)}" if oqs else ""
        return body + extra, ok
    if item_id == "decisions":
        return _bullets(ctx.get("decisions", {}))
    if item_id == "baseline":
        from shared.project_context import get_context as _gc
        from shared.traceability import extract_ids

        ids = extract_ids(brd)
        n = sum(len(v) for v in ids.values())
        try:
            ver = _gc(state).get("ba_current_version", "v1.0")
        except Exception:
            ver = "v1.0"
        ok = n > 0
        inv = ", ".join(f"{fam}-{len(v)}" for fam, v in sorted(ids.items()) if v) or "none"
        return f"BA version: **{ver}**\n\nID inventory in BRD: {inv}", ok
    if item_id == "traceability":
        from shared.traceability import coverage_report

        cov = coverage_report(state)
        counts = ", ".join(f"{k}={v}" for k, v in sorted(cov.get("counts", {}).items()))
        unc = [f"{k}: {', '.join(v) if isinstance(v, list) else v}"
               for k, v in cov.items() if k.startswith("uncovered") and v]
        return f"Counts: {counts}\n\n" + ("\n".join(f"- {u}" for u in unc) if unc else "- fully covered"), True
    if item_id == "changelog":
        hist = ctx.get("history", []) if isinstance(ctx.get("history"), dict) is False else []
        crs = ctx.get("change_requests", [])
        lines = [f"- [{h.get('ts', '?')}] {h.get('type', '?')} ({h.get('actor', '?')}): "
                 f"{h.get('summary', '')}" for h in hist[-20:] if isinstance(h, dict)]
        lines += [f"- CR {c.get('id', '?')} [{c.get('status', '?')}]: {c.get('reason', '')[:120]}"
                  for c in crs if isinstance(c, dict)]
        try:
            from ba_agent.managers.impact import render as _impact_render

            imp = _impact_render(state)
            if imp:
                lines.append("Impact analyses (§20):")
                lines.append(imp)
        except Exception:
            pass
        return ("\n".join(lines) if lines else _NO_DATA), bool(lines)

    # ---- capabilities added with BA v2 (each backed by real artefact content)

    if item_id == "business_case":
        body, ok = _section_body(state, "business_case", brd)
        mirror = ctx.get("business_case") if isinstance(ctx.get("business_case"), dict) else {}
        if ok and mirror.get("chosen_option"):
            body = f"**Chosen option**: {mirror['chosen_option']}\n\n{body}"
        return body, ok

    if item_id == "solution_assessment":
        return _section_body(state, "solution_assessment", brd)

    if item_id == "readiness":
        body, ok = _section_body(state, "solution_assessment", brd)
        if not ok:
            return _NO_DATA, False
        dims = ("people", "process", "technology", "training")
        lines = [ln.strip() for ln in body.splitlines()
                 if any(d in ln.lower() for d in dims) and ln.strip()]
        if not lines:
            return _NO_DATA, False
        return "\n".join(lines[:12]), True

    if item_id == "priorities":
        from shared.traceability import extract_ids

        ids = extract_ids(brd)
        try:
            from ba_agent.managers.quality_engine import priorities_untagged

            untagged = priorities_untagged(brd)
        except Exception:
            untagged = []
        tags = re.findall(r"\[(?:moscow\s*[:=]\s*)?(must|should|could|won'?t|p[0-3])\b",
                          brd, re.IGNORECASE)
        dist: dict[str, int] = {}
        for t in tags:
            dist[t.lower()] = dist.get(t.lower(), 0) + 1
        lines = [f"- Requirements: {len(ids.get('US', []))} US, {len(ids.get('BR', []))} BR",
                 "- Priority distribution: "
                 + (", ".join(f"{k}={v}" for k, v in sorted(dist.items())) or "none tagged")]
        if untagged:
            lines.append(f"- **Untagged (gate blocks)**: {', '.join(untagged[:10])}")
        try:
            from ba_agent.managers import ba_plan as BP

            appr = [i for i in BP.items(state) if i.get("title", "").lower().startswith(
                "prioritisation approach")]
            if appr:
                lines.append(f"- Approach: {appr[0].get('title')} ({appr[0].get('status')})")
        except Exception:
            pass
        ok = bool(ids.get("US") or ids.get("BR")) and not untagged
        return "\n".join(lines), ok

    if item_id == "evidence_sources":
        try:
            from ba_agent.managers import evidence as EV

            rep = EV.report(state, brd)
            lines = [f"- Covered requirements: {rep['covered']}/{rep['total']}",
                     "- Sources: " + (", ".join(f"{k}={v}" for k, v in sorted(
                         (rep.get('by_source') or {}).items())) or "none")]
            if rep["uncovered"]:
                lines.append(f"- **No evidence (gate blocks)**: "
                             f"{', '.join(rep['uncovered'][:10])}")
            for l in EV.links(state)[:12]:
                lines.append(f"- {l['artifact_id']} <- {l['source_type']}:"
                             f"{l['source_ref'] or '-'}{(' (' + l['note'] + ')') if l.get('note') else ''}")
            return "\n".join(lines), bool(rep["links"]) and not rep["uncovered"]
        except Exception:
            return _NO_DATA, False

    if item_id == "outcome_metrics":
        try:
            from ba_agent.managers import outcome as OC

            body = OC.render(state)
            return (body, True) if body else (_NO_DATA, False)
        except Exception:
            return _NO_DATA, False

    if item_id == "ba_plan":
        try:
            from ba_agent.managers import ba_plan as BP

            body = BP.render(state)
            return (body, True) if body else (_NO_DATA, False)
        except Exception:
            return _NO_DATA, False

    if item_id == "ba_summary":
        return _ba_summary(state, ctx, brd)

    return _NO_DATA, False


def _ba_summary(state: dict[str, Any], ctx: dict, brd: str) -> tuple[str, bool]:
    """§37 item 27 — the one-screen state of the BA engagement."""
    lines: list[str] = []
    try:
        from shared.traceability import coverage_report, extract_ids

        ids = extract_ids(brd)
        counts = ", ".join(f"{k}={len(v)}" for k, v in sorted(ids.items()) if v) or "none"
        lines.append(f"- Requirements in BRD: {counts}")
    except Exception:
        pass
    try:
        from ba_agent import stage_engine as SE

        ev = SE.evidence(state)
        marks = " ".join(f"{s}{'✓' if ev.get(s) else '○'}" for s in SE.STAGES)
        lines.append(f"- Stage progress: {marks}; current focus {SE.current(state)}")
    except Exception:
        pass
    try:
        from ba_agent.eval.version_manager import current as _ver

        lines.append(f"- BA version: {_ver(state)}")
    except Exception:
        pass
    try:
        from ba_agent.managers.quality_engine import open_contradictions, report as _qr

        rep = _qr(brd)
        lines.append("- Quality: " + (", ".join(f"{k}={v}" for k, v in sorted(
            rep["by_attribute"].items())) or "no findings")
            + f"; blocking={len(rep['blocking'])}")
        open_c = open_contradictions(state)
        if open_c:
            lines.append(f"- Open contradictions: {', '.join(c['id'] for c in open_c)}")
    except Exception:
        pass
    try:
        from ba_agent.managers import assumptions as ASM

        items = ASM.assumptions(state)
        if items:
            by: dict[str, int] = {}
            for a in items:
                by[a.get("status", "?")] = by.get(a.get("status", "?"), 0) + 1
            lines.append("- Assumptions: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    except Exception:
        pass
    try:
        from ba_agent.managers import ba_plan as BP

        rep = BP.report(state)
        lines.append(f"- Plan: {rep['items']} items, variance {len(rep['variance'])}")
    except Exception:
        pass
    try:
        from ba_agent.managers import outcome as OC

        rep = OC.report(state)
        lines.append(f"- Outcome metrics: {len(rep['metrics'])} "
                     f"(measured {rep['measured']}, gaps {len(rep['gaps'])})")
    except Exception:
        pass
    try:
        from shared.gates import ba_gate

        g = ba_gate(state)
        lines.append(f"- Formal gate: {'PASSED' if g['passed'] else 'FAILED'} "
                     f"({g['checked']} checks, {len(g['missing'])} missing)")
    except Exception:
        pass
    # A summary needs substance: ids, decisions or metrics — a stub draft does
    # not produce a "BA summary", it produces a gap.
    substantial = bool(ctx.get("decisions")) or bool(ctx.get("success_metrics"))
    try:
        from shared.traceability import extract_ids

        substantial = substantial or any(extract_ids(brd).values())
    except Exception:
        pass
    if not substantial:
        return _NO_DATA, False
    return "\n".join(lines), True


def assemble(state: dict[str, Any], project: str = "project") -> dict:
    """Assemble the BA Package markdown from state. Deterministic, pure read.

    Returns {ok, markdown, items, missing[], package_version}.
    `ok` is True only when nothing is missing.
    """
    ctx = _ctx(state)
    brd = _brd(state)
    try:
        from shared.project_context import get_context as _gc

        ver = _gc(state).get("ba_current_version", "v1.0") or "v1.0"
    except Exception:
        ver = "v1.0"
    slug = "".join(c if c.isalnum() else "_" for c in (project or "project")).strip("_") or "project"
    parts = [f"# BA Package — {slug}", "", f"BA version: **{ver}**", ""]
    missing: list[str] = []
    for item_id, title in PACKAGE_ITEMS:
        body, ok = _render_item(item_id, state, ctx, brd)
        parts.append(f"## {title}")
        parts.append("")
        parts.append(body)
        parts.append("")
        if not ok:
            missing.append(f"{item_id}: no source data")
    return {
        "ok": not missing,
        "markdown": "\n".join(parts).rstrip() + "\n",
        "items": len(PACKAGE_ITEMS),
        "missing": missing,
        "package_version": ver,
    }


HANDOFF_POINTS = (
    "what the business wants",
    "why it wants it",
    "who is involved",
    "what is in scope",
    "what is out of scope",
    "how the business process works",
    "what rules must be respected",
    "how success will be measured",
    "what remains unresolved",
)


def handoff_brief(state: dict[str, Any], project: str = "project") -> dict:
    """9-point handoff brief for the Project Agent (pure read).

    Each point is answered from state where possible, else marked unresolved —
    never invented. Returns {markdown, unresolved[]}.
    """
    ctx = _ctx(state)
    brd = _brd(state)

    def _get(item_id: str) -> str:
        body, _ = _render_item(item_id, state, ctx, brd)
        return body.strip()

    answers = {
        "what the business wants": _get("business_need"),
        "why it wants it": _get("business_objectives") + "\n\n" + _get("business_case"),
        "who is involved": _get("stakeholders"),
        "what is in scope": _get("solution_scope"),
        "what is out of scope": _get("solution_scope"),
        "how the business process works": _get("current_state") + "\n\n" + _get("future_state"),
        "what rules must be respected": _get("business_rules"),
        "how success will be measured": _get("outcome_metrics") + "\n\n"
                                      + _get("business_objectives"),
        "what remains unresolved": _get("open_questions"),
    }
    unresolved = [k for k, v in answers.items() if _NO_DATA in v]
    lines = [f"# BA → Project Handoff — {project}", ""]
    for point in HANDOFF_POINTS:
        lines += [f"## {point.capitalize()}", "", answers[point], ""]
    return {"markdown": "\n".join(lines).rstrip() + "\n", "unresolved": unresolved}


def project_intake(state: dict[str, Any]) -> dict:
    """Downstream intake for the Project Agent (pure read, never raises).

    Returns {has_content, package_ok, missing[], unresolved[],
    pending_approvals[], package_path, markdown}. `has_content` is False when
    no BA evidence exists at all (legacy/fresh states) — callers then omit
    the intake block so brief shape stays backward compatible.
    """
    try:
        ctx = _ctx(state)
        brd = _brd(state)
        pkg = assemble(state)
        hb = handoff_brief(state)
        try:
            from ba_agent.managers.decision_manager import pending_approvals

            pend_ap = [a["id"] for a in pending_approvals(state)]
        except Exception:
            pend_ap = []
        art = ctx.get("artifacts", {}).get("BA", {}) if isinstance(ctx.get("artifacts"), dict) else {}
        pkg_path = art.get("path", "") if isinstance(art, dict) else ""
        # Real BA evidence (not just the 26 always-rendered items): BRD text,
        # recorded knowledge, or pending approvals. Otherwise omit the block.
        has_content = bool(brd.strip()) or bool(pend_ap) or bool(
            ctx.get("decisions")) or bool(ctx.get("stakeholders"))
        if not has_content:
            return {"has_content": False, "package_ok": pkg["ok"],
                    "missing": pkg["missing"], "unresolved": hb["unresolved"],
                    "pending_approvals": pend_ap, "package_path": pkg_path,
                    "markdown": ""}
        lines = ["## BA Package intake (resolve before planning — do not invent)",
                 f"BA package: {pkg['items']} items, {len(pkg['missing'])} missing "
                 f"(version {pkg['package_version']})"
                 + (f" | artifact: `{pkg_path}`" if pkg_path else " | artifact: not recorded")]
        if hb["unresolved"]:
            lines.append("Must-resolve handoff points:")
            lines += [f"- {u}" for u in hb["unresolved"][:9]]
        if pkg["missing"]:
            lines.append("Package gaps to carry explicitly (assumption, CR, or question — never silently dropped):")
            lines += [f"- {m}" for m in pkg["missing"][:10]]
        if pend_ap:
            lines.append(f"Pending BA approvals: {', '.join(pend_ap[:5])}")
        lines.append("Rule: resolve each item via recorded stakeholder answers, change requests, "
                     "or explicit assumptions carried into the plan.")
        return {"has_content": True, "package_ok": pkg["ok"],
                "missing": pkg["missing"], "unresolved": hb["unresolved"],
                "pending_approvals": pend_ap, "package_path": pkg_path,
                "markdown": "\n".join(lines)}
    except Exception as e:
        return {"has_content": False, "package_ok": False, "missing": [],
                "unresolved": [], "pending_approvals": [], "package_path": "",
                "markdown": "", "error": str(e)[:200]}


# ---------------- audience views (S6, §22) ----------------

STAKEHOLDER_VIEW_ITEMS = (
    "business_need", "business_context", "business_objectives", "stakeholders",
    "current_state", "future_state", "solution_scope", "risks", "open_questions",
)

DELIVERY_VIEW_ITEMS = (
    "business_requirements", "stakeholder_requirements",
    "functional_requirements", "business_rules", "user_stories",
    "acceptance_criteria", "use_cases", "traceability", "baseline",
    "decisions", "dependencies", "transition",
)

# §22 — one source of truth, five consumer cuts.
AUDIENCE_VIEW_ITEMS: dict[str, tuple[str, ...]] = {
    "business_owner": (
        "business_need", "business_context", "business_objectives", "stakeholders",
        "business_case", "solution_scope", "risks", "outcome_metrics", "readiness",
        "open_questions",
    ),
    "project": (
        "business_need", "business_objectives", "solution_scope", "priorities",
        "user_stories", "acceptance_criteria", "dependencies", "constraints",
        "assumptions", "risks", "transition", "open_questions",
    ),
    "functional": (
        "functional_requirements", "business_rules", "user_stories", "use_cases",
        "acceptance_criteria", "models", "current_state", "future_state",
    ),
    "technical": (
        "functional_requirements", "nfrs", "constraints", "dependencies",
        "solution_assessment", "models", "transition",
    ),
    "frappe": (
        "functional_requirements", "business_rules", "acceptance_criteria",
        "user_stories", "priorities", "baseline", "decisions",
    ),
}

# Backward-compatible aliases (runbook / downstream references).
AUDIENCE_ALIASES = {"stakeholder": "business_owner", "delivery": "project"}

AUDIENCE_TITLES = {
    "business_owner": "BA Package — Business Owner View",
    "project": "BA Package — Project View",
    "functional": "BA Package — Functional View",
    "technical": "BA Package — Technical View",
    "frappe": "BA Package — Frappe Implementation View",
}


def audience_views(state: dict[str, Any], project: str = "project",
                    audience: str = "") -> dict:
    """Audience cuts over the same items (pure read, never raises).

    `audience` selects one cut (or a legacy alias); empty renders all five.
    Every cut renders from identical sources, so numbers cannot diverge.
    Returns {<audience>: markdown, ..., "missing": [...]}.
    """
    try:
        ctx = _ctx(state)
        brd = _brd(state)
        slug = "".join(c if c.isalnum() else "_" for c in (project or "project")).strip("_") or "project"
        want = (audience or "").strip().lower()
        want = AUDIENCE_ALIASES.get(want, want)
        names = [want] if want in AUDIENCE_VIEW_ITEMS else list(AUDIENCE_VIEW_ITEMS)

        def _render(ids: tuple[str, ...], title: str) -> tuple[str, list[str]]:
            titles = dict(PACKAGE_ITEMS)
            parts = [f"# {title} — {slug}", ""]
            missing: list[str] = []
            for item_id in ids:
                body, ok = _render_item(item_id, state, ctx, brd)
                parts += [f"## {titles[item_id]}", "", body, ""]
                if not ok:
                    missing.append(item_id)
            return "\n".join(parts).rstrip() + "\n", missing

        out: dict[str, Any] = {}
        missing: list[str] = []
        for name in names:
            md, miss = _render(AUDIENCE_VIEW_ITEMS[name], AUDIENCE_TITLES[name])
            out[name] = md
            missing += miss
        out["missing"] = sorted(set(missing))
        # legacy keys for existing callers/tests
        if "business_owner" in out:
            out["stakeholder"] = out["business_owner"]
        if "project" in out:
            out["delivery"] = out["project"]
        return out
    except Exception as e:
        return {"missing": [], "error": str(e)[:200]}
