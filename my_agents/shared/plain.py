"""Plain-language surfacing for the delivery pipeline's user-facing chat.

The pipeline's native vocabulary is internal (sections, gates, check names, id
families). A non-technical owner must never see it. This module provides:

- `is_generation_request()` — recognises an explicit "produce my documents" ask.
- `is_affirmative()` / `is_negative()` — for the agent's "shall I write it up?".
- `say()` — the deterministic fallback phrasing used when the agent cannot phrase
  an outcome itself (never a phrasebook for every check: internal detail is
  reduced to what a person needs to know).
- `strip_jargon()` — removes ids/internal tokens from any outgoing text.

Verbosity: BA_CHAT_VERBOSITY=quiet|normal|technical (default quiet).
`technical` restores the pipeline's native messages for developers.
"""

from __future__ import annotations

import os
import re

VERBOSITY_LEVELS = ("quiet", "normal", "technical")


def verbosity() -> str:
    v = (os.getenv("BA_CHAT_VERBOSITY", "quiet") or "quiet").strip().lower()
    return v if v in VERBOSITY_LEVELS else "quiet"


def is_technical() -> bool:
    return verbosity() == "technical"


# ------------------------------------------------------------------ the trigger

_ASK = r"(?:generate|write|produce|create|draft|prepare|make|give me|put together|type up|write up)"
_DOC = r"(?:brd|business\s+requirements(?:\s+document)?|requirements\s+document|the\s+document|documents?|srs|spec\w*|statement\s+of\s+work)"
_ARTEFACTS = {
    "diagrams": r"(?:diagrams?|flow\s?charts?|process\s+map|gantt|models?)",
    "package": r"(?:package|handoff|everything|all\s+of\s+it|the\s+whole\s+thing)",
    "brd": _DOC,
}
_GENERATE_RX = re.compile(
    rf"\b{_ASK}\b[^.?!]{{0,40}}\b(?:{_DOC}|{_ARTEFACTS['diagrams']}|{_ARTEFACTS['package']})"
    rf"|\b(?:{_DOC}|{_ARTEFACTS['package']})\b[^.?!]{{0,30}}\b(?:now|please|for\s+me)\b"
    rf"|\bwrite\s+it\s+up\b|\bgenerate\s+(?:it|them|now)\b|\bmake\s+the\s+documents?\b",
    re.IGNORECASE)
# Discussion of documents must not fire the trigger.
_DISCUSSION_RX = re.compile(
    r"\b(?:we(?:'| a)?ll|i(?:'| a)?ll|we\s+will|will\s+need|later|at\s+some\s+point|"
    r"should\s+we|do\s+we\s+need|what\s+about|think\s+about|maybe)\b",
    re.IGNORECASE)

_AFFIRM_RX = re.compile(
    r"^\s*(?:yes|yep|yeah|yup|sure|ok(?:ay)?|go\s+ahead|please\s+do|do\s+it|"
    r"go\s+for\s+it|proceed|sounds\s+good|that\s+works|continue|carry\s+on|"
    r"go\s+on|please|let'?s\s+go)\b",
    re.IGNORECASE)
_NEGATIVE_RX = re.compile(
    r"^\s*(?:no|nope|not\s+yet|hold\s+on|wait|later|don'?t|do\s+not|not\s+now)\b",
    re.IGNORECASE)


def is_generation_request(text: str) -> dict | None:
    """Explicit ask to produce documents → {artifacts[], reason} else None.

    Deliberately conservative: talking *about* documents ("we'll need a BRD at
    some point") does not trigger; asking for them does.
    """
    t = (text or "").strip()
    if not t or len(t) > 2000:
        return None
    if not _GENERATE_RX.search(t):
        return None
    low = t.lower()
    if _DISCUSSION_RX.search(t) and not re.search(r"\b(generate|write\s+it\s+up|now)\b", low):
        return None
    arts = ["brd"]
    for name, rx in _ARTEFACTS.items():
        if name != "brd" and re.search(rx, low, re.IGNORECASE):
            arts.append(name)
    if re.search(r"\b(package|handoff|everything|all of it)\b", low):
        pass  # already captured above
    return {"artifacts": sorted(set(arts)), "reason": t[:200]}


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRM_RX.search(text or ""))


def is_negative(text: str) -> bool:
    return bool(_NEGATIVE_RX.search(text or ""))


# ------------------------------------------------------------------- phrasing

# Internal check/detail vocabulary → what it means to a person.
_PLAIN = (
    (re.compile(r"business_case", re.I),
     "the business case (the options you have and why one is better)"),
    (re.compile(r"solution_assessment", re.I),
     "how the solution covers what you asked for"),
    (re.compile(r"readiness", re.I),
     "whether your people, process, technology and training are ready"),
    (re.compile(r"quality_attributes", re.I),
     "a few requirements that are still vague"),
    (re.compile(r"priorities_tagged", re.I),
     "priorities for the requirements"),
    (re.compile(r"evidence_coverage", re.I),
     "where each requirement came from"),
    (re.compile(r"assumptions_registered", re.I),
     "assumptions that still need confirmation"),
    (re.compile(r"decisions_recorded", re.I),
     "decisions taken so far"),
    (re.compile(r"ba_plan", re.I), "the plan for the analysis"),
    (re.compile(r"outcome_metrics", re.I),
     "how success will be measured"),
    (re.compile(r"cr_impact_recorded", re.I),
     "the impact of the change you asked for"),
    (re.compile(r"contradictions_resolved", re.I),
     "answers that disagree with each other"),
    (re.compile(r"flow (?:diagram|nodes?)", re.I), "the process diagram"),
    (re.compile(r"user_stories?", re.I), "the user stories"),
    (re.compile(r"business_rules?", re.I), "the business rules"),
    (re.compile(r"open questions?", re.I), "open questions"),
    (re.compile(r"acceptance criteria", re.I), "the acceptance criteria"),
    (re.compile(r"scope (?:in|out)", re.I), "the scope"),
    (re.compile(r"stakeholders?", re.I), "who is involved"),
    (re.compile(r"asis_tobe|as-is|to-be", re.I), "how things work today versus later"),
    (re.compile(r"data_needs|data\b", re.I), "the data involved"),
)

