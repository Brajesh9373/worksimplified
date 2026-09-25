"""Connector identity mapping (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from connectors.identity import bind_new_workspace, identify, workspace_id  # noqa: E402
from shared.project_context import get_context  # noqa: E402


def test_identify_is_deterministic_and_channel_scoped():
    a = identify("telegram", "1555")
    b = identify("telegram", "1555")
    assert a == b
    assert a.user_id == "telegram:1555"
    assert a.session_id == "telegram-1555"

    wa = identify("whatsapp", "919812345678")
    assert wa.user_id == "whatsapp:919812345678"
    assert wa.session_id != a.session_id

    web = identify("web", "abc123")
    assert web.user_id == "web:abc123"


def test_identify_sanitizes_external_ids():
    ident = identify("telegram", " 91 98/12@x ")
    assert " " not in ident.session_id and "/" not in ident.session_id
    assert ident.session_id.startswith("telegram-")


def test_workspace_binds_once_and_is_reused():
    import shutil

    state: dict = {}
    get_context(state)  # the orchestrator's state container
    assert workspace_id(state) == ""

    first = bind_new_workspace(state, "Travel and Expense")
    try:
        assert first
        # a second call must reuse, never create a second project
        assert bind_new_workspace(state, "Something Else") == first
        assert workspace_id(state) == first
    finally:
        shutil.rmtree(Path(MY_AGENTS / "projects" / first), ignore_errors=True)
