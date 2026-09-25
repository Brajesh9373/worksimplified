"""Conversation state and the discovery register.

Two phases, one agent:

- **discovery** (default for a new project): the owner talks freely; the agent
  listens, responds like a person, and captures what it hears. No documents,
  no sections, no gates.
- **generation**: the user explicitly asks for the artefacts; the formal phases
  run against everything the conversation produced.

This module is plumbing only. It owns the mode, the transcript, the discovery
prompt and the deterministic readiness check — never the conversational
choreography (when to summarise, how many questions, what to challenge is the
agent's judgement, not a counter in code).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

CONV_KEY = "conversation"
MODES = ("discovery", "generation")
TRANSCRIPT_CAP = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conversation(ctx: dict[str, Any]) -> dict[str, Any]:
    """The conversation block, created on first use (never raises)."""
    block = ctx.get(CONV_KEY)
    if not isinstance(block, dict):
        block = {}
        ctx[CONV_KEY] = block
    block.setdefault("mode", "")
    block.setdefault("turn", 0)
    if not isinstance(block.get("transcript"), list):
        block["transcript"] = []
    block.setdefault("generation_requests", [])
    block.setdefault("pending_proposal", False)
    block.setdefault("artifacts", [])
    return block


def ensure_mode(state: dict[str, Any], ctx: dict[str, Any], fresh: bool = False,
                first_text: str = "") -> str:
    """Decide the initial mode for this project (idempotent).

    A new project starts in discovery, and a workspace with prior pipeline work
    stays in generation so in-flight projects are unchanged.

    A generation request in the very first message is deliberately NOT honoured
    here: the turn handler runs the agent first (so what the owner said is
    captured) and only then switches to generation. Otherwise "generate my BRD"
    as an opening line would start writing from nothing.
    """
    block = conversation(ctx)
    if block.get("mode") in MODES:
        return block["mode"]
    # A conversation block seeded at the session's top level is honoured too
    # (useful for tests, migrations and operator-provided state).
    try:
        top = state.get("conversation") if hasattr(state, "get") else None
    except Exception:
        top = None
    if isinstance(top, dict):
        for key in ("mode", "pending_proposal", "artifacts", "auto_continue",
                    "pending_generation"):
            if key in top and not block.get(key):
                block[key] = top[key]
        if block.get("mode") in MODES:
            return block["mode"]
    # "Prior work" means content this project produced — never the orchestrator's
    # own bookkeeping (a resume writes history, and the first message becomes the
    # goal) and never the pipeline banners.
    prior = bool((ctx.get("brd") or "").strip()) or bool(
        (ctx.get("artifacts") or {}).get("BA")) or bool(ctx.get("sections_done_BA")) or bool(
        ctx.get("ba_versions"))
    # A workspace with prior work never drops back into discovery: an in-flight
    # project resumes in generation. Only a genuinely empty workspace starts as
    # a conversation.
    block["mode"] = "generation" if prior else "discovery"
    return block["mode"]


def set_mode(state: dict[str, Any], ctx: dict[str, Any], mode: str) -> str:
    if mode not in MODES:
        return conversation(ctx).get("mode", "")
    conversation(ctx)["mode"] = mode
    return mode


def record_turn(state: dict[str, Any], role: str, text: str) -> None:
    """Append one turn to the orchestrator's own transcript (never raises)."""
    try:
        block = conversation(_ctx(state))
        block["transcript"].append({"role": role, "text": (text or "")[:6000], "at": _now()})
        del block["transcript"][:-TRANSCRIPT_CAP]
        if role == "user":
            block["turn"] = int(block.get("turn", 0)) + 1
        _commit(state)
    except Exception:
        pass


def _ctx(state: Any) -> dict[str, Any]:
    from .project_context import get_context

    return get_context(state)


def _commit(state: Any) -> None:
    try:
        from .project_context import commit

        commit(state)
    except Exception:
        pass


