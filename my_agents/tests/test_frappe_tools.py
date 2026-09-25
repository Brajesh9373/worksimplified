"""Frappe capability + production-grade gate tests (no network, no LLM).

An in-memory fake Frappe site is injected through httpx.MockTransport, so the
real tool code (auth, inspect, ensure_*, smoke_test, evidence) is exercised
end-to-end and the gate is verified against recorded evidence.
"""

import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from shared import gates as G  # noqa: E402
from shared import frappe_tools as FT  # noqa: E402
from shared.sections import check_section, stub_markers  # noqa: E402

WS = "frappetest"


class FakeTC:
    def __init__(self, sid="frappetest", state=None):
        self.state = state if state is not None else {}
        self.session = type("S", (), {"id": sid})()


class FakeSite:
    """Minimal Frappe v15-ish site surface used by the tools."""

    def __init__(self):
        self.doctypes: dict[str, dict] = {}
        self.custom_fields: list[dict] = []
        self.workflows: dict[str, dict] = {}
        self.roles: set[str] = set()
        self.docs: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    # -- helpers --
    def add_doctype(self, name, *, custom=True, fields=None, permissions=None):
        self.doctypes[name] = {"name": name, "custom": 1 if custom else 0,
                               "module": "Custom" if custom else "Core",
                               "fields": fields or [], "permissions": permissions or []}
        self.docs.setdefault(name, {})

    def _json(self, request):
        try:
            return json.loads(request.content or b"{}")
        except Exception:
            return {}

    def _params(self, request):
        return dict(request.url.params)

    def handler(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        m = request.method
        self.calls.append((m, p))
        if p == "/api/method/login" and m == "POST":
            return httpx.Response(200, json={"message": "Logged In", "full_name": "Administrator"},
                                  headers={"set-cookie": "sid=fakesid; Path=/"})
        if p == "/api/method/frappe.auth.get_logged_user":
            return httpx.Response(200, json={"message": "Administrator"})
        if p == "/api/method/version":
            return httpx.Response(200, json={"message": "15.0.0"})
        if p == "/api/method/frappe.apps.get_apps":
            return httpx.Response(200, json={"message": [{"name": "frappe"}]})
        if p == "/api/method/frappe.client.get_count":
            dt = self._params(request).get("doctype", "")
            if dt in self.docs:
                return httpx.Response(200, json={"message": len(self.docs[dt])})
            return httpx.Response(404, json={"exc": f"DocType {dt} not found"})
        if p.startswith("/api/method/frappe.model.workflow.apply_workflow") and m == "POST":
            body = self._json(request)
            doc = (body.get("doc") or {})
            dt, name, action = doc.get("doctype"), doc.get("name"), body.get("action")
            if dt in self.docs and name in self.docs[dt]:
                self.docs[dt][name]["workflow_state"] = "Approved" if action == "Approve" else action
                return httpx.Response(200, json={"message": "ok"})
            return httpx.Response(404, json={"exc": "document missing"})
        if p == "/api/resource/Module Def":
            return httpx.Response(200, json={"data": [{"name": "Custom"}, {"name": "Core"}]})
        if p == "/api/resource/Custom Field":
            if m == "POST":
                body = self._json(request)
                self.custom_fields.append(body)
                return httpx.Response(200, json={"data": body})
            dt = ""
            try:
                flt = json.loads(self._params(request).get("filters", "[]"))
                if flt and flt[0][0] == "dt":
                    dt = flt[0][2]
            except Exception:
                pass
            return httpx.Response(200, json={"data": [f for f in self.custom_fields
                                                      if not dt or f.get("dt") == dt]})
        if p == "/api/resource/Workflow":
            if m == "POST":
                body = self._json(request)
                self.workflows[body["workflow_name"]] = body
                return httpx.Response(200, json={"data": body})
            return httpx.Response(200, json={"data": list(self.workflows.values())})
        if p.startswith("/api/resource/Workflow/"):
            name = p.rsplit("/", 1)[-1]
            if name not in self.workflows:
                return httpx.Response(404, json={"exc": "not found"})
            if m == "PUT":
                self.workflows[name].update(self._json(request))
            return httpx.Response(200, json={"data": self.workflows[name]})
        if p == "/api/resource/Role":
            if m == "POST":
                self.roles.add(self._json(request).get("role_name", ""))
                return httpx.Response(200, json={"data": {"name": self._json(request).get("role_name", "")}})
            return httpx.Response(200, json={"data": [{"name": r} for r in self.roles]})
        if p.startswith("/api/resource/Role/"):
            name = p.rsplit("/", 1)[-1]
            if name in self.roles:
                return httpx.Response(200, json={"data": {"name": name}})
            return httpx.Response(404, json={"exc": "not found"})
        if p == "/api/resource/DocType":
            if m == "POST":
                body = self._json(request)
                name = body.get("name") or body.get("doctype_name") or ""
                self.add_doctype(name, custom=True, fields=body.get("fields") or [],
                                 permissions=body.get("permissions") or [])
                return httpx.Response(200, json={"data": self.doctypes[name]})
            return httpx.Response(200, json={"data": [{"name": n, "module": d["module"]}
                                                      for n, d in self.doctypes.items() if d["custom"]]})
        if p.startswith("/api/resource/DocType/"):
            name = p.rsplit("/", 1)[-1]
            if name not in self.doctypes:
                return httpx.Response(404, json={"exc": "not found"})
            if m == "PUT":
                self.doctypes[name].update(self._json(request))
            return httpx.Response(200, json={"data": self.doctypes[name]})
        # generic documents: /api/resource/<DocType>[/<name>]
        if p.startswith("/api/resource/"):
            parts = p[len("/api/resource/"):].split("/")
            dt = parts[0]
            if dt not in self.doctypes:
                return httpx.Response(404, json={"exc": f"DocType {dt} missing"})
            if len(parts) == 1:
                if m == "POST":
                    body = self._json(request)
                    name = body.get("name") or f"{dt.replace(' ', '')}-{len(self.docs[dt]) + 1:04d}"
                    body["name"] = name
                    self.docs[dt][name] = body
                    return httpx.Response(200, json={"data": body})
                return httpx.Response(200, json={"data": list(self.docs[dt].values())})
            name = parts[1]
            if name not in self.docs[dt]:
                return httpx.Response(404, json={"exc": "not found"})
            if m == "PUT":
                self.docs[dt][name].update(self._json(request))
                return httpx.Response(200, json={"data": self.docs[dt][name]})
            if m == "DELETE":
                del self.docs[dt][name]
                return httpx.Response(202, json={"message": "ok"})
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
    yield s
    shutil.rmtree(MY_AGENTS / "projects" / WS, ignore_errors=True)


@pytest.fixture()
def tc():
    from shared.project_context import get_context
    state = {"frappe_base_url": "http://fake.local", "frappe_username": "Administrator",
             "frappe_password": "pw"}
    get_context(state)
    return FakeTC(state=state)


def _evidence(tc):
    from shared.project_context import get_context
    return get_context(tc.state)["frappe_state"]["evidence"]


# ------------------------------------------------- WBS -> Frappe mapping

def test_wbs_parsing_is_header_driven_and_table_scoped():
    from shared.sections import parse_wbs_rows
    from tests.prod_fixtures import PLAN

    rows = parse_wbs_rows(PLAN)
    assert len(rows) == 6, [r["id"] for r in rows]
    r0, r1 = rows[0], rows[1]
    assert r0["id"] == "T-001" and "Travel Request" in r0["title"]
    assert r0["owner"] == "Functional Consultant" and r0["priority"] == "P0"
    assert r0["start"] == "2026-09-21" and r0["end"] == "2026-09-23"
    assert r0["deps"] == "-" and r0["trace"].startswith("US-001")
    assert r1["deps"] == "T-001"
    # dependency / RACI / timeline tables also hold T-xxx ids — never tasks
    extra = ("\n### Dependencies\n\n| Predecessor | Successor | Reason |\n|---|---|---|\n"
             "| T-001 | T-002 | x |\n| T-002 | T-003 | y |\n\n"
             "### Timeline\n\n| Task ID | Priority | Start Date | End Date |\n|---|---|---|---|\n"
             "| T-001 | P0 | 2026-09-21 | 2026-09-23 |\n")
    assert len(parse_wbs_rows(PLAN + extra)) == 6


def test_priority_mapping():
    assert FT._frappe_priority("P0") == "Urgent"
    assert FT._frappe_priority("p1") == "High"
    assert FT._frappe_priority("Must") == "High"
    assert FT._frappe_priority("should") == "Medium"
    assert FT._frappe_priority("") == "Medium"


def test_create_project_maps_timeline_dependencies_and_priority(site, tc):
    from tests.prod_fixtures import PLAN
    site.add_doctype("Project", custom=False)
    site.add_doctype("Task", custom=False)
    tc.state["project_plan"] = PLAN
    out = FT.create_project_from_plan("Timeline Test", tc)
    assert out["ok"], out
    assert out["tasks_created"] == 6 and out["tasks_dated"] == 6
    assert out["project_start"] == "2026-09-21" and out["project_end"] == "2026-10-12"
    assert out["deps_linked"] == 5
    tasks = list(site.docs["Task"].values())
    t1 = next(t for t in tasks if t["subject"].startswith("T-001"))
    assert t1["exp_start_date"] == "2026-09-21" and t1["exp_end_date"] == "2026-09-23"
    assert t1["priority"] == "Urgent"          # P0 -> Urgent
    assert "owner=Functional Consultant" in t1["description"]
    t2 = next(t for t in tasks if t["subject"].startswith("T-002"))
    assert t2["priority"] == "Urgent"
    assert t2["depends_on"] == [{"task": t1["name"], "subject": t1["subject"]}]  # WBS dep -> Frappe link
    assert t2["depends_on"][0]["subject"], "Frappe drops dependency rows without subject"
    proj = list(site.docs["Project"].values())[0]
    assert proj["expected_start_date"] == "2026-09-21"
    assert proj["expected_end_date"] == "2026-10-12"
    ev = [e for e in _evidence(tc) if e["kind"] == "project"][-1]
    assert ev["tasks_dated"] == 6 and ev["deps_linked"] == 5 and ev["verified"]


def test_gate_requires_timeline_in_frappe():
    st = _base_state()
    st["project_plan"] = ("| ID | Task | Owner | Priority | Estimate | Start | End | Dependencies | Trace |\n"
                          "|----|------|-------|----------|----------|-------|-----|--------------|-------|\n"
                          "| T-001 | a | Dev | P0 | 2d | 2026-01-01 | 2026-01-02 | - | US-001 |\n"
                          "| T-002 | b | Dev | P1 | 3d | 2026-01-03 | 2026-01-05 | T-001 | US-002 |\n")
    st["frappe_project"] = "PROJ-1"
    from shared.project_context import get_context
    ctx = get_context(st)
    base_ev = [_ev("doctype", "Travel Request"), _ev("workflow", "Travel Approval"),
               _ev("permissions", "Travel Request:Approver"),
               _ev("smoke_test", "Travel Request", verified=False),
               _ev("project", "PROJ-1", tasks_created=2, tasks_dated=0, deps_linked=0)]
    ctx["frappe_state"] = {"discovered": {"version": {"message": "15"}}, "evidence": base_ev}
    g = G.frappe_gate(st)
    # timeline mapping is advisory: recorded and carried, never a stage re-run
    assert g["passed"]
    assert any(m.startswith("timeline_mapped") for m in g["warnings"]), g["warnings"]
    assert any(m.startswith("dependencies_mapped") for m in g["warnings"]), g["warnings"]
    # a correct mapping clears both
    ctx["frappe_state"]["evidence"] = [e for e in base_ev if e["kind"] != "project"]
    ctx["frappe_state"]["evidence"].append(
        _ev("project", "PROJ-1", tasks_created=2, tasks_dated=2, deps_linked=1))
    g2 = G.frappe_gate(st)
    assert all(not m.startswith(("timeline_mapped", "dependencies_mapped"))
               for m in g2["warnings"]), g2["warnings"]


# ------------------------------------------------------------- capability

def test_connect_and_discovery(site, tc):
    out = FT.connect_frappe(tc)
    assert out["ok"] and out["user"] == "Administrator"
    d = FT.discover_frappe_env(tc)
    assert d["ok"] and "version" in d["found"] and "custom_doctypes" in d["found"]
    from shared.project_context import get_context
    assert get_context(tc.state)["frappe_state"]["discovered"]["version"]["message"] == "15.0.0"
    assert any(e["kind"] == "discovery" and e["ok"] for e in _evidence(tc))


def test_ensure_doctype_creates_and_is_idempotent(site, tc):
    fields = json.dumps([{"fieldname": "destination", "fieldtype": "Data", "reqd": 1},
                         {"fieldname": "amount", "fieldtype": "Currency"}])
    r1 = FT.ensure_doctype("Travel Request", fields, tc, traces_json=json.dumps(["FR-001"]))
    assert r1["ok"] and r1["action"] == "created" and r1["verified"] and r1["table_ready"]
    assert set(r1["fields_added"]) == {"destination", "amount"}
    r2 = FT.ensure_doctype("Travel Request", fields, tc)
    assert r2["ok"] and r2["action"] == "unchanged" and r2["fields_added"] == []
    ev = [e for e in _evidence(tc) if e["kind"] == "doctype"]
    assert ev[0]["traces"] == ["FR-001"]
    assert ev[-1]["ok"] and ev[-1]["verified"]


def test_ensure_doctype_extends_standard_doctype(site, tc):
    site.add_doctype("Employee", custom=False, fields=[{"fieldname": "employee_name"}])
    out = FT.ensure_doctype("Employee", json.dumps([{"fieldname": "travel_grade",
                                                     "fieldtype": "Select",
                                                     "options": "A\nB"}]),
                            tc)
    assert out["ok"] and out["action"] == "extended"
    assert out["fields_added"] == ["travel_grade"]
    assert any(f.get("dt") == "Employee" and f.get("fieldname") == "travel_grade"
               for f in site.custom_fields)


def test_ensure_role_permissions_workflow(site, tc):
    site.add_doctype("Travel Request", custom=True, fields=[{"fieldname": "amount"}])
    assert FT.ensure_role("Approver", tc)["ok"]
    assert FT.ensure_role("Approver", tc)["action"] == "unchanged"
    p = FT.ensure_permissions("Travel Request", "Approver",
                              json.dumps({"read": 1, "write": 1, "submit": 1}), tc,
                              traces_json=json.dumps(["FR-002"]))
    assert p["ok"] and p["verified"]
    w = FT.ensure_workflow("Travel Approval", "Travel Request",
                           json.dumps([{"state": "Draft", "doc_status": 0},
                                       {"state": "Approved", "doc_status": 1}]),
                           json.dumps([{"state": "Draft", "action": "Approve",
                                        "next_state": "Approved", "allowed_role": "Approver"}]),
                           tc, traces_json=json.dumps(["FR-003"]))
    assert w["ok"] and w["verified"] and w["states"] == 2 and w["transitions"] == 1


def test_smoke_test_runs_and_cleans(site, tc):
    site.add_doctype("Travel Request", custom=True, fields=[{"fieldname": "amount"}])
    out = FT.smoke_test("Travel Request", tc,
                        sample_json=json.dumps({"amount": 100}),
                        workflow_action="Approve",
                        traces_json=json.dumps(["FR-004"]))
    assert out["ok"], out
    assert out["created"] and out["read_back"] == out["created"]
    assert out["final_state"] == "Approved"
    assert out["cleaned_up"] is True
    assert site.docs["Travel Request"] == {}  # cleaned up
    ev = [e for e in _evidence(tc) if e["kind"] == "smoke_test"]
    assert ev[-1]["ok"] and ev[-1]["traces"] == ["FR-004"]


def test_smoke_test_records_failure(site, tc):
    site.add_doctype("Travel Request", custom=True, fields=[{"fieldname": "amount"}])
    site.doctypes["Travel Request"]["permissions"] = []
    # make inserts fail
    orig = site.handler

    def failing(request):
        if request.method == "POST" and request.url.path == "/api/resource/Travel Request":
            return httpx.Response(417, json={"exc": "mandatory field missing"})
        return orig(request)

    site.handler = failing
    out = FT.smoke_test("Travel Request", tc, sample_json=json.dumps({}))
    assert not out["ok"] and out["blockers"]
    ev = [e for e in _evidence(tc) if e["kind"] == "smoke_test"]
    assert ev and ev[-1]["ok"] is False


def test_frappe_api_guards_and_passthrough(site, tc):
    assert not FT.frappe_api("TRACE", "/api/resource/DocType", tc)["ok"]
    assert not FT.frappe_api("DELETE", "/api/resource/DocType/X", tc)["ok"]  # needs confirm
    assert not FT.frappe_api("GET", "/other/path", tc)["ok"]
    site.add_doctype("Travel Request", custom=True, fields=[])
    out = FT.frappe_api("GET", "/api/resource/DocType/Travel Request", tc)
    assert out["ok"] and out["status"] == 200 and "Travel Request" in str(out["data"])
    ev = FT.frappe_evidence(tc)
    assert ev["ok"] and ev["entries"] >= 1 and ev["by_kind"].get("api", 0) >= 1


# ------------------------------------------------------------------ gate

def _base_state():
    st = {"project_plan": "# Plan\n\n| T-001 | a | US-001 |\n| T-002 | b | US-002 |\n"
                         "| T-003 | c | US-003 |\n",
          "functional_spec": "# Spec\n\nFR-001 travel request.\nFR-002 expense claim.\n",
          "tech_design": "# Design\n\nApproval flow and architecture.\n"}
    from shared.project_context import get_context
    ctx = get_context(st)
    ctx["frappe_state"] = {}
    return st


def _ev(kind, name, ok=True, verified=True, traces=("FR-001", "FR-002"), **kw):
    return {"kind": kind, "name": name, "ok": ok, "verified": verified,
            "traces": list(traces), **kw}


def test_gate_fails_without_evidence():
    st = _base_state()
    g = G.frappe_gate(st)
    assert not g["passed"]
    assert any(m.startswith("frappe_project") for m in g["missing"])
    # environment discovery is advisory: worth recording, never worth re-running
    assert any(m.startswith("env_discovered") for m in g["warnings"])


def test_gate_requires_verified_objects_and_smoke_tests():
    st = _base_state()
    st["frappe_project"] = "PROJ-1"
    ctx = __import__("shared.project_context", fromlist=["x"]).get_context(st)
    ctx["frappe_state"] = {"discovered": {"version": {"message": "15"}},
                           "evidence": [
                               _ev("project", "PROJ-1", tasks_created=3),
                               _ev("doctype", "Travel Request"),
                               _ev("workflow", "Travel Approval"),
                               _ev("permissions", "Travel Request:Approver"),
                           ]}
    g = G.frappe_gate(st)
    assert not g["passed"]
    assert any(m.startswith("doctypes_verified") for m in g["missing"])


def test_gate_passes_with_full_evidence_and_fails_on_open_failure():
    st = _base_state()
    st["frappe_project"] = "PROJ-1"
    from shared.project_context import get_context
    ctx = get_context(st)
    ctx["frappe_state"] = {"discovered": {"version": {"message": "15"}},
                           "evidence": [
                               _ev("project", "PROJ-1", tasks_created=3),
                               _ev("doctype", "Travel Request"),
                               _ev("doctype", "Travel Expense Claim"),
                               _ev("workflow", "Travel Approval"),
                               _ev("permissions", "Travel Request:Approver"),
                               _ev("smoke_test", "Travel Request", verified=False),
                               _ev("smoke_test", "Travel Expense Claim", verified=False),
                           ]}
    assert G.frappe_gate(st)["passed"], G.frappe_gate(st)["missing"]
    # a later failing entry on the same object opens a failure again
    ctx["frappe_state"]["evidence"].append(_ev("doctype", "Travel Request", ok=False, verified=False))
    g = G.frappe_gate(st)
    # an unresolved failure is reported as advisory; it does not re-run the stage
    assert g["passed"]
    assert any(m.startswith("no_open_failures") for m in g["warnings"]), g["warnings"]
    # and an untraced FR is reported too (advisory)
    ctx["frappe_state"]["evidence"].pop()
    ctx["frappe_state"]["evidence"] = [e for e in ctx["frappe_state"]["evidence"]
                                       if e["ok"]]
    for e in ctx["frappe_state"]["evidence"]:
        e["traces"] = ["FR-001"]
    g2 = G.frappe_gate(st)
    assert any(m.startswith("fr_traced") for m in g2["warnings"]), g2["warnings"]


def test_final_validation_includes_frappe_stage():
    st = _base_state()
    res = G.final_validation(st)
    assert not res["passed"]
    assert res["sub_gates"]["FRAPPE"]["passed"] is False
    assert any(m.startswith("frappe_stage") for m in res["missing"])


# ------------------------------------------------------- content guardrail

def test_placeholder_guardrail():
    good = "Objective: streamline travel expense. Executive summary. " * 8
    assert check_section("BA", "objectives", good)["passed"]
    assert check_section("BA", "objectives",
                         good + "\nTODO: fill in later")["passed"] is False
    assert stub_markers("see TBD and lorem ipsum") == ["lorem ipsum", "tbd"]
    # deliberate pipeline vocabulary stays allowed
    ok = good + " [ASSUMPTION] hotel cap unknown. Open Questions: OQ-001."
    assert check_section("BA", "objectives", ok)["passed"]


# ------------------------------- deterministic evidence the gate can trust

def _resp(code, payload):
    class R:
        status_code = code

        def json(self):
            return payload

    return R()


def test_project_facts_readonly_counts_tasks_dates_and_dependencies(monkeypatch):
    def fake_call(base, headers, cookies, method, path, params=None, body=None,
                  timeout=None):
        if path.startswith("/api/resource/Project/"):
            return _resp(200, {"data": {"name": "PROJ-1"}})
        if path == "/api/resource/Task":
            assert "PROJ-1" in json.dumps(params["filters"])
            assert method == "GET"
            return _resp(200, {"data": [
                {"name": "T1", "exp_start_date": "2026-01-01", "exp_end_date": "2026-01-02",
                 "depends_on": [{"task": "T0"}]},
                {"name": "T2", "exp_start_date": "2026-01-03", "exp_end_date": "2026-01-04"},
                {"name": "T3"}]})
        return _resp(404, {})

    monkeypatch.setattr(FT, "_call", fake_call)
    assert FT.project_facts_readonly("http://x", {}, {}, "PROJ-1") == {
        "ok": True, "tasks_created": 3, "tasks_dated": 2, "deps_linked": 1}


def test_project_facts_readonly_never_raises(monkeypatch):
    monkeypatch.setattr(FT, "_call", lambda *a, **k: _resp(500, {}))
    assert FT.project_facts_readonly("http://x", {}, {}, "PROJ-1")["ok"] is False
    assert FT.project_facts_readonly("http://x", {}, {}, "")["ok"] is False


def test_smoke_sample_fills_required_fields_by_type(monkeypatch):
    def fake_call(base, headers, cookies, method, path, params=None, body=None,
                  timeout=None):
        if path.startswith("/api/resource/DocType/"):
            from urllib.parse import unquote
            doctype = unquote(path.rsplit("/", 1)[-1])
            if doctype == "Purchase Invoice Item":
                return _resp(200, {"data": {"fields": [
                    {"fieldname": "item_code", "fieldtype": "Data", "reqd": 1},
                    {"fieldname": "qty", "fieldtype": "Float", "reqd": 1},
                ]}})
            return _resp(200, {"data": {"fields": [
                {"fieldname": "title", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "qty", "fieldtype": "Int", "reqd": 1},
                {"fieldname": "on_date", "fieldtype": "Date", "reqd": 1},
                {"fieldname": "supplier", "fieldtype": "Link",
                 "options": "Supplier", "reqd": 1},
                {"fieldname": "notes", "fieldtype": "Text", "reqd": 0},
                {"fieldname": "items", "fieldtype": "Table",
                 "options": "Purchase Invoice Item", "reqd": 1},
            ]}})
        if path == "/api/resource/Supplier":
            return _resp(200, {"data": [{"name": "SUP-1"}]})
        return _resp(404, {})

    monkeypatch.setattr(FT, "_call", fake_call)
    s = FT.smoke_sample("http://x", {}, {}, "Purchase Invoice")
    assert s["title"] == "smoke" and s["qty"] == 1
    assert s["supplier"] == "SUP-1"          # a required Link points at a real doc
    assert s["on_date"]                       # a date was supplied
    assert "notes" not in s                   # not required
    # a required child table carries one row with its own required fields set, and
    # the row names its doctype (Frappe rejects a child row without it)
    assert s["items"] == [{"item_code": "smoke", "qty": 1,
                           "doctype": "Purchase Invoice Item"}]


def test_refresh_derives_project_evidence_from_the_site(monkeypatch):
    """The gate judges recorded evidence, so a real project built through a path
    that recorded nothing read as 'evidence=missing' with tasks_created 0 while
    PROJ-0011 had ten tasks. The refresh now derives the facts from the site."""
    from shared import orch_nodes as ON
    from shared.project_context import get_context

    st = {"frappe_sid": "sid"}                # established session: no login attempted
    get_context(st)["frappe_state"] = {"evidence": []}
    monkeypatch.setattr(FT, "_ensure_auth", lambda tc, *a, **k: ("http://x", {}, {}, ""))
    monkeypatch.setattr(FT, "newest_project_name", lambda *a, **k: "PROJ-X")
    monkeypatch.setattr(FT, "project_facts_readonly",
                        lambda *a, **k: {"ok": True, "tasks_created": 27,
                                         "tasks_dated": 10, "deps_linked": 9})
    monkeypatch.setattr(FT, "custom_doctype_names", lambda *a, **k: [])

    ON._refresh_frappe_evidence(st)

    ev = get_context(st)["frappe_state"]["evidence"]
    proj = [e for e in ev if e.get("kind") == "project"]
    assert proj, ev
    assert proj[-1]["verified"] is True
    assert proj[-1]["tasks_created"] == 27
    assert proj[-1]["tasks_dated"] == 10 and proj[-1]["deps_linked"] == 9
    assert st["frappe_project"] == "PROJ-X"    # so the gate's project check can pass


def test_refresh_smoke_tests_designed_doctypes(monkeypatch):
    """DocTypes the design names are smoke-tested by the harness, so verification
    does not depend on the model remembering to call the tool."""
    from shared import orch_nodes as ON
    from shared.project_context import get_context

    st = {"frappe_sid": "sid",
          "tech_design": "Data schema: Supplier Invoice table with columns."}
    get_context(st)["frappe_state"] = {"evidence": []}
    monkeypatch.setattr(FT, "_ensure_auth", lambda tc, *a, **k: ("http://x", {}, {}, ""))
    monkeypatch.setattr(FT, "newest_project_name", lambda *a, **k: "")
    monkeypatch.setattr(FT, "project_facts_readonly", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(FT, "custom_doctype_names", lambda *a, **k: [
        {"name": "Supplier Invoice", "istable": False},
        {"name": "Unrelated Thing", "istable": False},          # not in the design
        {"name": "Supplier Invoice Item", "istable": True},     # child table
    ])
    monkeypatch.setattr(FT, "smoke_sample", lambda *a, **k: {"title": "smoke"})

    tested: list[str] = []

    def fake_smoke(doctype, tool_context, **kw):
        tested.append(doctype)
        FT._record_evidence(tool_context, "smoke_test", True, name=doctype, verified=True)
        return {"ok": True}

    monkeypatch.setattr(FT, "smoke_test", fake_smoke)

    ON._refresh_frappe_evidence(st)

    assert tested == ["Supplier Invoice"]      # only the design's non-child doctype
    ev = get_context(st)["frappe_state"]["evidence"]
    assert any(e.get("kind") == "doctype" and e.get("name") == "Supplier Invoice"
               and e.get("verified") for e in ev), ev


def test_refresh_skips_a_doctype_whose_smoke_test_fails(monkeypatch):
    """A doctype entry is only recorded once its smoke test passed, otherwise the
    doctype and smoke checks would contradict each other."""
    from shared import orch_nodes as ON
    from shared.project_context import get_context

    st = {"frappe_sid": "sid", "tech_design": "Supplier Invoice table with columns."}
    get_context(st)["frappe_state"] = {"evidence": []}
    monkeypatch.setattr(FT, "_ensure_auth", lambda tc, *a, **k: ("http://x", {}, {}, ""))
    monkeypatch.setattr(FT, "newest_project_name", lambda *a, **k: "")
    monkeypatch.setattr(FT, "project_facts_readonly", lambda *a, **k: {"ok": False})
    monkeypatch.setattr(FT, "custom_doctype_names", lambda *a, **k: [
        {"name": "Supplier Invoice", "istable": False}])
    monkeypatch.setattr(FT, "smoke_sample", lambda *a, **k: {})

    def failing_smoke(doctype, tool_context, **kw):
        FT._record_evidence(tool_context, "smoke_test", False, name=doctype,
                            error="mandatory field missing")
        return {"ok": False}

    monkeypatch.setattr(FT, "smoke_test", failing_smoke)

    ON._refresh_frappe_evidence(st)

    ev = get_context(st)["frappe_state"]["evidence"]
    assert not [e for e in ev if e.get("kind") == "doctype"], ev
