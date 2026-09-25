"""Web channel routes (offline): chat, intake with uploads, health.

Uses Starlette's TestClient against the real FastAPI app with a stub core, so the
routes, cookies, form parsing and ingest path are all exercised for real.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from connectors.app import build_app  # noqa: E402
from connectors.identity import identify  # noqa: E402
from connectors.ingest import source_documents  # noqa: E402

WS = "connectors_webtest"


class StubCore:
    """Minimal core surface the web routes depend on."""

    def __init__(self):
        self.turns: list[tuple[str, str, str]] = []

    async def ask(self, channel, external_id, text):
        self.turns.append((channel, external_id, text))
        return [f"echo:{text}"]

    async def bound_workspace(self, channel, external_id):
        return WS

    async def ensure_session(self, channel, external_id, *, name="", workspace_id=""):
        return identify(channel, external_id)


@pytest.fixture()
def core():
    return StubCore()


@pytest.fixture()
def client(core):
    shutil.rmtree(MY_AGENTS / "projects" / WS, ignore_errors=True)
    yield TestClient(build_app(core=core, start_telegram=False))
    shutil.rmtree(MY_AGENTS / "projects" / WS, ignore_errors=True)


def _wait_for(predicate, timeout=2.0):
    """The intake starts the run in the background, so its effect lands just after
    the response. Wait for it rather than racing the event loop."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_index_serves_the_page_and_sets_a_browser_id(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "WorkSimplified" in resp.text
    assert resp.cookies.get("wc_id")


