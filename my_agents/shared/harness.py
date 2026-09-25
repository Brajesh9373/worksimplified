"""Deterministic harness for the delivery pipeline.

Principle: the model produces TEXT; the harness owns structure, placement,
identity (IDs) and acceptance. Nothing here edits a stage prompt — every
directive is a plain-text brief fragment built by the orchestrator, the same
pattern as ``sections.section_assignment``.

What the harness guarantees:
- A section is accepted ONLY through ``accept_section``: byte/keyword/ID
  checks (``sections.check_section``) + allocated-ID exactness + project
  domain anchoring. Fail closed; never trust agent self-report.
- Accepted sections are snapshotted to the project workspace
  (``context/sections/<STAGE>/<section_id>.md``) and become the ONLY source
  of the canonical ``<doc_key>_assembled.md`` document, recomposed in plan
  order. Duplicates, stale fragments and whole-doc rewrites cannot survive.
- Progress is monotonic: an accepted section can only be replaced by another
  accepted version of the SAME section.
- Prose replies (the measured strong suit of small models) are ingested via
  ``ingest_prose_reply`` so a pass that ends without a tool call still counts.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .project_context import commit, get_context
from .sections import check_section, sections_for
from .traceability import extract_ids

DOC_TITLES = {
    "BA": "Business Requirements Document",
    "PROJECT": "Project Plan",
    "FUNCTIONAL": "Functional Specification",
    "TECHNICAL": "Technical Design",
    "FRAPPE": "Frappe Setup",
}

_FENCE_RX = re.compile(r"```(?:markdown|md)?[ \t]*\n(.*?)```", re.DOTALL)
_META_RX = re.compile(
    r"^\s*(?:\*\*)?(section added|added section|here is|here's|i have|i've|"
    r"let me know|note:|brd path|the document|next,|following)",
    re.I,
)
_ID_NUM_RX = re.compile(r"^([A-Z]+)-(\d+)$")

# generic business/software words that cannot anchor a project's domain
_GENERIC_TERMS = {
    "build", "based", "using", "system", "systems", "project", "projects",
    "application", "applications", "app", "apps", "software", "platform",
    "management", "manage", "employee", "employees", "user", "users",
    "business", "customer", "customers", "process", "processes", "workflow",
    "workflows", "data", "report", "reports", "reporting", "record",
    "records", "form", "forms", "status", "approval", "approvals",
    "request", "requests", "role", "roles", "permission", "permissions",
    "module", "modules", "feature", "features", "support", "tracking",
    "track", "create", "creating", "frappe", "erp", "portal", "dashboard",
    "dashboards", "notification", "notifications", "access", "policy",
    "policies", "solution", "solutions", "tool", "tools", "service",
    "services", "team", "teams", "company", "organization", "department",
    "departments", "steps", "make", "want", "need", "needs", "help",
    "should", "must", "with", "that", "this", "from", "into", "your", "our",
}


# ---------------------------------------------------------------- text utils

_HEADING_RX = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")


def heading_title(line: str) -> str:
    """Title of any markdown heading line (H1-H6), else ""."""
    m = _HEADING_RX.match(line or "")
    return m.group(2).strip() if m else ""


def heading_level(line: str) -> int:
    """Markdown heading level (1-6), or 0 when the line is not a heading."""
    m = _HEADING_RX.match(line or "")
    return len(m.group(1)) if m else 0


def _norm_title(s: str) -> str:
    s = (s or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def title_match(line_title: str, want: str) -> bool:
    """Tolerant header match: punctuation/case-insensitive, first 24 chars."""
    a, b = _norm_title(line_title), _norm_title(want)
    if not a or not b:
        return False
    if min(len(a), len(b)) < 12:
        return a == b
    return a[:24] == b[:24]


def has_section(full: str, title: str) -> bool:
    """True when any heading (H1-H6) matches the title."""
    return any(title_match(heading_title(ln), title)
               for ln in (full or "").splitlines())


def replace_section(full: str, title: str, block: str) -> str:
    """Replace the first matching `## title` block; purge later duplicates."""
    out, inside, done, level = [], False, False, 7
    for ln in (full or "").splitlines(keepends=True):
        head, lvl = heading_title(ln), heading_level(ln)
        if lvl:
            if title_match(head, title):
                if inside and not done:
                    out.append(block if block.endswith("\n") else block + "\n")
                    done = True
                inside, level = True, lvl
                continue                # drop old header (and, below, its body)
            if inside and lvl <= level:
                out.append(block if block.endswith("\n") else block + "\n")
                done, inside = True, False
        if inside:
            continue
        out.append(ln)
    if inside and not done:
        out.append(block if block.endswith("\n") else block + "\n")
    return "".join(out)


def extract_section_text(full: str, title: str) -> str:
    """Body of the LAST `## <title>` block (repairs supersede originals)."""
    if not full or not title:
        return ""
    buf, inside, found, level = [], False, False, 7
    for ln in full.splitlines():
        head, lvl = heading_title(ln), heading_level(ln)
        if lvl:
            if title_match(head, title):
                # newer block supersedes; its own level sets the boundary
                buf, inside, found, level = [], True, True, lvl
                continue
            if inside and lvl <= level:
                inside = False          # a sibling/outer heading ends the block
            if inside:
                buf.append(ln)          # a deeper subheading stays in the body
            continue
        if inside:
            buf.append(ln)
    return "\n".join(buf).strip() if found else ""


def _drop_meta(text: str) -> str:
    lines = text.splitlines()
    while lines and (not lines[0].strip() or _META_RX.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _META_RX.match(lines[-1])):
        lines.pop()
    return "\n".join(lines).strip()


def ingest_prose_reply(reply: str, title: str) -> str:
    """Extract a section body from a plain-text (non-tool) model reply."""
    text = (reply or "").strip()
    if not text:
        return ""
    m = _FENCE_RX.search(text)
    if m and m.group(1).strip():
        text = m.group(1).strip()
    body = extract_section_text(text, title)
    if body.strip():
        return _drop_meta(body)
    # no header for this title: accept the whole reply as the body unless it
    # clearly belongs to some other section (headers present but none match)
    headers = [heading_title(ln) for ln in text.splitlines() if heading_title(ln)]
    if headers and not any(title_match(h, title) for h in headers):
        return ""
    return _drop_meta(text)


def prose_prompt_fragment(section: dict, missing: list[str] | None = None) -> str:
    """Rung-2/3 directive: prose output instead of tool calls."""
    lines = [
        "PROSE TASK — do not call any tools.",
        f"Reply with ONLY the markdown body of section '{section['title']}' "
        f"(id: {section['id']}). No other sections, no preamble, no commentary. "
        "Write 1,500-3,000 characters: complete detail inside that budget, no "
        "repetition and no commentary about the work.",
    ]
    if missing:
        lines.append("Your previous attempt failed these checks — fix exactly these: "
                     + "; ".join(str(m) for m in missing[:6]))
    return "\n".join(lines)


# -------------------------------------------------------------------- domain

def domain_terms(state: Any) -> list[str]:
    """Distinctive goal terms used to anchor a project's domain (heuristic)."""
    ctx = get_context(state)
    project = ctx.get("project") or {}
    goal = str(project.get("goal") or project.get("name") or "")
    out: list[str] = []
    for tok in re.findall(r"[a-z][a-z0-9]{3,}", goal.lower()):
        if tok in _GENERIC_TERMS or tok in out:
            continue
        out.append(tok)
    return out[:12]


