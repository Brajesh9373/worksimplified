"""Session persistence for the BA chatbot — SQLite, no ADK, no ORM.

One row per interview session: the transcript (conversation memory), the
requirement board (fact memory — never truncated), probes, assumptions and
the setup-job record. The DB lives under the package's ``.state/`` directory,
which is git-ignored like every other local runtime file.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

_STAGES = ("INTAKE", "CONTEXT", "ELICIT", "REVIEW", "READY", "DONE")

MAX_TURNS = 400  # hard cap: transcript window is trimmed, facts are not


def default_path() -> Path:
    """DB location. ``BA_CHAT_DB`` overrides for tests and containers."""
    override = (os.getenv("BA_CHAT_DB") or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / ".state" / "sessions.db"


class Store:
    """Thin SQLite wrapper. Sessions are plain dicts; callers own the schema."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection per thread: the setup job runs in a worker thread
        # while request threads keep chatting. Sharing a connection across
        # threads corrupts cursors; SQLite serializes the rest itself.
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                   timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "id TEXT PRIMARY KEY, created REAL, updated REAL, "
                "project_type TEXT, stage TEXT, summary TEXT, "
                "items_json TEXT, transcript_json TEXT, extra_json TEXT)"
            )
            conn.commit()
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------- sessions
    def create(self) -> dict:
        now = time.time()
        session = {"id": uuid.uuid4().hex[:12], "created": now, "updated": now,
                   "project_type": None, "stage": "INTAKE", "summary": "",
                   "items": {}, "transcript": [], "extra": {}}
        self._write(session)
        return session

    def get(self, session_id: str) -> dict | None:
        row = self._conn().execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "created": row["created"], "updated": row["updated"],
                "project_type": row["project_type"], "stage": row["stage"] or "INTAKE",
                "summary": row["summary"] or "",
                "items": json.loads(row["items_json"] or "{}"),
                "transcript": json.loads(row["transcript_json"] or "[]"),
                "extra": json.loads(row["extra_json"] or "{}")}

    def save(self, session: dict) -> None:
        session["updated"] = time.time()
        self._write(session)

    def _write(self, session: dict) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO sessions "
            "(id, created, updated, project_type, stage, summary, "
            "items_json, transcript_json, extra_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (session["id"], session.get("created", time.time()),
             session.get("updated", time.time()),
             session.get("project_type"), session.get("stage", "INTAKE"),
             session.get("summary", ""),
             json.dumps(session.get("items", {})),
             json.dumps(session.get("transcript", [])[-MAX_TURNS:]),
             json.dumps(session.get("extra", {}))))
        self._conn().commit()

    # ----------------------------------------------------------------- turns
    def append_turn(self, session: dict, role: str, text: str) -> None:
        transcript = session.setdefault("transcript", [])
        transcript.append({"role": role, "text": text or ""})
        del transcript[:-MAX_TURNS]
        self.save(session)

    # ----------------------------------------------------------------- items
    def set_item(self, session: dict, item_id: str, status: str,
                 value: str = "") -> None:
        items = session.setdefault("items", {})
        entry = items.setdefault(item_id, {"status": "pending", "value": ""})
        entry["status"] = status
        if value:
            entry["value"] = value
        self.save(session)

    def get_item(self, session: dict, item_id: str) -> dict:
        return session.get("items", {}).get(item_id, {"status": "pending", "value": ""})
