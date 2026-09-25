"""Launcher for the connectors service.

``python -m connectors.app`` needs ``my_agents/`` on ``sys.path``; it also needs
the repo's ``src/`` — this checkout's virtualenv has an editable ``.pth`` pointing
at a different (now missing) path, so ``google.adk`` only resolves when ``src`` is
added explicitly. Running this file directly puts ``my_agents/`` on the path, and
the launcher adds ``src`` too, so it works from anywhere:

    .venv/bin/python my_agents/connectors_service.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_MY_AGENTS = Path(__file__).resolve().parent
_REPO = _MY_AGENTS.parent
for _p in (str(_MY_AGENTS), str(_REPO / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# The delivery pipeline re-plans between passes (a run pauses for a human and
# resumes; a stage is re-entered to resolve a change request), so the workflow's
# replay-ordering guard must be advisory — otherwise every re-run dies with
# "Replay divergence detected". Set before the pipeline is imported so no barrier can
# be built in strict mode. /health reports it.
os.environ.setdefault("ADK_REPLAY_BARRIER_ADVISORY", "1")

# `adk web` loads an agent's .env for us; this service does not, so load the
# pipeline's config (LLM_*, FRAPPE_*) and the connectors' own settings. Real
# environment variables still win — nothing already set is overwritten.
try:
    from dotenv import load_dotenv

    load_dotenv(_MY_AGENTS / "delivery_pipeline" / ".env", override=False)
    load_dotenv(_MY_AGENTS / "connectors" / ".env", override=False)
except Exception:  # dotenv is optional at runtime
    pass

from connectors.app import main  # noqa: E402

if __name__ == "__main__":
    main()
