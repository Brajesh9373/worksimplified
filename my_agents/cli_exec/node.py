"""CliStageNode — a pipeline stage's execution engine, backed by a headless CLI.

Satisfies the orchestrator's dynamic-child contract
(``shared/orch_nodes.py``: ``reply = await ctx.run_node(pass_agent, sec_prompt)``)
without any orchestrator change:

- a section pass returns the CLI's section markdown as the node output, which
  the orchestrator recovers via ``harness.ingest_prose_reply`` and accepts via
  ``harness.accept_section`` (snapshot + canonical recompose + gate);
- a diagram pass extracts the mermaid block, writes the bundle through
  ``shared.eng_tools.build_diagram_bundle`` and sets the ``*_diagram`` state key
  the gate reads;
- for the BA stage, the CLI's recorded ledger is applied through the real
  managers (``tools.py``) so the fail-closed BA gate can pass.

All acceptance, gating, persistence and resume behaviour stay with the existing
harness/gate machinery. The CLI writes no ADK session state directly.

State writes go through ``ctx.state``, whose mutations the ADK ``NodeRunner``
flushes onto the yielded event — so history and the diagram key persist normally.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from google.adk.workflow import Node

from .prompt import build_diagram_prompt, build_section_prompt
from .providers import (
    CLIResult,
    CliExecError,
    get_provider,
    retry_attempts,
    retry_backoff,
)
from .tools import apply_tool_calls, extract_tool_calls

_DIAGRAM_MARKER = "DIAGRAM TASK"
_KIND_RX = re.compile(r'diagram_kind\s*=\s*"?(\w+)"?')
_SECTION_ID_RX = re.compile(r"\(id:\s*(\w+)\)")
_MERMAID_RX = re.compile(r"```(?:mermaid)?[ \t]*\n(.*?)```", re.DOTALL)
_MERMAID_HEADS = ("flowchart", "graph", "sequenceDiagram", "gantt", "erDiagram")


class _StateShim:
    """Minimal ToolContext stand-in: ``shared.eng_tools`` only reads ``.state``."""

    def __init__(self, state: Any):
        self.state = state


def _extract_mermaid(text: str) -> str:
    """First mermaid block in ``text`` (fenced, or the bare source)."""
    for m in _MERMAID_RX.finditer(text or ""):
        body = (m.group(1) or "").strip()
        if body and body.split(None, 1)[0] in _MERMAID_HEADS:
            return body
    stripped = (text or "").strip()
    if stripped and stripped.split(None, 1)[0] in _MERMAID_HEADS:
        return stripped
    return ""


def _section_label(text: str) -> str:
    m = _SECTION_ID_RX.search(text or "")
    if m:
        return m.group(1)
    return "diagram" if _DIAGRAM_MARKER in (text or "") else "pass"


def _current_doc(state: Any, doc_key: str) -> str:
    """The stage's current canonical document text (state first, workspace next)."""
    try:
        from shared.traceability import doc_texts

        return (doc_texts(state) or {}).get(doc_key, "") or ""
    except Exception:
        value = state.get(doc_key, "") if hasattr(state, "get") else ""
        return value if isinstance(value, str) else ""


