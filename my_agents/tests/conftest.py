"""Shared test fixtures.

The Frappe tools write connection facts into os.environ as a dev-server
turn-visibility fallback (see frappe_tools._remember). That must not leak
between tests, or later tests attempt real network calls to a fake host.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Bootstrap import paths once for the whole suite. Several test modules import
# the agents (`google.adk` from the repo's src/ tree, and the my_agents
# packages) without setting these themselves, which made collection depend on
# which module pytest happened to import first.
_MY_AGENTS = Path(__file__).resolve().parents[1]
for _p in (str(_MY_AGENTS), str(_MY_AGENTS.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@pytest.fixture(autouse=True)
def _isolate_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)