def recent_transcript(ctx: dict[str, Any], limit: int = 24) -> str:
    """Recent turns as text for the prompt (the session also carries history)."""
    turns = conversation(ctx).get("transcript") or []
    lines = [f"{'Owner' if t.get('role') == 'user' else 'BA'}: {t.get('text', '')}"
             for t in turns[-limit:] if isinstance(t, dict)]
    return "\n".join(lines)


def knowledge_snapshot(state: Any) -> int:
    """Count recorded knowledge entries (capture audit baseline)."""
    try:
        from ba_agent.managers import knowledge as K

        return K.count(state)
    except Exception:
        return 0


def capture_audit(state: Any, before: int, user_text: str) -> dict:
    """Flag a substantive user turn that produced no captured knowledge.

    Deterministic and non-invasive: it warns (so the next prompt can nudge),
    it never invents content on the agent's behalf.
    """
    try:
        after = knowledge_snapshot(state)
        substantive = len((user_text or "").strip()) >= 120 or bool(
            __import__("re").search(r"\b\d+\b|\b(must|only|always|never|approve|budget|policy)\b",
                                    (user_text or ""), __import__("re").IGNORECASE))
        if substantive and after <= before:
            try:
                from .project_context import append_history

                append_history(state, "capture_gap", "orchestrator",
                               f"discovery turn captured nothing ({len(user_text)} chars)")
            except Exception:
                pass
            return {"captured": 0, "gap": True}
        return {"captured": max(0, after - before), "gap": False}
    except Exception:
        return {"captured": 0, "gap": False}


def readiness(state: Any, artifacts: list[str] | None = None) -> dict:
    """Deterministic pre-check before generation: is there enough to write from?

    Returns {ok, missing[]} where missing[] holds plain-language items (never
    raw check names) so the agent can ask a short, human question.
    """
    from ba_agent.managers import knowledge as K

    try:
        gap = K.thin(state)
    except Exception:
        gap = []
    asks: list[str] = []
    for item in gap[:4]:
        asks.append(item)
    return {"ok": not asks, "missing": asks}


def discovery_prompt(state: Any, user_text: str = "") -> str:
    """The prompt for one discovery turn.

    States the register and hands over the facts (digest + transcript). It does
    NOT script the reply: how to respond, what to ask and whether to summarise
    are the agent's decisions.
    """
    ctx = _ctx(state)
    try:
        from ba_agent.managers import knowledge as K

        digest = K.digest_text(state)
        thin = K.thin(state)
    except Exception:
        digest, thin = "", []
    transcript = recent_transcript(ctx)
    parts = [
        "DISCOVERY TURN — you are talking with the owner of this project, who is not "
        "technical.",
        "",
        "Register:",
        "- Talk like a person. Listen more than you ask; keep your reply short and warm.",
        "- No documents yet, and never mention sections, ids, requirements terminology, "
        "quality checks or stages. The owner should feel like they are simply telling you "
        "about their world.",
        "- Capture what you learn with record_knowledge (one call per fact). If you need to "
        "know something, ask it in your own words — use ask_user so it is tracked. Ask as "
        "many or as few questions as the moment deserves; you decide.",
        "- You may challenge, suggest a better way, or offer an alternative — that is what a "
        "good analyst does in a conversation.",
        "- If nothing new was said, just respond naturally; do not invent facts.",
        "- Only when the owner asks for the document should it be written; you can also offer "
        "to write it up with propose_generation when you believe you have enough.",
        "",
        f"What you know so far:\n{digest or '(nothing captured yet)'}",
    ]
    if thin:
        parts.append("Still thin (use your judgement about whether it matters now):\n- "
                     + "\n- ".join(thin[:6]))
    if transcript:
        parts.append(f"Conversation so far:\n{transcript}")
    parts.append(f"Owner's new message:\n{user_text.strip()}")
    parts.append("Reply to the owner in plain language.")
    return "\n\n".join(parts)