class CliStageNode(Node):
    """One pipeline stage pass executed by a headless CLI provider."""

    stage: str = ""
    provider_name: str = ""
    model: str = ""
    cli_timeout: float = 0.0

    def _workspace(self, state: Any):
        from shared.workspace import current_workspace

        return current_workspace(state)

    def _actor(self) -> str:
        return f"cli_{self.stage.lower()}" if self.stage else "cli"

    async def _invoke(self, provider: Any, prompt: str, ws: Any,
                      node_input: str) -> CLIResult:
        """Run the CLI with bounded retry/backoff on transient failures."""
        attempts = retry_attempts()
        backoff = retry_backoff()
        result: CLIResult | None = None
        for attempt in range(attempts):
            result = await provider.run(prompt, ws.root, self.cli_timeout or None,
                                        self.model)
            self._trace(ws, node_input, result, attempt + 1)
            if result.ok:
                return result
            if attempt + 1 < attempts:
                await asyncio.sleep(backoff * (attempt + 1))
        assert result is not None
        return result

    async def run_node_impl(self, *, ctx, node_input) -> AsyncGenerator[Any, None]:
        from shared.project_context import append_history

        text = node_input if isinstance(node_input, str) else str(node_input or "")
        state = ctx.state
        ws = self._workspace(state)
        provider = get_provider(self.provider_name or None)

        if _DIAGRAM_MARKER in text:
            yield await self._diagram_pass(state, ws, provider, text)
            return

        prompt = build_section_prompt(text, ws.root, self.stage)
        res = await self._invoke(provider, prompt, ws, text)
        if not res.ok:
            append_history(state, "agent_failed", self._actor(),
                           f"{provider.name} exit={res.exit_code}: "
                           f"{res.stderr.strip()[:200] or 'empty output'}")
            raise CliExecError(
                f"{provider.name} produced no output (exit {res.exit_code}) after "
                f"{retry_attempts()} attempt(s): "
                f"{res.stderr.strip()[:300] or 'empty stdout'}"
            )
        section, calls = extract_tool_calls(res.stdout)
        # The ADK agent writes the section and only then records the ledger, so
        # ids it just introduced are already known. The orchestrator accepts this
        # section after the node returns, so record against the document as it
        # will be: current doc + this section.
        key = _doc_key(self.stage)
        doc = ((_current_doc(state, key) + "\n\n" + section).strip() if section
               else _current_doc(state, key))
        rec = apply_tool_calls(state, calls, doc) if calls else {
            "applied": [], "errors": [], "unknown": []}
        append_history(state, "cli_pass", self._actor(),
                       f"{_section_label(text)} ({len(section)} chars, "
                       f"{res.duration_s:.1f}s) tools: {rec['applied'] or 'none'}")
        if rec["errors"]:
            append_history(state, "cli_tool_error", self._actor(),
                           json.dumps(rec["errors"])[:300])
        yield section

    async def _diagram_pass(self, state: Any, ws: Any, provider: Any, text: str) -> str:
        from shared.eng_tools import build_diagram_bundle
        from shared.project_context import append_history

        m = _KIND_RX.search(text)
        kind = m.group(1) if m else "flow"
        prompt = build_diagram_prompt(text, ws.root, self.stage, kind)
        res = await self._invoke(provider, prompt, ws, text)
        body, calls = extract_tool_calls(res.stdout)
        rec = apply_tool_calls(state, calls, _current_doc(state, _doc_key(self.stage))) \
            if calls else {"applied": [], "errors": [], "unknown": []}
        mermaid = _extract_mermaid(body)
        if not mermaid:
            append_history(state, "agent_failed", self._actor(),
                           f"no mermaid for {kind} diagram")
            raise CliExecError(f"{provider.name} returned no mermaid diagram")
        out = build_diagram_bundle(
            f"{self.stage.lower() or 'stage'}_{kind}_{ws.project_id}", kind, mermaid,
            f"{self.stage or 'Stage'} Diagram — {ws.project_id}", _StateShim(state))
        if out.get("error"):
            append_history(state, "agent_failed", self._actor(), str(out["error"])[:200])
            raise CliExecError(str(out["error"]))
        append_history(state, "cli_pass", self._actor(),
                       f"{kind} diagram ({out.get('nodes')} nodes) "
                       f"tools: {rec['applied'] or 'none'}")
        return f"{kind} diagram written ({out.get('nodes')} nodes)"

    def _trace(self, ws: Any, node_input: str, res: CLIResult, attempt: int) -> None:
        """Append a machine-readable execution trace for this pass (best effort)."""
        try:
            d = Path(ws.root) / "execution"
            d.mkdir(parents=True, exist_ok=True)
            record = {
                "provider": res.provider,
                "stage": self.stage,
                "section": _section_label(node_input),
                "attempt": attempt,
                "argv": res.argv,
                "exit_code": res.exit_code,
                "duration_s": round(res.duration_s, 2),
                "stdout_chars": len(res.stdout),
                "stderr_tail": res.stderr.strip()[-400:],
            }
            fname = f"cli_{self.stage or 'stage'}.jsonl"
            with (d / fname).open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass


_DOC_KEYS = {"BA": "brd", "PROJECT": "project_plan", "FUNCTIONAL": "functional_spec",
             "TECHNICAL": "tech_design", "FRAPPE": "frappe_setup"}


def _doc_key(stage: str) -> str:
    return _DOC_KEYS.get((stage or "").strip().upper(), "")
