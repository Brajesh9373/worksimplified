"""BA chatbot Frappe layer: planners are pure, transport is faked.

No network here — ``run_setup`` is exercised against a scripted
``FrappeClient`` double, including idempotency (existing project/tasks reused)
and the missing-credentials path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_chat import frappe_client as fc


def _session(items):
    return {"id": "s1", "project_type": "erp_frappe",
            "items": {k: {"status": "fulfilled", "value": v}
                      for k, v in items.items()}}


def test_split_list_keeps_content_intact():
    assert fc._split_list("8 lakh budget approved") == ["8 lakh budget approved"]
    assert fc._split_list("- alpha\n- beta\n- gamma") == ["alpha", "beta", "gamma"]
    assert fc._split_list("1. first\n2. second") == ["first", "second"]
    assert fc._split_list("billing, alerts, gst reports") == ["billing", "alerts",
                                                              "gst reports"]
    story = "As a manager, I want alerts, so that shelves stay full."
    assert fc._split_list(story) == [story]  # prose is never shredded


def test_derive_doctypes_only_from_explicit_lists():
    dts = fc._derive_doctypes("Masters: Customer (name, phone, address) and "
                              "Item (code, rate, stock). Everything else standard.")
    assert {d["name"] for d in dts} == {"Customer", "Item"}
    assert fc._derive_doctypes("we have customers and items somewhere") == []
    assert fc._derive_doctypes("") == []


def test_plan_setup_builds_project_and_tasks():
    plan = fc.plan_setup(_session({
        "a0": "Sharma ERP",
        "a1": "stockouts everywhere", "a2": "cut stockouts",
        "c2": ("As a manager, I want alerts, so that shelves stay full.\n"
               "As a clerk, I want fast billing, so that queues shrink."),
        "d1": "billing, alerts, gst reports",
        "c4": "Must have billing; Could have mobile app",
        "d7": "Customer (name, phone)"}))
    assert plan["project_name"] == "Sharma ERP"
    assert len(plan["tasks"]) == 5  # 2 stories + 3 features
    assert all(t["priority"] == "High" for t in plan["tasks"])
    assert [d["name"] for d in plan["doctypes"]] == ["Customer"]


class FakeFrappe:
    """Scripted transport double; records what the job asked for."""

    existing_project = False
    existing_tasks: set = set()

    def __init__(self, *a, **k):
        self.calls = []

    def close(self):
        pass

    def login(self):
        return {"ok": True, "user": "Administrator", "host": "http://x:9000"}

    def ensure_project(self, name, description=""):
        self.calls.append(("project", name))
        return {"ok": True, "name": name,
                "created": not type(self).existing_project}

    def ensure_task(self, project, subject, description="", priority="Medium"):
        self.calls.append(("task", subject))
        if subject in type(self).existing_tasks:
            return {"ok": True, "name": "TASK-1", "created": False}
        return {"ok": True, "name": "TASK-9", "created": True}

    def ensure_doctype(self, doctype, fields):
        self.calls.append(("doctype", doctype))
        return {"ok": True, "name": doctype, "created": True}

    def _get(self, path, **params):  # read-back verification
        return {"ok": True, "data": [{"name": "TASK-1"}, {"name": "TASK-9"}]}


@pytest.fixture()
def creds(monkeypatch):
    monkeypatch.setenv("FRAPPE_BASE_URL", "http://x:9000")
    monkeypatch.setenv("FRAPPE_USERNAME", "Administrator")
    monkeypatch.setenv("FRAPPE_PASSWORD", "pw")


def test_run_setup_full_path(monkeypatch, creds):
    monkeypatch.setattr(fc, "FrappeClient", FakeFrappe)
    FakeFrappe.existing_project = False
    FakeFrappe.existing_tasks = set()
    session = _session({"a0": "Sharma ERP", "a1": "pain", "a2": "goal",
                        "c2": "As a manager, I want alerts, so that shelves stay full.",
                        "d1": "alerts", "c4": "Must have alerts", "d7": ""})
    log: list = []
    outcome = fc.run_setup(session, log)
    assert outcome["ok"] is True
    assert outcome["project"] == "Sharma ERP"
    assert outcome["tasks_created"] == 2
    assert outcome["tasks_total"] == 2
    assert any("nothing guessed" in line for line in log)
    assert any("Verified by read-back" in line for line in log)


def test_run_setup_reuses_existing(monkeypatch, creds):
    monkeypatch.setattr(fc, "FrappeClient", FakeFrappe)
    FakeFrappe.existing_project = True
    FakeFrappe.existing_tasks = {"As a manager, I want alerts, so that shelves stay full."}
    session = _session({"a0": "Sharma ERP", "a1": "pain", "a2": "goal",
                        "c2": "As a manager, I want alerts, so that shelves stay full.",
                        "d1": "", "c4": "", "d7": ""})
    log: list = []
    outcome = fc.run_setup(session, log)
    assert outcome["ok"] is True
    assert outcome["tasks_created"] == 0
    assert any("already existed — reused" in line for line in log)


def test_run_setup_without_credentials(monkeypatch):
    monkeypatch.delenv("FRAPPE_BASE_URL", raising=False)
    monkeypatch.delenv("FRAPPE_USERNAME", raising=False)
    monkeypatch.delenv("FRAPPE_PASSWORD", raising=False)
    outcome = fc.run_setup(_session({"a0": "X"}), [])
    assert outcome["ok"] is False
    assert "not configured" in outcome["error"]


def test_run_setup_login_failure(monkeypatch, creds):
    class BadLogin(FakeFrappe):
        def login(self):
            return {"ok": False, "error": "Frappe login failed (HTTP 401)"}

    monkeypatch.setattr(fc, "FrappeClient", BadLogin)
    outcome = fc.run_setup(_session({"a0": "X"}), [])
    assert outcome["ok"] is False
    assert "401" in outcome["error"]
