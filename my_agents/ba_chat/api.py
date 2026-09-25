"""HTTP surface of the BA chatbot — plain FastAPI, no ADK.

  POST /api/ba/chat            one chatbot turn → reply + checklist + diagram
  GET  /api/ba/state           checklist + coverage + diagram for a session
  GET  /api/ba/flow            diagram data only (cheap poll while chatting)
  POST /api/ba/confirm         explicit Yes/Change from the quick-reply buttons
  POST /api/ba/create-project  sign-off gate → background Frappe setup job
  GET  /api/ba/job             setup-job progress
  GET  /api/ba/export          approved pack as a BRD-draft download
  GET  /api/ba/catalogue       groups + item labels (fresh-session checklist)

The setup job runs in a worker thread; the browser polls ``/job`` exactly
like the run view polls ``/api/progress``.
"""

from __future__ import annotations

import threading
import time
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse, PlainTextResponse

from . import engine, llm
from .catalogue import GROUPS, ITEMS
from .flow import build_flow, coverage
from .frappe_client import plan_setup, run_setup
from .store import Store

router = APIRouter(prefix="/api/ba")

_store: Store | None = None
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def get_store() -> Store:
    """One store per process; ``BA_CHAT_DB`` env redirects it (tests)."""
    global _store
    if _store is None:
        _store = Store()
    return _store


def _bad(message: str, code: int = 422) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=code)


def _checklist(session: dict) -> list[dict]:
    """Checklist without touching the transcript (safe to poll)."""
    from .flow import applicable_items
    out = []
    for item_id in applicable_items(session.get("project_type")):
        entry = session.get("items", {}).get(item_id, {})
        out.append({"id": item_id, "group": ITEMS[item_id]["group"],
                    "label": ITEMS[item_id]["label"],
                    "status": entry.get("status", "pending")})
    return out


# --------------------------------------------------------------------- chat
@router.post("/chat")
def chat(payload: dict) -> JSONResponse:
    message = str(payload.get("message") or "")
    session_id = str(payload.get("session_id") or "")
    try:
        result = engine.handle_message(get_store(), session_id or None, message)
    except RuntimeError as exc:
        return _bad(str(exc), 502)
    result["ok"] = True
    return JSONResponse(result)


@router.get("/state")
def state(session_id: str = "") -> JSONResponse:
    session = get_store().get(session_id)
    if session is None:
        return _bad("unknown session — start a new chat", 404)
    cov = coverage(session)
    return JSONResponse({"ok": True, "session_id": session["id"],
                         "stage": session.get("stage"),
                         "project_type": session.get("project_type"),
                         "coverage": cov, "flow": build_flow(session),
                         "items": _checklist(session),
                         "transcript": (session.get("transcript") or [])[-60:],
                         "done": session.get("stage") == "READY"})


@router.get("/flow")
def flow(session_id: str = "") -> JSONResponse:
    session = get_store().get(session_id)
    if session is None:
        return _bad("unknown session", 404)
    return JSONResponse({"ok": True, **build_flow(session)})


@router.get("/catalogue")
def catalogue() -> JSONResponse:
    return JSONResponse({"ok": True, "groups": list(GROUPS),
                         "items": [{"id": i["id"], "group": i["group"],
                                    "label": i["label"]} for i in ITEMS.values()]})


@router.post("/confirm")
def confirm(payload: dict) -> JSONResponse:
    """Quick-reply buttons: {"session_id", "item_id", "confirmed": bool}."""
    store = get_store()
    session = store.get(str(payload.get("session_id") or ""))
    if session is None:
        return _bad("unknown session", 404)
    item_id = str(payload.get("item_id") or "")
    if item_id not in ITEMS:
        return _bad("unknown requirement", 404)
    entry = store.get_item(session, item_id)
    if entry.get("status") != "proposed":
        return _bad("nothing awaiting confirmation for that requirement", 409)
    if payload.get("confirmed"):
        store.set_item(session, item_id, "fulfilled")
        result = engine._advance(store, store.get(session["id"]))
    else:
        store.set_item(session, item_id, "pending")
        from .catalogue import question_for
        result = engine.state_result(
            store, store.get(session["id"]),
            f"No problem — let's redo it: {question_for(ITEMS[item_id], session.get('project_type'))}")
    result["ok"] = True
    return JSONResponse(result)