_ID_RX = re.compile(r"\b(?:G|BN|OPT|BR|US|FR|UC|T|TECH|DEC|SM|OQ|EL|CT|AP|QB|PL|AC)-\d+\b")
_JARGON_RX = re.compile(
    r"\b(?:gate(?:s)?|PASSED|FAILED|STAGE TASK|stage|section|checks?|blockers?|MoSCoW|NFRs?|"
    r"criteria|gate_feedback|harness|sections_done|revision \d+)\b",
    re.IGNORECASE)
_WS_RX = re.compile(r"\s{2,}")


def strip_jargon(text: str) -> str:
    """Remove ids and internal vocabulary from outgoing text (keeps sentences)."""
    out = _ID_RX.sub("", text or "")
    out = _JARGON_RX.sub("", out)
    out = re.sub(r"\(\s*[,;:]?\s*\)", "", out)          # now-empty parentheses
    out = re.sub(r"\s*,\s*(?=[,.)])", "", out)           # doubled commas
    out = re.sub(r"\s+([.,;:)])", r"\1", out)
    out = re.sub(r"^[\s,;:.-]+", "", out)
    return _WS_RX.sub(" ", out).strip()


def _plain_detail(detail: str) -> str:
    """Turn an internal missing-item string into a short human phrase."""
    for rx, phrase in _PLAIN:
        if rx.search(detail or ""):
            return phrase
    stripped = strip_jargon(re.sub(r"^[a-z_]+\s*:\s*", "", (detail or ""))).rstrip(".").strip()
    # Degenerate fragments ("not covered", "is missing") are noise, not information.
    return stripped if len(stripped.split()) >= 3 else ""


def phrase_missing(missing: list[str], limit: int = 4) -> str:
    """Plain sentence listing what is still needed (deduplicated)."""
    seen: list[str] = []
    for m in missing or []:
        p = _plain_detail(m)
        if p and p not in seen:
            seen.append(p)
        if len(seen) >= limit:
            break
    if not seen:
        return ""
    if len(seen) == 1:
        return seen[0]
    return ", ".join(seen[:-1]) + " and " + seen[-1]


def say(kind: str, detail: str = "", missing: list[str] | None = None) -> str:
    """Deterministic fallback phrasing for pipeline outcomes.

    Used when the agent cannot phrase an outcome itself, and always in
    `technical` mode it is bypassed by the caller.
    """
    if kind == "discovery_start":
        return ("Great — tell me about it. I'll listen first and we can go into detail as we "
                "go; nothing gets written up until you ask.")
    if kind == "generation_start":
        return "Right — let me write this up properly. I'll come back when it's ready."
    if kind == "readiness_needed":
        what = phrase_missing(missing or [])
        return (f"Before I write this up I want to get a couple of things right: {what}."
                if what else
                "One or two details are still thin — can you fill me in?")
    if kind == "gate_failed":
        what = phrase_missing(missing or [])
        return (f"There are still a few things I need to fix ({what}). Working on it."
                if what else "I still have a few things to fix — working on them now.")
    if kind == "gate_passed":
        return "All good — that part is complete."
    if kind == "review_pause":
        return ("Your document is ready for review. Read it over: tell me what to change, "
                "or say 'approve' and I'll release it to the next stage.")
    if kind == "offer_continue":
        return ("Done — your requirements document and the package are saved. Shall I continue "
                "with the next stage (the functional specification and design), or do you want "
                "to change something first?")
    if kind == "continue_declined":
        return ("No problem — take your time. Say 'continue' when you want me to carry on, or "
                "tell me what to change.")
    if kind == "continued":
        return "Continuing to the next stage."
    if kind == "section_progress":
        return f"Working on {strip_jargon(detail) or 'the document'}…"
    if kind == "document_ready":
        return "Done — your documents are written and saved. Want me to continue with the next stage?"
    if kind == "finished":
        return ("All done — your requirements document, the delivery plan, the functional "
                "specification, the design and the Frappe build are all in place.")
    if kind == "not_mine":
        return ("That part comes from the next stage (the functional specification). Say the word "
                "and I'll carry on once your requirements document is signed off.")
    if kind == "discovery_ack":
        return ("Thanks — that's useful. Tell me more about how it works today, or ask me "
                "anything about how we'll capture this.")
    if kind == "resumed":
        return "Welcome back — picking up where we left off."
    return strip_jargon(detail) if detail else ""


def strip_ids(text: str) -> str:
    """Remove internal identifiers only — safe for the agent's own prose.

    The owner never needs `US-003`; ordinary English words are left alone (the
    word "stage" in a sentence is not machinery).
    """
    out = _ID_RX.sub("", text or "")
    out = re.sub(r"\(\s*[,;:]?\s*\)", "", out)
    out = re.sub(r"\s+([.,;:)])", r"\1", out)
    return _WS_RX.sub(" ", out).strip()


def public_message(text: str) -> str:
    """Final filter for anything shown to a non-technical owner."""
    if is_technical():
        return text or ""
    return strip_ids(text or "")