def _domain_min() -> int:
    try:
        return max(0, int(os.environ.get("HARNESS_DOMAIN_MIN", "1")))
    except Exception:
        return 1


def _domain_missing(state: Any, section_id: str, body: str) -> list[str]:
    terms = domain_terms(state)
    need = min(_domain_min(), len(terms))
    if len(terms) < 2 or need <= 0:
        return []  # nothing usable to anchor against
    low = (body or "").lower()
    hits = sum(1 for t in terms if t in low)
    if hits >= need:
        return []
    return [f"domain_drift: section '{section_id}' hits {hits}/{need} project terms "
            f"{terms[:6]}"]


# -------------------------------------------------------------- ID registry

def _family_of(rx: str) -> str:
    m = re.search(r"([A-Z]+)-", rx)
    return m.group(1) if m else ""


def _spec_for(stage: str, section_id: str) -> dict:
    return {s["id"]: s for s in sections_for(stage)}.get(section_id) or {}


def allocate_ids(state: Any, stage: str, section: dict, doc_key: str) -> dict[str, list[str]]:
    """Harness-owned ID allocation: exact IDs handed to the model, verified on receipt."""
    spec = _spec_for(stage, section["id"])
    pairs = spec.get("ids") or []
    if not pairs:
        return {}
    ctx = get_context(state)
    stage_store = ctx.setdefault("id_alloc", {}).setdefault(stage.upper(), {})
    if section["id"] in stage_store:
        return stage_store[section["id"]]
    reg = ctx.setdefault("id_registry", {})
    out: dict[str, list[str]] = {}
    for rx, minimum in pairs:
        fam = _family_of(rx)
        if not fam:
            continue
        if fam not in reg:
            used = extract_ids(str(state.get(doc_key, "") or "")).get(fam, [])
            nums = [int(m.group(2)) for m in
                    (_ID_NUM_RX.match(i) for i in used) if m]
            reg[fam] = (max(nums) + 1) if nums else 1
        nxt = int(reg.get(fam, 1))
        ids = [f"{fam}-{i:03d}" for i in range(nxt, nxt + int(minimum))]
        reg[fam] = nxt + int(minimum)
        out[fam] = ids
    stage_store[section["id"]] = out
    commit(state)
    return out


