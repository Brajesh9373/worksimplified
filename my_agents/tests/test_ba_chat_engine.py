"""BA chatbot engine: the deterministic interview state machine.

The LLM is fully stubbed — these tests prove the *engine* rules: greeting,
extract → propose → confirm → fulfill, coded validators, probe budget with
assumptions, classification, review → sign-off → READY.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_chat import engine
from ba_chat.catalogue import ITEMS
from ba_chat.store import Store


class FakeLLM:
    """Scripted model: each turn pops the next canned JSON reply."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, messages, **kwargs):
        self.calls += 1
        if not self.script:
            raise AssertionError("FakeLLM ran out of scripted replies")
        return json.dumps(self.script.pop(0))


def _reply(value="", confirmed=False, text="noted"):
    return {"reply": text, "value": value, "confirmed": confirmed,
            "contradiction": "", "off_topic": False}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("BA_CHAT_DB", str(tmp_path / "s.db"))
    return Store()


@pytest.fixture()
def llm(monkeypatch):
    fake = FakeLLM([])
    monkeypatch.setattr("ba_chat.llm.complete", fake)
    return fake


def test_greeting_on_empty_first_call(store, llm):
    result = engine.handle_message(store, None, "")
    assert "business analyst" in result["reply"]
    assert result["stage"] == "INTAKE"
    assert llm.calls == 0


def test_extract_propose_confirm_fulfill(store, llm):
    llm.script.append(_reply("Sharma ERP", text="Good name — noted: Sharma ERP."))
    first = engine.handle_message(store, None, "Let's call it Sharma ERP")
    assert first["items"][0]["status"] == "proposed"
    assert first["quick_replies"] == ["Yes", "Change"]
    assert llm.calls == 1

    second = engine.handle_message(store, first["session_id"], "yes")
    ids = [i["id"] for i in second["items"]]
    assert second["items"][ids.index("a0")]["status"] == "fulfilled"
    assert second["reply"].startswith("Locked in.")
    assert llm.calls == 1  # confirmation is coded — zero-call turn


def test_coded_validator_rejects_placeholders(store, llm):
    llm.script.append(_reply("TBD for now", text="Okay, noted."))
    first = engine.handle_message(store, None, "TBD for now")
    assert first["items"][0]["status"] == "pending"
    assert first["reply"] == "Okay, noted."


def test_probe_budget_ends_in_assumption(store, llm):
    llm.script.extend([_reply("x", text="Say more?"), _reply("y", text="A little more?")])
    r1 = engine.handle_message(store, None, "x")
    assert r1["items"][0]["status"] == "pending"
    r2 = engine.handle_message(store, r1["session_id"], "y")
    assert r2["items"][0]["status"] == "assumed"
    assert "Moving on" in r2["reply"]


def test_classification_after_viability(store, llm):
    session = store.create()
    for item_id in ("a0", "a1", "a2", "a3", "a4", "a5", "a6"):
        store.set_item(session, item_id, "fulfilled", f"value for {item_id}")
    session = store.get(session["id"])
    llm.script.append({"reply": "ERP it is. Objectives?", "value": "",
                       "confirmed": False, "contradiction": "",
                       "off_topic": False, "project_type": "erp_frappe"})
    result = engine.handle_message(store, session["id"], "we need all of purchase and stock")
    assert result["project_type"] == "erp_frappe"
    assert result["stage"] == "ELICIT"


def test_review_then_signoff_locks_ready(store, llm):
    session = store.create()
    session["project_type"] = "website"
    store.save(session)
    from ba_chat.flow import applicable_items
    for item_id in applicable_items("website"):
        store.set_item(session, item_id, "fulfilled", f"value for {item_id}")
    session = store.get(session["id"])
    assert session["stage"] == "INTAKE"

    review = engine.handle_message(store, session["id"], "that's everything")
    assert review["stage"] == "REVIEW"
    assert "sign off" in review["reply"]

    ready = engine.handle_message(store, session["id"], "sign off")
    assert ready["stage"] == "READY"
    assert ready["done"] is True
    assert "Create project in Frappe" in ready["reply"]
    assert llm.calls == 0  # summary and sign-off are engine-composed/coded


def test_review_corrections_reopen_items(store, llm):
    session = store.create()
    session["project_type"] = "website"
    session["stage"] = "REVIEW"
    store.save(session)
    from ba_chat.flow import applicable_items
    for item_id in applicable_items("website"):
        store.set_item(session, item_id, "fulfilled", f"value for {item_id}")
    llm.script.append({"reply": "Reopening budget.", "reopen": ["a3"],
                       "value": "", "confirmed": False,
                       "contradiction": "", "off_topic": False})
    result = engine.handle_message(store, session["id"], "the budget is actually 8 lakh")
    assert result["stage"] == "ELICIT"
    assert store.get_item(store.get(session["id"]), "a3")["status"] == "pending"


def test_contradiction_is_flagged_and_replayed(store, llm):
    llm.script.append({"reply": "Noted.", "value": "Project Phoenix",
                       "confirmed": False,
                       "contradiction": "a3: budget 5L vs 8L mentioned earlier",
                       "off_topic": False})
    result = engine.handle_message(store, None, "call it Project Phoenix")
    assert "doesn't line up" in result["reply"]
    flags = store.get(result["session_id"])["extra"]["flags"]
    assert any("a3" in f for f in flags)


def test_done_session_stays_done(store, llm):
    session = store.create()
    session["stage"] = "DONE"
    store.save(session)
    result = engine.handle_message(store, session["id"], "hello again")
    assert "already been created" in result["reply"]
    assert llm.calls == 0


def test_silent_retry_on_malformed_model_json(store, monkeypatch):
    texts = ["Sure — here you go, but wrapped in prose with no JSON at all",
             json.dumps({"reply": "Noted: Sharma ERP.", "value": "Sharma ERP",
                         "confirmed": False, "contradiction": "",
                         "off_topic": False})]
    monkeypatch.setattr("ba_chat.llm.complete",
                        lambda messages, **kwargs: texts.pop(0))
    result = engine.handle_message(store, None, "call it Sharma ERP")
    # the user never sees the joinery — the turn still lands proposed
    assert result["items"][0]["status"] == "proposed"
    assert "didn't quite catch" not in result["reply"]


def test_empty_message_after_start_nudges(store, llm):
    llm.script.append({"reply": "Hello! What should we call this project?",
                       "value": "", "confirmed": False,
                       "contradiction": "", "off_topic": False})
    first = engine.handle_message(store, None, "hi")
    second = engine.handle_message(store, first["session_id"], "")
    assert "listening" in second["reply"]
