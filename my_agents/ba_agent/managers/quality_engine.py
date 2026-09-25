"""Requirement quality engine — the 8 attributes of §15/§16, deterministically.

Pure functions over BRD text and the elicitation log. Every finding names the
offending text and the fix, so gate feedback is actionable. Findings carry a
severity: `blocking` findings fail the BA gate (correct, unambiguous,
modifiable, testable), `advisory` findings are surfaced in the brief and the
package but never block (consistent, cohesive, feasible).

Also implements contradiction detection (§7 3.15) between stakeholder answers:
numeric conflicts for the same topic and explicit corrections block; polarity
(yes/no) conflicts are advisory. Findings are citable back to both sources.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

BLOCKING_ATTRIBUTES = ("correct", "unambiguous", "modifiable", "testable")

# Deliberately modest word lists: each entry is a term a reviewer would query.
_VAGUE = (
    "quickly", "fast", "soon", "asap", "user-friendly", "user friendly",
    "robust", "scalable", "efficient", "flexible", "seamless", "intuitive",
    "minimal", "several", "many", "various", "appropriate", "reasonable",
    "as needed", "as required", "etc", "and/or", "state of the art", "modern",
)
_ABSOLUTE = (
    "instant", "instantly", "immediately", "always", "never", "24/7",
    "all data", "zero downtime", "no downtime", "every time",
)
_MODALS = re.compile(r"\b(shall|must|should|will|may not|must not|shall not)\b", re.IGNORECASE)
_BOUND = re.compile(r"(<=|>=|<|>|\bwithin\b|\bper\b|\bmax\w*\b|\bmin\w*\b|\bup to\b|"
                    r"\bno more than\b|\bno less than\b|\d)")
_NUMBER_UNIT = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:%|percent|seconds?|secs?|s\b|minutes?|mins?|hours?|hrs?|days?|"
    r"weeks?|months?|years?|ms\b|euros?|usd|inr|eur|gb|mb|kb|tb|requests?|users?|"
    r"records?|rows?|items?|transactions?)",
    re.IGNORECASE)
_OUTCOME_VERB = re.compile(
    r"\b(becomes?|is saved|are saved|saved|rejects?|rejected|approves?|approved|notifies?|"
    r"notified|blocks?|blocked|creates?|created|sets?|returns?|returned|displays?|shown|"
    r"records?|recorded|deletes?|deleted|updates?|updated|computes?|redirects?|assigns?|"
    r"closes?|opens?|sends?|sent|receives?|raises?|validates?|fails?)\b",
    re.IGNORECASE)
_NFR_HINT = re.compile(
    r"\bnfr\b|non[- ]functional|performance|secur|usab|availab|scalab|reliab|maintainab",
    re.IGNORECASE)
_ASSERTION = re.compile(r"\b(shall|must|should|will|is|are|supports?|provides?|handles?)\b",
                        re.IGNORECASE)
# Measurability applies to requirement statements, not to narrative prose: a
# line must name a requirement id or a system/solution subject to be judged.
_REQ_CONTEXT = re.compile(
    r"\b(?:US|BR|FR|NFR|TECH|UC)-\d+\b|\b(system|solution|application|platform|service)\b",
    re.IGNORECASE)
_CORRECTION = re.compile(
    r"\b(actually|correction|to correct|that'?s wrong|not true|we don'?t|we do not|"
    r"no,? we|contrary to|misheard)\b", re.IGNORECASE)
_YES = re.compile(r"^\s*(yes|true|correct|agreed)\b", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|false|incorrect|disagree)\b", re.IGNORECASE)
_STOP = {
    "the", "and", "for", "with", "that", "this", "what", "when", "which", "does",
    "your", "our", "you", "are", "was", "were", "have", "has", "how", "why", "who",
    "must", "shall", "should", "will", "per", "from", "into", "about",
}


def _sentence(text: str, start: int, end: int) -> str:
    """Sentence-ish window around a match, for a citable evidence snippet."""
    lo = max(0, text.rfind("\n", 0, start) + 1)
    hi = text.find("\n", end)
    hi = len(text) if hi < 0 else hi
    return re.sub(r"\s+", " ", text[lo:hi]).strip()[:220]


def _quantified(snippet: str) -> bool:
    """Is this statement quantified? Ids and tags are not quantification.

    `NFR-001 The system must be fast` is NOT measurable — the digits belong to
    the identifier, so they are stripped before looking for a number or bound.
    """
    bare = re.sub(r"\[[^\]]*\]", " ", snippet or "")
    bare = re.sub(r"\b[A-Za-z]{1,8}-\d+\b", " ", bare)
    return bool(_NUMBER_UNIT.search(bare) or _BOUND.search(bare))


def _finding(attribute: str, severity: str, detail: str, evidence: str = "") -> dict[str, str]:
    return {"attribute": attribute, "severity": severity, "detail": detail,
            "evidence": evidence}


def _us_blocks(text: str) -> list[tuple[str, str]]:
    """[(US-id, block)] with each block cut at the next markdown heading."""
    parts = re.split(r"\b(US-\d+)\b", text or "")
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        m = re.search(r"(?m)^\s*#{1,4}\s+\S", body)
        out.append((parts[i], body[: m.start()] if m else body))
    return out


def _br_blocks(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"\b(BR-\d+)\b", text or "")
    out: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        body = parts[i + 1]
        m = re.search(r"(?m)^\s*#{1,4}\s+\S", body)
        out.append((parts[i], body[: m.start()] if m else body))
    return out


def _glossary_variants(text: str) -> list[str]:
    """Defined glossary terms that collide: the same term defined twice, or two
    terms that differ only by case/plural — a renamed term mid-document.
    """
    terms = [t.strip() for t in re.findall(r"(?m)^\s*[-*]\s*\*\*([A-Za-z][^*]{2,60})\*\*",
                                           text or "")]
    variants: list[str] = []
    seen: dict[str, str] = {}
    for t in terms:
        key = re.sub(r"[^a-z]", "", t.lower())
        if key in seen and seen[key] != t:
            variants.append(f"'{seen[key]}' vs '{t}'")
        seen.setdefault(key, t)
    return sorted(set(variants))[:5]


def report(text: str) -> dict[str, Any]:
    """Quality report for the 8 attributes. Deterministic, never raises."""
    t = text or ""
    findings: list[dict[str, str]] = []
    try:
        from ba_agent.managers.requirement_engine import validate_requirements

        for m in validate_requirements(t).get("missing", []):
            findings.append(_finding("complete", "blocking", m))
    except Exception:
        pass

    try:
        from shared.sections import stub_markers

        stubs = stub_markers(t)
        if stubs:
            findings.append(_finding("correct", "blocking",
                                     f"placeholder markers present: {stubs}"))
    except Exception:
        pass

    # correct: an id reused with materially different text. Only line-leading
    # definitions count — a cross-reference inside prose or a coverage table is
    # not a redefinition.
    seen: dict[str, str] = {}
    for line in t.splitlines():
        m = re.match(r"\s*(?:[-*+>]\s*|\d+[.)]\s*|\|\s*)?\*{0,2}((?:US|BR|FR|UC|OPT)-\d+)\b",
                     line)
        if not m:
            continue
        iid = m.group(1)
        tail = re.sub(r"\s+", " ", line[m.end():m.end() + 70]).strip().lower()
        if iid in seen and tail and seen[iid] and tail[:25] != seen[iid][:25]:
            findings.append(_finding("correct", "advisory",
                                     f"{iid} is defined twice with different text",
                                     seen[iid][:80] + " | " + tail[:80]))
        seen.setdefault(iid, tail)

    # unambiguous: vague terms anywhere, unquantified non-functional statements
    low = t.lower()
    for term in _VAGUE:
        for m in re.finditer(rf"\b{re.escape(term)}\b", low):
            snip = _sentence(t, m.start(), m.end())
            if _quantified(snip):
                continue  # quantified elsewhere in the sentence
            findings.append(_finding("unambiguous", "blocking",
                                     f"vague term '{term}' is not quantified",
                                     snip))
            break
    for line in t.splitlines():
        if not _NFR_HINT.search(line) or not _ASSERTION.search(line):
            continue
        if not _REQ_CONTEXT.search(line):
            continue  # narrative prose, not a requirement statement
        if _quantified(line):
            continue
        findings.append(_finding("unambiguous", "blocking",
                                 "non-functional statement is not measurable "
                                 "(no number, unit or bound)",
                                 re.sub(r"\s+", " ", line).strip()[:220]))

    # modifiable: atomicity of rules and stories (bracket tags such as
    # [MoSCoW: Must] are metadata, not obligations — strip them first)
    for iid, block in _br_blocks(t) + _us_blocks(t):
        body = block.strip()
        if not body:
            continue
        for line in body.splitlines():
            bare = re.sub(r"\[[^\]]*\]", " ", line)
            if not _MODALS.search(bare):
                continue
            obligations = len(_MODALS.findall(bare))
            joined = len(re.findall(r"\band\b", bare, re.IGNORECASE))
            if obligations > 1 or (obligations == 1 and joined >= 1):
                findings.append(_finding("modifiable", "blocking",
                                         f"{iid} is not atomic (multiple obligations)",
                                         re.sub(r"\s+", " ", line).strip()[:220]))
                break

    # testable: every story needs a measurable acceptance signal
    for iid, block in _us_blocks(t):
        if not re.search(r"accept|given|when|then|criteria", block, re.IGNORECASE):
            continue
        if _NUMBER_UNIT.search(block) or _OUTCOME_VERB.search(block):
            continue
        findings.append(_finding("testable", "blocking",
                                 f"{iid} acceptance criteria have no measurable outcome "
                                 "(no number and no state change)"))

    # consistent: stories should reference a rule (co-occurrence convention)
    for iid, block in _us_blocks(t):
        if not re.search(r"\bBR-\d+\b", block):
            findings.append(_finding("consistent", "advisory",
                                     f"{iid} references no BR-xxx rule"))
    for pair in _glossary_variants(t):
        findings.append(_finding("consistent", "advisory",
                                 f"glossary term renamed mid-document: {pair}"))

    # cohesive: orphan requirements (no upstream G/BN alignment)
    try:
        from shared.traceability import coverage_report

        cov = coverage_report({"brd": t})
        for fam in ("uncovered_US", "uncovered_G", "uncovered_BN"):
            for missing_id in cov.get(fam, []) or []:
                findings.append(_finding("cohesive", "advisory",
                                         f"{missing_id} is not traced upstream ({fam})"))
    except Exception:
        pass

    # feasible: unbounded absolutes
    for term in _ABSOLUTE:
        for m in re.finditer(rf"\b{re.escape(term)}\b", low):
            snip = _sentence(t, m.start(), m.end())
            if _quantified(snip):
                continue
            findings.append(_finding("feasible", "advisory",
                                     f"absolute '{term}' has no bound or scope", snip))
            break

    by_attr: dict[str, int] = {}
    for f in findings:
        by_attr[f["attribute"]] = by_attr.get(f["attribute"], 0) + 1
    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {"ok": not blocking, "findings": findings, "by_attribute": by_attr,
            "blocking": blocking,
            "blocking_missing": [f"{f['attribute']}: {f['detail']}" for f in blocking]}


def blocking_missing(text: str) -> list[str]:
    """Gate-facing summary: blocking findings only."""
    try:
        return report(text)["blocking_missing"]
    except Exception:
        return []


_PRIORITY_TAG = re.compile(
    r"\[(?:moscow\s*[:=]\s*)?(must|should|could|won'?t|p[0-3])\b", re.IGNORECASE)
_PRIORITY_TOKEN = re.compile(r"\b(?:P[0-3])\b")


def priorities_untagged(text: str) -> list[str]:
    """Ids carrying no priority (§14/§39) — MoSCoW tag or P0-P3 anywhere near it.

    A priority must be attached explicitly (a tag or a P-token in one of the
    id's blocks); vague prose ("important") does not count. An id mentioned in
    several places counts as tagged when any of its blocks carries the tag.
    """
    flagged: dict[str, bool] = {}
    t = text or ""
    for iid, block in _us_blocks(t) + _br_blocks(t):
        head = block[:600]
        tagged = bool(_PRIORITY_TAG.search(head) or _PRIORITY_TOKEN.search(head))
        flagged[iid] = flagged.get(iid, False) or tagged
    return [iid for iid, tagged in flagged.items() if not tagged]


# --------------------------------------------------------------- contradictions

_TOPIC_STOP = {"what", "which", "who", "when", "where", "why", "how", "is", "are",
               "the", "for", "and", "with", "does", "do", "your", "our", "you",
               "many", "much", "list", "describe", "explain", "give"}


def _topics(question: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (question or "").lower())
    return {w for w in words if w not in _TOPIC_STOP}


def _numbers(text: str) -> list[tuple[str, str]]:
    """[(value, unit)] pairs, e.g. ('200', 'requests')."""
    return [(m.group(1), m.group(2).lower()) for m in re.finditer(
        r"(\d+(?:[.,]\d+)?)\s*([A-Za-z%/]{1,12})", text or "")]


def detect_contradictions(log: list[dict], brd: str = "") -> list[dict]:
    """Contradictions between stakeholder answers (pure, never raises).

    Blocking: conflicting numbers with the same unit for an overlapping topic,
    and explicit corrections. Advisory: yes/no polarity conflicts.
    Each finding cites both elicitation ids so a human can adjudicate.
    """
    out: list[dict] = []
    entries = [e for e in (log or []) if isinstance(e, dict)]
    try:
        for i, a in enumerate(entries):
            for b in entries[i + 1:]:
                shared = _topics(a.get("q", "")) & _topics(b.get("q", ""))
                if not shared:
                    continue
                an, bn = _numbers(a.get("a", "")), _numbers(b.get("a", ""))
                conflict = None
                for (av, au) in an:
                    for (bv, bu) in bn:
                        if au == bu and av != bv:
                            conflict = (f"conflicting {au!r} values {av} vs {bv}")
                            break
                    if conflict:
                        break
                if conflict:
                    out.append({
                        "kind": "numeric", "severity": "blocking",
                        "topic": ", ".join(sorted(shared)[:4]),
                        "detail": conflict,
                        "sources": [a.get("id", "?"), b.get("id", "?")],
                        "answers": [str(a.get("a", ""))[:120], str(b.get("a", ""))[:120]],
                    })
                    continue
                if _CORRECTION.search(str(b.get("a", ""))) or _CORRECTION.search(
                        str(a.get("a", ""))):
                    out.append({
                        "kind": "correction", "severity": "blocking",
                        "topic": ", ".join(sorted(shared)[:4]),
                        "detail": "one answer explicitly corrects the other",
                        "sources": [a.get("id", "?"), b.get("id", "?")],
                        "answers": [str(a.get("a", ""))[:120], str(b.get("a", ""))[:120]],
                    })
                    continue
                a_yes, a_no = bool(_YES.match(str(a.get("a", "")))), bool(
                    _NO.match(str(a.get("a", ""))))
                b_yes, b_no = bool(_YES.match(str(b.get("a", "")))), bool(
                    _NO.match(str(b.get("a", ""))))
                if (a_yes and b_no) or (a_no and b_yes):
                    out.append({
                        "kind": "polarity", "severity": "advisory",
                        "topic": ", ".join(sorted(shared)[:4]),
                        "detail": "one answer affirms what the other denies",
                        "sources": [a.get("id", "?"), b.get("id", "?")],
                        "answers": [str(a.get("a", ""))[:120], str(b.get("a", ""))[:120]],
                    })
    except Exception:
        return out
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_findings(state: dict[str, Any], brd: str = "") -> dict:
    """Persist current quality findings + open contradictions (never raises)."""
    try:
        from shared.project_context import commit, get_context

        ctx = get_context(state)
        rep = report(brd)
        ctx["quality_findings"] = rep["findings"][:100]
        log = ((ctx.get("elicitation") or {}).get("log") or [])
        found = detect_contradictions(log, brd)
        store = ctx.get("contradictions")
        if not isinstance(store, list):
            store = []
            ctx["contradictions"] = store
        known = {(c.get("sources", [None])[0], c.get("detail")) for c in store
                 if isinstance(c, dict)}
        added = 0
        for f in found:
            key = (f["sources"][0], f["detail"])
            if key in known:
                continue
            rec = dict(f)
            rec["id"] = f"CT-{len(store) + 1:03d}"
            rec["status"] = "open"
            rec["detected_at"] = _now()
            store.append(rec)
            added += 1
        del store[:-100]
        commit(state)
        return {"ok": True, "quality": rep["by_attribute"], "contradictions": added,
                "blocking": len(rep["blocking"])}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def open_contradictions(state: dict[str, Any], blocking_only: bool = True) -> list[dict]:
    """Unresolved contradiction findings (never raises)."""
    try:
        from shared.project_context import get_context

        store = get_context(state).get("contradictions", [])
        return [dict(c) for c in store if isinstance(c, dict)
                and c.get("status") == "open"
                and (not blocking_only or c.get("severity") == "blocking")]
    except Exception:
        return []


def resolve_contradiction(state: dict[str, Any], finding_id: str, resolution: str,
                          resolved_by: str) -> dict:
    """Close one contradiction with a human resolution (audited). Never raises."""
    try:
        from shared.project_context import append_history, commit, get_context

        if not (resolution or "").strip():
            return {"ok": False, "error": "resolution text is required"}
        if not (resolved_by or "").strip():
            return {"ok": False, "error": "resolved_by is required"}
        store = get_context(state).get("contradictions", [])
        for c in store if isinstance(store, list) else []:
            if isinstance(c, dict) and c.get("id") == finding_id:
                if c.get("status") != "open":
                    return {"ok": False, "error": f"{finding_id} already {c.get('status')}"}
                c["status"] = "resolved"
                c["resolution"] = resolution.strip()[:1000]
                c["resolved_by"] = resolved_by.strip()[:120]
                c["resolved_at"] = _now()
                commit(state)
                try:
                    append_history(state, "ba_contradiction_resolved", c["resolved_by"],
                                   f"{finding_id}: {c['resolution'][:120]}", finding_id)
                except Exception:
                    pass
                return {"ok": True, "finding": dict(c)}
        return {"ok": False, "error": f"unknown finding {finding_id!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
