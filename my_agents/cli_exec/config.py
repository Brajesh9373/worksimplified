"""Execution-engine selection: which agent engine runs a pipeline stage.

Each stage runs on exactly one of two engines:

  api  — the ADK stage agent (LiteLlm model via the configured gateway).
  cli  — a headless coding CLI (Command Code / OpenCode) driven by the adapter.

Resolution order for a stage::

    AGENT_EXEC_MODE_<STAGE>   ->   AGENT_EXEC_MODE   ->   api

FRAPPE is pinned to ``api`` regardless: it makes real tool calls with side
effects (``connect_frappe``, ``create_project_from_plan``, ``ensure_*``) that a
headless CLI cannot execute. Running it on the CLI would silently produce no
Frappe project.

The switch exists because model access is quota-limited: the gateway and the CLI
have separate quotas, so the token-heavy document stages can run on the CLI
while the tool-driven stage stays on the gateway (or vice versa).
"""

from __future__ import annotations

import os

STAGES = ("BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE")

API = "api"
CLI = "cli"
MODES = (API, CLI)

# Stages that cannot run on the CLI engine (real tool calls with side effects).
API_ONLY = ("FRAPPE",)

MODE_VAR = "AGENT_EXEC_MODE"
MODE_VAR_FMT = "AGENT_EXEC_MODE_{stage}"


def _clean(value: str | None) -> str:
    return (value or "").strip().lower()


def stage_mode(stage: str) -> str:
    """The engine for ``stage``: ``api`` or ``cli`` (never raises)."""
    name = (stage or "").strip().upper()
    if name in API_ONLY:
        return API
    for var in (MODE_VAR_FMT.format(stage=name), MODE_VAR):
        mode = _clean(os.getenv(var))
        if mode in MODES:
            return mode
    return API


def cli_stages() -> list[str]:
    """Stages currently configured to run on the CLI engine."""
    return [s for s in STAGES if stage_mode(s) == CLI]
