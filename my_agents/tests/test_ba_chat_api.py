"""BA chatbot HTTP surface: chat/state/flow/confirm/create-project/job/export.

LLM and Frappe are both stubbed — these tests prove routing, the sign-off
gate (409 before READY), job progress polling and the BRD-draft download.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_chat import api as ba_api
from ba_chat.flow import applicable_items


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BA_CHAT_DB", str(tmp_path / "s.db"))
    ba_api._store = None
    ba_api._jobs.clear()
    app = FastAPI()
    app.include_router(ba_api.router)
    yield TestClient(app)
    ba_api._store = None
    ba_api._jobs.clear()


@pytest.fixture()
def llm(monkeypatch):
    calls = {"n": 0}
    script = []

    def fake(messages, **kwargs):
        calls["n"] += 1
        return json.dumps(script.pop(0))

    monkeypatch.setattr("ba_chat.llm.complete", fake)
    return script, calls


def _ready_session(client, ptype="website"):
    """Drive a session to READY through the real chat route with a stub LLM."""
    chat = client.post("/api/ba/chat", json={"message": ""}).json()
    sid = chat["session_id"]
    store = ba_api.get_store()
    session = store.get(sid)
    session["project_type"] = ptype
    store.save(session)
    for item_id in applicable_items(ptype):
        store.set_item(session, item_id, "fulfilled", f"value for {item_id}")
    client.post("/api/ba/chat", json={"session_id": sid, "message": "all done"})
    return client.post("/api/ba/chat",
                       json={"session_id": sid, "message": "sign off"}).json()


def test_chat_roundtrip_and_state_flow(client, llm):
    script, _ = llm
    script.append({"reply": "Noted: Acme CRM.", "value": "Acme CRM",
                   "confirmed": False, "contradiction": "", "off_topic": False})
    body = client.post("/api/ba/chat", json={"message": "call it Acme CRM"}).json()
    assert body["ok"] is True
    sid = body["session_id"]
    assert body["items"][0] == {"id": "a0", "group": "A", "label": "Project name",
                                "status": "proposed"}
    assert body["flow"]["pct"] == 0
    assert body["coverage"]["total"] == 7  # viability only, pre-classification

    state = client.get("/api/ba/state", params={"session_id": sid}).json()
    assert state["ok"] is True and state["stage"] == "INTAKE"
    assert state["items"][0]["status"] == "proposed"

    flow = client.get("/api/ba/flow", params={"session_id": sid}).json()
    assert flow["ok"] and len(flow["nodes"]) == 7


def test_confirm_buttons_fulfill_or_reopen(client, llm):
    script, _ = llm
    script.append({"reply": "Noted.", "value": "Acme CRM",
                   "confirmed": False, "contradiction": "", "off_topic": False})
    body = client.post("/api/ba/chat", json={"message": "Acme CRM"}).json()
    sid = body["session_id"]

    ok = client.post("/api/ba/confirm",
                     json={"session_id": sid, "item_id": "a0", "confirmed": True}).json()
    assert ok["ok"] is True
    assert "what is not working today" in ok["reply"]  # moves to the next question

    # confirming twice is a conflict — nothing is proposed any more
    again = client.post("/api/ba/confirm",
                        json={"session_id": sid, "item_id": "a0", "confirmed": True})
    assert again.status_code == 409


def test_catalogue_lists_groups_and_labels(client):
    body = client.get("/api/ba/catalogue").json()
    assert body["ok"] is True
    assert [g["id"] for g in body["groups"]] == ["A", "B", "C", "D", "E", "F", "G"]
    assert any(i["id"] == "c2" for i in body["items"])


def test_create_project_gated_until_signoff(client, llm):
    script, _ = llm
    script.append({"reply": "Noted.", "value": "Acme CRM",
                   "confirmed": False, "contradiction": "", "off_topic": False})
    body = client.post("/api/ba/chat", json={"message": "Acme CRM"}).json()
    resp = client.post("/api/ba/create-project",
                       json={"session_id": body["session_id"]})
    assert resp.status_code == 409
    assert "signed off" in resp.json()["error"]


def test_create_project_runs_job_and_reports_progress(client, llm, monkeypatch):
    ready = _ready_session(client)
    assert ready["done"] is True
    sid = ready["session_id"]

    monkeypatch.setattr(
        "ba_chat.api.run_setup",
        lambda session, log: (log.append("fake build ok"),
                              {"ok": True, "project": "Acme", "tasks_created": 3,
                               "tasks_total": 3, "doctypes": []})[1])

    started = client.post("/api/ba/create-project",
                          json={"session_id": sid}).json()
    assert started["ok"] is True and started["status"] == "running"

    deadline = time.time() + 5
    final = {}
    while time.time() < deadline:
        final = client.get("/api/ba/job",
                           params={"job_id": started["job_id"]}).json()
        if final.get("status") == "done":
            break
        time.sleep(0.02)
    assert final.get("status") == "done"
    assert final["project"] == "Acme"
    assert "fake build ok" in final["log"]

    # second call reuses the finished job instead of rebuilding
    again = client.post("/api/ba/create-project",
                        json={"session_id": sid}).json()
    assert again.get("reused") is True


def test_export_downloads_brd_draft(client, llm):
    ready = _ready_session(client)
    resp = client.get("/api/ba/export",
                      params={"session_id": ready["session_id"]})
    assert resp.status_code == 200
    assert "Business Requirements" in resp.text
    assert "attachment" in resp.headers["content-disposition"]


def test_unknown_session_is_404(client):
    assert client.get("/api/ba/state",
                      params={"session_id": "nope"}).status_code == 404
    assert client.get("/api/ba/job", params={"job_id": "nope"}).status_code == 404


def test_llm_status_never_leaks_the_key(client, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "super-secret-key-12345")
    body = client.get("/api/ba/llm-status").json()
    assert body["ok"] is True
    assert "super-secret-key-12345" not in body["key"]
    assert "2345" in body["key"]