def verify_ids(state: Any, stage: str, section: dict, text: str) -> list[str]:
    """Allocated IDs must appear verbatim; section ID minimums must hold.

    Allocated-set strictness applies only when the section does not already
    carry enough ids of that family — otherwise renumbering would be forced on
    documents that already satisfy the requirement.
    """
    sid = section["id"]
    missing: list[str] = []
    spec = _spec_for(stage, sid)
    need = {_family_of(rx): int(minimum) for rx, minimum in (spec.get("ids") or [])}
    for rx, minimum in spec.get("ids") or []:
        found = len(set(re.findall(rx, text or "")))
        if found < int(minimum):
            missing.append(f"section '{sid}' has {found} {rx} ids (need >={minimum})")
    alloc = (get_context(state).get("id_alloc", {}).get(stage.upper(), {}) or {}).get(sid) or {}
    for fam, ids in alloc.items():
        present = set(re.findall(rf"\b{fam}-\d+\b", text or ""))
        gone = [i for i in ids if i not in present]
        if gone and len(present) < need.get(fam, 0):
            missing.append(f"section '{sid}' missing allocated ids {gone} "
                           f"(use exactly these, do not renumber)")
    return missing


def _advance_registry(state: Any, body: str) -> None:
    ctx = get_context(state)
    reg = ctx.setdefault("id_registry", {})
    for fam, ids in extract_ids(body or "").items():
        nums = [int(m.group(2)) for m in (_ID_NUM_RX.match(i) for i in ids) if m]
        if nums:
            reg[fam] = max(int(reg.get(fam, 1)), max(nums) + 1)
    commit(state)


# -------------------------------------------------- snapshots and assembly

def _ws_for(state: Any):
    try:
        from .workspace import bound_workspace_id, ProjectWorkspace
        pid = bound_workspace_id(state)
        if not pid:
            return None
        ws = ProjectWorkspace(pid, create=True)
        return ws if ws.exists() else None
    except Exception:
        return None


def _snapshot_dir(ws: Any, stage: str):
    d = ws.root / "context" / "sections" / stage.upper()
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_snapshot(ws: Any, stage: str, section_id: str, text: str) -> str:
    p = _snapshot_dir(ws, stage) / f"{section_id}.md"
    p.write_text((text or "").strip() + "\n", encoding="utf-8")
    return str(p)


