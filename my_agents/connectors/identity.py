"""Channel identity: map an external user to an ADK identity and a project.

One external conversation maps to exactly one ADK ``(user_id, session_id)`` pair
and therefore to one project workspace, so a second message never spawns a second
project. The mapping is deterministic and stateless — nothing to persist here; the
workspace id itself lives in the session state the orchestrator writes.

    telegram  chat id 1555   -> user_id "telegram:1555"      session_id "telegram-1555"
    whatsapp  919812345678   -> user_id "whatsapp:9198123..." session_id "whatsapp-9198..."
    web       uuid abc123    -> user_id "web:abc123"          session_id "web-abc123"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SAFE = re.compile(r"[^a-zA-Z0-9_.:@+-]+")


def _clean(value: str) -> str:
    return _SAFE.sub("-", (value or "").strip())[:120]


@dataclass(frozen=True)
class Identity:
    """The ADK identity derived from a channel + external id."""

    channel: str
    external_id: str
    user_id: str
    session_id: str


def identify(channel: str, external_id: str) -> Identity:
    """Derive the ADK identity for one external conversation."""
    ch = _clean(channel).lower() or "unknown"
    ext = _clean(external_id) or "anonymous"
    return Identity(channel=ch, external_id=ext,
                    user_id=f"{ch}:{ext}",
                    session_id=f"{ch}-{ext}")


def workspace_id(state) -> str:
    """The workspace already bound to this session (``""`` when none)."""
    from shared.workspace import bound_workspace_id

    try:
        return bound_workspace_id(state) or ""
    except Exception:
        return ""


def bind_new_workspace(state, name: str) -> str:
    """Bind a fresh workspace for a first contact and return its id.

    Reuses an existing binding when there is one, so calling this on every turn
    is safe.
    """
    from shared.workspace import bind_workspace, generate_project_id

    existing = workspace_id(state)
    if existing:
        return existing
    pid = generate_project_id(name or "project")
    bind_workspace(state, pid)
    return pid
