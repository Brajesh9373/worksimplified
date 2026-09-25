"""Headless CLI providers for the execution adapter.

Each provider turns one prompt into one non-interactive CLI invocation in the
project workspace and returns its captured output. The CLI is the *execution
engine*; the orchestrator (``shared.orch_nodes``) owns the stage lifecycle.

Selection is by ``CLI_PROVIDER`` (``commandcode`` | ``opencode``) and only
applies to stages whose engine is ``cli`` — see ``cli_exec.config``.

Flags were taken from the installed binaries (``--help``):
  commandcode -p <query> --output-format text --skip-onboarding --no-session -t
  opencode run <message> --format default --dir <workspace>
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

DEFAULT_TIMEOUT = 600.0

_ALIASES = {
    "commandcode": "commandcode",
    "command-code": "commandcode",
    "cmd": "commandcode",
    "opencode": "opencode",
    "open-code": "opencode",
}


class CliExecError(RuntimeError):
    """Raised when a headless CLI pass cannot produce usable output."""


@dataclass
class CLIResult:
    """Outcome of one headless CLI invocation."""

    provider: str
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and bool(self.stdout.strip())


class CLIProvider(Protocol):
    """The execution engine contract the adapter depends on."""

    name: str

    def build_argv(self, prompt: str, workspace: Path, model: str = "") -> list[str]:
        ...

    async def run(self, prompt: str, workspace: Path, timeout: float | None = None,
                  model: str = "") -> CLIResult:
        ...


def _default_timeout() -> float:
    try:
        return float(os.getenv("CLI_TIMEOUT", "") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT


def retry_attempts() -> int:
    """Total attempts per pass including the first (``CLI_RETRIES``)."""
    try:
        return max(1, int(os.getenv("CLI_RETRIES", "2") or 2))
    except (TypeError, ValueError):
        return 2


def retry_backoff() -> float:
    """Base backoff between attempts in seconds (``CLI_RETRY_BACKOFF``)."""
    try:
        return max(0.0, float(os.getenv("CLI_RETRY_BACKOFF", "3") or 3))
    except (TypeError, ValueError):
        return 3.0


def permission_mode() -> str:
    """Command Code permission mode (``CLI_PERMISSION_MODE``, default standard).

    ``standard`` lets the CLI answer the task normally; ``plan`` is read-only but
    measurably degrades output — models interpret it as "only write a plan" and
    produce a fraction of the content (observed on both DeepSeek and the free
    models). In ``-p`` mode edit tools are withheld unless ``--tools-all`` is
    passed, and the adapter's contract forbids file edits, so the harness stays
    the only writer.
    """
    return (os.getenv("CLI_PERMISSION_MODE", "") or "standard").strip()


def agent_name() -> str:
    """OpenCode ``--agent`` selection (``CLI_AGENT``; blank = its default)."""
    return (os.getenv("CLI_AGENT", "") or "").strip()


class _BaseProvider:
    """Shared subprocess execution; subclasses only define argv."""

    name = ""
    default_bin = ""

    def __init__(self, binary: str = ""):
        self.binary = (binary or os.getenv("CLI_BIN", "") or self.default_bin).strip()

    def build_argv(self, prompt: str, workspace: Path, model: str = "") -> list[str]:
        raise NotImplementedError

    async def run(self, prompt: str, workspace: Path, timeout: float | None = None,
                  model: str = "") -> CLIResult:
        limit = float(timeout or _default_timeout())
        argv = self.build_argv(prompt, Path(workspace), model)
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            return CLIResult(self.name, argv, -1, "",
                             f"timeout after {limit:.0f}s", time.monotonic() - start)
        return CLIResult(
            self.name,
            argv,
            int(proc.returncode or 0),
            out.decode(errors="replace"),
            err.decode(errors="replace"),
            time.monotonic() - start,
        )


class CommandCodeProvider(_BaseProvider):
    """Command Code headless (`-p`) pass."""

    name = "commandcode"
    default_bin = "commandcode"

    def build_argv(self, prompt: str, workspace: Path, model: str = "") -> list[str]:
        argv = [
            self.binary,
            "-p", prompt,
            "--output-format", "text",
            "--skip-onboarding",
            "--no-session",
            "-t",
            "--permission-mode", permission_mode(),
        ]
        if model:
            argv += ["-m", model]
        return argv


class OpenCodeProvider(_BaseProvider):
    """OpenCode headless (`run`) pass."""

    name = "opencode"
    default_bin = "opencode"

    def build_argv(self, prompt: str, workspace: Path, model: str = "") -> list[str]:
        argv = [
            self.binary,
            "run", prompt,
            "--format", "default",
            "--dir", str(workspace),
        ]
        if model:
            argv += ["-m", model]
        if agent_name():
            argv += ["--agent", agent_name()]
        return argv


_PROVIDERS: dict[str, type[_BaseProvider]] = {
    "commandcode": CommandCodeProvider,
    "opencode": OpenCodeProvider,
}


def normalize_provider(name: str | None) -> str:
    """Canonical provider key for a name (or ``""`` when unknown)."""
    return _ALIASES.get((name or "").strip().lower(), "")


def is_cli_provider(name: str | None) -> bool:
    """True when ``name`` selects one of the supported headless CLIs."""
    return normalize_provider(name) in _PROVIDERS


def get_provider(name: str | None = None) -> CLIProvider:
    """Build the configured provider (``name`` defaults to ``CLI_PROVIDER``)."""
    raw = name if name is not None else os.getenv("CLI_PROVIDER", "")
    key = normalize_provider(raw)
    if key not in _PROVIDERS:
        raise CliExecError(
            f"unknown CLI provider {raw!r}; known providers: "
            f"{', '.join(sorted(_PROVIDERS))}"
        )
    return _PROVIDERS[key]()
