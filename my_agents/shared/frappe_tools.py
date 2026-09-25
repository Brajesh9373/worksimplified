"""Shared Frappe REST tools (Frappe v14/v15 compatible).

Auth: token-based `Authorization: token <api_key>:<api_secret>` OR
session-cookie auth via login_frappe(username/password) which stores `sid`.
Creds resolution order: explicit tool args > session state > env vars.
Secrets are NEVER returned in outputs or logs — only key names + host.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx

from google.adk.tools.tool_context import ToolContext

from .eng_tools import save_doc

_DEFAULT_TIMEOUT = 30.0
MAX_EVIDENCE = 200


def _state_dict(tool_context: ToolContext | None) -> dict[str, Any]:
    if tool_context is None:
        return {}
    from .project_context import state_dict
    try:
        return state_dict(tool_context.state)
    except Exception:
        return {}


def _remember(tool_context: ToolContext | None, **pairs: Any) -> None:
    """Persist to session state AND in-process env (dev-server turn-visibility fallback)."""
    if tool_context is not None:
        try:
            for k, v in pairs.items():
                tool_context.state[k] = v
        except Exception:
            pass
    for k, v in pairs.items():
        env_key = {"frappe_base_url": "FRAPPE_BASE_URL", "frappe_username": "FRAPPE_USERNAME"}.get(k)
        if env_key and v:
            os.environ[env_key] = str(v)
    if pairs.get("frappe_sid"):
        os.environ["FRAPPE_SID"] = str(pairs["frappe_sid"])


def _login_request(base: str, username: str, password: str) -> tuple[str, str]:
    """Raw login. Returns (sid, full_name) or raises."""
    with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
        r = c.post(
            f"{base}/api/method/login",
            json={"usr": username, "pwd": password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    if r.status_code != 200:
        raise RuntimeError(f"Login HTTP {r.status_code}: {r.text[:200]}")
    body = r.json()
    if body.get("message") != "Logged In":
        raise RuntimeError(f"Login rejected: {str(body)[:200]}")
    sid = r.cookies.get("sid", "")
    if not sid:
        raise RuntimeError("Login ok but no sid cookie returned.")
    return sid, body.get("full_name", username)


def _ensure_auth(
    tool_context: ToolContext | None,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    username: str = "",
    password: str = "",
) -> tuple[str, dict[str, str], dict[str, str], str]:
    """Resolve (base, headers, cookies, error), auto-logging in if needed.

    Order: explicit token args > stored/env token > stored/env sid >
    username+password login (args > state > env).
    """
    state = _state_dict(tool_context)
    base = (
        base_url or state.get("frappe_base_url", "") or os.getenv("FRAPPE_BASE_URL", "")
    ).strip().rstrip("/")
    if not base:
        return "", {}, {}, "Missing Frappe base_url."
    key = (
        api_key or state.get("frappe_api_key", "") or os.getenv("FRAPPE_API_KEY", "")
    ).strip()
    secret = (
        api_secret
        or state.get("frappe_api_secret", "")
        or os.getenv("FRAPPE_API_SECRET", "")
    ).strip()
    if key and secret and "put_" not in key and "put_" not in secret:
        return base, _headers(key, secret), {}, ""
    sid = (state.get("frappe_sid", "") or os.getenv("FRAPPE_SID", "")).strip()
    if sid:
        return base, _headers("__sid__", ""), {"sid": sid}, ""
    user = (
        username
        or state.get("frappe_username", "")
        or os.getenv("FRAPPE_USERNAME", "")
    ).strip()
    pwd = (
        password
        or state.get("frappe_password", "")
        or os.getenv("FRAPPE_PASSWORD", "")
    )
    if user and pwd:
        try:
            sid, full = _login_request(base, user, pwd)
        except Exception as e:
            return base, {}, {}, f"Auto-login as {user} failed: {_safe_err(e)}"
        _remember(tool_context, frappe_base_url=base, frappe_username=user, frappe_sid=sid, frappe_user=full, frappe_connected=True)
        return base, _headers("__sid__", ""), {"sid": sid}, ""
    return base, {}, {}, (
        "No valid Frappe auth: provide api_key/api_secret or username/password "
        "(login_frappe), or set FRAPPE_* env vars."
    )


def _headers(key: str, secret: str) -> dict[str, str]:
    if key == "__sid__":
        return {"Content-Type": "application/json", "Accept": "application/json"}
    return {
        "Authorization": f"token {key}:{secret}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def login_frappe(
    base_url: str,
    username: str,
    password: str,
    tool_context: ToolContext,
) -> dict:
    """Login to Frappe with username/password, store session sid in state.

    Args:
        base_url: e.g. http://10.0.13.119:9000 (no trailing slash).
        username: Frappe username (e.g. Administrator).
        password: Frappe password (stored in session state only, never echoed).

    Returns:
        Dict with ok, user, host (never the password or sid).
    """
    base = (base_url or os.getenv("FRAPPE_BASE_URL", "")).strip().rstrip("/")
    if not base:
        return {"ok": False, "error": "base_url missing."}
    try:
        sid, full = _login_request(base, username, password)
        _remember(
            tool_context,
            frappe_base_url=base,
            frappe_username=username,
            frappe_sid=sid,
            frappe_user=full,
            frappe_connected=True,
        )
        return {"ok": True, "user": full, "host": base}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def _safe_err(exc: Exception) -> str:
    msg = str(exc)
    # redact anything that looks like a secret
    msg = re.sub(r"token \S+:\S+", "token <redacted>", msg)
    return msg[:500]


def set_frappe_creds(
    base_url: str,
    api_key: str,
    api_secret: str,
    tool_context: ToolContext,
) -> dict:
    """Store Frappe connection creds in session state (not logged).

    Args:
        base_url: e.g. https://erp.example.com (no trailing slash).
        api_key: Frappe API key.
        api_secret: Frappe API secret.

    Returns:
        Dict with base_url host + which keys are set (never secret values).
    """
    base = base_url.strip().rstrip("/")
    try:
        tool_context.state["frappe_base_url"] = base
        tool_context.state["frappe_api_key"] = api_key.strip()
        tool_context.state["frappe_api_secret"] = api_secret.strip()
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}
    return {"ok": True, "base_url": base, "keys_set": ["frappe_api_key", "frappe_api_secret"]}


def connect_frappe(
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Validate Frappe creds against the logged-user endpoint.

    Returns:
        Dict with ok, user, host on success; ok False + error otherwise.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            r = c.get(
                f"{base}/api/method/frappe.auth.get_logged_user",
                headers=headers,
                cookies=cookies,
            )
        if r.status_code == 200:
            user = r.json().get("message", "")
            try:
                tool_context.state["frappe_connected"] = True
                tool_context.state["frappe_user"] = user
            except Exception:
                pass
            return {"ok": True, "user": user, "host": base}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def list_projects(
    tool_context: ToolContext,
    limit: int = 20,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """List Frappe Projects (name, status, expected dates).

    Returns:
        Dict with ok + projects list.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            r = c.get(
                f"{base}/api/resource/Project",
                headers=headers,
                cookies=cookies,
                params={
                    "fields": '["name","project_name","status","expected_start_date","expected_end_date"]',
                    "limit_page_length": str(max(1, min(limit, 100))),
                },
            )
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        return {"ok": True, "projects": r.json().get("data", [])}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


_PRIORITY_MAP = {"p0": "Urgent", "p1": "High", "p2": "Medium", "p3": "Low",
                 "must": "High", "should": "Medium", "could": "Low", "won't": "Low"}


def _frappe_priority(value: str) -> str:
    """Map a WBS priority (P0-P3 / MoSCoW) to a Frappe Task priority."""
    return _PRIORITY_MAP.get((value or "").strip().lower(), "Medium")


def _task_body(row: dict, proj_id: str) -> dict[str, Any]:
    """Frappe Task payload from one WBS row — dates and priority included."""
    body: dict[str, Any] = {
        "subject": f"{row['id']} {row['title']}"[:140],
        "project": proj_id,
        "status": "Open",
        "priority": _frappe_priority(row.get("priority", "")),
        "description": (f"WBS {row['id']} | owner={row.get('owner', '')} | "
                        f"deps={row.get('deps', '')} | est={row.get('est', '')} | "
                        f"trace={row.get('trace', '')}"),
    }
    if row.get("start"):
        body["exp_start_date"] = row["start"]
    if row.get("end"):
        body["exp_end_date"] = row["end"]
    return body


def _task_wbs_id(subject: str, description: str) -> str:
    """The WBS id a Frappe Task carries (leading subject id or the WBS trace)."""
    m = re.match(r"\s*(T-\d+)\b", subject or "")
    if m:
        return m.group(1)
    m = re.search(r"\bWBS\s+(T-\d+)\b", description or "")
    return m.group(1) if m else ""


