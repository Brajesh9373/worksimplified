"""The discovery turn — one owner-facing reply, owned by the orchestrator.

Why this is not an agent node: an ADK agent streams every model turn into the
session, so on a weak or routed model the owner reads the model's scratchpad
("The user asks…", "Let me capture what I've learned…") and the node's curated
message duplicates the final one. A conversation is not a document pipeline — it
needs exactly one visible writer, bounded tool use, and no leakage of reasoning.

So the orchestrator calls the model itself, offers the knowledge tools it owns,
executes them, and returns the single reply. Nothing is streamed: the reply is
the only thing the owner sees, by construction, whatever the model's habits are.

Generation is deliberately NOT routed through here: document sections are
produced by the BA agent, whose prose IS the artefact the harness ingests.
"""

from __future__ import annotations

import json
from typing import Any

MAX_TOOL_ROUNDS = 3   # a chat turn is not a document pass: keep it short

# The knowledge tools the owner's conversation can use. They are the same
# managers the agent tools wrap, called directly — no ToolContext needed.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "record_knowledge",
            "description": "Remember one thing the owner told you, in their words. "
                           "Capture as soon as you hear something that matters; never invent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["need", "problem", "objective", "goal", "metric", "driver",
                                 "stakeholder", "actor", "process", "rule", "requirement",
                                 "use_case", "risk", "dependency", "integration",
                                 "transition", "constraint", "decision", "assumption"],
                    },
                    "key": {"type": "string",
                            "description": "short name, e.g. a role or the rule's subject"},
                    "value": {"type": "string",
                              "description": "what they said, faithfully"},
                    "note": {"type": "string",
                             "description": "optional context, e.g. why it matters"},
                },
                "required": ["kind", "key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Track a question you want the owner to answer, in your own words, "
                           "so it is never lost or asked twice by accident.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "why": {"type": "string",
                            "description": "what the answer changes"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_generation",
            "description": "Offer to write the documents up. The owner still decides.",
            "parameters": {
                "type": "object",
                "properties": {
                    "artifacts": {"type": "string",
                                  "description": "e.g. 'brd, diagrams, package'"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "knowledge_status",
            "description": "What has been captured so far and what is still missing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM = (
    "You are a senior business analyst talking with the owner of a small business. "
    "This is a conversation, not a document: nothing is written up until they ask.\n"
    "How to be:\n"
    "- Listen first and answer like a person; keep it short and warm.\n"
    "- Your reply is the ONLY thing they read: whatever you return as the message. "
    "Never narrate your plan, your analysis, or what you are about to do.\n"
    "- Never mention sections, requirement ids, stages, quality checks or tools.\n"
    "- Capture what you hear with record_knowledge as you hear it; ask only what "
    "would change the outcome, in your own words.\n"
    "- You may disagree, suggest a better way, or offer an alternative."
)


def _dispatch(state: Any, name: str, args: dict, source_ref: str = "") -> dict:
    """Execute one tool call against the BA managers (never raises)."""
    from ba_agent.managers import knowledge as K

    try:
        if name == "record_knowledge":
            return K.capture(state, args.get("kind", ""), args.get("key", ""),
                             args.get("value", ""), source_ref=source_ref,
                             note=args.get("note", ""))
        if name == "ask_user":
            return K.capture(state, "question", args.get("question", "")[:120],
                             args.get("question", ""), source_ref=source_ref,
                             note=args.get("why", ""))
        if name == "knowledge_status":
            return K.status(state)
        if name == "propose_generation":
            from .conversation import conversation

            from .project_context import commit, get_context

            block = conversation(get_context(state))
            block["pending_proposal"] = True
            block["artifacts"] = [a.strip() for a in
                                  (args.get("artifacts") or "brd").split(",") if a.strip()]
            commit(state)
            return {"ok": True, "proposed": block["artifacts"]}
        return {"ok": False, "error": f"unknown tool {name!r}"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def run_turn(state: Any, user_text: str, source_ref: str = "") -> dict:
    """One conversational turn. Returns {reply, tools_used, steps, error?}.

    Nothing is emitted to the session: the caller decides what the owner sees.
    """
    from .conversation import discovery_prompt
    from .meta_model import LLM_API_BASE, LLM_API_KEY, agent_temperature, model_string

    try:
        import litellm
    except Exception as e:                                  # pragma: no cover
        return {"reply": "", "tools_used": [], "steps": 0, "error": f"litellm: {e}"}

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": discovery_prompt(state, user_text)},
    ]
    used: list[str] = []
    try:
        for step in range(MAX_TOOL_ROUNDS):
            response = litellm.completion(
                model=model_string("ba"), api_base=LLM_API_BASE, api_key=LLM_API_KEY,
                temperature=agent_temperature("ba"), messages=messages, tools=TOOLS,
                tool_choice="auto", num_retries=1, timeout=60)
            message = response.choices[0].message
            calls = list(getattr(message, "tool_calls", None) or [])
            if not calls:
                return {"reply": (message.content or "").strip(), "tools_used": used,
                        "steps": step + 1}
            messages.append({
                "role": "assistant", "content": message.content or "",
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments}}
                               for c in calls],
            })
            for call in calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except Exception:
                    args = {}
                result = _dispatch(state, call.function.name, args, source_ref)
                used.append(call.function.name)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result)[:2000]})
        # tool budget spent: ask for the reply on its own
        messages.append({"role": "user",
                         "content": "Reply to the owner now, in one or two sentences. "
                                    "No tool calls, no analysis."})
        response = litellm.completion(
            model=model_string("ba"), api_base=LLM_API_BASE, api_key=LLM_API_KEY,
            temperature=agent_temperature("ba"), messages=messages,
            num_retries=1, timeout=60)
        return {"reply": (response.choices[0].message.content or "").strip(),
                "tools_used": used, "steps": MAX_TOOL_ROUNDS}
    except Exception as e:
        return {"reply": "", "tools_used": used, "steps": 0, "error": str(e)[:300]}
