"""Requirement engine — pure validators for US/BR/AC minima (prod-grade).

Thresholds mirror BA_INSTRUCTION + gates.ba_gate:
US >= 3, BR >= 3, each US >= 2 acceptance criteria, Scope OUT >= 3, risks >= 3.

AC counting is format-agnostic (prod): a story may express criteria as
Given/When/Then triplets, explicit AC-xxx codes, bullet AC lines,
pipe-table rows, or numbered acceptance lists. The counter detects each
signal independently and takes the max (signals are alternative renderings
of the same criteria, not additive). Blocks are truncated at the next
markdown heading so the BR catalog after the stories section never inflates
the last story's count.
"""

from __future__ import annotations

import re

from shared.traceability import extract_ids

MIN_STORIES = 3
MIN_RULES = 3
MIN_CRITERIA_PER_STORY = 2

_HEADING_RX = re.compile(r"(?m)^\s*#{1,4}\s+\S")
_US_RX = re.compile(r"\bUS-(\d+)\b")
_AC_CODE_RX = re.compile(r"\bAC[-_ ]?(\d+)\b", re.IGNORECASE)
_GIVEN_RX = re.compile(r"\bGiven\b", re.IGNORECASE)
_WHEN_RX = re.compile(r"\bWhen\b", re.IGNORECASE)
_THEN_RX = re.compile(r"\bThen\b", re.IGNORECASE)
_BULLET_AC_RX = re.compile(r"(?m)^\s*[-*+]\s*(?:\[?AC\b|Given\b)", re.IGNORECASE)
_NUMBERED_RX = re.compile(r"(?m)^\s*\d+[.)]\s+\S")
_ACCEPT_RX = re.compile(r"accept", re.IGNORECASE)


def _criteria_blocks(text: str) -> list[str]:
    """Split per-US blocks; truncate each at the next markdown heading.

    Split on US-xxx boundaries (existing convention); additionally cut each
    block at the first markdown heading so a trailing '## Business Rules'
    section does not leak BR text into the last story's count.
    """
    parts = re.split(r"\bUS-\d+\b", text or "")
    blocks: list[str] = []
    for p in parts[1:]:
        if not p.strip():
            continue
        m = _HEADING_RX.search(p)
        blocks.append(p[: m.start()] if m else p)
    return blocks


def _gwt_triplets(block: str) -> int:
    """Complete Given/When/Then triplets = min(Given, When, Then) counts."""
    g = len(_GIVEN_RX.findall(block))
    w = len(_WHEN_RX.findall(block))
    t = len(_THEN_RX.findall(block))
    if g and w and t:
        return min(g, w, t)
    return 0


def _ac_codes(block: str) -> int:
    """Distinct AC-xxx codes referenced in the block."""
    return len({_m.group(1) for _m in _AC_CODE_RX.finditer(block)})


def _bullet_ac(block: str) -> int:
    """Bullet lines starting with AC or Given."""
    return len(_BULLET_AC_RX.findall(block))


def _table_rows(block: str) -> int:
    """Pipe-table rows carrying acceptance signals (AC/Given/Then/expect/result).

    Two modes: (a) per-row signals (default); (b) acceptance-table context —
    when the header row names Given/When/Then/AC/criterion, every data row is
    one criterion even without per-row keywords (e.g. '| logged in | submit
    | saved |' under '| Given | When | Then |').
    """
    rows: list[list[str]] = []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        if all(re.fullmatch(r"[-:\s]*", c or "") for c in cells):
            continue  # separator row
        rows.append(cells)
    if not rows:
        return 0
    header_cue = bool(
        _AC_CODE_RX.search(" | ".join(rows[0]))
        or _GIVEN_RX.search(" | ".join(rows[0]))
        or re.search(r"\b(Then|criter|accept|expect|result)\b", " | ".join(rows[0]), re.IGNORECASE)
    )
    if header_cue:
        return max(len(rows) - 1, 0)  # data rows under the header
    n = 0
    for cells in rows:
        joined = " | ".join(cells)
        if _AC_CODE_RX.search(joined) or _GIVEN_RX.search(joined) or re.search(
            r"\b(Then|expect|result|accept)\b", joined, re.IGNORECASE
        ):
            n += 1
    return n


def _numbered_acceptance(block: str) -> int:
    """Numbered list items, counted only under an acceptance context.

    Guards against counting arbitrary numbered lists (e.g. BRD section
    numbering): requires an 'accept' cue in the block.
    """
    if not _ACCEPT_RX.search(block):
        return 0
    return len(_NUMBERED_RX.findall(block))


def _criteria_count(block: str) -> int:
    """Max over independent AC signals (alternative renderings, not additive)."""
    return max(
        _gwt_triplets(block),
        _ac_codes(block),
        _bullet_ac(block),
        _table_rows(block),
        _numbered_acceptance(block),
        0,
    )


def count_criteria(block: str) -> dict:
    """Explainable per-block count (for tests/diagnostics)."""
    signals = {
        "gwt_triplets": _gwt_triplets(block),
        "ac_codes": _ac_codes(block),
        "bullet_ac": _bullet_ac(block),
        "table_rows": _table_rows(block),
        "numbered": _numbered_acceptance(block),
    }
    return {"count": max(signals.values(), default=0), "signals": signals}


def validate_requirements(text: str) -> dict:
    """Return {ok, us, br, per_story_counts, missing[]} — pure, no LLM."""
    ids = extract_ids(text or "")
    us, br = ids.get("US", []), ids.get("BR", [])
    blocks = _criteria_blocks(text or "")
    counts = [_criteria_count(b) for b in blocks]
    missing: list[str] = []
    if len(us) < MIN_STORIES:
        missing.append(f"user_stories: found {len(us)} (need >={MIN_STORIES})")
    if len(br) < MIN_RULES:
        missing.append(f"business_rules: found {len(br)} (need >={MIN_RULES})")
    for i, c in enumerate(counts[: len(us)]):
        if c < MIN_CRITERIA_PER_STORY:
            missing.append(
                f"US {us[i] if i < len(us) else i}: {c} criteria "
                f"(need >={MIN_CRITERIA_PER_STORY})"
            )
    return {
        "ok": not missing,
        "us": us,
        "br": br,
        "per_story_counts": counts,
        "missing": missing,
    }
