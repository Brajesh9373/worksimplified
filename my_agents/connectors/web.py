"""Web channel — the console page plus the intake and progress endpoints.

- ``GET /``             the console page (sets a browser id cookie)
- ``POST /chat``        one message -> the reply
- ``POST /intake``      a brief + optional documents -> starts the pipeline and
                        returns at once; the work continues in the background
- ``GET /api/progress`` the running state of one workspace: stage, gate, section
                        counts, carried stages and recent events
- ``GET /api/download`` one produced file from the workspace artifacts, as an
                        attachment (each stage offers its own outputs)

The browser id is the channel identity, so a returning browser keeps its
conversation and its project.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from .core import ChannelCore
from .ingest import ingest_files, set_mode, set_structured

log = logging.getLogger("connectors.web")

CHANNEL = "web"
COOKIE = "wc_id"
STATIC = Path(__file__).resolve().parent / "static"

GENERATE_HINT = "Generate the BRD and the documents now."

STAGE_ORDER = ["BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE"]
PROGRESS_EVENTS = 80

# long turns outlive the request that started them; keep a reference so the task is
# not garbage collected mid-run
_TASKS: set[asyncio.Task] = set()
# workspace -> number of turns running for it. Progress events only land at stage
# boundaries, so a stage that works through tool calls (FRAPPE builds on the live
# site) looks idle for minutes. Whether a turn is actually running is server-side
# truth, so the page can tell "working quietly" from "the turn died".
_INFLIGHT: dict[str, int] = {}


def _spawn(coro, workspace: str = "") -> None:
    key = workspace or ""
    _INFLIGHT[key] = _INFLIGHT.get(key, 0) + 1
    task = asyncio.create_task(coro)
    _TASKS.add(task)

    def _finished(_task) -> None:
        _TASKS.discard(_task)
        left = _INFLIGHT.get(key, 1) - 1
        if left > 0:
            _INFLIGHT[key] = left
        else:
            _INFLIGHT.pop(key, None)

    task.add_done_callback(_finished)


def _browser_id(request: Request) -> tuple[str, bool]:
    value = (request.cookies.get(COOKIE) or "").strip()
    if value:
        return value, False
    return uuid.uuid4().hex, True


def _stage_rows(ctx: dict, exc: dict, hist: list) -> list[dict]:
    """One row per stage: sections written, gate verdict, what it produced."""
    done_ids = {h.get("summary", "") for h in hist}
    passed = {name for name in STAGE_ORDER
              if any(h.get("type") == "gate_passed"
                     and str(h.get("summary", "")).startswith(name) for h in hist)}
    failed = {name for name in STAGE_ORDER
              if any(h.get("type") == "gate_failed"
                     and str(h.get("summary", "")).startswith(name) for h in hist)}
    carried = set(ctx.get("carried_stages") or [])
    current = str(exc.get("current_stage") or "")
    try:
        from shared.sections import sections_for
    except Exception:
        sections_for = None

    rows: list[dict] = []
    for name in STAGE_ORDER:
        spec = sections_for(name) if sections_for else []
        written_ids = list(dict.fromkeys(ctx.get(f"sections_done_{name}") or []))
        if name in carried:
            state = "carried"
        elif name in passed:
            state = "done"
        elif name == current:
            state = "active"
        elif name in failed:
            state = "held"
        else:
            state = "queued"
        rows.append({"name": name, "state": state,
                     "sections_done": len(written_ids), "sections_total": len(spec),
                     "sections": [s["id"] for s in spec],
                     "sections_written": written_ids,
                     "retries": sum(1 for h in hist
                                    if h.get("type") in ("section_failed", "gate_failed")
                                    and str(h.get("summary", "")).startswith(f"{name}/"))})
    rows.append({"name": "VALIDATION", "state": "done" if "validation_passed" in done_ids else
                 ("active" if current == "VALIDATION" else "queued"),
                 "sections_done": 0, "sections_total": 0, "sections": [],
                 "sections_written": [], "retries": 0})
    return rows


# What each stage leaves in <workspace>/artifacts: the assembled document the gate
# judged, plus everything else that phase produced (flow/gantt/architecture bundles,
# the Frappe evidence trail). Prefixes are matched lower-cased so a legacy
# "Frappe_Setup_*.md" still lands under FRAPPE.
_STAGE_OUTPUTS = {
    "BA": ("brd", ("brd", "ba_", "ba_package")),
    "PROJECT": ("project_plan", ("project_plan", "project_gantt")),
    "FUNCTIONAL": ("functional_spec", ("functional_spec", "functional_")),
    "TECHNICAL": ("tech_design", ("tech_design", "tech_architecture",
                                  "tech_sequence", "tech_")),
    "FRAPPE": ("frappe_setup", ("frappe_", "frappesetup", "frappeimplementation",
                                "verification_", "implementation_report")),
}


def stage_outputs(ws) -> dict:
    """Files each stage produced: the primary document first, then the rest.

    Discovery is workspace-scoped (only this project's artifacts folder), so a
    stage gets its outputs even when artifacts.json never recorded a path for it.
    """
    out: dict = {stage: [] for stage in _STAGE_OUTPUTS}
    folder = Path(getattr(ws, "root", "")) / "artifacts"
    files = [p for p in folder.iterdir() if p.is_file()] if folder.is_dir() else []
    try:
        arts = ws.get_artifacts()
    except Exception:
        arts = {}
    for stage, (doc_key, prefixes) in _STAGE_OUTPUTS.items():
        canonical = f"{doc_key}_assembled.md".lower()
        entries = [p for p in files
                   if p.name.lower().startswith(tuple(prefixes))
                   or p.name.lower() == canonical]
        if not entries:
            continue
        recorded = str(((arts.get(stage) or {}) if isinstance(arts, dict) else {})
                       .get("path") or "")
        recorded = Path(recorded).name if recorded else ""
        primary = next((p for p in entries if recorded and p.name == recorded), None)
        if primary is None:
            primary = next((p for p in entries if p.name.lower() == canonical), None)
        if primary is None:
            primary = max(entries, key=lambda p: p.stat().st_size)
        ordered = [primary] + sorted((p for p in entries if p is not primary),
                                     key=lambda p: p.name)
        out[stage] = [{"name": p.name, "bytes": p.stat().st_size,
                       "primary": p is primary} for p in ordered]
    return out


def progress_snapshot(ws_id: str) -> dict:
    """Everything the console needs for one workspace. Never raises."""
    out: dict = {"workspace": ws_id or "", "exists": False, "stage": "", "status": "",
                 "iteration": 0, "gate": "", "started": "", "elapsed_s": 0,
                 "stages": [], "events": [], "advisories": 0, "carried": [],
                 "done": False, "next_stage": "", "warnings": {}, "artifacts": {},
                 "validation": {}, "sources": [], "outputs": {}}
    if not ws_id:
        return out
    try:
        from shared.workspace import ProjectWorkspace

        ws = ProjectWorkspace(ws_id, create=False)
        if not ws.exists():
            return out
        ctx = ws.get_context() or {}
        exc = ws.get_execution() or {}
        hist = ws.get_history() or []
        arts = ws.get_artifacts() or {}
    except Exception:
        log.warning("progress read failed for %s", ws_id, exc_info=True)
        return out

    out["exists"] = True
    out["stage"] = str(exc.get("current_stage") or "")
    out["status"] = str(exc.get("workflow_status") or "")
    out["iteration"] = int(exc.get("iteration_count") or 0)
    out["gate"] = str(exc.get("current_gate") or "")
    out["done"] = out["status"] in ("SUCCESS", "ABORTED")
    out["carried"] = list(ctx.get("carried_stages") or [])
    # the documents the customer handed over at intake, so the page can show they
    # were picked up rather than silently stored
    out["sources"] = [{"file": str(d.get("file") or ""),
                       "name": str(d.get("name") or ""),
                       "chars": int(d.get("chars") or 0)}
                      for d in ((ctx.get("business") or {}).get("source_documents") or [])
                      if isinstance(d, dict) and d.get("file")]
    out["advisories"] = sum(len(v or []) for v in (ctx.get("gate_warnings") or {}).values())
    # the flow view draws the gates, the artifacts and the final validation, so it
    # needs the recorded detail behind each row — not just the row's state
    try:
        out["next_stage"] = str(exc.get("next_stage") or "")
        out["warnings"] = {str(k): [str(x) for x in (v or [])[:6]]
                           for k, v in (ctx.get("gate_warnings") or {}).items()
                           if v}
        out["artifacts"] = {str(k): {"doc_key": str((v or {}).get("doc_key") or ""),
                                     "path": str((v or {}).get("path") or "")}
                            for k, v in arts.items()
                            if isinstance(v, dict)}
        val = ctx.get("validation") or {}
        if isinstance(val, dict) and val:
            out["validation"] = {
                "passed": bool(val.get("passed")),
                "missing": [str(x) for x in (val.get("missing") or [])[:8]],
                "coverage": {str(k): int(v) for k, v in
                             (val.get("coverage") or {}).items()
                             if isinstance(v, (int, float))},
            }
    except Exception:
        log.warning("progress detail read failed for %s", ws_id, exc_info=True)
    out["stages"] = _stage_rows(ctx, exc, hist)
    # what each stage already produced on disk, so the page can offer the files
    try:
        out["outputs"] = stage_outputs(ws)
    except Exception:
        log.warning("output listing failed for %s", ws_id, exc_info=True)
    out["events"] = [{"ts": str(h.get("ts") or ""), "type": str(h.get("type") or ""),
                      "actor": str(h.get("actor") or ""),
                      "summary": str(h.get("summary") or "")[:220]}
                     for h in hist[-PROGRESS_EVENTS:]]

    def _parse(value: str):
        try:
            import datetime as _dt

            return _dt.datetime.fromisoformat(str(value))
        except Exception:
            return None

    first, last = _parse(hist[0].get("ts", "")) if hist else None, None
    if hist:
        last = _parse(hist[-1].get("ts", ""))
    if first is not None:
        import datetime as _dt

        end = last if (out["done"] and last is not None) else _dt.datetime.now(
            first.tzinfo or _dt.timezone.utc)
        out["started"] = first.isoformat()
        out["elapsed_s"] = max(0, int((end - first).total_seconds()))
    return out


def router(core: ChannelCore) -> APIRouter:
    api = APIRouter()

    @api.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        browser, is_new = _browser_id(request)
        try:
            page = (STATIC / "chat.html").read_text(encoding="utf-8")
        except Exception as e:
            return HTMLResponse(f"<h1>connectors</h1><p>chat page unavailable: {e}</p>",
                                status_code=500)
        response = HTMLResponse(page)
        if is_new:
            response.set_cookie(COOKIE, browser, httponly=True, samesite="lax")
        return response

    @api.get("/api/progress")
    async def progress(request: Request, workspace: str = ""):
        browser, _ = _browser_id(request)
        ws_id = (workspace or "").strip()
        if not ws_id:
            try:
                ws_id = await core.bound_workspace(CHANNEL, browser)
            except Exception:
                ws_id = ""
        snap = progress_snapshot(ws_id)
        snap["busy"] = _INFLIGHT.get(ws_id, 0) > 0
        return JSONResponse(snap)

    @api.get("/api/download")
    async def download(request: Request, workspace: str = "", name: str = ""):
        """Serve one file a stage produced, as an attachment.

        The file has to sit directly in this workspace's artifacts folder — the
        name is checked against separators and the resolved path against the
        folder, so no request can read outside the project.
        """
        ws_id = (workspace or "").strip()
        fname = (name or "").strip()
        if not ws_id or not fname or Path(ws_id).name != ws_id or Path(fname).name != fname:
            return JSONResponse({"error": "unknown output"}, status_code=404)
        try:
            from shared.workspace import ProjectWorkspace

            ws = ProjectWorkspace(ws_id, create=False)
            if not ws.exists():
                return JSONResponse({"error": "unknown output"}, status_code=404)
            base = (ws.root / "artifacts").resolve()
            target = (base / fname).resolve()
            if base not in target.parents or not target.is_file():
                return JSONResponse({"error": "unknown output"}, status_code=404)
        except Exception:
            log.warning("download failed for %s/%s", ws_id, fname, exc_info=True)
            return JSONResponse({"error": "unknown output"}, status_code=404)
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, filename=target.name, media_type=media)

    @api.post("/answer")
    async def answer(request: Request):
        """Answer a pending human pause without blocking the request.

        A run stops mid-flight for a human decision (the BA package review, a gate
        that exhausted its revisions, a budget stop). The answer resumes that paused
        turn — which then runs for minutes — so it is scheduled like the intake and
        the page keeps following /api/progress.
        """
        browser, _ = _browser_id(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str((body or {}).get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        # Only ever answer a conversation that already has a run. Without this a
        # stray answer (a new browser, an expired cookie) is a first contact, and
        # the message becomes a brand-new project's name instead of a resume —
        # which is exactly what created a junk "continue_<date>" run.
        try:
            workspace = await core.bound_workspace(CHANNEL, browser)
        except Exception:
            workspace = ""
        if not workspace:
            return JSONResponse(
                {"error": "this conversation has no run to continue"},
                status_code=409)
        _spawn(core.ask(CHANNEL, browser, message), workspace)
        return {"ok": True, "answer": message, "workspace": workspace}

    @api.post("/chat")
    async def chat(request: Request):
        browser, _ = _browser_id(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str((body or {}).get("message") or "").strip()
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        replies = await core.ask(CHANNEL, browser, message)
        return {"reply": "\n\n".join(r for r in replies if r),
                "workspace": await core.bound_workspace(CHANNEL, browser)}

    @api.post("/intake")
    async def intake(request: Request,
                     project_name: str = Form(""),
                     company: str = Form(""),
                     goal: str = Form(""),
                     mode: str = Form("generation"),
                     files: list[UploadFile] = File(default=[])):
        browser, _ = _browser_id(request)
        name = (project_name or goal or "project").strip()
        ident = await core.ensure_session(CHANNEL, browser, name=name)
        workspace = await core.bound_workspace(CHANNEL, browser)

        # Straight to work: the intake form IS the "generate" instruction, so the
        # pipeline skips the discovery chat (and its model call).
        mode_result = set_mode(workspace, mode)
        structured = set_structured(workspace, {"project_name": project_name,
                                               "company": company, "goal": goal})

        uploads: list[tuple[str, bytes]] = []
        for item in files or []:
            try:
                uploads.append((item.filename or "document", await item.read()))
            except Exception:
                log.warning("could not read upload %s", getattr(item, "filename", "?"))
        ingested = ingest_files(workspace, uploads) if uploads else {"ok": True,
                                                                     "files": [],
                                                                     "rejected": []}

        kickoff = "\n\n".join(p for p in [goal.strip(), GENERATE_HINT] if p)
        # A run takes minutes: start it and answer now. The page follows it through
        # /api/progress, so the request never blocks on the pipeline.
        _spawn(core.ask(CHANNEL, browser, kickoff), workspace)
        return {"workspace": workspace,
                "session": ident.session_id,
                "mode": mode_result.get("mode", ""),
                "structured": structured,
                "files": ingested.get("files", []),
                "rejected": ingested.get("rejected", []),
                "started": True}

    return api