def test_health_reports_channels(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["channels"]["web"] is True
    assert body["channels"]["telegram"] is False       # no token configured
    assert body["app"] == "delivery_pipeline"


def test_chat_returns_the_reply_and_the_workspace(client, core):
    resp = client.post("/chat", json={"message": "I want an ERP"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "echo:I want an ERP"
    assert body["workspace"] == WS
    assert core.turns and core.turns[0][0] == "web"


def test_chat_requires_a_message(client):
    assert client.post("/chat", json={}).status_code == 400


def test_intake_writes_structured_fields_and_uploads(client, core):
    resp = client.post(
        "/intake",
        data={"project_name": "Travel and Expense", "company": "Acme",
              "goal": "Run order-to-cash on Frappe"},
        files=[("files", ("interview_notes.txt", b"CFO wants approvals.", "text/plain"))],
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["workspace"] == WS
    assert body["mode"] == "generation"          # intake skips the discovery chat
    assert body["structured"]["project"]["name"] == "Travel and Expense"
    assert body["structured"]["business_fields"]
    assert len(body["files"]) == 1 and body["files"][0].startswith("uploaded_interview_notes")

    # the mode is seeded in the workspace context the orchestrator hydrates
    from connectors.ingest import set_mode  # noqa: F401  (imported for the surface)
    from shared.workspace import ProjectWorkspace

    assert (ProjectWorkspace(WS).get_context().get("conversation") or {}).get("mode") == "generation"

    # the document is on disk in the project workspace and recorded as a source
    uploaded = MY_AGENTS / "projects" / WS / "artifacts" / body["files"][0]
    assert uploaded.is_file() and "CFO wants approvals" in uploaded.read_text()
    sources = source_documents(WS)
    assert sources and sources[0]["file"] == body["files"][0]

    # and the pipeline was kicked off with the goal, in the background
    assert _wait_for(lambda: bool(core.turns))
    assert "Run order-to-cash on Frappe" in core.turns[0][2]


def _tiny_pdf(text="Hello from a PDF document"):
    """A minimal PDF with a text layer, so the test needs no PDF writer."""
    stream = ("BT /F1 12 Tf 20 100 Td (" + text + ") Tj ET").encode()
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(stream)).encode() + b">>stream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<</Size " + str(len(objects) + 1).encode()
            + b"/Root 1 0 R>>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


def _stored(filename):
    return (MY_AGENTS / "projects" / WS / "artifacts" / filename).read_text()


def test_intake_reports_unsupported_uploads(client):
    resp = client.post(
        "/intake",
        data={"project_name": "X", "goal": "g"},
        files=[("files", ("archive.zip", b"PK\x03\x04 not a document", "application/zip"))],
    )
    body = resp.json()
    assert body["files"] == []
    assert body["rejected"] and "unsupported" in body["rejected"][0]["error"]
    assert body["rejected"][0]["name"] == "archive.zip"


def test_intake_extracts_a_pdf(client):
    """A PDF's text is the input material, so it is read rather than refused."""
    resp = client.post(
        "/intake", data={"goal": "g"},
        files=[("files", ("policy.pdf", _tiny_pdf(), "application/pdf"))])
    body = resp.json()
    assert body["rejected"] == []
    assert len(body["files"]) == 1 and body["files"][0].startswith("uploaded_policy_")
    assert "Hello from a PDF document" in _stored(body["files"][0])


def test_intake_extracts_a_word_document(client):
    import io

    import docx

    buf = io.BytesIO()
    document = docx.Document()
    document.add_paragraph("The Production Manager signs off above 500,000.")
    document.save(buf)

    resp = client.post(
        "/intake", data={"goal": "g"},
        files=[("files", ("policy.docx", buf.getvalue(),
                          "application/vnd.openxmlformats-officedocument"
                          ".wordprocessingml.document"))])
    body = resp.json()
    assert body["rejected"] == []
    assert "Production Manager signs off" in _stored(body["files"][0])


def test_intake_refuses_a_pdf_it_cannot_read(client):
    """A corrupt or scanned PDF is refused with the reason — never ingested as junk."""
    resp = client.post(
        "/intake", data={"goal": "g"},
        files=[("files", ("scan.pdf", b"%PDF-1.4 not really a pdf", "application/pdf"))])
    body = resp.json()
    assert body["files"] == []
    assert body["rejected"][0]["name"] == "scan.pdf"
    assert "pdf" in body["rejected"][0]["error"].lower()


def test_intake_reports_legacy_office_formats(client):
    """`.doc` shares a suffix family with `.docx` but is a different container."""
    resp = client.post(
        "/intake", data={"goal": "g"},
        files=[("files", ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1 binary",
                          "application/msword"))])
    body = resp.json()
    assert body["files"] == []
    assert "docx" in body["rejected"][0]["error"]


def test_intake_answers_before_the_run_finishes(client, core):
    """A run takes minutes, so the request returns as soon as the work is scheduled
    and the page follows it through /api/progress."""
    import time

    started = time.time()
    body = client.post("/intake", data={"goal": "Stock control"}).json()
    assert time.time() - started < 1.0
    assert body["started"] is True
    assert "reply" not in body          # nothing here waits on the pipeline


def test_answer_resumes_a_paused_run_in_the_background(client, core):
    """A run stops for a human (the BA package review, an exhausted gate, a budget
    stop). The answer resumes that turn, which then runs for minutes, so the request
    returns at once and the page keeps following /api/progress."""
    import time

    started = time.time()
    r = client.post("/answer", json={"message": "approve"})
    assert time.time() - started < 1.0
    assert r.json() == {"ok": True, "answer": "approve", "workspace": WS}
    assert _wait_for(lambda: bool(core.turns))
    assert core.turns[0][2] == "approve"


def test_answer_requires_a_message(client):
    assert client.post("/answer", json={}).status_code == 400
    assert client.post("/answer", json={"message": "  "}).status_code == 400


def test_answer_refuses_a_conversation_with_no_run(monkeypatch):
    """A stray answer must not start a run. An answer from a new browser (or an
    expired cookie) is a first contact; treating it as a resume turned the word
    "continue" into a brand-new project named continue_<date>."""
    from connectors.app import build_app

    class NoRunCore(StubCore):
        async def bound_workspace(self, channel, external_id):
            return ""

    client = TestClient(build_app(core=NoRunCore(), start_telegram=False))
    r = client.post("/answer", json={"message": "continue"})
    assert r.status_code == 409
    assert "no run" in r.json()["error"]


def test_progress_reports_stages_gates_and_events(client):
    """The console renders the run from this payload alone."""
    from shared.workspace import ProjectWorkspace

    ws = ProjectWorkspace(WS)
    ws.update_context({"sections_done_BA": ["objectives", "scope"],
                       "carried_stages": ["FRAPPE"],
                       "gate_warnings": {"FRAPPE": ["a", "b"]},
                       "business": {"source_documents": [
                           {"name": "interview_notes.txt",
                            "file": "uploaded_interview_notes_20260924_201144.md",
                            "chars": 2340}]},
                       "validation": {"passed": False,
                                      "missing": ["all_gates: failing gates: ['FRAPPE']"],
                                      "coverage": {"BR": 4, "US": 3}}})
    # artifact references live beside the context, the way the orchestrator writes them
    ws._write_json("context", "artifacts.json",
                   {"BA": {"doc_key": "brd", "path": "artifacts/brd_assembled.md"}})
    ws.update_execution({"current_stage": "PROJECT", "workflow_status": "RUNNING",
                         "iteration_count": 2, "current_gate": "PROJECT_GATE",
                         "next_stage": "PROJECT"})
    ws.add_history({"ts": "2026-09-24T17:00:00+00:00", "type": "pipeline_started",
                    "actor": "orchestrator", "summary": "workspace: " + WS})
    ws.add_history({"ts": "2026-09-24T17:00:30+00:00", "type": "gate_passed",
                    "actor": "orchestrator", "summary": "BA gate (28 checks)"})
    ws.add_history({"ts": "2026-09-24T17:01:00+00:00", "type": "section_failed",
                    "actor": "project_agent", "summary": "PROJECT/wbs (rung 1): too thin"})

    body = client.get(f"/api/progress?workspace={WS}").json()
    assert body["exists"] is True
    assert body["stage"] == "PROJECT" and body["status"] == "RUNNING"
    assert body["iteration"] == 2
    assert body["gate"] == "PROJECT_GATE"
    assert body["advisories"] == 2
    assert body["carried"] == ["FRAPPE"]
    assert body["done"] is False
    assert body["elapsed_s"] >= 30       # running: measured to now

    rows = {r["name"]: r for r in body["stages"]}
    assert [r["name"] for r in body["stages"]] == [
        "BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE", "VALIDATION"]
    assert rows["BA"]["state"] == "done"           # a passing gate in the log
    assert rows["BA"]["sections_done"] == 2
    assert rows["BA"]["sections_total"] == 9
    assert rows["PROJECT"]["state"] == "active"    # where the run is now
    assert rows["FUNCTIONAL"]["state"] == "queued"
    assert rows["FRAPPE"]["state"] == "carried"    # advanced with its gaps
    assert rows["VALIDATION"]["state"] == "queued"

    # the flow view renders each stage from its real sections, not a static list
    assert "objectives" in rows["BA"]["sections"] and len(rows["BA"]["sections"]) == 9
    assert rows["BA"]["sections_written"] == ["objectives", "scope"]
    assert rows["PROJECT"]["sections"] == ["objective", "wbs", "schedule", "raci_raid"]
    assert rows["VALIDATION"]["sections"] == []
    assert rows["PROJECT"]["retries"] == 1         # a failing rewrite shows as a retry
    assert rows["BA"]["retries"] == 0

    # the flow view draws from the recorded detail behind each row
    assert body["next_stage"] == "PROJECT"
    assert body["warnings"] == {"FRAPPE": ["a", "b"]}
    assert body["artifacts"]["BA"]["doc_key"] == "brd"
    assert body["artifacts"]["BA"]["path"].endswith("brd_assembled.md")
    assert body["validation"]["passed"] is False
    assert body["validation"]["coverage"]["BR"] == 4
    assert body["validation"]["missing"][0].startswith("all_gates")

    # the documents the customer handed over, so the page can show they were taken
    assert body["sources"] == [{"file": "uploaded_interview_notes_20260924_201144.md",
                                "name": "interview_notes.txt", "chars": 2340}]

    assert [e["type"] for e in body["events"]] == [
        "pipeline_started", "gate_passed", "section_failed"]
    assert body["events"][0]["summary"].startswith("workspace:")


def test_progress_is_empty_but_valid_for_an_unknown_workspace(client):
    body = client.get("/api/progress?workspace=does_not_exist").json()
    assert body["exists"] is False
    assert body["stages"] == [] and body["events"] == []
    assert body["done"] is False
    assert body["busy"] is False            # nothing is running for it


def test_progress_lists_stage_outputs_and_serves_them(client):
    """Every phase's files are listed per stage and downloadable one by one."""
    from shared.workspace import ProjectWorkspace

    ws = ProjectWorkspace(WS)
    ws.save_artifact("brd_assembled.md", "# BRD\n")
    ws.save_artifact("ba_flow_demo_20260925.mmd", "graph TD; A-->B\n")
    ws.save_artifact("project_plan_assembled.md", "# plan\n")
    ws.save_artifact("Verification_20260925.md", "evidence\n")

    outs = client.get(f"/api/progress?workspace={WS}").json()["outputs"]
    assert [f["name"] for f in outs["BA"]] == ["brd_assembled.md",
                                               "ba_flow_demo_20260925.mmd"]
    assert outs["BA"][0]["primary"] is True and outs["BA"][1]["primary"] is False
    assert outs["BA"][0]["bytes"] == 6
    assert [f["name"] for f in outs["PROJECT"]] == ["project_plan_assembled.md"]
    assert [f["name"] for f in outs["FRAPPE"]] == ["Verification_20260925.md"]
    assert outs["TECHNICAL"] == []           # nothing produced yet

    got = client.get(f"/api/download?workspace={WS}&name=brd_assembled.md")
    assert got.status_code == 200
    assert got.headers["content-disposition"].startswith(
        'attachment; filename="brd_assembled.md"')
    assert got.text == "# BRD\n"

    # nothing outside the artifacts folder, and nothing invented
    assert client.get(f"/api/download?workspace={WS}&name=nope.md").status_code == 404
    assert client.get(
        f"/api/download?workspace={WS}&name=..%2Fcontext%2Fcontext.json"
    ).status_code == 404
    assert client.get(
        f"/api/download?workspace=..%2F&name=brd_assembled.md"
    ).status_code == 404


def test_inflight_counter_tracks_running_turns():
    """The busy signal the page relies on: set while a turn is running for a
    workspace, cleared when it ends.

    Tested directly rather than through TestClient: that client runs a request's
    event loop only for the duration of the request, so a background task cannot
    outlive it (a real uvicorn server keeps one long-lived loop, which is why a run
    survives the response that started it).
    """
    import asyncio

    from connectors import web as W

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def turn():
            started.set()
            await release.wait()

        before = W._INFLIGHT.get("ws-busy", 0)
        W._spawn(turn(), "ws-busy")
        await started.wait()
        during = W._INFLIGHT.get("ws-busy", 0)
        release.set()
        await asyncio.sleep(0.05)          # let the done callback run
        return before, during, W._INFLIGHT.get("ws-busy", 0)

    before, during, after = asyncio.run(scenario())
    assert before == 0
    assert during == 1
    assert after == 0
