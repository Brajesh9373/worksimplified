"""Delivery Pipeline — orchestrated delivery (orchestration only).

BA → BA_GATE → PROJECT → PROJECT_GATE → FUNCTIONAL → FUNCTIONAL_GATE →
TECHNICAL → TECHNICAL_GATE → FRAPPE → FINAL VALIDATION → SUCCESS,
with bounded auto-revision, Change-Request iteration, and human review.

Stage agent system prompts are NOT defined here and are never modified by
this module — they are imported as-is. All pipeline behavior lives in
shared.orch_nodes.DeliveryOrchestrator (deterministic state machine).

Per-agent models come from shared.meta_model (LLM_MODEL_<AGENT> env, falling
back to LLM_MODEL); the escalation rung is off unless LLM_MODEL_ESCALATION is
set, in which case each stage agent is cloned onto that model — instructions
and tools stay identical.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.append(str(MY_AGENTS))

from google.adk import Workflow  # noqa: E402

from ba_agent.agent import root_agent as ba_engineer  # noqa: E402
from cli_exec import build_stage_agent  # noqa: E402
from frappe_agent.agent import root_agent as frappe_engineer  # noqa: E402
from functional_agent.agent import root_agent as functional_engineer  # noqa: E402
from project_agent.agent import root_agent as project_engineer  # noqa: E402
from shared.meta_model import get_meta_model  # noqa: E402
from shared.orch_nodes import DeliveryOrchestrator  # noqa: E402
from technical_agent.agent import root_agent as technical_engineer  # noqa: E402

STAGE_DEFAULTS = {
    "BA": ba_engineer,
    "PROJECT": project_engineer,
    "FUNCTIONAL": functional_engineer,
    "TECHNICAL": technical_engineer,
    "FRAPPE": frappe_engineer,
}

# Each stage runs on its ADK agent unless the environment selects the headless
# CLI engine for it (AGENT_EXEC_MODE / AGENT_EXEC_MODE_<STAGE>; see
# cli_exec.config). FRAPPE is pinned to the api engine — it makes real Frappe
# tool calls with side effects a headless CLI cannot execute.
STAGE_AGENTS = {stage: build_stage_agent(stage, agent)
                for stage, agent in STAGE_DEFAULTS.items()}


def build_escalation_agents(stage_agents: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clone stage agents onto LLM_MODEL_ESCALATION (empty when unset).

    The orchestrator swaps in the escalated agent for the prose rung once a
    stage has failed repeatedly; prompts and tools are unchanged.
    """
    if not os.getenv("LLM_MODEL_ESCALATION"):
        return {}
    model = get_meta_model("escalation")
    agents = stage_agents if stage_agents is not None else STAGE_AGENTS
    out: dict[str, Any] = {}
    for stage, agent in agents.items():
        try:
            out[stage] = agent.model_copy(update={"model": model})
        except Exception:
            continue  # non-LLM execution engines (e.g. the CLI adapter) don't escalate
    return out


orchestrator = DeliveryOrchestrator(
    name="delivery_orchestrator",
    stage_agents=STAGE_AGENTS,
    escalation_agents=build_escalation_agents(),
)

root_agent = Workflow(
    name="delivery_pipeline",
    edges=[("START", orchestrator)],
)
