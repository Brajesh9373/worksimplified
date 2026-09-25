"""ProjectWorkspace — per-project isolation boundary.

Every project owns exactly one workspace::

    projects/<project_id>/
        context/          context.json (PROJECT_CONTEXT sections snapshot)
        artifacts/        BRD_*.md, ProjectPlan_*.md, *.mmd/.json/.html bundles
        traceability/     trace.json
        change_requests/  change_requests.json
        history/          history.json
        handoffs/         <STAGE>.md briefs
        execution/        state.json
        frappe/           state.json (project-specific frappe knowledge)

RULES (mandatory):
- No orchestration code searches outside the bound workspace.
- No "latest/most recent/longest global file" fallbacks (removed).
- Session state stays the LIVE layer; the workspace is the persisted,
  isolated source of truth. Sync via persist_project_state/load_project_state.
- CR numbering, history, traceability, frappe knowledge restart per project.

Pure stdlib. No new database/framework.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECTS_ROOT = Path(__file__).resolve().parents[1] / "projects"

SUBDIRS = ("context", "artifacts", "traceability", "change_requests",
           "history", "handoffs", "execution", "frappe")

WORKSPACE_KEY = "project_workspace_id"

_ID_RX = re.compile(r"^[a-z0-9_][a-z0-9_]{1,80}$")


def generate_project_id(name: str) -> str:
    """Unique id: slug + UTC date, e.g. service_management_20260918."""
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "project").lower()).strip("_")[:40] or "project"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = f"{slug}_{stamp}"
    pid, n = base, 2
    while (PROJECTS_ROOT / pid).exists():
        pid = f"{base}_{n}"
        n += 1
    return pid


def validate_project_id(pid: str) -> str:
    pid = (pid or "").strip().lower()
    if not _ID_RX.match(pid):
        raise ValueError(f"Invalid project_id {pid!r}: use [a-z0-9_], 2-81 chars")
    return pid


class ProjectWorkspace:
    """Controlled per-project storage. All reads/writes stay inside."""

    def __init__(self, project_id: str, create: bool = True):
        self.project_id = validate_project_id(project_id)
        self.root = PROJECTS_ROOT / self.project_id
        if create:
            for sub in SUBDIRS:
                (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- internal helpers --
    def _path(self, sub: str, filename: str) -> Path:
        if sub not in SUBDIRS:
            raise ValueError(f"Unknown workspace area: {sub!r}")
        p = (self.root / sub / Path(filename).name).resolve()
        if not p.is_relative_to(self.root.resolve()):
            raise ValueError("Path escapes workspace")
        return p

    def _read_json(self, sub: str, filename: str, default: Any) -> Any:
        p = self._path(sub, filename)
        if not p.is_file():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, sub: str, filename: str, data: Any) -> Path:
        p = self._path(sub, filename)
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return p

    # -- context / execution / history / CRs / traceability / frappe --
    def get_context(self) -> dict:
        return self._read_json("context", "context.json", {})

    def update_context(self, patch: dict) -> dict:
        ctx = self.get_context()
        if not isinstance(ctx, dict):
            ctx = {}
        ctx.update(patch or {})
        self._write_json("context", "context.json", ctx)
        return ctx

    def get_execution(self) -> dict:
        return self._read_json("execution", "state.json", {})

    def get_artifacts(self) -> dict:
        """Artifact references per stage (doc key + path), kept beside the context."""
        arts = self._read_json("context", "artifacts.json", {})
        return arts if isinstance(arts, dict) else {}

    def update_execution(self, patch: dict) -> dict:
        exc = self.get_execution()
        if not isinstance(exc, dict):
            exc = {}
        exc.update(patch or {})
        self._write_json("execution", "state.json", exc)
        return exc

    def get_history(self) -> list:
        h = self._read_json("history", "history.json", [])
        return h if isinstance(h, list) else []

    def add_history(self, entry: dict) -> dict:
        hist = self.get_history()
        hist.append(entry)
        self._write_json("history", "history.json", hist[-200:])
        return entry

    def get_change_requests(self) -> list:
        crs = self._read_json("change_requests", "change_requests.json", [])
        return crs if isinstance(crs, list) else []

    def save_change_requests(self, crs: list) -> None:
        self._write_json("change_requests", "change_requests.json", crs or [])

    def get_traceability(self) -> dict:
        t = self._read_json("traceability", "trace.json", {})
        return t if isinstance(t, dict) else {}

    def save_traceability(self, data: dict) -> None:
        self._write_json("traceability", "trace.json", data or {})

    def get_frappe_state(self) -> dict:
        f = self._read_json("frappe", "state.json", {})
        return f if isinstance(f, dict) else {}

    def update_frappe_state(self, patch: dict) -> dict:
        cur = self.get_frappe_state()
        cur.update(patch or {})
        self._write_json("frappe", "state.json", cur)
        return cur

    # -- handoffs --
    def save_handoff(self, stage: str, content: str) -> Path:
        p = self._path("handoffs", f"{stage.upper()}.md")
        p.write_text(content, encoding="utf-8")
        return p

    def read_handoff(self, stage: str) -> str:
        p = self._path("handoffs", f"{stage.upper()}.md")
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    # -- artifacts (workspace-scoped discovery ONLY) --
    def save_artifact(self, filename: str, content: str | bytes) -> Path:
        p = self._path("artifacts", filename)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
        return p

    def read_artifact(self, filename: str, max_bytes: int = 200_000) -> str:
        p = self._path("artifacts", filename)
        if not p.is_file():
            raise FileNotFoundError(f"Not in project workspace: {filename}")
        if p.stat().st_size > max_bytes:
            raise ValueError("Artifact too large")
        return p.read_text(encoding="utf-8")

    def list_artifacts(self) -> list[str]:
        d = self.root / "artifacts"
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    def find_artifact(self, prefix: str, suffix: str = ".md",
                      largest: bool = True) -> Path | None:
        """Workspace-scoped discovery: newest/longest WITHIN this project only."""
        cands = [p for p in (self.root / "artifacts").glob(f"{prefix}*{suffix}")
                 if p.is_file() and p.stat().st_size > 200]
        if not cands:
            return None
        key = (lambda p: p.stat().st_size) if largest else (lambda p: p.stat().st_mtime)
        return max(cands, key=key)

    def exists(self) -> bool:
        return self.root.is_dir()


# ---- session binding helpers ----

def _session_id_of(tool_context: Any = None, state: Any = None) -> str:
    for obj in (tool_context,):
        try:
            if obj is not None and getattr(obj, "session", None) is not None:
                sid = getattr(obj.session, "id", "")
                if sid:
                    return re.sub(r"[^a-z0-9]", "", str(sid).lower())[:12]
        except Exception:
            pass
    return "nosession"


def bind_workspace(state: dict, project_id: str) -> ProjectWorkspace:
    """Bind this session/run to exactly one workspace (top-level set = committed)."""
    ws = ProjectWorkspace(project_id)
    try:
        state[WORKSPACE_KEY] = ws.project_id
    except Exception:
        pass
    return ws


def bound_workspace_id(state: Any) -> str:
    try:
        pid = state.get(WORKSPACE_KEY, "")
        return pid if isinstance(pid, str) else ""
    except Exception:
        return ""


def current_workspace(state: Any = None, tool_context: Any = None,
                      create: bool = True) -> ProjectWorkspace:
    """Resolve the CURRENT workspace: explicit binding, else session-scoped.

    Standalone agent runs (no orchestrator) get an isolated per-session
    workspace — never a shared/global one.
    """
    pid = bound_workspace_id(state) if state is not None else ""
    if not pid and tool_context is not None:
        try:
            pid = bound_workspace_id(tool_context.state)
        except Exception:
            pid = ""
    if not pid:
        pid = f"_session_{_session_id_of(tool_context)}"
        if state is not None:
            try:
                state[WORKSPACE_KEY] = pid
            except Exception:
                pass
    return ProjectWorkspace(pid, create=create)


def load_project_state(state: dict, ws: ProjectWorkspace) -> None:
    """Hydrate live session state from workspace (resume path)."""
    from .project_context import get_context
    saved = ws.get_context()
    if isinstance(saved, dict) and saved:
        ctx = get_context(state)
        for k, v in saved.items():
            ctx[k] = v
        # the Frappe project is a top-level doc key the gate reads directly; without
        # this it reloads as '' and a real project looks missing ("evidence=missing")
        if saved.get("frappe_project"):
            try:
                state["frappe_project"] = saved["frappe_project"]
            except Exception:
                pass
    exc = ws.get_execution()
    if isinstance(exc, dict) and exc:
        get_context(state)["execution"].update(exc)
    hist = ws.get_history()
    if hist:
        get_context(state)["history"] = hist
    crs = ws.get_change_requests()
    if crs:
        get_context(state)["change_requests"] = crs
        get_context(state)["cr_counter"] = max(
            [int(str(c.get("id", "CR-0")).split("-")[1]) for c in crs
             if str(c.get("id", "")).startswith("CR-")] + [0])
    try:
        arts = ws._read_json("context", "artifacts.json", {})
        if isinstance(arts, dict) and arts:
            get_context(state)["artifacts"] = arts
    except Exception:
        pass
    from .project_context import commit
    commit(state)


def persist_project_state(state: dict, ws: ProjectWorkspace) -> None:
    """Write-through live state → workspace (call at stage boundaries)."""
    from .project_context import get_context, state_dict
    try:
        ctx = state_dict(state).get("project_context", {})
    except Exception:
        ctx = {}
    if not isinstance(ctx, dict) or not ctx:
        return
    keep = {k: v for k, v in ctx.items()
            if k in ("project", "business", "stakeholders", "actors", "processes",
                     "requirements", "business_rules", "functional_requirements",
                     "use_cases", "architecture", "data_model", "integrations",
                     "delivery", "tasks", "dependencies", "risks", "decisions",
                     "technical_design", "frappe_state", "validation",
                     "open_questions", "elicitation", "cr_counter",
                     "ba_eval", "ba_versions", "ba_current_version",
                     "ba_candidates", "ba_stage_done", "ba_stage_current",
                     "ba_stage_log", "ba_stage_batched",
                     "ba_question_batches", "ba_approvals", "ba_field_labels",
                     "ba_baseline_snapshots", "ba_improvement_memory",
                     "ba_plan", "decision_log", "evidence_links", "assumptions",
                     "contradictions", "impact_reports", "success_metrics",
                     "outcome_measurements", "business_case", "solution_assessment",
                     "quality_findings", "conversation", "knowledge")
            # harness/turn state that must survive a workspace reload
            or k.startswith(("revisions_", "sections_", "sec_rev_", "sec_missing_",
                             "id_registry", "id_alloc", "stage_fail_",
                             "pending_section", "gate_feedback_"))
            or k in ("section_advisories", "gate_warnings", "carried_stages",
                     "frappe_project")}
    ws.update_context(keep)
    hist = ctx.get("history", [])
    if isinstance(hist, list):
        ws._write_json("history", "history.json", hist[-200:])
    crs = ctx.get("change_requests", [])
    if isinstance(crs, list):
        ws.save_change_requests(crs)
    exc = ctx.get("execution", {})
    if isinstance(exc, dict):
        ws.update_execution(exc)
    arts = ctx.get("artifacts", {})
    if isinstance(arts, dict):
        ws._write_json("context", "artifacts.json", arts)
