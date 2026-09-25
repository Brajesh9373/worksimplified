"""Headless-CLI execution adapter for the delivery pipeline.

Swaps a stage's *execution engine* without touching the orchestrator, briefs,
sections, gates, package or workspace: the CLI (Command Code / OpenCode) answers
each stage pass and the existing harness/gate machinery accepts and judges the
result.

The engine is chosen per stage from the environment (``cli_exec.config``)::

    AGENT_EXEC_MODE=cli                  # global default (api when unset)
    AGENT_EXEC_MODE_PROJECT=cli          # per-stage override
    CLI_PROVIDER=commandcode|opencode
    CLI_MODEL=deepseek/deepseek-v4.1-flash
    CLI_TIMEOUT=900
    CLI_RETRIES=3

FRAPPE is pinned to ``api``: it makes real Frappe tool calls with side effects
that a headless CLI cannot execute. The seam is
``delivery_pipeline.agent.STAGE_AGENTS``.
"""

from __future__ import annotations

import os

from .config import API, API_ONLY, CLI, MODES, STAGES, cli_stages, stage_mode
from .node import CliStageNode
from .providers import (
    CLIResult,
    CliExecError,
    CommandCodeProvider,
    OpenCodeProvider,
    get_provider,
    is_cli_provider,
)

__all__ = [
    "API",
    "API_ONLY",
    "CLI",
    "CLIResult",
    "CliExecError",
    "CliStageNode",
    "CommandCodeProvider",
    "MODES",
    "OpenCodeProvider",
    "STAGES",
    "build_stage_agent",
    "cli_stages",
    "get_provider",
    "is_cli_provider",
    "stage_mode",
]

_DEFAULT_PROVIDER = "commandcode"


def build_stage_agent(stage: str, default_agent=None):
    """The engine for ``stage``: a CLI-backed node, or ``default_agent``.

    Returns ``default_agent`` (the ADK agent) unless the stage is configured for
    the ``cli`` engine, so an unconfigured checkout behaves exactly as before.
    Raises when ``cli`` is requested with an unknown provider — a configuration
    error the operator should see immediately rather than silently falling back.
    """
    name = (stage or "").strip().upper()
    if stage_mode(name) != CLI:
        return default_agent
    provider = (os.getenv("CLI_PROVIDER", "") or _DEFAULT_PROVIDER).strip()
    if not is_cli_provider(provider):
        raise CliExecError(
            f"{name} is set to the cli engine but CLI_PROVIDER={provider!r} is "
            f"unknown; set CLI_PROVIDER=commandcode|opencode"
        )
    return CliStageNode(
        name=f"{name.lower()}_cli_stage",
        stage=name,
        provider_name=provider,
        model=os.getenv("CLI_MODEL", ""),
        rerun_on_resume=True,
    )
