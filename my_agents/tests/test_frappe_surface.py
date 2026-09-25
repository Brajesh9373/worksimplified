"""Frappe demo-surface tools: seed_records + ensure_workspace (no network, no LLM).

An in-memory fake site is injected through httpx.MockTransport so the real tool
code (auth, create, filters, read-back, evidence) is exercised end-to-end.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from shared import frappe_tools as FT  # noqa: E402
from shared.project_context import get_context  # noqa: E402


class FakeTC:
    def __init__(self, state=None):
        self.state = state if state is not None else {}
        self.session = type("S", (), {"id": "surface"})()


class FakeSite:
    """Minimal Frappe surface: documents with filter-aware list, and Workspace."""

    def __init__(self):
        self.docs: dict[str, dict[str, dict]] = {}
        self.workspaces: dict[str, dict] = {}
        self.fail_post = False
        self._n = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        p, m = request.url.path, request.method
        if p == "/api/method/login" and m == "POST":
            return httpx.Response(200, json={"message": "Logged In", "full_name": "Administrator"},
                                  headers={"set-cookie": "sid=fake; Path=/"})
        params = dict(request.url.params)

        def _filters():
            try:
                flt = json.loads(params.get("filters", "[]"))
                return flt[0] if flt else None
            except Exception:
                return None

        if p == "/api/resource/Module Def":
            return httpx.Response(200, json={"data": [{"name": "Custom"}, {"name": "Core"}]})

        if p == "/api/resource/Workspace":
            if m == "POST":
                body = json.loads(request.content or b"{}")
                name = body.get("title", "Workspace")
                self.workspaces[name] = body
                return httpx.Response(200, json={"data": {**body, "name": name}})
            f = _filters()
            rows = [{"name": k} for k, b in self.workspaces.items()
                    if not f or b.get(f[0]) == f[2]]
            return httpx.Response(200, json={"data": rows})
        if p.startswith("/api/resource/Workspace/"):
            name = p.rsplit("/", 1)[-1]
            if name not in self.workspaces:
                return httpx.Response(404, json={"exc": "missing"})
            if m == "PUT":
                self.workspaces[name].update(json.loads(request.content or b"{}"))
            return httpx.Response(200, json={"data": {**self.workspaces[name], "name": name}})

        if p.startswith("/api/resource/"):
            parts = p[len("/api/resource/"):].split("/")
            dt = parts[0]
            self.docs.setdefault(dt, {})
            if len(parts) == 1:
                if m == "POST":
                    if self.fail_post:
                        return httpx.Response(417, text="ValidationError: bad field")
                    body = json.loads(request.content or b"{}")
                    self._n += 1
                    name = f"{dt.replace(' ', '')}-{self._n:04d}"
                    self.docs[dt][name] = {**body, "name": name}
                    return httpx.Response(200, json={"data": self.docs[dt][name]})
                f = _filters()
                rows = list(self.docs[dt].values())
                if f:
                    rows = [r for r in rows if r.get(f[0]) == f[2]]
                return httpx.Response(200, json={"data": rows})
            name = parts[1]
            if name not in self.docs[dt]:
                return httpx.Response(404, json={"exc": "missing"})
            return httpx.Response(200, json={"data": self.docs[dt][name]})
        return httpx.Response(404, json={"exc": f"no route {m} {p}"})


@pytest.fixture()
def site(monkeypatch):
    s = FakeSite()
    orig = httpx.Client

    def make_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(s.handler)
        return orig(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", make_client)
    return s


@pytest.fixture()
def tc():
    state = {"frappe_base_url": "http://fake.local", "frappe_username": "Administrator",
             "frappe_password": "pw"}
    get_context(state)
    return FakeTC(state=state)


def _evidence(tc):
    return get_context(tc.state)["frappe_state"]["evidence"]


def test_seed_records_creates_then_reuses(site, tc):
    records = json.dumps([{"requester": "A", "state": "Pending"},
                          {"requester": "B", "state": "Approved"},
                          {"requester": "C", "state": "Rejected"}])
    first = FT.seed_records("Travel Request", records, tc, key_field="requester",
                            traces_json=json.dumps(["US-001"]))
    assert first["ok"] and first["created_count"] == 3 and first["reused_count"] == 0
    assert len(site.docs["Travel Request"]) == 3

    second = FT.seed_records("Travel Request", records, tc, key_field="requester")
    assert second["ok"] and second["created_count"] == 0 and second["reused_count"] == 3
    assert len(site.docs["Travel Request"]) == 3, "re-run must not duplicate"


def test_seed_records_rejects_empty(site, tc):
    res = FT.seed_records("Travel Request", "[]", tc)
    assert res["ok"] is False and "non-empty" in res["error"]


def test_seed_records_reports_failures(site, tc):
    site.fail_post = True
    res = FT.seed_records("Travel Request", json.dumps([{"requester": "A"}]), tc,
                          key_field="requester")
    assert res["ok"] is False and res["failed"]
    assert "417" in res["failed"][0]["error"]
    assert any(e.get("kind") == "seed" and not e.get("ok") for e in _evidence(tc))


def test_seed_records_evidence_recorded(site, tc):
    FT.seed_records("Travel Request", json.dumps([{"requester": "A"}]), tc,
                    key_field="requester", traces_json=json.dumps(["FR-002"]))
    seeds = [e for e in _evidence(tc) if e.get("kind") == "seed"]
    assert seeds and seeds[0]["created"] == 1 and seeds[0]["traces"] == ["FR-002"]


def test_ensure_workspace_creates_then_updates(site, tc):
    first = FT.ensure_workspace("Travel and Expense", json.dumps(["Travel Request"]), tc)
    assert first["ok"] and first["action"] == "created" and first["shortcuts"] == 1
    assert first["module"] == "Custom"

    second = FT.ensure_workspace("Travel and Expense",
                                 json.dumps(["Travel Request", "Expense Claim"]), tc)
    assert second["ok"] and second["action"] == "updated" and second["shortcuts"] == 2
    assert len(site.workspaces) == 1, "idempotent by title"
    assert any(e.get("kind") == "workspace" for e in _evidence(tc))


def test_ensure_workspace_rejects_empty(site, tc):
    res = FT.ensure_workspace("X", "[]", tc)
    assert res["ok"] is False
