"""Prompt construction for the headless CLI stage passes.

The orchestrator already builds the full stage brief — the handoff, the
``SECTION TASK`` / ``STAGE TASK`` assignment, the harness-allocated ids and any
gate feedback — and passes it as the node input. That brief was written for an
in-process ADK agent that owns project tools (``append_doc``, ``read_output_file``,
``save_doc``); a headless CLI has none of them. This module translates the brief
into the CLI's world:

- an explicit override so the CLI does not stall trying to call those tools;
- the output format the orchestrator ingests (``harness.ingest_prose_reply``);
- for the BA stage, the record contract (data in the reply, executed by the
  adapter — see ``tools.py``), which keeps the CLI out of any contradiction
  about "tool calls".
"""

from __future__ import annotations

from pathlib import Path

_ROLE = """You are the WorkSimplified {role} executing ONE pass of the delivery pipeline.

RULES:
- Work only from the evidence available in the project workspace and the task
  below. Never invent facts. Keep FACTS, INFERENCES, ASSUMPTIONS and UNKNOWNS
  separate.
- Use the exact identifiers given in the task below — allocated ids are fixed;
  do not renumber and do not invent other ids.
- No ambiguous, unquantified language. Every requirement is atomic and testable.
"""

_BA_RULES = """- Tag every assumption inline as [ASSUMPTION:short-name], using a REAL short
  kebab-case name of your own that names the actual subject (for example
  [ASSUMPTION:approval-threshold]). Never use a placeholder or a generic word as
  the name — not "xxx", "name", "label" or "assumption" — and mirror each tag as
  an open question numbered OQ-001, OQ-002, and so on.
"""

_BA_ROLE = ("WorkSimplified BA Agent — a Senior Business Analyst executing one "
            "stage of the seven-stage BA lifecycle:\n"
            "  S1 Understand Business · S2 Analyze Business · S3 Elicit Information ·\n"
            "  S4 Analyze Requirements · S5 Verify & Validate · S6 Requirements "
            "Management ·\n  S7 Assess Solution")

_STAGE_ROLE = {
    "PROJECT": "WorkSimplified Project Agent — a delivery planner turning a "
               "validated BA package into a scheduled, resourced work breakdown.",
    "FUNCTIONAL": "WorkSimplified Functional Agent — a functional consultant "
                  "turning business requirements into implementable system behavior.",
    "TECHNICAL": "WorkSimplified Technical Agent — a solution architect turning "
                 "functional requirements into a buildable technical design.",
}

_OVERRIDE = """OVERRIDE:
Everything you need is ALREADY in this prompt. Do NOT read files, search the
repository, or explore the workspace — there is nothing else to find and doing
so wastes the pass. The task below was written for an in-process agent that owns
project tools (append_doc, save_doc, read_output_file, get_project_status, ...).
YOU HAVE NONE OF THEM: do not call them, do not announce them, do not wait for
them. Anything the task says to hand to a tool, write directly in your reply.
Never reply with a plan, an acknowledgement, a question, or a description of what
you are about to do — reply with the content itself, now.
"""

_SECTION_OUTPUT = """OUTPUT FORMAT — exactly two parts, in this order, as plain text:
1. the assigned section, as markdown beginning with `## <the exact section title
   from the task below>` — complete tables and all required ids, 1,500-3,000
   characters;
2. optionally, ONE fenced json block of BA records (see RECORDS below).
Nothing else: no preamble, no commentary, no explanations, no questions.
"""

_SECTION_OUTPUT_PLAIN = """OUTPUT FORMAT — one part only:
the assigned section, as markdown beginning with `## <the exact section title
from the task below>` — complete tables and all required ids, 1,500-3,000
characters. Nothing else: no preamble, no commentary, no explanations, no
questions.
"""

_DIAGRAM_OUTPUT = """OUTPUT FORMAT — one part only:
a single fenced mermaid block (```mermaid ... ```). The diagram must start with
`flowchart TD`, contain at least 6 nodes and at least one decision node written
as { ... }, and have a clear start and an end. Nothing else.
"""


def _role(stage: str) -> str:
    name = (stage or "").strip().upper()
    if name == "BA":
        return _BA_ROLE + "\n" + _BA_RULES
    return _STAGE_ROLE.get(name, f"WorkSimplified {name.title()} Agent")


def _workspace_block(workspace: Path) -> str:
    return f"""PROJECT WORKSPACE (read-only inputs for this pass):
{workspace}
  - handoffs/             the brief assembled for this pass
  - context/context.json  the shared PROJECT_CONTEXT
  - context/sections/     sections already accepted
  - artifacts/uploaded_*  stakeholder source documents
"""


def _wrap(header: str, output: str, workspace: Path, task: str, stage: str,
          records: bool = False) -> str:
    """Order matters: the task (written for a tool-owning ADK agent) comes
    before the contract, so the adapter's instruction is the LAST thing the CLI
    reads and dominates the inherited 'call append_doc' wording."""
    parts = [
        _ROLE.format(role=_role(stage)),
        _workspace_block(Path(workspace)),
        "=" * 60,
        f"TASK ({header})",
        "=" * 60,
        task,
    ]
    if records:
        from .tools import tool_contract_text

        parts.append(tool_contract_text())
    parts += [
        "=" * 60,
        "YOUR OUTPUT (this overrides anything above)",
        "=" * 60,
        _OVERRIDE,
        output,
    ]
    return "\n".join(parts)


def build_section_prompt(node_input: str, workspace: Path, stage: str = "") -> str:
    """Wrap the orchestrator's section brief in the CLI contract.

    The BA record contract is published only for the BA stage — it is the stage
    whose gate consumes a recorded ledger.
    """
    task = node_input if isinstance(node_input, str) else str(node_input or "")
    is_ba = (stage or "").strip().upper() == "BA"
    return _wrap("from the WorkSimplified orchestrator",
                 _SECTION_OUTPUT if is_ba else _SECTION_OUTPUT_PLAIN,
                 workspace, task, stage, records=is_ba)


def build_diagram_prompt(node_input: str, workspace: Path, stage: str = "",
                         kind: str = "flow") -> str:
    """Wrap the orchestrator's diagram brief in the CLI contract."""
    task = node_input if isinstance(node_input, str) else str(node_input or "")
    return _wrap(f"{kind} diagram pass", _DIAGRAM_OUTPUT, workspace, task, stage)
