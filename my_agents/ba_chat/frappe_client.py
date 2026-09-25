"""Standalone Frappe client for the BA chatbot — REST only, no ADK.

Mirrors the proven pattern from the pipeline (session login → idempotent
create → read-back verification) without importing any of it:

  * Project: looked up by name first, created only when missing.
  * Tasks: one per story/feature, looked up by (subject, project) first.
  * DocTypes: created ONLY from explicit ``Entity (field, field)`` lists in
    the data answer — never guessed, never partial. When the BA pack has no
    explicit field lists the step reports "skipped" honestly.

Every public function returns ``{"ok": bool, ...}`` with user-safe errors;
secrets are never echoed.
"""

from __future__ import annotations

import os
import re
from typing import Any

try:
    import httpx
except Exception:                                       # pragma: no cover
    httpx = None  # type: ignore

TIMEOUT = 30


def settings() -> dict[str, str]:
    """Live Frappe connection settings (call time, like everything else)."""
    return {"base_url": (os.getenv("FRAPPE_BASE_URL") or "").strip().rstrip("/"),
            "username": (os.getenv("FRAPPE_USERNAME") or "").strip(),
            "password": os.getenv("FRAPPE_PASSWORD") or ""}


def _err(exc: Exception) -> str:
    msg = str(exc)
    msg = re.sub(r"token \S+:\S+", "token <redacted>", msg)
    return msg[:300]


# Standard ERPNext names must never be created as fresh custom DocTypes — on an
# ERPNext site they already exist (→ "already present"); on a plain Frappe
# site creating them would collide with a later ERPNext install.
_RESERVED = {"customer", "supplier", "item", "item group", "warehouse",
             "price list", "employee", "department", "company", "account",
             "sales order", "purchase order", "sales invoice", "purchase invoice",
             "delivery note", "quotation", "lead", "opportunity", "payment entry",
             "journal entry", "stock entry", "bom", "work order", "project",
             "task", "user", "role"}