# -------------------------------------------------------------------- build
def _run_job(store: Store, session_id: str, job_id: str) -> None:
    session = store.get(session_id)
    job = _jobs[job_id]
    if session is None:                                     # pragma: no cover
        job.update({"status": "failed", "error": "session vanished"})
        return
    log: list[str] = []
    try:
        outcome = run_setup(session, log)
    except Exception as exc:                                # never leak a trace
        outcome = {"ok": False, "error": str(exc)[:300]}
    job["log"] = log
    if outcome.get("ok"):
        session["stage"] = "DONE"
        store.save(session)
        job.update({"status": "done", **{k: v for k, v in outcome.items() if k != "ok"}})
    else:
        job.update({"status": "failed", "error": outcome.get("error")})


@router.post("/create-project")
def create_project(payload: dict) -> JSONResponse:
    """Sign-off gate: only a READY session may build. Idempotent per session."""
    store = get_store()
    session = store.get(str(payload.get("session_id") or ""))
    if session is None:
        return _bad("unknown session", 404)
    with _lock:
        for job_id, job in _jobs.items():
            if job.get("session_id") == session["id"] and job.get("status") in ("running", "done"):
                return JSONResponse({"ok": True, "job_id": job_id,
                                     "reused": True, "status": job["status"]})
    if session.get("stage") != "READY":
        cov = coverage(session)
        return _bad(f"requirements are not signed off yet "
                    f"({cov['resolved']}/{cov['total']} captured) — finish the interview first", 409)
    with _lock:
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"session_id": session["id"], "status": "running",
                         "started": time.time(), "log": [],
                         "plan": plan_setup(session)}
        thread = threading.Thread(target=_run_job, args=(store, session["id"], job_id),
                                  daemon=True)
        thread.start()
    return JSONResponse({"ok": True, "job_id": job_id, "status": "running"})


@router.get("/job")
def job(job_id: str = "") -> JSONResponse:
    found = _jobs.get(job_id)
    if found is None:
        return _bad("unknown job", 404)
    return JSONResponse({"ok": True, "job_id": job_id, **found})


# ------------------------------------------------------------------- export
def pack_markdown(session: dict) -> str:
    """Approved pack rendered as a BRD draft."""
    items = session.get("items", {})
    lines = [f"# Business Requirements — {items.get('a0', {}).get('value', 'Untitled Project')}",
             "", f"Project type: {session.get('project_type') or '—'}",
             f"Captured: {time.strftime('%Y-%m-%d')} via BA interview", ""]
    for group in GROUPS:
        lines.append(f"## {group['id']}. {group['label']}")
        for item_id, item in ITEMS.items():
            if item["group"] != group["id"]:
                continue
            entry = items.get(item_id, {})
            if entry.get("status") in ("fulfilled", "assumed") and entry.get("value"):
                mark = " *(assumed — verify)*" if entry["status"] == "assumed" else ""
                lines.append(f"### {item['label']}{mark}")
                lines.append(entry["value"])
                lines.append("")
    return "\n".join(lines).strip() + "\n"


@router.get("/export")
def export(session_id: str = "") -> PlainTextResponse:
    session = get_store().get(session_id)
    if session is None:
        return PlainTextResponse("unknown session", status_code=404)
    name = (session.get("items", {}).get("a0", {}).get("value") or "requirements")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)[:60]
    return PlainTextResponse(
        pack_markdown(session), media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe}_BRD_draft.md"'})


@router.get("/llm-status")
def llm_status() -> JSONResponse:
    """Whether the chatbot can talk right now (key present, no secret leaked)."""
    cfg = llm.settings()
    tail = cfg["api_key"][-4:] if len(cfg["api_key"]) > 8 else ""
    return JSONResponse({"ok": True, "model": cfg["model"], "base": cfg["api_base"],
                         "key": f"set (…{tail})" if tail else "missing"})