def read_snapshots(ws: Any, stage: str) -> dict[str, str]:
    d = ws.root / "context" / "sections" / stage.upper()
    out: dict[str, str] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):
        try:
            out[p.stem] = p.read_text(encoding="utf-8").strip()
        except Exception:
            continue
    return out


def snapshots_exist(state: Any, stage: str) -> bool:
    ws = _ws_for(state)
    if ws is None:
        return False
    d = ws.root / "context" / "sections" / stage.upper()
    return d.is_dir() and any(d.glob("*.md"))


def sync_canonical(state: Any, stage: str, doc_key: str) -> int:
    """Restore the canonical doc from accepted snapshots (no model call).

    Called before a gate judges the stage: whatever a model rewrote in the
    workspace is replaced by the monotonic, accepted assembly.
    """
    ws = _ws_for(state)
    if ws is None or not snapshots_exist(state, stage):
        return 0
    try:
        full = recompose(stage, doc_key, read_snapshots(ws, stage))
        ws.save_artifact(canonical_name(doc_key), full)
        state[doc_key] = full
        return len(full)
    except Exception:
        return 0


def drop_snapshots(state: Any, stage: str, ids: list[str] | None = None,
                   doc_key: str = "") -> int:
    """Forget accepted sections (repair/reset) and rewrite the canonical doc.

    Dropping the snapshot is what makes a rewrite mandatory: recompose no
    longer contains the dropped section, so a stale copy cannot resurface.
    """
    ws = _ws_for(state)
    if ws is None:
        return 0
    d = ws.root / "context" / "sections" / stage.upper()
    if not d.is_dir():
        return 0
    want = set(ids) if ids else None
    n = 0
    for p in list(d.glob("*.md")):
        if want is None or p.stem in want:
            try:
                p.unlink()
                n += 1
            except Exception:
                continue
    if doc_key:
        try:
            full = recompose(stage, doc_key, read_snapshots(ws, stage))
            ws.save_artifact(canonical_name(doc_key), full)
            state[doc_key] = full
        except Exception:
            pass
    return n


def recompose(stage: str, doc_key: str, snapshots: dict[str, str]) -> str:
    """Canonical document = plan sections in plan order (accepted bodies only)."""
    parts = [f"# {DOC_TITLES.get(stage.upper(), doc_key)}"]
    for sec in sections_for(stage):
        body = (snapshots or {}).get(sec["id"], "").strip()
        if body:
            parts.append(f"## {sec['title']}\n\n{body}")
    return "\n\n".join(parts) + "\n"


def canonical_name(doc_key: str) -> str:
    return f"{doc_key}_assembled.md"


def accept_section(state: Any, stage: str, section: dict, text: str,
                   doc_key: str) -> dict:
    """Single acceptance authority for one section. Fail closed."""
    sid = section["id"]
    body = (text or "").strip()
    chk = check_section(stage, sid, body)
    missing = list(chk.get("missing") or [])
    advisory = list(chk.get("advisory") or [])
    if advisory:
        # carried into the handoff instead of costing a rewrite
        try:
            state.setdefault("section_advisories", {})[f"{stage}/{sid}"] = advisory
        except Exception:
            pass
    missing += verify_ids(state, stage, section, body)
    missing += _domain_missing(state, sid, body)
    if missing:
        return {"passed": False, "missing": missing, "advisory": advisory, "chars": len(body)}

    full = None
    path = ""
    ws = _ws_for(state)
    if ws is not None:
        try:
            write_snapshot(ws, stage, sid, body)
            full = recompose(stage, doc_key, read_snapshots(ws, stage))
            p = ws.save_artifact(canonical_name(doc_key), full)
            path = str(p)
        except Exception:
            full = None
    if full is None:
        full = recompose(stage, doc_key, {sid: body})
    try:
        state[doc_key] = full
    except Exception:
        pass
    _advance_registry(state, body)
    return {"passed": True, "missing": [], "advisory": advisory, "chars": len(body),
            "doc_chars": len(full), "path": path}