class FrappeClient:
    """Session-cookie client over the Frappe REST API."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        if httpx is None:                               # pragma: no cover
            raise RuntimeError("httpx is not installed")
        self.base = (base_url or "").rstrip("/")
        self.username = username
        self._client = httpx.Client(base_url=self.base, timeout=TIMEOUT)
        self._password = password

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ auth
    def login(self) -> dict:
        try:
            resp = self._client.post("/api/method/login",
                                     data={"usr": self.username, "pwd": self._password})
        except Exception as exc:
            return {"ok": False, "error": f"cannot reach Frappe at {self.base}: {_err(exc)}"}
        if resp.status_code != 200:
            return {"ok": False, "error": f"Frappe login failed (HTTP {resp.status_code}) — check username/password"}
        return {"ok": True, "user": self.username, "host": self.base}

    # ------------------------------------------------------------------ rest
    def _get(self, path: str, **params: Any) -> dict:
        try:
            resp = self._client.get(path, params=params or None)
        except Exception as exc:
            return {"ok": False, "error": _err(exc)}
        if resp.status_code >= 400:
            return {"ok": False, "error": f"HTTP {resp.status_code}"}
        try:
            return {"ok": True, "data": resp.json().get("data")}
        except Exception:
            return {"ok": False, "error": "bad JSON from Frappe"}

    def _post(self, path: str, payload: dict) -> dict:
        try:
            resp = self._client.post(path, json=payload)
        except Exception as exc:
            return {"ok": False, "error": _err(exc)}
        if resp.status_code >= 400:
            detail = ""
            try:
                detail = str(resp.json().get("exc") or resp.json().get("message") or "")[:200]
            except Exception:
                detail = resp.text[:200]
            return {"ok": False, "error": f"HTTP {resp.status_code} {detail}"}
        try:
            return {"ok": True, "data": resp.json().get("data")}
        except Exception:
            return {"ok": False, "error": "bad JSON from Frappe"}

    # ----------------------------------------------------------------- build
    def ensure_project(self, name: str, description: str = "") -> dict:
        """Get-or-create a Project by name. Returns {ok, name, created}."""
        import json as _json
        found = self._get("/api/resource/Project",
                          filters=_json.dumps([["Project", "project_name", "=", name]]),
                          fields=_json.dumps(["name", "project_name"]), limit_page_length=1)
        rows = (found.get("data") or []) if found.get("ok") else []
        if rows:
            return {"ok": True, "name": rows[0].get("name", name), "created": False}
        payload = {"project_name": name, "status": "Open"}
        if description:
            payload["notes"] = description[:2000]
        created = self._post("/api/resource/Project", payload)
        if not created.get("ok"):
            return {"ok": False, "error": f"could not create Project: {created.get('error')}"}
        data = created.get("data") or {}
        return {"ok": True, "name": data.get("name", name), "created": True}

    def ensure_task(self, project: str, subject: str, description: str = "",
                    priority: str = "Medium") -> dict:
        """Get-or-create a Task by (subject, project)."""
        import json as _json
        found = self._get("/api/resource/Task",
                          filters=_json.dumps([["Task", "subject", "=", subject],
                                               ["Task", "project", "=", project]]),
                          fields=_json.dumps(["name"]), limit_page_length=1)
        rows = (found.get("data") or []) if found.get("ok") else []
        if rows:
            return {"ok": True, "name": rows[0].get("name"), "created": False}
        created = self._post("/api/resource/Task",
                             {"subject": subject[:140], "project": project,
                              "description": (description or subject)[:2000],
                              "priority": priority, "status": "Open"})
        if not created.get("ok"):
            return {"ok": False, "error": f"could not create Task: {created.get('error')}"}
        data = created.get("data") or {}
        return {"ok": True, "name": data.get("name"), "created": True}

    def ensure_doctype(self, doctype: str, fields: list[dict]) -> dict:
        """Create a custom DocType only — never modifies existing ones, and
        never creates reserved ERPNext names from scratch."""
        exists = self._get(f"/api/resource/DocType/{doctype}", fields='["name"]')
        if exists.get("ok"):
            return {"ok": True, "name": doctype, "created": False}
        if re.sub(r"\s+", " ", doctype).strip().lower() in _RESERVED:
            return {"ok": True, "name": doctype, "created": False,
                    "skipped": "reserved ERPNext name — not created from scratch"}
        modules = self._get("/api/resource/Module Def", fields='["name"]',
                            limit_page_length=50)
        names = [m.get("name") for m in (modules.get("data") or []) if m.get("name")]
        module = next((m for m in ("Projects", "Custom", "Setup") if m in names),
                      names[0] if names else "Projects")
        clean = []
        for f in fields:
            fn = re.sub(r"\W+", "_", str(f.get("fieldname", "")).strip().lower()).strip("_")
            if re.fullmatch(r"[a-z][a-z0-9_]{0,60}", fn):
                clean.append({"fieldname": fn, "fieldtype": "Data",
                              "label": fn.replace("_", " ").title()})
        if not clean:
            return {"ok": False, "error": "no usable fields"}
        created = self._post("/api/resource/DocType",
                             {"doctype": "DocType", "name": doctype, "module": module,
                              "custom": 1, "fields": clean,
                              "permissions": [{"role": "System Manager", "read": 1,
                                               "write": 1, "create": 1, "delete": 1}]})
        if not created.get("ok"):
            return {"ok": False, "error": f"could not create DocType: {created.get('error')}"}
        verify = self._get(f"/api/resource/DocType/{doctype}", fields='["name"]')
        if not verify.get("ok"):
            return {"ok": False, "error": "created but failed read-back verification"}
        return {"ok": True, "name": doctype, "created": True}


# ------------------------------------------------------------- pack → plan
_MOSCOW = {"must": "High", "should": "Medium", "could": "Low"}


_STORY = re.compile(r"\bas a\b.*\bwant\b.*\bso that\b", re.IGNORECASE | re.DOTALL)


def _split_list(text: str) -> list[str]:
    items = []
    for ln in (text or "").splitlines():
        # strip only a leading bullet/number marker, never content
        ln = re.sub(r"^(\d+[.)]|[-*•])\s+", "", ln.strip()).strip()
        if len(ln) > 3:
            items.append(ln)
    if len(items) < 2 and not _STORY.search(text or ""):
        # comma lists ("billing, alerts, reports") split; a user story in
        # prose ("As a …, I want …, so that …") must NEVER be shredded —
        # only split when every part is short.
        for sep in (";", ","):
            if sep in (text or ""):
                parts = [p.strip() for p in text.split(sep) if len(p.strip()) > 3]
                if len(parts) >= 2 and all(len(p) <= 45 for p in parts):
                    items = parts
                    break
    seen, out = set(), []
    for ln in items:
        key = ln.lower()
        if key not in seen:
            seen.add(key)
            out.append(ln[:140])
    return out


def _derive_doctypes(d7: str) -> list[dict]:
    """Only explicit ``Entity (field, field)`` lists become DocTypes."""
    out = []
    for match in re.finditer(
            r"([A-Z][A-Za-z0-9 ]{2,40}?)\s*\(([^()]{3,200}?)\)", d7 or ""):
        name = " ".join(match.group(1).split())
        if len(name.split()) > 4:
            continue
        fields = [{"fieldname": f.strip()} for f in match.group(2).split(",")
                  if f.strip()]
        if name and len(fields) >= 1:
            out.append({"name": name, "fields": fields})
    seen, unique = set(), []
    for dt in out:
        if dt["name"].lower() not in seen:
            seen.add(dt["name"].lower())
            unique.append(dt)
    return unique[:10]


def plan_setup(session: dict) -> dict:
    """Turn an approved requirement pack into a concrete setup plan."""
    items = session.get("items", {})
    val = lambda i: (items.get(i, {}).get("value") or "").strip()
    stories = _split_list(val("c2")) + _split_list(val("d1"))
    prio_text = val("c4").lower()
    prio = next((p for k, p in _MOSCOW.items() if k in prio_text), "Medium")
    project_name = val("a0") or "Untitled Project"
    description = (f"{val('a1')}\n\nGoal: {val('a2')}\n\nIn scope: {val('b2')}"[:2000]).strip()
    return {"project_name": project_name[:140], "description": description,
            "tasks": [{"subject": s, "priority": prio} for s in stories[:40]],
            "doctypes": _derive_doctypes(val("d7"))}


def run_setup(session: dict, log: list) -> dict:
    """Execute the plan with a shared log list. Returns the job record."""
    plan = plan_setup(session)
    cfg = settings()
    if not cfg["base_url"] or not cfg["username"] or not cfg["password"]:
        return {"ok": False, "error": "Frappe is not configured — set FRAPPE_BASE_URL/USERNAME/PASSWORD first"}
    client = FrappeClient(cfg["base_url"], cfg["username"], cfg["password"])
    try:
        login = client.login()
        if not login.get("ok"):
            return {"ok": False, "error": login.get("error")}
        log.append(f"connected to {login['host']} as {login['user']}")

        proj = client.ensure_project(plan["project_name"], plan["description"])
        if not proj.get("ok"):
            return {"ok": False, "error": proj.get("error")}
        log.append(f"Project '{proj['name']}' "
                   f"{'created' if proj.get('created') else 'already existed — reused'}")

        made, reused = 0, 0
        for task in plan["tasks"]:
            res = client.ensure_task(proj["name"], task["subject"],
                                     task["subject"], task["priority"])
            if not res.get("ok"):
                log.append(f"Task skipped ({task['subject'][:60]}): {res.get('error')}")
                continue
            if res.get("created"):
                made += 1
            else:
                reused += 1
        log.append(f"Tasks: {made} created, {reused} already present")

        dts = []
        for dt in plan["doctypes"]:
            res = client.ensure_doctype(dt["name"], dt["fields"])
            dts.append({"name": dt["name"], **{k: v for k, v in res.items() if k != "error"},
                        **({} if res.get("ok") else {"error": res.get("error")})})
            if res.get("skipped"):
                log.append(f"DocType '{dt['name']}': skipped — {res['skipped']}")
            else:
                log.append(f"DocType '{dt['name']}': "
                           f"{'created (read-back verified — run bench migrate if its table is missing)' if res.get('created') else 'already present' if res.get('ok') else 'skipped — ' + str(res.get('error'))}")
        if not plan["doctypes"]:
            log.append("DocTypes: none with explicit field lists — skipped (nothing guessed)")

        verify = client._get(f"/api/resource/Task",
                             filters=f'[["Task","project","=","{proj["name"]}"]]',
                             fields='["name"]', limit_page_length=200)
        tasks = verify.get("data") or [] if verify.get("ok") else []
        log.append(f"Verified by read-back: Project '{proj['name']}' with {len(tasks)} tasks")
        return {"ok": True, "project": proj["name"], "tasks_created": made,
                "tasks_total": len(tasks), "doctypes": dts}
    finally:
        client.close()