def _project_task_docs(c, base: str, headers: dict, cookies: dict,
                       proj_id: str) -> dict[str, dict]:
    """WBS id -> existing Frappe Task doc for one project (empty on any failure)."""
    out: dict[str, dict] = {}
    try:
        r = c.get(
            f"{base}/api/resource/Task", headers=headers, cookies=cookies,
            params={"filters": json.dumps([["project", "=", proj_id]]),
                    "fields": json.dumps(["name", "subject", "description",
                                          "exp_start_date", "exp_end_date", "depends_on"]),
                    "limit_page_length": "0"})
        if r.status_code == 200:
            for t in (r.json().get("data") or []):
                wid = _task_wbs_id(t.get("subject", ""), t.get("description", ""))
                if wid:
                    out.setdefault(wid, t)
    except Exception:
        pass
    return out


def _project_verified(base: str, headers: dict, cookies: dict, proj_id: str) -> bool:
    """Read a Project back: a create response alone is never evidence."""
    try:
        r = _call(base, headers, cookies, "GET",
                  f"/api/resource/Project/{quote(str(proj_id))}")
        return r.status_code == 200
    except Exception:
        return False


def newest_project_name(base: str, headers: dict, cookies: dict,
                        timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Name of the most recently created Project on the site (GET only)."""
    try:
        r = _call(base, headers, cookies, "GET", "/api/resource/Project", timeout=timeout,
                  params={"fields": json.dumps(["name"]),
                          "order_by": "creation desc", "limit_page_length": "1"})
        if r.status_code == 200:
            rows = r.json().get("data") or []
            if rows:
                return str(rows[0].get("name") or "")
    except Exception:
        pass
    return ""


def project_facts_readonly(base: str, headers: dict, cookies: dict, proj_id: str,
                           timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """GET-only facts about a project and its tasks — never writes, never raises.

    The gate judges Frappe by recorded evidence. When the agent built a real
    project through a path that recorded nothing, that reads as missing; these are
    the same facts, read from the site, so the gate reports reality instead of
    depending on which tool the model happened to call.
    """
    out = {"ok": False, "tasks_created": 0, "tasks_dated": 0, "deps_linked": 0}
    if not proj_id:
        return out
    try:
        rp = _call(base, headers, cookies, "GET",
                   f"/api/resource/Project/{quote(str(proj_id))}", timeout=timeout)
        if rp.status_code != 200:
            return out
        rt = _call(base, headers, cookies, "GET", "/api/resource/Task", timeout=timeout,
                   params={"filters": json.dumps([["project", "=", str(proj_id)]]),
                           "fields": json.dumps(["name", "subject", "description",
                                                 "exp_start_date", "exp_end_date",
                                                 "depends_on"]),
                           "limit_page_length": "0"})
        tasks = (rt.json().get("data") or []) if rt.status_code == 200 else []
        dated = [t for t in tasks if t.get("exp_start_date") and t.get("exp_end_date")]
        linked = [t for t in tasks if t.get("depends_on")]
        out.update({"ok": True, "tasks_created": len(tasks),
                    "tasks_dated": len(dated), "deps_linked": len(linked)})
    except Exception:
        pass
    return out


def custom_doctype_names(base: str, headers: dict, cookies: dict,
                         timeout: float = _DEFAULT_TIMEOUT) -> list[dict]:
    """GET-only list of custom DocTypes on the site: [{name, istable}]."""
    try:
        r = _call(base, headers, cookies, "GET", "/api/resource/DocType", timeout=timeout,
                  params={"filters": json.dumps([["custom", "=", 1]]),
                          "fields": json.dumps(["name", "istable"]),
                          "limit_page_length": "0"})
        if r.status_code == 200:
            return [d for d in (r.json().get("data") or []) if d.get("name")]
    except Exception:
        pass
    return []


_SKIP_FIELDTYPES = {"Section Break", "Column Break", "Tab Break", "Table",
                    "Table MultiSelect", "HTML", "Button", "Fold", "Heading",
                    "Image", "Attach", "Attach Image", "Geolocation", "Signature",
                    "Barcode", "Code", "Password"}


def _first_doc_name(base: str, headers: dict, cookies: dict, doctype: str) -> str:
    """Name of any existing document of `doctype` (GET only) — for required Links."""
    if not doctype:
        return ""
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/{quote(doctype)}",
                  params={"fields": json.dumps(["name"]), "limit_page_length": "1"})
        if r.status_code == 200:
            rows = r.json().get("data") or []
            if rows:
                return str(rows[0].get("name") or "")
    except Exception:
        pass
    return ""


def _unused_link_value(base: str, headers: dict, cookies: dict, doctype: str,
                       fieldname: str, target_doctype: str,
                       timeout: float = _DEFAULT_TIMEOUT) -> str:
    """An existing target that no record of `doctype` already points at (GET only).

    A required Link is often also unique — one cost per work order, say — so
    reusing the first target makes the create fail with UniqueValidationError
    (observed on `Work Order Cost`: "Duplicate entry 'MFG-WO-2026-00001' for key
    'work_order'"). Pick a target with no record referencing it yet.
    """
    if not target_doctype:
        return ""
    used: set[str] = set()
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/{quote(doctype)}",
                  params={"fields": json.dumps([fieldname]),
                          "limit_page_length": "0"}, timeout=timeout)
        if r.status_code == 200:
            used = {str(d.get(fieldname)) for d in (r.json().get("data") or [])}
    except Exception:
        pass
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/{quote(target_doctype)}",
                  params={"fields": json.dumps(["name"]), "order_by": "creation desc",
                          "limit_page_length": "50"}, timeout=timeout)
        rows = (r.json().get("data") or []) if r.status_code == 200 else []
    except Exception:
        rows = []
    for row in rows:
        name = str(row.get("name") or "")
        if name and name not in used:
            return name
    # every target is taken: fall back to the first, which reports the real conflict
    return _first_doc_name(base, headers, cookies, target_doctype)


def doctype_has_records(base: str, headers: dict, cookies: dict, doctype: str,
                        timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Does this DocType already hold a real document? (GET only)"""
    if not doctype:
        return False
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/{quote(doctype)}",
                  params={"fields": json.dumps(["name"]),
                          "limit_page_length": "1"}, timeout=timeout)
        return r.status_code == 200 and bool(r.json().get("data"))
    except Exception:
        return False


def smoke_sample(base: str, headers: dict, cookies: dict, doctype: str,
                 timeout: float = _DEFAULT_TIMEOUT) -> dict:
    """Minimal valid field values for a smoke-test record, from the DocType meta.

    Fills only REQUIRED fields, by fieldtype, recursing into required child tables
    (a missing mandatory child row fails the create with HTTP 417 — observed on
    `Supplier Invoice`, whose `items` table is mandatory). Deterministic and
    read-only: the caller does the write. A required Link points at an existing
    document; when no target exists the field is omitted and the caller's create
    will fail honestly rather than fabricate a reference.
    """
    return _sample_fields(base, headers, cookies, doctype, timeout=timeout)


def _sample_fields(base: str, headers: dict, cookies: dict, doctype: str,
                   depth: int = 0, timeout: float = _DEFAULT_TIMEOUT) -> dict:
    out: dict[str, Any] = {}
    if depth > 2 or not doctype:
        return out
    try:
        r = _call(base, headers, cookies, "GET",
                  f"/api/resource/DocType/{quote(str(doctype))}", timeout=timeout)
        if r.status_code != 200:
            return out
        meta = r.json().get("data") or {}
        for f in (meta.get("fields") or []):
            fn = str(f.get("fieldname") or "")
            ftype = str(f.get("fieldtype") or "")
            options = str(f.get("options") or "")
            if not fn or not f.get("reqd"):
                continue
            if ftype in ("Table", "Table MultiSelect"):
                child = _sample_fields(base, headers, cookies, options,
                                       depth + 1, timeout)
                if child:
                    out[fn] = [{**child, "doctype": options}]
                continue
            if ftype in _SKIP_FIELDTYPES:
                continue
            if "Int" in ftype:
                out[fn] = 1
            elif ftype in ("Float", "Currency", "Percent"):
                out[fn] = 1
            elif ftype in ("Date", "Datetime"):
                out[fn] = datetime.date.today().isoformat()
            elif ftype == "Check":
                out[fn] = 0
            elif ftype == "Time":
                out[fn] = "09:00:00"
            elif ftype == "Link":
                target = _unused_link_value(base, headers, cookies, doctype, fn, options)
                if target:
                    out[fn] = target
            elif ftype in ("Select", "Dynamic Link"):
                choices = [o for o in options.split("\n") if o.strip()]
                if choices:
                    out[fn] = choices[0].strip()
            else:
                out[fn] = "smoke"
    except Exception:
        pass
    return out


def _sync_wbs_tasks(c, base: str, headers: dict, cookies: dict, proj_id: str,
                    rows: list, all_rows: list) -> dict:
    """Create only the missing WBS tasks, then verify the tasks and dependencies.

    A task already on the project (matched by its WBS id) is reused, never
    re-posted, so a second call cannot duplicate the WBS. Task and dependency
    counts are read back from the project, so a failed POST or an unlinked
    dependency is never reported as mapped.
    """
    existing = _project_task_docs(c, base, headers, cookies, proj_id)
    created: list[str] = []
    reused: list[str] = []
    failed: list[dict] = []
    task_info_by_wbs: dict[str, dict] = {
        wid: {"name": doc.get("name", ""), "subject": doc.get("subject", "")}
        for wid, doc in existing.items()}
    for row in rows:
        if row["id"] in existing:
            reused.append(row["id"])
            continue
        body = _task_body(row, proj_id)
        try:
            rt = c.post(f"{base}/api/resource/Task", headers=headers,
                        cookies=cookies, json=body)
            if rt.status_code in (200, 201):
                created.append(row["id"])
                tname = ((rt.json().get("data") or {}).get("name") or "")
                if tname:
                    task_info_by_wbs[row["id"]] = {"name": tname,
                                                   "subject": body["subject"]}
            else:
                failed.append({"id": row["id"], "error": f"HTTP {rt.status_code}"})
        except Exception as e:
            failed.append({"id": row["id"], "error": _safe_err(e)})
    linked, dep_errors = _link_deps(c, base, headers, cookies, rows, task_info_by_wbs)
    present = _project_task_docs(c, base, headers, cookies, proj_id)
    dated = sum(1 for r in all_rows
                if r["id"] in present and present[r["id"]].get("exp_start_date")
                and present[r["id"]].get("exp_end_date"))
    deps_mapped = sum(1 for r in all_rows
                      if r["id"] in present and present[r["id"]].get("depends_on"))
    return {"created": created, "reused": reused, "failed": failed,
            "tasks_verified": sum(1 for r in all_rows if r["id"] in present),
            "tasks_dated": dated, "deps_linked": deps_mapped,
            "linked_now": linked, "dependency_errors": dep_errors}


def _link_deps(c, base: str, headers: dict, cookies: dict, rows: list,
               task_info_by_wbs: dict) -> tuple[int, list[dict]]:
    """Wire WBS dependencies as real Frappe 'Task Depends On' rows.

    Frappe silently drops a dependency row that lacks the `subject` fetch
    field, so both task and subject are sent. Returns (linked, errors): a
    dependency that failed or referenced a task that is not present is reported,
    never silently dropped.
    """
    linked = 0
    errors: list[dict] = []
    for row in rows:
        deps = re.findall(r"T-\d+", row.get("deps", "") or "")
        info = task_info_by_wbs.get(row["id"]) or {}
        tname = info.get("name", "")
        if not deps:
            continue
        missing = [d for d in deps if d not in task_info_by_wbs]
        if missing:
            errors.append({"id": row["id"],
                           "error": f"unresolved dependency ids: {', '.join(missing)}"})
        targets = [task_info_by_wbs[d] for d in deps if d in task_info_by_wbs]
        if not tname:
            errors.append({"id": row["id"], "error": "task not present to link dependencies"})
            continue
        if not targets:
            continue
        try:
            rd = c.put(
                f"{base}/api/resource/Task/{quote(tname)}", headers=headers, cookies=cookies,
                json={"depends_on": [{"task": t.get("name", ""), "subject": t.get("subject", "")}
                                     for t in targets]})
            if rd.status_code in (200, 201):
                linked += 1
            else:
                errors.append({"id": row["id"], "error": f"HTTP {rd.status_code}"})
        except Exception as e:
            errors.append({"id": row["id"], "error": _safe_err(e)})
    return linked, errors


def create_project_from_plan(
    project_name: str,
    tool_context: ToolContext,
    company: str = "",
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    max_tasks: int = 40,
    start: int = 0,
) -> dict:
    """Create a Frappe Project + Tasks from the project_plan in session state.

    Reads state['project_plan'] (or 'ProjectPlan_*'), creates one Project and
    one Task per WBS row (T-001..) found in the plan markdown table. Idempotent:
    a task already on the project (matched by its WBS id) is reused, never
    re-posted, so a second call cannot duplicate the WBS.

    Args:
        project_name: Frappe project name to create.
        company: optional Frappe Company link.
        max_tasks: cap on tasks created per call (default 40).
        start: 0-based WBS row offset to resume from (default 0).

    Returns:
        Dict with project id, tasks created/reused, rows remaining, and the
        saved mapping file.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    state = _state_dict(tool_context)
    plan = state.get("project_plan", "") or ""
    if not plan:
        for k, v in state.items():
            if k.lower().startswith("projectplan") and isinstance(v, str) and len(v) > 500:
                plan = v
                break
    if not plan or len(plan) < 200:
        # Workspace-scoped fallback ONLY: same-project plan file.
        # NEVER a global search — cross-project reads are forbidden.
        try:
            from .workspace import bound_workspace_id, ProjectWorkspace
            pid = ""
            try:
                pid = bound_workspace_id(tool_context.state)
            except Exception:
                pass
            if pid:
                ws = ProjectWorkspace(pid, create=False)
                if ws.exists():
                    found = ws.find_artifact("ProjectPlan_", ".md", largest=True)
                    if found:
                        plan = found.read_text(encoding="utf-8")
                        try:
                            tool_context.state["project_plan"] = plan
                        except Exception:
                            pass
        except Exception:
            pass
    if not plan or len(plan) < 200:
        return {
            "ok": False,
            "error": "No project_plan in this project workspace. Run project_agent first or paste the plan.",
        }
    # Header-driven WBS parsing (shared with the gates): dependency/RACI/timeline
    # tables also contain T-xxx ids and must never become tasks, and columns are
    # mapped by NAME so owner/priority/start/end land in the right fields.
    from .sections import parse_wbs_rows
    all_rows = parse_wbs_rows(plan)
    total_rows = len(all_rows)
    if not all_rows:
        return {
            "ok": False,
            "error": ("No WBS task rows found in project_plan. The plan needs a markdown "
                      "pipe table whose header names an owner/role and an estimate, and "
                      "each row must start with a T-nnn id (e.g. T-001)."),
            "rows_total": 0,
            "rows_remaining": 0,
            "dependency_errors": [],
        }
    batch = max(1, min(max_tasks, 100))
    try:
        lo = max(0, int(start or 0))
    except Exception:
        lo = 0
    hi = lo + batch
    rows = all_rows[lo:hi]
    remaining = max(0, total_rows - hi)
    if not rows:
        return {
            "ok": False,
            "error": (f"start={lo} is at or past the {total_rows} WBS task rows — "
                      "nothing left to create."),
            "rows_total": total_rows,
            "rows_remaining": 0,
            "dependency_errors": [],
        }
    starts = [r["start"] for r in all_rows if r.get("start")]
    ends = [r["end"] for r in all_rows if r.get("end")]
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            payload: dict[str, Any] = {"project_name": project_name, "status": "Open"}
            if company:
                payload["company"] = company
            if starts:
                payload["expected_start_date"] = min(starts)
            if ends:
                payload["expected_end_date"] = max(ends)
            rp = c.post(
                f"{base}/api/resource/Project",
                headers=headers,
                cookies=cookies,
                json=payload,
            )
            if rp.status_code not in (200, 201):
                # never duplicate: reuse an existing Project with the same name
                rq = c.get(f"{base}/api/resource/Project", headers=headers, cookies=cookies,
                           params={"filters": json.dumps([["project_name", "=", project_name]]),
                                   "fields": json.dumps(["name"]),
                                   "limit_page_length": "1"})
                existing = (rq.json().get("data") or []) if rq.status_code == 200 else []
                if not existing:
                    _record_evidence(tool_context, "project", False, name=project_name,
                                     error=f"HTTP {rp.status_code}: {rp.text[:250]}")
                    return {"ok": False,
                            "error": f"Project create HTTP {rp.status_code}: {rp.text[:300]}"}
                proj_id = existing[0].get("name", project_name)
                # keep the reused project's timeline in sync with the WBS
                if starts or ends:
                    upd: dict[str, Any] = {}
                    if starts:
                        upd["expected_start_date"] = min(starts)
                    if ends:
                        upd["expected_end_date"] = max(ends)
                    c.put(f"{base}/api/resource/Project/{quote(str(proj_id))}",
                          headers=headers, cookies=cookies, json=upd)
                sync = _sync_wbs_tasks(c, base, headers, cookies, proj_id, rows, all_rows)
                verified = _project_verified(base, headers, cookies, proj_id)
                _record_evidence(tool_context, "project", verified, name=str(proj_id),
                                 action="reused",
                                 tasks_created=sync["tasks_verified"],
                                 tasks_new=len(sync["created"]),
                                 tasks_reused=len(sync["reused"]),
                                 tasks_verified=sync["tasks_verified"],
                                 tasks_failed=len(sync["failed"]),
                                 tasks_dated=sync["tasks_dated"],
                                 deps_linked=sync["deps_linked"],
                                 dependency_errors=sync["dependency_errors"][:10],
                                 project_start=min(starts) if starts else "",
                                 project_end=max(ends) if ends else "",
                                 verified=verified, traces=[])
                return {"ok": True, "project": proj_id, "reused": True,
                        "tasks_created": len(sync["created"]),
                        "tasks_reused": len(sync["reused"]),
                        "tasks_failed": len(sync["failed"]),
                        "tasks_dated": sync["tasks_dated"],
                        "tasks_verified": sync["tasks_verified"],
                        "deps_linked": sync["deps_linked"],
                        "dependency_errors": sync["dependency_errors"][:10],
                        "project_start": min(starts) if starts else "",
                        "project_end": max(ends) if ends else "",
                        "failed": sync["failed"][:10],
                        "rows_total": total_rows,
                        "rows_remaining": remaining,
                        "next_start": hi if remaining else 0,
                        "verified": verified}
            proj = (rp.json().get("data") or {})
            proj_id = proj.get("name", project_name)
            # Best-effort: surface the requirement traceability on the Project
            # itself so the mapping is visible in the UI. Never fatal — a site
            # without the `notes` field must not fail the project creation.
            try:
                trace_ids = sorted({t for r in rows for t in re.findall(
                    r"\b(?:US|BR|FR|UC|G|BN|OPT|T|TECH)-\d+\b", r.get("trace", "") or "")})
                note = (f"<b>Generated by WorkSimplified from the validated plan.</b><br>"
                        f"{len(rows)} delivery tasks traced to: "
                        f"{', '.join(trace_ids) or 'the validated requirement set'}")
                c.put(f"{base}/api/resource/Project/{quote(str(proj_id))}",
                      headers=headers, cookies=cookies, json={"notes": note})
            except Exception:
                pass
            sync = _sync_wbs_tasks(c, base, headers, cookies, proj_id, rows, all_rows)
        mapping_md = (
            f"# Frappe setup — {project_name}\n\nProject: `{proj_id}`\nHost: {base}\n"
            f"Timeline: {min(starts) if starts else '-'} → {max(ends) if ends else '-'}\n\n"
            + "\n".join(f"- {t}" for t in sync["created"])
            + (f"\n\nFailed: {sync['failed']}\n" if sync["failed"] else "\n")
        )
        saved = save_doc(f"FrappeSetup_{project_name}", mapping_md, tool_context)
        try:
            tool_context.state["frappe_project"] = proj_id
        except Exception:
            pass
        # verify by read-back (a create response alone is not evidence). The read-back
        # must never cost us the evidence entry itself: this site answers some
        # endpoints with HTTP 417, and an exception here used to abort the whole
        # record — leaving real projects and tasks invisible to the gate
        # ("evidence=missing", "tasks_created 0" while 20 tasks existed).
        try:
            verified = _project_verified(base, headers, cookies, proj_id)
        except Exception:
            verified = False
        _record_evidence(tool_context, "project", verified, name=str(proj_id),
                         tasks_created=sync["tasks_verified"],
                         tasks_new=len(sync["created"]),
                         tasks_reused=len(sync["reused"]),
                         tasks_verified=sync["tasks_verified"],
                         tasks_failed=len(sync["failed"]),
                         tasks_dated=sync["tasks_dated"],
                         deps_linked=sync["deps_linked"],
                         dependency_errors=sync["dependency_errors"][:10],
                         project_start=min(starts) if starts else "",
                         project_end=max(ends) if ends else "",
                         verified=verified)
        return {
            "ok": True,
            "project": proj_id,
            "tasks_created": len(sync["created"]),
            "tasks_reused": len(sync["reused"]),
            "tasks_failed": len(sync["failed"]),
            "tasks_dated": sync["tasks_dated"],
            "tasks_verified": sync["tasks_verified"],
            "deps_linked": sync["deps_linked"],
            "dependency_errors": sync["dependency_errors"][:10],
            "project_start": min(starts) if starts else "",
            "project_end": max(ends) if ends else "",
            "failed": sync["failed"][:10],
            "rows_total": total_rows,
            "rows_remaining": remaining,
            "next_start": hi if remaining else 0,
            "verified": verified,
            "mapping_file": saved.get("path", ""),
        }
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def update_task(
    task_id: str,
    status: str,
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    comment: str = "",
) -> dict:
    """Update a Frappe Task status (Open/Working/Completed/Cancelled).

    Returns:
        Dict with ok + task id + status.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    allowed = {"Open", "Working", "Pending Review", "Completed", "Cancelled"}
    if status not in allowed:
        return {"ok": False, "error": f"status must be one of {sorted(allowed)}"}
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            r = c.put(
                f"{base}/api/resource/Task/{task_id}",
                headers=headers,
                cookies=cookies,
                json={"status": status},
            )
            if r.status_code not in (200, 201):
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
            if comment:
                c.post(
                    f"{base}/api/method/frappe.desk.form.utils.add_comment",
                    headers=headers,
                    cookies=cookies,
                    json={"reference_doctype": "Task", "reference_name": task_id, "content": comment[:1000], "comment_email": "", "comment_by": ""},
                )
        return {"ok": True, "task": task_id, "status": status}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


# ---- Frappe environment state (orchestration support; record-only, no fabrication) ----

def record_frappe_env(
    tool_context: ToolContext,
    info_json: dict,
) -> dict:
    """Record observed Frappe environment facts into shared PROJECT_CONTEXT.

    Only store facts returned by actual Frappe tools or explicitly provided
    by the user/agent (version, apps, DocTypes, workflows, roles,
    permissions, customizations, validation results). Never invent values.

    Args:
        info_json: e.g. {"frappe_version": "15.x", "apps": [...], "custom_doctypes": [...] remaining keys merge as-is.
    """
    from .project_context import commit, get_context
    try:
        ctx = get_context(tool_context.state)
        env = ctx.get("frappe_state", {})
        if not isinstance(env, dict):
            env = {}
        for k, v in (info_json or {}).items():
            env[str(k)[:80]] = v
        ctx["frappe_state"] = env
        commit(tool_context.state)
        return {"ok": True, "keys": sorted(env.keys())}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def discover_frappe_env(
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Best-effort discovery of Frappe environment facts (version, user).

    Only stores what endpoints actually return. Endpoints that fail are
    reported, never fabricated.
    """
    from .project_context import commit, get_context
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    found: dict[str, Any] = {}
    failures: list[str] = []
    probes = {
        "version": "/api/method/version",
        "logged_user": "/api/method/frappe.auth.get_logged_user",
        "installed_apps": "/api/method/frappe.apps.get_apps",
    }
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            for name, path in probes.items():
                try:
                    r = c.get(f"{base}{path}", headers=headers, cookies=cookies)
                    if r.status_code == 200:
                        found[name] = r.json()
                    else:
                        failures.append(f"{name}: HTTP {r.status_code}")
                except Exception as e:
                    failures.append(f"{name}: {_safe_err(e)}")
            # facts about the object graph the engineer will work with
            for name, (dt, fields) in {
                "custom_doctypes": ("DocType", ["name", "module"]),
                "workflows": ("Workflow", ["name", "document_type", "is_active"]),
                "roles": ("Role", ["name"]),
            }.items():
                try:
                    r = c.get(
                        f"{base}/api/resource/{quote(dt)}",
                        headers=headers,
                        cookies=cookies,
                        params={
                            "filters": json.dumps([["custom", "=", 1]]) if dt == "DocType" else "[]",
                            "fields": json.dumps(fields),
                            "limit_page_length": "100",
                        },
                    )
                    if r.status_code == 200:
                        found[name] = r.json().get("data", [])
                    else:
                        failures.append(f"{name}: HTTP {r.status_code}")
                except Exception as e:
                    failures.append(f"{name}: {_safe_err(e)}")
        if found:
            ctx = get_context(tool_context.state)
            env = ctx.get("frappe_state", {})
            if not isinstance(env, dict):
                env = {}
            env["discovered"] = found
            ctx["frappe_state"] = env
            commit(tool_context.state)
            _record_evidence(tool_context, "discovery", True,
                             facts=sorted(found.keys()), failures=failures[:10])
        return {"ok": True, "found": sorted(found.keys()), "failures": failures}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


# =====================================================================
# Production-grade Frappe engineering tools
#
# These give the agent the same capability a human Frappe engineer has:
# inspect, create/extend DocTypes, workflows, roles and permissions, run a
# real smoke test on the resulting documents, and record every claim as
# EVIDENCE. A write that is not verified is never reported as verified.
# =====================================================================

_FIELD_KEYS = {
    "fieldname", "label", "fieldtype", "options", "reqd", "unique", "default",
    "in_list_view", "description", "depends_on", "read_only", "hidden",
    "precision", "length", "insert_after", "in_standard_filter",
}
_PERM_KEYS = ("read", "write", "create", "submit", "cancel", "delete",
              "report", "export", "import", "share", "print", "email")


def _jload(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return default


def _evidence_list(tool_context: ToolContext | None) -> list:
    from .project_context import get_context
    try:
        ctx = get_context(tool_context.state)
        env = ctx.get("frappe_state")
        if not isinstance(env, dict):
            env = {}
            ctx["frappe_state"] = env
        ev = env.get("evidence")
        if not isinstance(ev, list):
            ev = []
            env["evidence"] = ev
        return ev
    except Exception:
        return []


def _record_evidence(tool_context: ToolContext | None, kind: str, ok: bool = True,
                     traces: Any = None, **fields: Any) -> dict:
    """Append one evidence entry to PROJECT_CONTEXT.frappe_state (never fabricated)."""
    entry: dict[str, Any] = {
        "kind": str(kind)[:40],
        "ok": bool(ok),
        "traces": [str(t)[:20] for t in (_jload(traces, []) if not isinstance(traces, list) else traces)][:20],
    }
    for k, v in fields.items():
        entry[str(k)[:40]] = v if isinstance(v, (int, float, bool, list, dict, type(None))) else str(v)[:500]
    try:
        from .project_context import commit, utcnow
        entry["ts"] = utcnow()
        ev = _evidence_list(tool_context)
        ev.append(entry)
        del ev[:-MAX_EVIDENCE]
        commit(tool_context.state)
    except Exception:
        pass
    return entry


def _call(base: str, headers: dict, cookies: dict, method: str, path: str,
          params: dict | None = None, body: Any = None,
          timeout: float | None = None) -> httpx.Response:
    with httpx.Client(timeout=timeout or _DEFAULT_TIMEOUT) as c:
        return c.request(method, f"{base}{path}", headers=headers, cookies=cookies,
                         params=params, json=body)


def _doc_data(resp: httpx.Response) -> dict:
    try:
        d = resp.json()
        return d.get("data", d) if isinstance(d, dict) else {}
    except Exception:
        return {}


def _inspect_core(base: str, headers: dict, cookies: dict, doctype: str,
                  timeout: float | None = None) -> dict:
    """Read-back of a DocType: existence, fields, permissions, doc count."""
    out: dict[str, Any] = {
        "ok": True, "doctype": doctype, "exists": False, "custom": False,
        "module": "", "istable": False, "fields": [], "permissions": [],
        "doc_count": None, "_raw_fields": [], "_raw_permissions": [],
    }
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/DocType/{quote(doctype)}", timeout=timeout)
        if r.status_code == 200:
            d = _doc_data(r)
            fields = d.get("fields") or []
            perms = d.get("permissions") or []
            out.update(
                exists=True,
                custom=bool(d.get("custom")),
                istable=bool(d.get("istable")),
                module=d.get("module", ""),
                fields=[{k: f.get(k) for k in ("fieldname", "label", "fieldtype",
                                               "options", "reqd", "unique", "default")}
                        for f in fields],
                permissions=[{k: p.get(k) for k in ("role", *_PERM_KEYS)} for p in perms],
                _raw_fields=fields,
                _raw_permissions=perms,
            )
        elif r.status_code in (404, 403, 417):
            out["exists"] = False
            if r.status_code != 404:
                out["note"] = f"HTTP {r.status_code}: {r.text[:200]}"
        else:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
        # custom fields attached to the doctype (extension path)
        rf = _call(base, headers, cookies, "GET", f"/api/resource/{quote('Custom Field')}",
                   params={"filters": json.dumps([["dt", "=", doctype]]),
                           "fields": json.dumps(["fieldname", "label", "fieldtype", "options", "reqd"]),
                           "limit_page_length": "200"}, timeout=timeout)
        if rf.status_code == 200:
            out["custom_fields"] = _doc_data(rf)
            if isinstance(out["custom_fields"], dict):
                out["custom_fields"] = []
        # doc count doubles as a "is the table live?" probe
        rc = _call(base, headers, cookies, "GET", "/api/method/frappe.client.get_count",
                   params={"doctype": doctype}, timeout=timeout)
        if rc.status_code == 200:
            out["doc_count"] = rc.json().get("message")
        else:
            out["count_error"] = f"HTTP {rc.status_code}: {rc.text[:150]}"
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}
    return out


def inspect_doctype(
    doctype: str,
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Inspect the ACTUAL Frappe state of a DocType before changing anything.

    Returns existence, custom flag, module, fields, attached custom fields,
    permissions and document count. Never fabricates: unknown = error text.

    Args:
        doctype: DocType name, e.g. 'Travel Request'.

    Returns:
        Dict with ok, exists, fields[], custom_fields[], permissions[], doc_count.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    ins = _inspect_core(base, headers, cookies, doctype)
    _record_evidence(tool_context, "inspect", bool(ins.get("ok")),
                     name=doctype, exists=ins.get("exists"),
                     doc_count=ins.get("doc_count"))
    if ins.get("ok") and ins.get("exists"):
        # an inspection is a read-back: it refreshes the doctype's evidence
        _record_evidence(tool_context, "doctype", True, name=doctype,
                         action="inspected", verified=True,
                         istable=bool(ins.get("istable")),
                         table_ready=ins.get("doc_count") is not None)
    return ins


def _pick_module(base: str, headers: dict, cookies: dict, requested: str) -> str:
    """Choose an existing Module Def for a new custom DocType (never invent one)."""
    if requested.strip():
        return requested.strip()
    r = _call(base, headers, cookies, "GET", f"/api/resource/{quote('Module Def')}",
              params={"fields": json.dumps(["name"]), "limit_page_length": "0"})
    names = [m.get("name", "") for m in (_doc_data(r) or [])] if r.status_code == 200 else []
    for cand in ("Custom", "Core", "Desk"):
        if cand in names:
            return cand
    return names[0] if names else "Core"


def ensure_doctype(
    doctype: str,
    fields_json: Any,
    tool_context: ToolContext,
    permissions_json: Any = "",
    module: str = "",
    traces_json: Any = "",
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Create OR extend a DocType so it matches the required fields (idempotent).

    Never removes anything: an existing DocType only gains the missing fields
    (custom fields on standard DocTypes). Verifies by read-back and reports the
    table state; a site that still needs a migration is reported as a blocker,
    never claimed as done.

    Args:
        doctype: DocType name, e.g. 'Travel Expense Claim'.
        fields_json: JSON list of fields, e.g. [{"fieldname":"amount","fieldtype":"Currency"}].
        permissions_json: optional JSON {"Role Name": {"read":1,"write":1,...}}.
        module: optional Module Def for a NEW custom DocType (auto-picked if empty).
        traces_json: requirement ids this object implements, e.g. ["FR-001"].

    Returns:
        Dict with ok, action (created/extended/unchanged), fields_added, verified, table_ready.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    fields = _jload(fields_json, [])
    if not isinstance(fields, list) or not fields:
        return {"ok": False, "error": "fields_json must be a non-empty JSON list of field dicts"}
    clean: list[dict] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        fn = str(f.get("fieldname", "")).strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,60}", fn):
            return {"ok": False, "error": f"invalid fieldname {fn!r}: use lower_snake_case"}
        row = {k: f[k] for k in f if k in _FIELD_KEYS}
        row["fieldname"] = fn
        row.setdefault("label", fn.replace("_", " ").title())
        row.setdefault("fieldtype", "Data")
        clean.append(row)
    perms = _jload(permissions_json, {}) or {}
    traces = _jload(traces_json, []) or []

    ins = _inspect_core(base, headers, cookies, doctype)
    if not ins.get("ok"):
        _record_evidence(tool_context, "doctype", False, name=doctype,
                         error=ins.get("error", ""), traces=traces)
        return ins
    try:
        if ins["exists"]:
            existing = {str(f.get("fieldname")) for f in ins.get("fields", [])}
            custom_existing = {str(f.get("fieldname")) for f in (ins.get("custom_fields") or [])}
            missing = [f for f in clean if f["fieldname"] not in existing
                       and f["fieldname"] not in custom_existing]
            action = "unchanged"
            added: list[str] = []
            if missing:
                if ins.get("custom"):
                    body = {"fields": list(ins.get("_raw_fields") or []) + missing}
                    r = _call(base, headers, cookies, "PUT",
                              f"/api/resource/DocType/{quote(doctype)}", body=body)
                    if r.status_code not in (200, 201):
                        _record_evidence(tool_context, "doctype", False, name=doctype,
                                         error=f"HTTP {r.status_code}: {r.text[:300]}", traces=traces)
                        return {"ok": False, "error": f"DocType update HTTP {r.status_code}: {r.text[:300]}"}
                else:
                    for f in missing:
                        r = _call(base, headers, cookies, "POST",
                                  f"/api/resource/{quote('Custom Field')}",
                                  body={"dt": doctype, **f})
                        if r.status_code in (200, 201):
                            added.append(f["fieldname"])
                        else:
                            _record_evidence(tool_context, "doctype", False, name=doctype,
                                             error=f"Custom Field {f['fieldname']} HTTP {r.status_code}",
                                             traces=traces)
                            return {"ok": False,
                                    "error": f"Custom Field {f['fieldname']} HTTP {r.status_code}: {r.text[:250]}"}
                action = "extended"
                added = [f["fieldname"] for f in missing]
        else:
            mod = _pick_module(base, headers, cookies, module)
            perm_rows = []
            for role, flags in (perms.items() if isinstance(perms, dict) else []):
                row = {"role": role}
                row.update({k: int(bool(flags.get(k))) for k in _PERM_KEYS
                            if isinstance(flags, dict) and k in flags})
                perm_rows.append(row)
            payload = {"name": doctype, "module": mod, "custom": 1, "fields": clean}
            if perm_rows:
                payload["permissions"] = perm_rows
            r = _call(base, headers, cookies, "POST", f"/api/resource/DocType", body=payload)
            if r.status_code not in (200, 201):
                _record_evidence(tool_context, "doctype", False, name=doctype,
                                 error=f"HTTP {r.status_code}: {r.text[:300]}", traces=traces)
                return {"ok": False,
                        "error": f"DocType create HTTP {r.status_code}: {r.text[:300]}"}
            action, added = "created", [f["fieldname"] for f in clean]
        # verify by read-back — never trust the write response
        ver = _inspect_core(base, headers, cookies, doctype)
        present = {str(f.get("fieldname")) for f in (ver.get("fields") or [])} | \
                  {str(f.get("fieldname")) for f in (ver.get("custom_fields") or [])}
        verified = bool(ver.get("exists")) and all(f["fieldname"] in present for f in clean)
        table_ready = ver.get("doc_count") is not None
        ok = bool(verified and table_ready)
        entry = _record_evidence(tool_context, "doctype", ok, name=doctype,
                                 action=action, verified=verified, table_ready=table_ready,
                                 not_ready=not table_ready,
                                 istable=bool(ver.get("istable") or ins.get("istable")),
                                 fields_added=added, traces=traces)
        return {"ok": ok, "doctype": doctype, "action": action, "fields_added": added,
                "verified": verified, "table_ready": table_ready, "not_ready": not table_ready,
                "istable": bool(ver.get("istable") or ins.get("istable")),
                "doc_count": ver.get("doc_count"),
                "note": "" if table_ready else
                        "documents not queryable yet (site may require a migration)",
                "evidence": entry}
    except Exception as e:
        _record_evidence(tool_context, "doctype", False, name=doctype,
                         error=_safe_err(e), traces=traces)
        return {"ok": False, "error": _safe_err(e)}


def ensure_role(
    role: str,
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Create a Role if it does not exist (idempotent, verified by read-back)."""
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    try:
        r = _call(base, headers, cookies, "GET", f"/api/resource/Role/{quote(role)}")
        if r.status_code == 200:
            verified = True
            action = "unchanged"
        else:
            r = _call(base, headers, cookies, "POST", "/api/resource/Role",
                      body={"role_name": role, "desk_access": 1})
            if r.status_code not in (200, 201):
                _record_evidence(tool_context, "role", False, name=role,
                                 error=f"HTTP {r.status_code}: {r.text[:250]}")
                return {"ok": False, "error": f"Role create HTTP {r.status_code}: {r.text[:250]}"}
            action = "created"
            v = _call(base, headers, cookies, "GET", f"/api/resource/Role/{quote(role)}")
            verified = v.status_code == 200
        _record_evidence(tool_context, "role", verified, name=role, action=action,
                         verified=verified)
        return {"ok": verified, "role": role, "action": action, "verified": verified}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def ensure_permissions(
    doctype: str,
    role: str,
    perms_json: Any,
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    traces_json: Any = "",
) -> dict:
    """Set role permissions on a DocType (merge semantics, verified by read-back).

    Args:
        doctype: DocType name.
        role: Role name.
        perms_json: JSON flags, e.g. {"read":1,"write":1,"create":1,"submit":1}.
        traces_json: requirement ids this covers.

    Returns:
        Dict with ok, role, doctype, flags, verified.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    flags = _jload(perms_json, {})
    if not isinstance(flags, dict) or not flags:
        return {"ok": False, "error": "perms_json must be a JSON object of permission flags"}
    traces = _jload(traces_json, []) or []
    ins = _inspect_core(base, headers, cookies, doctype)
    if not ins.get("ok") or not ins.get("exists"):
        return {"ok": False, "error": ins.get("error") or f"DocType {doctype} not found"}
    if ins.get("istable"):
        # child tables have no permissions of their own; the parent governs access
        _record_evidence(tool_context, "permissions", True, name=f"{doctype}:{role}",
                         note="child table: permissions are governed by the parent",
                         skipped=True, written=False, verified=False, traces=traces)
        return {"ok": False, "doctype": doctype, "role": role, "skipped": "child table",
                "written": False, "verified": False,
                "note": "child tables have no standalone permissions; configure them on the parent"}
    try:
        rows = list(ins.get("_raw_permissions") or [])
        target = next((p for p in rows if str(p.get("role")) == role), None)
        if target is None:
            target = {"role": role}
            rows.append(target)
        for k in _PERM_KEYS:
            if k in flags:
                target[k] = int(bool(flags[k]))
        r = _call(base, headers, cookies, "PUT", f"/api/resource/DocType/{quote(doctype)}",
                  body={"permissions": rows})
        if r.status_code not in (200, 201):
            _record_evidence(tool_context, "permissions", False, name=f"{doctype}:{role}",
                             error=f"HTTP {r.status_code}: {r.text[:250]}", traces=traces)
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:250]}"}
        ver = _inspect_core(base, headers, cookies, doctype)
        got = next((p for p in (ver.get("permissions") or [])
                    if str(p.get("role")) == role), {})
        verified = all(int(bool(got.get(k))) == int(bool(v)) for k, v in flags.items())
        _record_evidence(tool_context, "permissions", verified,
                         name=f"{doctype}:{role}", flags={k: int(bool(v)) for k, v in flags.items()},
                         verified=verified, traces=traces)
        return {"ok": verified, "doctype": doctype, "role": role,
                "flags": {k: int(bool(v)) for k, v in flags.items()}, "verified": verified}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def ensure_workflow(
    workflow_name: str,
    doctype: str,
    states_json: Any,
    transitions_json: Any,
    tool_context: ToolContext,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    traces_json: Any = "",
) -> dict:
    """Create OR update a Workflow over a DocType (idempotent, verified).

    Args:
        workflow_name: e.g. 'Travel Request Approval'.
        doctype: DocType the workflow applies to.
        states_json: [{"state":"Draft","doc_status":0}, {"state":"Approved","doc_status":1}].
        transitions_json: [{"state":"Draft","action":"Approve","next_state":"Approved",
                            "allowed_role":"Approver"}].
        traces_json: requirement ids this implements.

    Returns:
        Dict with ok, action, states, transitions, verified.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    states = _jload(states_json, [])
    transitions = _jload(transitions_json, [])
    traces = _jload(traces_json, []) or []
    if not isinstance(states, list) or not states:
        return {"ok": False, "error": "states_json must be a non-empty JSON list"}
    body = {
        "workflow_name": workflow_name,
        "document_type": doctype,
        "is_active": 1,
        "workflow_state_field": "workflow_state",
        # Frappe requires allow_edit (per state) and allowed (per transition);
        # default them so a workflow write never fails on a missing mandatory link
        "states": [{"state": str(s.get("state", "")),
                    "doc_status": str(s.get("doc_status", 0)),
                    "allow_edit": str(s.get("allow_edit") or "System Manager")}
                   for s in states if isinstance(s, dict) and s.get("state")],
        "transitions": [{"state": str(t.get("state", "")), "action": str(t.get("action", "")),
                         "next_state": str(t.get("next_state", "")),
                         "allowed": str(t.get("allowed_role") or "System Manager")}
                        for t in transitions
                        if isinstance(t, dict) and t.get("state") and t.get("next_state")],
    }
    if not body["states"]:
        return {"ok": False, "error": "no valid states provided"}
    try:
        # Workflow states link to the 'Workflow State' master: ensure them first
        # (idempotent) so the workflow write cannot fail on a missing link.
        for st in body["states"]:
            nm = st["state"]
            g = _call(base, headers, cookies, "GET", f"/api/resource/{quote('Workflow State')}/{quote(nm)}")
            if g.status_code != 200:
                _call(base, headers, cookies, "POST", f"/api/resource/{quote('Workflow State')}",
                      body={"workflow_state_name": nm})
        r = _call(base, headers, cookies, "GET", f"/api/resource/Workflow/{quote(workflow_name)}")
        if r.status_code == 200:
            r = _call(base, headers, cookies, "PUT",
                      f"/api/resource/Workflow/{quote(workflow_name)}", body=body)
            action = "updated"
        else:
            r = _call(base, headers, cookies, "POST", "/api/resource/Workflow", body=body)
            action = "created"
        if r.status_code not in (200, 201):
            _record_evidence(tool_context, "workflow", False, name=workflow_name,
                             error=f"HTTP {r.status_code}: {r.text[:250]}", traces=traces)
            return {"ok": False, "error": f"Workflow HTTP {r.status_code}: {r.text[:250]}"}
        v = _call(base, headers, cookies, "GET", f"/api/resource/Workflow/{quote(workflow_name)}")
        ok = v.status_code == 200
        n_states = len(_doc_data(v).get("states") or []) if ok else 0
        verified = ok and n_states >= len(body["states"])
        _record_evidence(tool_context, "workflow", verified, name=workflow_name,
                         doctype=doctype, action=action, states=len(body["states"]),
                         transitions=len(body["transitions"]), verified=verified, traces=traces)
        return {"ok": verified, "workflow": workflow_name, "doctype": doctype,
                "action": action, "states": len(body["states"]),
                "transitions": len(body["transitions"]), "verified": verified}
    except Exception as e:
        return {"ok": False, "error": _safe_err(e)}


def smoke_test(
    doctype: str,
    tool_context: ToolContext,
    sample_json: Any = "",
    workflow_action: str = "",
    cleanup: bool = True,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    traces_json: Any = "",
) -> dict:
    """Prove the implementation works on the REAL site: create → read → (transition) → clean up.

    Args:
        doctype: DocType to exercise.
        sample_json: JSON object of field values for the test document.
        workflow_action: optional workflow action to apply, e.g. 'Approve'.
        cleanup: delete the test document afterwards (default True).
        traces_json: requirement ids this verifies.

    Returns:
        Dict with ok, created, read_back (key fields), final_state, cleaned_up, blockers.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    sample = _jload(sample_json, {})
    if not isinstance(sample, dict):
        sample = {}
    traces = _jload(traces_json, []) or []
    created = read_back = final_state = ""
    cleaned = False
    blockers: list[str] = []
    try:
        r = _call(base, headers, cookies, "POST", f"/api/resource/{quote(doctype)}",
                  body={**sample, "doctype": doctype})
        if r.status_code not in (200, 201):
            blockers.append(f"create HTTP {r.status_code}: {r.text[:250]}")
            _record_evidence(tool_context, "smoke_test", False, name=doctype,
                             error=blockers[0], traces=traces)
            return {"ok": False, "error": blockers[0], "blockers": blockers}
        created = (_doc_data(r) or {}).get("name", "")
        v = _call(base, headers, cookies, "GET", f"/api/resource/{quote(doctype)}/{quote(str(created))}")
        if v.status_code != 200:
            blockers.append(f"read-back HTTP {v.status_code}")
        else:
            doc = _doc_data(v)
            read_back = doc.get("name", "")
            final_state = str(doc.get("workflow_state") or doc.get("status") or "")
        # one or several workflow actions (JSON list or comma-separated)
        actions: list[str] = []
        if workflow_action:
            parsed = _jload(workflow_action, None)
            if isinstance(parsed, list):
                actions = [str(a) for a in parsed if str(a).strip()]
            else:
                actions = [a.strip() for a in str(workflow_action).split(",") if a.strip()]
        for action in actions:
            wa = _call(base, headers, cookies, "POST",
                       "/api/method/frappe.model.workflow.apply_workflow",
                       body={"doc": {"doctype": doctype, "name": created}, "action": action})
            if wa.status_code != 200:
                blockers.append(f"workflow '{action}' HTTP {wa.status_code}: {wa.text[:200]}")
                break
            v2 = _call(base, headers, cookies, "GET",
                       f"/api/resource/{quote(doctype)}/{quote(str(created))}")
            if v2.status_code == 200:
                d2 = _doc_data(v2)
                final_state = str(d2.get("workflow_state") or d2.get("status") or "")
        if cleanup and created:
            d = _call(base, headers, cookies, "DELETE",
                      f"/api/resource/{quote(doctype)}/{quote(str(created))}")
            cleaned = d.status_code in (200, 202, 204)
            if not cleaned:
                blockers.append(f"cleanup failed HTTP {d.status_code} (doc {created} left marked)")
        ok = not blockers and bool(created) and bool(read_back)
        _record_evidence(tool_context, "smoke_test", ok, name=doctype,
                         created=created, read_back=read_back, final_state=final_state,
                         cleaned_up=cleaned, blockers=blockers[:5], traces=traces)
        return {"ok": ok, "doctype": doctype, "created": created, "read_back": read_back,
                "final_state": final_state, "cleaned_up": cleaned, "blockers": blockers}
    except Exception as e:
        _record_evidence(tool_context, "smoke_test", False, name=doctype,
                         error=_safe_err(e), traces=traces)
        return {"ok": False, "error": _safe_err(e)}


def seed_records(
    doctype: str,
    records_json: Any,
    tool_context: ToolContext,
    key_field: str = "",
    traces_json: Any = "",
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Create demo records that STAY on the site (idempotent).

    Unlike ``smoke_test`` (which proves a DocType works and then deletes its
    document), this leaves records behind so the generated app is *populated* —
    e.g. one record resting in each workflow state, so the approval flow is
    visible rather than a set of empty forms.

    Never duplicates: ``key_field`` is required and names the field that
    identifies a record; an existing record with the same value is reused
    instead of created again. Reported per record; failures are listed, never
    hidden.

    Args:
        doctype: DocType to seed, e.g. 'Travel Request'.
        records_json: JSON list of field objects, e.g.
            [{"requester":"A. Rao","amount":4200,"workflow_state":"Pending Approval"}].
        key_field: field used for idempotency, e.g. 'requester' (required).
        traces_json: requirement ids this demo data illustrates, e.g. ["US-001"].

    Returns:
        Dict with created[], reused[], failed[], counts.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    records = _jload(records_json, [])
    if not isinstance(records, list) or not records:
        return {"ok": False, "error": "records_json must be a non-empty JSON list of objects"}
    key_field = str(key_field or "").strip()
    if not key_field:
        return {
            "ok": False,
            "error": ("key_field is required: supply the field that identifies a record "
                      "(e.g. 'requester') so a re-run reuses existing records instead of "
                      "duplicating them."),
        }
    traces = _jload(traces_json, []) or []
    created: list[str] = []
    reused: list[str] = []
    failed: list[dict] = []
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            for rec in records:
                if not isinstance(rec, dict) or not rec:
                    continue
                if key_field and key_field in rec:
                    rq = c.get(
                        f"{base}/api/resource/{quote(doctype)}", headers=headers,
                        cookies=cookies,
                        params={"filters": json.dumps([[key_field, "=", rec[key_field]]]),
                                "fields": json.dumps(["name"]),
                                "limit_page_length": "1"})
                    found = (rq.json().get("data") or []) if rq.status_code == 200 else []
                    if found:
                        reused.append(str(found[0].get("name", "")))
                        continue
                r = c.post(f"{base}/api/resource/{quote(doctype)}", headers=headers,
                           cookies=cookies, json={**rec, "doctype": doctype})
                if r.status_code in (200, 201):
                    created.append(str((r.json().get("data") or {}).get("name", "")))
                else:
                    failed.append({"record": str(rec.get(key_field or "name", "?"))[:60],
                                   "error": f"HTTP {r.status_code}: {r.text[:150]}"})
        ok = not failed
        _record_evidence(tool_context, "seed", ok, name=doctype, created=len(created),
                         reused=len(reused), failed=len(failed), traces=traces)
        return {"ok": ok, "doctype": doctype, "created": created[:20],
                "reused": reused[:20], "created_count": len(created),
                "reused_count": len(reused), "failed": failed[:10]}
    except Exception as e:
        _record_evidence(tool_context, "seed", False, name=doctype, error=_safe_err(e),
                         traces=traces)
        return {"ok": False, "error": _safe_err(e)}


def ensure_workspace(
    title: str,
    doctypes_json: Any,
    tool_context: ToolContext,
    module: str = "",
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
) -> dict:
    """Create OR update a Frappe Workspace grouping the generated DocTypes.

    Makes the generated app read as one app (a workspace with shortcuts to its
    DocTypes) rather than as scattered DocTypes. Idempotent by title; verified by
    read-back. Additive and non-blocking: if the site rejects the workspace, the
    DocTypes, workflow and records are unaffected.

    Args:
        title: workspace title/label, e.g. 'Travel and Expense'.
        doctypes_json: JSON list of DocType names, or objects {"doctype","label"}.
        module: optional Module Def (auto-picked if empty).

    Returns:
        Dict with ok, action (created/updated), shortcuts, verified.
    """
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    items = _jload(doctypes_json, [])
    if not items:
        return {"ok": False, "error": "doctypes_json must be a non-empty JSON list"}
    shortcuts: list[dict] = []
    for it in items:
        if isinstance(it, str) and it.strip():
            shortcuts.append({"type": "DocType", "label": it.strip(),
                              "link_to": it.strip(), "doc_view": "List"})
        elif isinstance(it, dict) and it.get("doctype"):
            label = str(it.get("label") or it["doctype"]).strip()
            shortcuts.append({"type": "DocType", "label": label,
                              "link_to": str(it["doctype"]).strip(), "doc_view": "List"})
    if not shortcuts:
        return {"ok": False, "error": "no usable DocType names in doctypes_json"}
    mod = module.strip() or _pick_module(base, headers, cookies, "")
    blocks = [{"id": "ws-header", "type": "header",
               "data": {"text": f'<span class="h4"><b>{title}</b></span>', "col": 12}}]
    blocks += [{"id": f"ws-sc-{i}", "type": "shortcut",
                "data": {"shortcut_name": sc["label"], "col": 3}}
               for i, sc in enumerate(shortcuts)]
    payload: dict[str, Any] = {
        "title": title, "label": title, "module": mod, "public": 1,
        "content": json.dumps(blocks), "shortcuts": shortcuts,
    }
    try:
        with httpx.Client(timeout=_DEFAULT_TIMEOUT) as c:
            rq = c.get(f"{base}/api/resource/{quote('Workspace')}", headers=headers,
                       cookies=cookies,
                       params={"filters": json.dumps([["title", "=", title]]),
                               "fields": json.dumps(["name"]),
                               "limit_page_length": "1"})
            found = (rq.json().get("data") or []) if rq.status_code == 200 else []
            action = "updated"
            if found:
                name = str(found[0].get("name", title))
                rw = c.put(f"{base}/api/resource/{quote('Workspace')}/{quote(name)}",
                           headers=headers, cookies=cookies, json=payload)
            else:
                action = "created"
                rw = c.post(f"{base}/api/resource/{quote('Workspace')}",
                            headers=headers, cookies=cookies, json=payload)
            if rw.status_code not in (200, 201):
                _record_evidence(tool_context, "workspace", False, name=title,
                                 error=f"HTTP {rw.status_code}: {rw.text[:200]}")
                return {"ok": False, "error": f"HTTP {rw.status_code}: {rw.text[:250]}"}
            name = str((rw.json().get("data") or {}).get("name", title))
            rv = c.get(f"{base}/api/resource/{quote('Workspace')}/{quote(name)}",
                       headers=headers, cookies=cookies)
            verified = rv.status_code == 200
        _record_evidence(tool_context, "workspace", verified, name=name, action=action,
                         shortcuts=len(shortcuts), verified=verified)
        return {"ok": verified, "workspace": name, "action": action, "module": mod,
                "shortcuts": len(shortcuts), "verified": verified}
    except Exception as e:
        _record_evidence(tool_context, "workspace", False, name=title, error=_safe_err(e))
        return {"ok": False, "error": _safe_err(e)}


def frappe_api(
    method: str,
    path: str,
    tool_context: ToolContext,
    params_json: Any = "",
    data_json: Any = "",
    confirm: bool = False,
    base_url: str = "",
    api_key: str = "",
    api_secret: str = "",
    traces_json: Any = "",
) -> dict:
    """Call any Frappe REST endpoint (escape hatch for reports, scripts, fixtures).

    Writes require explicit intent; DELETE additionally requires confirm=True.

    Args:
        method: GET | POST | PUT | PATCH | DELETE.
        path: e.g. '/api/resource/Travel Request' or '/api/method/frappe.client.get_list'.
        params_json: optional JSON object of query parameters.
        data_json: optional JSON object body for POST/PUT/PATCH.
        confirm: must be True for DELETE.
        traces_json: requirement ids this relates to.

    Returns:
        Dict with ok, status, data (truncated), error.
    """
    m = str(method or "").strip().upper()
    if m not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        return {"ok": False, "error": f"method must be one of GET/POST/PUT/PATCH/DELETE, got {method!r}"}
    p = str(path or "").strip()
    if not p.startswith("/api/"):
        return {"ok": False, "error": "path must start with /api/ (e.g. /api/resource/DocType)"}
    if m == "DELETE" and not confirm:
        return {"ok": False, "error": "DELETE requires confirm=True"}
    base, headers, cookies, err = _ensure_auth(tool_context, base_url, api_key, api_secret)
    if err:
        return {"ok": False, "error": err}
    params = _jload(params_json, {}) or {}
    body = _jload(data_json, None)
    traces = _jload(traces_json, []) or []
    try:
        r = _call(base, headers, cookies, m, p, params=params or None, body=body)
        payload: Any
        try:
            payload = r.json()
        except Exception:
            payload = r.text[:1000]
        text = json.dumps(payload)[:4000] if isinstance(payload, (dict, list)) else str(payload)[:1000]
        ok = 200 <= r.status_code < 300
        _record_evidence(tool_context, "api", ok, name=f"{m} {p}"[:120],
                         status=r.status_code, traces=traces)
        # a successful GET on a DocType IS a read-back: refresh its evidence so
        # stale facts (e.g. missing child-table flag) cannot mislead the gate
        m_dt = re.match(r"^/api/resource/DocType/([^/?]+)$", p)
        if ok and m == "GET" and m_dt:
            nm = unquote(m_dt.group(1))
            data = payload.get("data", {}) if isinstance(payload, dict) else {}
            if isinstance(data, dict) and data.get("name"):
                _record_evidence(tool_context, "doctype", True, name=nm,
                                 action="inspected", verified=True,
                                 istable=bool(data.get("istable")),
                                 custom=bool(data.get("custom")),
                                 traces=traces)
        return {"ok": ok, "status": r.status_code, "data": text,
                "error": "" if ok else f"HTTP {r.status_code}: {r.text[:300]}"}
    except Exception as e:
        _record_evidence(tool_context, "api", False, name=f"{m} {p}"[:120],
                         error=_safe_err(e), traces=traces)
        return {"ok": False, "error": _safe_err(e)}


def frappe_evidence(
    tool_context: ToolContext,
    traces_json: Any = "",
) -> dict:
    """Summarize recorded implementation evidence (facts only, failures included).

    Args:
        traces_json: optional extra requirement ids to mark as covered.

    Returns:
        Dict with counts by kind, verified objects, open failures, traced ids.
    """
    ev = _evidence_list(tool_context)
    extra = _jload(traces_json, []) or []
    by_kind: dict[str, int] = {}
    verified_objects: list[str] = []
    traces: set[str] = set(str(t) for t in extra)
    latest: dict[str, bool] = {}
    for e in ev:
        if not isinstance(e, dict):
            continue
        kind = str(e.get("kind", ""))
        by_kind[kind] = by_kind.get(kind, 0) + 1
        key = f"{kind}:{e.get('name', '')}"
        latest[key] = bool(e.get("ok"))
        if e.get("verified"):
            verified_objects.append(f"{kind}:{e.get('name', '')}")
        for t in (e.get("traces") or []):
            traces.add(str(t))
    open_failures = [k for k, ok in latest.items() if not ok]
    summary = {
        "ok": True,
        "entries": len(ev),
        "by_kind": by_kind,
        "verified_objects": sorted(set(verified_objects)),
        "open_failures": open_failures[:20],
        "traced_ids": sorted(traces)[:100],
    }
    try:
        from .project_context import commit, get_context
        ctx = get_context(tool_context.state)
        env = ctx.get("frappe_state")
        if not isinstance(env, dict):
            env = {}
            ctx["frappe_state"] = env
        env["evidence_summary"] = summary
        commit(tool_context.state)
    except Exception:
        pass
    return summary
