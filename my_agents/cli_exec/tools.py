"""BA tool contract for the headless CLI engine (structured tool calls).

The ADK BA agent records the BA v2 ledger through tools — evidence per
requirement, assumption statuses, ``DEC-n`` decisions, ``SM-n`` metrics, plan
items. A headless CLI cannot call ADK tools, so the adapter publishes the same
tool surface as a documented contract: each pass the CLI emits the tool calls it
decided, and the adapter executes them against the **same manager APIs** the ADK
tools use. Single source of truth — nothing is re-implemented here beyond
adapting arguments.

Transport (one fenced block at the end of the reply):

    ```json ba_tool_calls
    {"tool_calls": [{"name": "record_evidence", "arguments": {...}}, ...]}
    ```

``extract_tool_calls`` removes the block from the reply so the section text the
orchestrator ingests is clean; ``apply_tool_calls`` runs each call and reports
applied/error/unknown. Both are total: a malformed block yields no calls rather
than corrupting the section or raising.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

_FENCE_RX = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class ToolSpec:
    """One tool exposed to the CLI: name, description, handler and parameters."""

    name: str
    description: str
    handler: Callable[[Any, str, dict], dict]
    params: tuple[str, ...] = ()


# --------------------------------------------------------------- handlers

def _record_evidence(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import evidence as EV

    return EV.link(state, a.get("artifact_id", ""), a.get("source_type", ""),
                   a.get("source_ref", ""), a.get("note", ""), brd=brd)


def _record_assumption(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import assumptions as ASM

    return ASM.record(state, a.get("label", ""), a.get("text", ""))


def _record_decision(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import decision_manager as DM

    return DM.record_decision(state, a.get("decision", ""), a.get("reason", ""),
                              a.get("decision_maker", ""), a.get("related_ids", ""),
                              a.get("status", "approved"), brd=brd)


def _record_success_metric(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import outcome as OC

    return OC.record_metric(state, a.get("name", ""), a.get("baseline", ""),
                            a.get("target", ""), a.get("method", ""),
                            a.get("review_point", ""), a.get("unit", ""),
                            a.get("metric_id", ""))


def _record_plan_item(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import ba_plan as BP

    return BP.record(state, a.get("title", ""), a.get("type", "activity"),
                     a.get("stage", ""), a.get("owner", "ba_cli"),
                     a.get("status", "planned"), a.get("note", ""))


def _update_plan_item(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import ba_plan as BP

    return BP.update(state, a.get("item_id", ""), a.get("status", ""), a.get("note", ""))


def _record_open_question(state: Any, brd: str, a: dict) -> dict:
    from ba_agent.managers import knowledge as K

    question = (a.get("question") or "").strip()
    return K.capture(state, "question", question[:120], question, note=a.get("why", ""))


TOOLS: dict[str, ToolSpec] = {
    "record_evidence": ToolSpec(
        name="record_evidence",
        description="Link one requirement/objective id to where its information came from.",
        handler=_record_evidence,
        params=("artifact_id: US-001, BR-003, OQ-002 ...",
                "source_type: elicitation | upload | document | decision | human | system",
                "source_ref: the citable reference (EL-n answer, DEC-n, uploaded_* name)",
                "note: optional context")),
    "record_assumption": ToolSpec(
        name="record_assumption",
        description="Register one ASSUMPTION tag you wrote so it has a tracked status.",
        handler=_record_assumption,
        params=("label: the real short name from your ASSUMPTION tag, e.g. approval-threshold",
                "text: the assumption in one sentence")),
    "record_decision": ToolSpec(
        name="record_decision",
        description="Record a business decision (DEC-n) with its reason and owner.",
        handler=_record_decision,
        params=("decision", "reason", "decision_maker: named human/role",
                "related_ids: comma-separated ids",
                "status: approved | proposed | rejected")),
    "record_success_metric": ToolSpec(
        name="record_success_metric",
        description="Define a measurable business-outcome metric (SM-n).",
        handler=_record_success_metric,
        params=("name", "baseline", "target", "method: how it is measured",
                "review_point: when it is measured", "unit", "metric_id: SM-001")),
    "record_plan_item": ToolSpec(
        name="record_plan_item",
        description="Add a BA plan item (activity/deliverable/communication/review).",
        handler=_record_plan_item,
        params=("title", "type", "stage: S1..S7", "owner",
                "status: planned | in_progress | done | waived", "note")),
    "update_plan_item": ToolSpec(
        name="update_plan_item",
        description="Advance an existing plan item (PL-n).",
        handler=_update_plan_item,
        params=("item_id: PL-001", "status", "note")),
    "record_open_question": ToolSpec(
        name="record_open_question",
        description="Track an open question so it is never lost.",
        handler=_record_open_question,
        params=("question", "why: what it changes")),
}


# --------------------------------------------------------------- extraction

def _calls_in(data: Any) -> list[dict]:
    """Normalise the accepted call shapes to a list of call dicts.

    Accepts ``{"tool_calls": [...]}``, a bare list of calls, or a single call —
    a headless model is a non-deterministic producer, so the boundary validates
    shapes rather than trusting exactly one.
    """
    if isinstance(data, dict):
        if isinstance(data.get("tool_calls"), list):
            return [c for c in data["tool_calls"] if isinstance(c, dict)]
        if "name" in data or "call" in data:
            return [data]
        return []
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    return []


def extract_tool_calls(reply: str) -> tuple[str, list[dict]]:
    """Split a reply into (section text, tool calls).

    The tool-call block(s) are removed from the returned text so the section the
    orchestrator ingests contains only the section.
    """
    text = reply or ""
    calls: list[dict] = []
    spans: list[tuple[int, int]] = []
    for m in _FENCE_RX.finditer(text):
        info = (m.group(1) or "").strip().lower()
        body = (m.group(2) or "").strip()
        if not body or body[0] not in "{[":
            continue
        if "tool" not in info and "tool_call" not in body:
            continue  # never eat a section's own JSON/fenced content
        try:
            data = json.loads(body)
        except Exception:
            continue
        found = _calls_in(data)
        if not found:
            continue
        calls.extend(found)
        spans.append(m.span())
    for start, end in reversed(spans):
        text = text[:start] + text[end:]
    return text.strip(), calls


def apply_tool_calls(state: Any, calls: list[dict], brd: str = "") -> dict:
    """Execute each tool call against the manager APIs. Never raises."""
    applied: list[str] = []
    errors: list[dict] = []
    unknown: list[str] = []
    for call in calls or []:
        name = str(call.get("name") or call.get("call") or "").strip()
        args = call.get("arguments")
        if not isinstance(args, dict):
            args = {}
        spec = TOOLS.get(name)
        if spec is None:
            unknown.append(name)
            continue
        try:
            res = spec.handler(state, brd, args)
        except Exception as e:  # a tool must not break the pass
            res = {"ok": False, "error": str(e)[:200]}
        if isinstance(res, dict) and res.get("ok"):
            applied.append(name)
        else:
            errors.append({"tool": name,
                           "error": str((res or {}).get("error", "failed"))[:200]})
    return {"applied": applied, "errors": errors, "unknown": unknown}


def tool_contract_text() -> str:
    """Prompt fragment: the BA record list and the in-reply transport format."""
    lines = [
        "RECORDS — keep the BA ledger as you work.",
        "The formal BA gate requires these records (evidence per requirement,",
        "assumption statuses, decisions, success metrics, plan). After the section,",
        "append ONE fenced json block describing them. This is DATA in your reply,",
        "not a tool call — you do not execute anything:",
        "",
        "```json ba_tool_calls",
        '{"tool_calls": [{"name": "record_evidence", "arguments": '
        '{"artifact_id": "US-001", "source_type": "document", '
        '"source_ref": "BRD", "note": "stated in the requirements"}}]}',
        "```",
        "",
        "Available records:",
    ]
    for spec in TOOLS.values():
        lines.append(f"- {spec.name}({', '.join(spec.params)}) — {spec.description}")
    lines.append("")
    lines.append("Record one record_evidence entry for every US-/BR- id you write, and "
                 "one record_assumption entry for every ASSUMPTION tag. When the task "
                 "lists plan items still open although their stage is done, close each "
                 'with update_plan_item(item_id, "done"). Omit the block only when you '
                 "genuinely have nothing to record.")
    return "\n".join(lines)
