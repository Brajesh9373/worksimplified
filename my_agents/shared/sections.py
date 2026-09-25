"""Sectional writing plans (Phase B fallback for output ceilings).

A stage with a section plan is written ONE section per agent pass instead of
one full document per pass. Each section fits small-model output limits;
per-section checks gate advancement; the full gate runs on the assembled doc.

Generalized: STAGE_SECTIONS maps any stage -> ordered section list.
Stages without an entry keep today's single-pass behavior.
"""

from __future__ import annotations

import re
from typing import Any

# id, title, must_contain (case-insensitive alternatives groups: all groups
# must match at least one alternative), id_patterns (regex, min count)
BA_SECTIONS = [
    {"id": "objectives", "title": "Objectives and Executive Summary",
     "need": [["objective", "executive summary", "goal", "purpose"]], "ids": []},
    {"id": "scope", "title": "Scope IN and Scope OUT",
     "need": [["scope in", "in scope", "in-scope"], ["scope out", "out of scope", "out-of-scope"]], "ids": []},
    {"id": "stakeholders", "title": "Stakeholders and Actors",
     "need": [["stakeholder", "actor", "role"]], "ids": []},
    {"id": "asis_tobe", "title": "AS-IS and TO-BE Process",
     "need": [["as-is", "asis", "current process", "today"], ["to-be", "tobe", "proposed", "future"]], "ids": []},
    {"id": "business_case", "title": "Business Case and Solution Options",
     "need": [["capability gap", "gap"], ["option", "approach", "alternativ"],
              ["benefit"], ["feasib", "viab"], ["risk of doing nothing", "inaction", "do nothing"]],
     "ids": [(r"\bOPT-\d+", 2)],
     "rows": [(r"OPT-\d+", 2)],
     "detail": "Capability gap statement; options table with OPT-n rows and columns "
               "Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended "
               "(>=2 options, exactly one Recommended=yes); a pro and a con per option; "
               "feasibility (technical + organisational); quantified benefits; an explicit "
               "'Risk of doing nothing' statement; decision factors."},
    {"id": "stories", "title": "User Stories and Acceptance Criteria",
     "need": [["accept", "given", "when", "then", "criteria"]],
     "ids": [(r"\bUS-\d+", 3)]},
    {"id": "rules", "title": "Business Rules",
     "need": [["rule", "policy", "must", "shall", "require"]],
     "ids": [(r"\bBR-\d+", 3)]},
    {"id": "datarisks", "title": "Data Needs, Risks, Glossary and KPIs",
     "need": [["data", "entit", "field"], ["risk", "assumption", "constraint"]], "ids": []},
    {"id": "solution_assessment", "title": "Solution Assessment, Readiness and Gaps",
     "need": [["coverage"], ["readiness", "ready"], ["gap"], ["accept"]],
     "ids": [(r"\bSM-\d+", 1)],
     "rows": [(r"US-\d+", 3)],
     "detail": "Coverage table with a row per US-/FR- id: Requirement | Solution element | "
               "Option (OPT-n) | Coverage (Full/Partial/None) | Gap or note; readiness table "
               "with the four dimensions People, Process, Technology, Training, each with a "
               "status and an action; explicit solution gaps and requirement-solution "
               "mismatches; a business acceptance statement naming the accepting role; "
               "success metrics as SM-n with a baseline and a target."},
]

STAGE_SECTIONS: dict[str, list[dict[str, Any]]] = {
    "BA": BA_SECTIONS,
    "PROJECT": [
        {"id": "objective", "title": "Objective and Success Criteria",
         "need": [["objective", "goal", "success"]], "ids": []},
        {"id": "wbs", "title": "Work Breakdown Structure",
         "need": [["wbs", "work breakdown", "task", "epic", "sprint"]],
         "ids": [(r"\bT-\d+", 5)],
         "rows": [(r"T-\d+", 5)], "rows_traced": 0.5,
         "detail": "Every T-xxx row must have columns: ID | Task | Owner | Priority (P0-P3 or "
                   "Must/Should/Could) | Estimate | Start | End | Dependencies | Trace. Dates are "
                   "ISO YYYY-MM-DD with end >= start; Dependencies is a T-xxx id or '-'."},
        {"id": "schedule", "title": "Milestones, Dependencies and Critical Path",
         "need": [["milestone"], ["dependenc"], ["critical path"]], "ids": [],
         "detail": "Milestones table with an ISO date per milestone (>=3); a predecessor → "
                   "successor dependency table using T-xxx ids; a critical path naming T-xxx tasks."},
        {"id": "raci_raid", "title": "RACI and RAID Log",
         "need": [["raci"], ["raid", "risk", "assumption", "issue"]], "ids": [],
         "detail": "RACI matrix with at least one R and one A; RAID rows with type, severity/impact, "
                   "owner, mitigation and a review date (ISO YYYY-MM-DD)."},
    ],
    "FUNCTIONAL": [
        {"id": "overview", "title": "Overview and Scope",
         "need": [["overview", "scope", "purpose", "goal"]], "ids": []},
        {"id": "fr_catalog", "title": "Functional Requirements Catalog",
         "need": [["priority", "must", "should", "could", "moscow"]],
         "ids": [(r"\bFR-\d+", 5)]},
        {"id": "use_cases", "title": "Use Cases and Alternate Flows",
         "need": [["alternate", "alternative", "exception flow", "primary flow"]],
         "ids": [(r"\bUC-\d+", 1)],
         "detail": "Every UC-xxx must state Actor, Precondition, Postcondition and an "
                   "alternate/exception flow."},
        {"id": "validations_data", "title": "Validations, Errors and Data Model",
         "need": [["validation", "error"], ["entit", "table", "attribute", "relationship"]], "ids": [],
         "detail": "Validation rules must carry error codes (E-xxx, at least 3) and name the "
                   "entity/field they apply to."},
        {"id": "traceability", "title": "Traceability Matrix",
         "need": [["traceab", "trace matrix", "matrix"]], "ids": []},
    ],
    "TECHNICAL": [
        {"id": "architecture", "title": "Architecture and Decisions",
         "need": [["architect", "component", "service"], ["decision", "adr", "trade"]], "ids": []},
        {"id": "apis", "title": "API Contracts",
         "need": [["get", "post", "put", "delete", "patch", "endpoint"]], "ids": [],
         "detail": "Every endpoint as METHOD /path with its response code (e.g. 200/201/400); "
                   "at least 3 endpoints."},
        {"id": "data", "title": "Data Schema",
         "need": [["schema", "table", "column", "primary key", "index"]], "ids": [],
         "detail": "Schema entries must state field types (varchar/int/date/decimal/…) and keys "
                   "(primary key, unique, index)."},
        {"id": "nfr_deploy", "title": "NFRs, Security and Deployment",
         "need": [["secur", "auth", "nfr", "performance", "deploy"]], "ids": []},
        {"id": "build_tasks", "title": "Technical Build Tasks",
         "need": [["task", "estimate", "sprint", "phase"]],
         "ids": [(r"\bTECH-\d+", 1), (r"\bFR-\d+", 3)],
         "detail": "Name the FR-xxx id that each task or architecture entry covers: the gate "
                   "requires every functional requirement to reappear in this design, and "
                   "anything deliberately not built must be marked deferred in words."},
    ],
}

# stage -> diagram kinds requested via tool-only passes after sections
STAGE_DIAGRAMS: dict[str, list[str]] = {
    "BA": ["flow"],
    "PROJECT": ["gantt"],
    "FUNCTIONAL": ["functional"],
    "TECHNICAL": ["sequence", "architecture"],
}

# backwards-compat alias (first kind per stage)
STAGE_DIAGRAM_SECTION: dict[str, str] = {k: v[0] for k, v in STAGE_DIAGRAMS.items()}


def sections_for(stage: str) -> list[dict[str, Any]]:
    return STAGE_SECTIONS.get(stage.upper(), [])


def _pipe_rows(text: str) -> list[list[str]]:
    """Pipe-table rows (>=2 cells) — same shape gates._wbs_rows measures."""
    rows = []
    for line in (text or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2:
            rows.append(cells)
    return rows


# production-grade guardrail: stub/placeholder markers are never acceptable
# content ([ASSUMPTION], Open Questions and Risk are deliberate vocabulary).
# Note: bare X-runs are NOT matched — they legitimately appear in example ids
# (e.g. "LEG-XXXXXX"), and a false rejection costs more than this false negative.
_STUB_RX = re.compile(
    r"\b(todo|tbd|fixme|lorem ipsum|placeholder|coming soon|"
    r"to be defined|to be decided|to be determined|<insert|\[insert)",
    re.IGNORECASE,
)


def stub_markers(text: str) -> list[str]:
    """Distinct placeholder markers found in a section/document (deterministic)."""
    return sorted({m.group(0).lower() for m in _STUB_RX.finditer(text or "")})


# ---------------------------------------------------------------- detail checks
# Production-grade detail: vague plans are rejected with precise reasons.
# Priority values: P0-P3 or Must/Should/Could. Dates: ISO YYYY-MM-DD.

_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_PRIORITY = re.compile(r"^\s*(p[0-3]\b|must\b|should\b|could\b)", re.IGNORECASE)
_DEP_NONE = {"-", "–", "—", "none", "n/a", "na", "nil"}
_TYPE_TOKENS = ("varchar", "bigint", "integer", "int", "decimal", "numeric", "date",
                "datetime", "timestamp", "text", "boolean", "bool", "json", "uuid",
                "enum", "currency", "float", "char", "link", "select", "table")
_KEY_TOKENS = ("primary key", "foreign key", "unique", "index", " pk", " fk")


def _rows_with_headers(text: str, row_rx: str) -> list[tuple[list[str], list[str]]]:
    """Pipe-table rows whose first cell matches row_rx, with their header row."""
    out: list[tuple[list[str], list[str]]] = []
    headers: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(re.fullmatch(r"[-:\s]*", c or "") for c in cells):
            continue  # separator row
        if re.fullmatch(row_rx, cells[0]):
            out.append((list(headers), cells))
        else:
            headers = cells
    return out


def _table_blocks(text: str) -> list[list[list[str]]]:
    blocks: list[list[list[str]]] = []
    cur: list[list[str]] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"[-:\s]*", c or "") for c in cells):
                continue
            cur.append(cells)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _col(headers: list[str], *needles: str) -> int:
    for i, h in enumerate(headers):
        hl = h.lower()
        if any(n in hl for n in needles):
            return i
    return -1


def _cell(cells: list[str], idx: int) -> str:
    return cells[idx] if 0 <= idx < len(cells) else ""


def looks_like_wbs_header(headers: list[str]) -> bool:
    """True when a table header row is a WBS header (not a dependency/RACI/timeline table).

    A WBS assigns work: it must carry an ownership/effort column. Tables that
    only list task ids with dates (timelines) or roles with letters (RACI) are
    not WBS tables.
    """
    joined = " | ".join(h.lower() for h in headers)
    return any(k in joined for k in ("owner", "role", "estimate", "assignee", "effort"))


TRACE_ID_RX = r"(?:US|BR|FR|UC|TECH|PMO)-\d+"


def _wbs_detail(text: str) -> list[str]:
    missing: list[str] = []
    # only rows inside the real WBS table (a dependency table's T-xxx rows are
    # not tasks and must not be judged by WBS column rules)
    rows = [(h, c) for h, c in _rows_with_headers(text, r"T-\d+")
            if looks_like_wbs_header(h)]
    if not rows:
        return ["section 'wbs' has no T-xxx table rows"]
    for headers, cells in rows:
        rid = cells[0]
        pi = _col(headers, "priority", "prio")
        pri = _cell(cells, pi) if pi >= 0 else ""
        if not (pri and _PRIORITY.match(pri)) and not any(_PRIORITY.match(c) for c in cells):
            missing.append(f"section 'wbs' row {rid} lacks a priority "
                           "(allowed: P0-P3 or Must/Should/Could)")
        dates = _ISO_DATE.findall(" | ".join(cells))
        if len(dates) < 2:
            missing.append(f"section 'wbs' row {rid} lacks start/end dates (ISO YYYY-MM-DD)")
        elif dates[0] > dates[1]:
            missing.append(f"section 'wbs' row {rid} end date {dates[1]} is before start {dates[0]}")
        di = _col(headers, "depend", "deps", "blocked")
        dep = _cell(cells, di)
        if di < 0 or not (re.search(r"T-\d+", dep) or dep.strip().lower() in _DEP_NONE):
            missing.append(f"section 'wbs' row {rid} lacks dependencies (T-xxx or '-')")
        oi = _col(headers, "owner", "role", "assignee")
        if oi < 0 or not _cell(cells, oi).strip():
            missing.append(f"section 'wbs' row {rid} lacks an owner role")
        ei = _col(headers, "estimat", "effort", "days", "points")
        if ei < 0 or not _cell(cells, ei).strip():
            missing.append(f"section 'wbs' row {rid} lacks an estimate")
        if not any(re.search(TRACE_ID_RX, c) for c in cells):
            missing.append(f"section 'wbs' row {rid} lacks a trace id "
                           f"(US-/BR-/FR-/UC-/TECH-/PMO-)")
    return missing


def _schedule_detail(text: str) -> list[str]:
    missing: list[str] = []
    ms_rows = 0
    for block in _table_blocks(text):
        header = block[0]
        if _col(header, "milestone") >= 0:
            for row in block[1:]:
                ms_rows += 1
                if not _ISO_DATE.search(" | ".join(row)):
                    missing.append(f"section 'schedule' milestone '{row[0][:40]}' has no date "
                                   "(ISO YYYY-MM-DD)")
        if _col(header, "predecessor") >= 0 or _col(header, "successor") >= 0:
            for row in block[1:]:
                if not any(re.search(r"T-\d+", c) for c in row):
                    missing.append("section 'schedule' dependency row lacks T-xxx ids "
                                   f"('{row[0][:40]}')")
    if ms_rows < 3:
        missing.append(f"section 'schedule' has {ms_rows} dated milestones (need >=3)")
    if "critical path" not in (text or "").lower():
        missing.append("section 'schedule' does not name the critical path")
    elif not re.search(r"T-\d+", text or ""):
        missing.append("section 'schedule' critical path names no T-xxx task")
    return missing


def _raid_detail(text: str) -> list[str]:
    missing: list[str] = []
    has_r = has_a = False
    for block in _table_blocks(text):
        # layout A: rows = roles, cells = R/A/C/I (any markdown emphasis)
        for row in block:
            vals = {re.sub(r"[*_`\s]+", "", v or "").upper()
                    for c in row for v in c.split("/")}
            if "R" in vals or "RESPONSIBLE" in vals:
                has_r = True
            if "A" in vals or "ACCOUNTABLE" in vals:
                has_a = True
        # layout B: columns labelled Responsible/Accountable, cells = role names
        header = block[0]
        col_r = _col(header, "responsible")
        col_a = _col(header, "accountable")
        if col_r >= 0 and any(_cell(r, col_r).strip() for r in block[1:]):
            has_r = True
        if col_a >= 0 and any(_cell(r, col_a).strip() for r in block[1:]):
            has_a = True
    if not (has_r and has_a):
        missing.append("section 'raci_raid' RACI matrix needs at least one R and one A")
    raid_rows = 0
    for block in _table_blocks(text):
        header = block[0]
        if _col(header, "type") >= 0 and (_col(header, "mitigation") >= 0
                                          or _col(header, "resolution") >= 0):
            sev_i = _col(header, "severity", "impact", "probability", "rating")
            own_i = _col(header, "owner")
            mit_i = _col(header, "mitigation", "resolution")
            rev_i = _col(header, "review", "due")
            for row in block[1:]:
                raid_rows += 1
                rid = row[0][:40]
                if sev_i < 0 or not _cell(row, sev_i).strip():
                    missing.append(f"section 'raci_raid' entry '{rid}' lacks severity/impact")
                if own_i < 0 or not _cell(row, own_i).strip():
                    missing.append(f"section 'raci_raid' entry '{rid}' lacks an owner")
                if mit_i < 0 or not _cell(row, mit_i).strip():
                    missing.append(f"section 'raci_raid' entry '{rid}' lacks a mitigation")
                if not (_ISO_DATE.search(" | ".join(row)) or (rev_i >= 0 and _cell(row, rev_i).strip())):
                    missing.append(f"section 'raci_raid' entry '{rid}' lacks a review date "
                                   "(ISO YYYY-MM-DD)")
    if raid_rows == 0:
        missing.append("section 'raci_raid' has no RAID table (type/owner/mitigation columns)")
    return missing


def _use_cases_detail(text: str) -> list[str]:
    """Every real UC-xxx block must state actor, precondition, postcondition.

    Cross-references (ids inside traceability tables) are not use-case blocks
    and are skipped — a block is a heading or a chunk with flow text.
    """
    missing: list[str] = []
    t = text or ""
    hits = list(re.finditer(r"\bUC-\d+\b", t, re.IGNORECASE))
    if not hits:
        return ["section 'use_cases' names no UC-xxx use case"]
    in_scope = 0
    for i, m in enumerate(hits):
        nxt_uc = hits[i + 1].start() if i + 1 < len(hits) else len(t)
        nxt_head = t.find("\n## ", m.end())
        end = min(nxt_uc, nxt_head) if nxt_head >= 0 else nxt_uc
        chunk = t[m.start():end]
        low = chunk.lower()
        # a UC id on a markdown table row is a cross-reference (e.g. the traceability
        # matrix), never a use-case block — judging it here demanded pre/post
        # conditions from a section that cannot supply them.
        line_start = t.rfind("\n", 0, m.start()) + 1
        line_end = t.find("\n", m.start())
        row = t[line_start:(line_end if line_end >= 0 else len(t))]
        if row.strip().startswith("|"):
            continue
        is_block = bool(re.match(r"#{2,}\s*uc-", low)) or any(
            k in low for k in ("actor", "precondition", "postcondition", "flow"))
        if not is_block:
            continue  # a cross-reference, not a use-case block
        in_scope += 1
        for label in ("actor", "precondition", "postcondition"):
            if label not in low:
                missing.append(f"section 'use_cases' {m.group(0).upper()} lacks {label}")
    if in_scope == 0:
        missing.append("section 'use_cases' has no UC-xxx block with actor/pre/post conditions")
    return missing


def _validations_detail(text: str) -> list[str]:
    codes = re.findall(r"\b(?:E|ERR)-[A-Z0-9]{1,8}\b", text or "")
    if len(codes) < 3:
        return [f"section 'validations_data' has {len(codes)} error codes (need >=3, e.g. E-C01)"]
    return []


def _business_case_detail(text: str) -> list[str]:
    """Stage 2 artefact: capability gap, options with feasibility, risk of inaction."""
    missing: list[str] = []
    t = text or ""
    low = t.lower()
    opt_ids = sorted(set(re.findall(r"\bOPT-\d+\b", t, re.IGNORECASE)))
    if len(opt_ids) < 2:
        missing.append(f"section 'business_case' names {len(opt_ids)} option(s) "
                       "(need >=2 OPT-n options)")
    if not re.search(r"capability\s+gap|\bgap\b", low):
        missing.append("section 'business_case' states no capability gap")
    if not re.search(r"benefit", low):
        missing.append("section 'business_case' lists no benefits")
    if not re.search(r"feasib|viab", low):
        missing.append("section 'business_case' states no feasibility assessment")
    if not re.search(r"risk of doing nothing|inaction|do(-|\s)nothing", low):
        missing.append("section 'business_case' does not state the risk of doing nothing")
    if not re.search(r"decision factor|criteri", low):
        missing.append("section 'business_case' lists no decision factors")
    if not re.search(r"\d", t):
        missing.append("section 'business_case' benefits are not quantified (no digits)")
    # exactly one recommended option
    recommended: list[str] = []
    for headers, cells in _rows_with_headers(t, r"OPT-\d+"):
        i = _col(headers, "recommend")
        if i >= 0 and _cell(cells, i).strip().lower() in ("yes", "y", "true", "recommended"):
            recommended.append(cells[0].strip())
    if len(recommended) != 1:
        missing.append(f"section 'business_case' marks {len(recommended)} recommended option(s) "
                       "(need exactly 1 Recommended=yes row)")
    # pro and con per option. The option id is typically mentioned first in the
    # capability-gap table, thousands of characters before its own pro/con lines,
    # so gather the region after *every* mention — not only the first one, which
    # rejected correct business cases whose gap table cites the option early.
    _pro_rx = re.compile(r"\bpro\b|advantag|strength")
    _con_rx = re.compile(r"\bcon\b|disadvantag|weakness|limitation")
    for oid in opt_ids:
        windows = [t[m.start(): m.start() + 800].lower()
                   for m in re.finditer(rf"\b{re.escape(oid)}\b", t, re.IGNORECASE)]
        blob = "\n".join(windows)
        if not (_pro_rx.search(blob) and _con_rx.search(blob)):
            missing.append(f"section 'business_case' {oid} lacks a pro and a con")
    return missing


_READINESS_DIMENSIONS = ("people", "process", "technology", "training")


def _solution_assessment_detail(text: str) -> list[str]:
    """Stage 7 artefact: coverage per requirement, readiness, gaps, acceptance."""
    missing: list[str] = []
    t = text or ""
    low = t.lower()
    req_ids = sorted(set(re.findall(r"\b(?:US|FR)-\d+\b", t, re.IGNORECASE)))
    if not req_ids:
        missing.append("section 'solution_assessment' maps no US-/FR- requirement")
    if not re.search(r"coverage|covered|full|partial|none", low):
        missing.append("section 'solution_assessment' has no coverage column/statement")
    for dim in _READINESS_DIMENSIONS:
        if dim not in low:
            missing.append(f"section 'solution_assessment' readiness is missing '{dim}'")
    if not re.search(r"gap|mismatch", low):
        missing.append("section 'solution_assessment' lists no solution gaps")
    if not re.search(r"accept", low):
        missing.append("section 'solution_assessment' states no business acceptance")
    metrics = sorted(set(re.findall(r"\bSM-\d+\b", t, re.IGNORECASE)))
    if not metrics:
        missing.append("section 'solution_assessment' defines no SM-n success metric")
    if not re.search(r"baseline", low) or not re.search(r"target", low):
        missing.append("section 'solution_assessment' metrics lack a baseline and a target")
    return missing


_HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _is_endpoint_line(ln: str) -> bool:
    """Endpoint in either form: inline `METHOD /path` or a table row with a
    method cell and a path cell."""
    if re.search(r"\b(GET|POST|PUT|PATCH|DELETE)\b\s+/\S*", ln or ""):
        return True
    cells = [re.sub(r"[*_`]", "", c).strip()
             for c in (ln or "").strip().strip("|").split("|")]
    if len(cells) < 2:
        return False
    has_method = any(c.upper() in _HTTP_METHODS for c in cells)
    has_path = any(c.startswith("/") for c in cells)
    return has_method and has_path


def endpoint_count(text: str) -> int:
    return sum(1 for ln in (text or "").splitlines() if _is_endpoint_line(ln))


def _apis_detail(text: str) -> list[str]:
    lines = (text or "").splitlines()
    ep_lines = [i for i, ln in enumerate(lines) if _is_endpoint_line(ln)]
    if len(ep_lines) < 3:
        return [f"section 'apis' documents {len(ep_lines)} endpoints (need >=3: METHOD /path)"]
    no_code = 0
    ep_set = set(ep_lines)
    for i in ep_lines:
        window = [lines[i]]
        for j in range(i + 1, min(i + 3, len(lines))):
            if j in ep_set:
                break  # the next endpoint's own line is not this one's response
            window.append(lines[j])
        if not re.search(r"\b[1-5]\d{2}\b", " ".join(window)):
            no_code += 1
    if no_code:
        return [f"section 'apis' has {no_code} endpoints without a response code "
                "(e.g. 200/201/400)"]
    return []


def _data_detail(text: str) -> list[str]:
    low = (text or "").lower()
    types = sum(1 for t in _TYPE_TOKENS if re.search(rf"\b{t}\b", low))
    keys = sum(1 for t in _KEY_TOKENS if t in low)
    missing: list[str] = []
    if types < 3:
        missing.append(f"section 'data' states {types} field types (need >=3: varchar/int/date/...)")
    if keys < 1:
        missing.append("section 'data' declares no keys/indexes (primary key, unique, index)")
    return missing


def _stories_detail(text: str) -> list[str]:
    """Stage 4 artefact: every US-xxx carries a priority tag and >=2 criteria.

    Both rules are checked by reusing the requirement/quality engines' own
    functions, so a section accepted here cannot then block the gate — a summary
    that merely *mentions* the story ids is rejected instead of being written over
    a good section (which is what happened before: a repair pass replaced a 28k
    stories section with a 1.9k report about it and the gate saw no stories).
    """
    missing: list[str] = []
    t = text or ""
    try:
        from ba_agent.managers.requirement_engine import validate_requirements

        for m in validate_requirements(t)["missing"]:
            if "criteria" in m:
                missing.append(f"section 'stories' {m}")
    except Exception:
        pass
    try:
        from ba_agent.managers.quality_engine import priorities_untagged

        untagged = [i for i in priorities_untagged(t) if i.upper().startswith("US-")]
        if untagged:
            missing.append("section 'stories' carries no priority tag on "
                           + ", ".join(untagged[:6]))
    except Exception:
        pass
    missing.extend(_atomicity_missing(t, "US"))
    return missing


def _atomicity_missing(text: str, prefix: str) -> list[str]:
    """Non-atomic obligation lines, using the quality engine's own predicate.

    The gate blocks on `modifiable` but acceptance did not check it, so a
    non-atomic rule was accepted, blocked the gate, got rewritten, failed again,
    and the repeated re-run eventually tripped the workflow's replay scheduler.
    Same rule here as there, so the two cannot disagree.
    """
    out: list[str] = []
    try:
        from ba_agent.managers.quality_engine import (_MODALS, _br_blocks,
                                                      _us_blocks)
    except Exception:
        return []
    blocks = _br_blocks(text or "") if prefix == "BR" else _us_blocks(text or "")
    for iid, block in blocks:
        for line in (block or "").strip().splitlines():
            bare = re.sub(r"\[[^\]]*\]", " ", line)
            if not _MODALS.search(bare):
                continue
            obligations = len(_MODALS.findall(bare))
            joined = len(re.findall(r"\band\b", bare, re.IGNORECASE))
            if obligations > 1 or (obligations == 1 and joined >= 1):
                out.append(f"{iid} is not atomic (multiple obligations) — one obligation per "
                           f"rule: {re.sub(r'\\s+', ' ', line).strip()[:110]}")
                break
    return out


def _rules_detail(text: str) -> list[str]:
    """Stage 4 artefact: every BR-xxx carries a priority tag and is atomic."""
    missing: list[str] = []
    t = text or ""
    try:
        from ba_agent.managers.quality_engine import priorities_untagged

        untagged = [i for i in priorities_untagged(t) if i.upper().startswith("BR-")]
        if untagged:
            missing.append("section 'rules' carries no priority tag on "
                           + ", ".join(untagged[:6]))
    except Exception:
        pass
    missing.extend(_atomicity_missing(t, "BR"))
    return missing


_DETAIL_CHECKS = {
    ("BA", "business_case"): _business_case_detail,
    ("BA", "stories"): _stories_detail,
    ("BA", "rules"): _rules_detail,
    ("BA", "solution_assessment"): _solution_assessment_detail,
    ("PROJECT", "wbs"): _wbs_detail,
    ("PROJECT", "schedule"): _schedule_detail,
    ("PROJECT", "raci_raid"): _raid_detail,
    ("FUNCTIONAL", "use_cases"): _use_cases_detail,
    ("FUNCTIONAL", "validations_data"): _validations_detail,
    ("TECHNICAL", "apis"): _apis_detail,
    ("TECHNICAL", "data"): _data_detail,
}


def _ambiguity_detail(text: str) -> list[str]:
    """Unquantified vague terms AND unmeasurable NFR statements.

    The gate's `unambiguous` attribute has two halves and both block. Acceptance
    checked only the first, so a section was accepted, then failed the gate on the
    second, was rewritten, failed again, exhausted the revision budget and hung the
    workflow's replay scheduler. Both halves are checked here, using the quality
    engine's own regexes so the two can never disagree.
    """
    missing: list[str] = []
    try:
        from ba_agent.managers.quality_engine import (_ASSERTION, _NFR_HINT,
                                                      _REQ_CONTEXT, _VAGUE,
                                                      _quantified, _sentence)
    except Exception:
        return []
    t = text or ""
    low = t.lower()
    for term in _VAGUE:
        for m in re.finditer(rf"\b{re.escape(term)}\b", low):
            snip = _sentence(t, m.start(), m.end())
            if _quantified(snip):
                continue
            flat = re.sub(r"\s+", " ", snip).strip()[:120]
            missing.append(f"vague term '{term}' is not quantified — give it a number, "
                           f"unit or bound in the same sentence: {flat}")
            break
    for line in t.splitlines():
        if not _NFR_HINT.search(line) or not _ASSERTION.search(line):
            continue
        if not _REQ_CONTEXT.search(line) or _quantified(line):
            continue
        flat = re.sub(r"\s+", " ", line).strip()[:140]
        missing.append("non-functional statement is not measurable — add a number, unit "
                       f"or bound: {flat}")
    return missing


def detail_missing(stage: str, section_id: str, text: str) -> list[str]:
    """Production-grade detail failures for one section (shared by tools and gates)."""
    fn = _DETAIL_CHECKS.get((stage.upper(), section_id))
    out: list[str] = []
    if fn:
        try:
            out = list(fn(text))
        except Exception:
            out = []
    if (stage or "").upper() == "BA":
        try:
            out.extend(_ambiguity_detail(text))
        except Exception:
            pass
    return out


def parse_wbs_rows(plan: str) -> list[dict[str, str]]:
    """Header-driven WBS parsing (single source of truth for consumers).

    Only rows inside a real WBS table count: dependency, RACI and timeline
    tables also contain T-xxx ids and must never be turned into tasks.
    Columns are mapped by header name (owner/priority/estimate/start/end/
    dependencies/trace) so column order and wording do not matter. Legacy
    tables without a WBS header fall back to positional parsing.
    """
    out: list[dict[str, str]] = []
    for headers, cells in _rows_with_headers(plan or "", r"T-\d+"):
        if not looks_like_wbs_header(headers):
            continue

        def v(*needles: str, default: int = -1) -> str:
            i = _col(headers, *needles)
            if i < 0:
                i = default
            return _cell(cells, i).strip()

        out.append({
            "id": cells[0].strip(),
            "title": (v("task", "description", "activity", "title", default=1) or "task")[:140],
            "owner": v("owner", "role", "assignee"),
            "priority": v("priority", "prio"),
            "est": v("estimat", "effort", "days", "points"),
            "start": v("start"),
            "end": v("end", "due", "finish"),
            "deps": v("depend", "deps", "blocked"),
            "trace": v("trace", "source", "req"),
        })
    if out:
        return out
    # legacy fallback: positional parse for tables without a recognisable header
    for line in (plan or "").splitlines():
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(c) >= 3 and re.fullmatch(r"T-\d+", c[0]):
            out.append({"id": c[0], "title": c[1][:140], "owner": c[2] if len(c) > 2 else "",
                        "priority": "", "est": c[3] if len(c) > 3 else "",
                        "start": "", "end": "", "deps": "", "trace": ""})
    return out


def check_section(stage: str, section_id: str, text: str) -> dict:
    """Validate one freshly-written section. Returns {passed, missing[]}."""
    secs = {s["id"]: s for s in sections_for(stage)}
    spec = secs.get(section_id)
    if not spec:
        return {"passed": True, "missing": [], "note": "no plan; skipping"}
    missing = []
    low = (text or "").lower()
    for group in spec["need"]:
        if not any(a in low for a in group):
            missing.append(f"section '{section_id}' lacks any of {group}")
    for rx, minimum in spec["ids"]:
        found = len(set(re.findall(rx, text or "")))
        if found < minimum:
            missing.append(f"section '{section_id}' has {found} {rx} ids (need >={minimum})")
    rows_spec = spec.get("rows") or []
    if rows_spec:
        rows = _pipe_rows(text)
        for rx, minimum in rows_spec:
            n = len([r for r in rows if re.fullmatch(rx, r[0])])
            if n < minimum:
                missing.append(f"section '{section_id}' has {n} table rows matching {rx} "
                               f"(need >={minimum})")
        frac = spec.get("rows_traced")
        if frac and rows_spec:
            matching = [r for r in rows
                        if any(re.fullmatch(rx, r[0]) for rx, _ in rows_spec)]
            if matching:
                traced = [r for r in matching
                          if re.search(TRACE_ID_RX, " | ".join(r), re.IGNORECASE)]
                if len(traced) < len(matching) * float(frac):
                    missing.append(f"section '{section_id}' has {len(traced)}/{len(matching)} rows "
                                   f"tracing to US/BR/TECH/PMO (need >={float(frac):.0%})")
    if len(text or "") < 200:
        missing.append(f"section '{section_id}' too short ({len(text or '')} chars, need >=200)")
    if len(text or "") > MAX_SECTION_CHARS:
        missing.append(f"section '{section_id}' is {len(text or '')} chars "
                       f"(max {MAX_SECTION_CHARS}) — rewrite it inside the output budget")
    # Polish (atomicity, measurability, priority tags, per-item criteria, evidence
    # links) is ADVISORY, not blocking. Each rewrite costs a full model pass, and a
    # chain that must hand off early cannot spend them on polish — the same items are
    # recorded and carried into the handoff instead. Structure still blocks.
    advisory = detail_missing(stage, section_id, text or "")
    stubs = stub_markers(text)
    if stubs:
        missing.append(f"section '{section_id}' contains placeholder markers {stubs} "
                       "(production-grade content required)")
    return {"passed": not missing, "missing": missing, "advisory": advisory}


def pending_sections(stage: str, done: list[str]) -> list[dict[str, Any]]:
    done_set = set(done or [])
    return [s for s in sections_for(stage) if s["id"] not in done_set]


# The length contract lives in one place: the section brief, the prose rung and the
# CLI adapter all quote it. An over-long section costs minutes of generation, and it
# is also what overflows the output cap and gets the whole tool call discarded.
MAX_SECTION_CHARS = 16000


def _sample_id(pattern: str) -> str:
    """A human-readable example id from a validation regex (``\\bOPT-\\d+`` -> OPT-001)."""
    s = re.sub(r"\\b|\^|\$", "", pattern or "")
    s = re.sub(r"\\d\+", "001", s)
    return (s.replace("(?:", "").replace(")", "") or "ID-001").strip()


# Literal templates for the sections whose *shape* the checks are strict about.
# A model writing free prose reliably misses table columns and per-item
# requirements; a template it can copy does not.
_SHAPES: dict[tuple[str, str], str] = {
    ("BA", "business_case"): """Capability gap: <what the business cannot do today, in one sentence>

| Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended |
|---|---|---|---|---|---|
| OPT-001 | <approach> | <benefit, with a number> | <cost/effort> | <feasibility> | yes |
| OPT-002 | <approach> | <benefit, with a number> | <cost/effort> | <feasibility> | no |

OPT-001 pro: <one line>
OPT-001 con: <one line>
OPT-002 pro: <one line>
OPT-002 con: <one line>

Feasibility: <technical and organisational, with a number where possible>
Risk of doing nothing: <one explicit sentence>
Decision factors: <what the business will weigh>""",
    ("BA", "stories"): """US-001 [MoSCoW: Must] As a <role> I want <capability> so that <benefit>.
- AC-001 Given <context> when <action> then <measurable outcome>.
- AC-002 Given <context> when <action> then <measurable outcome>.

US-002 [MoSCoW: Should] As a <role> I want <capability> so that <benefit>.
- AC-001 Given <context> when <action> then <measurable outcome>.
- AC-002 Given <context> when <action> then <measurable outcome>.

US-003 [MoSCoW: Could] As a <role> I want <capability> so that <benefit>.
- AC-001 Given <context> when <action> then <measurable outcome>.
- AC-002 Given <context> when <action> then <measurable outcome>.""",
    ("BA", "rules"): """BR-001 [MoSCoW: Must] Only a user holding the <role> role may approve a <document> whose value exceeds <amount>.
BR-002 [MoSCoW: Should] Every <document> must carry itemised <evidence> before it can be reimbursed.
BR-003 [MoSCoW: Could] A <document> that duplicates an earlier one within <n> days must be rejected.""",
    ("BA", "solution_assessment"): """| Requirement | Solution element | Option | Coverage | Gap or note |
|---|---|---|---|---|
| US-001 | <solution element> | OPT-001 | Full | none |
| US-002 | <solution element> | OPT-001 | Partial | <the gap> |
| US-003 | <solution element> | OPT-001 | Full | none |

Readiness:
- People: status <status>, action <action>
- Process: status <status>, action <action>
- Technology: status <status>, action <action>
- Training: status <status>, action <action>

Solution gaps: <explicit gaps, or "none identified">
Business acceptance: <role> accepts the solution against these criteria.
Success metrics: SM-001 <metric> (baseline <value>, target <value>, measured <how>, reviewed <when>).""",
    ("PROJECT", "wbs"): """| ID | Task | Owner | Priority | Estimate | Start | End | Dependencies | Trace |
|---|---|---|---|---|---|---|---|---|
| T-001 | <task> | <owner role> | P1 | 5 days | 2026-01-05 | 2026-01-09 | - | US-001 |
| T-002 | <task> | <owner role> | P2 | 3 days | 2026-01-12 | 2026-01-14 | T-001 | BR-002 |
| T-003 | <task> | <owner role> | Must | 2 days | 2026-01-12 | 2026-01-13 | T-001 | FR-003 |

<continue until every deliverable is covered: at least 5 rows, every row with an
owner, a priority, an estimate, a start and end date (YYYY-MM-DD, end on or after
start), a dependency (T-nnn or -) and a trace id>""",
}

# Rules enforced outside the section spec (quality engine + BA ledger): kept here
# so the brief states them next to the section that must satisfy them.
_EXTRA_GUARDRAILS: dict[tuple[str, str], tuple[str, ...]] = {
    ("BA", "stories"): (
        "every US-xxx carries a priority tag, e.g. [MoSCoW: Must]",
        "every US-xxx has at least 2 acceptance criteria",
        "acceptance criteria read Given … when … then … with a measurable outcome",
        "one obligation per story — do not join two obligations with 'and'",
    ),
    ("BA", "rules"): (
        "every BR-xxx carries a priority tag, e.g. [MoSCoW: Must]",
        "one obligation per rule — do not join two obligations with 'and'",
    ),
}

# Applies to every section of the stage, not just the requirements ones: the gate
# checks these document-wide, so they have to be stated everywhere.
_STAGE_GUARDRAILS: dict[str, tuple[str, ...]] = {
    "BA": (
        "no unquantified vague terms anywhere in this section — every use of quickly, "
        "fast, soon, robust, scalable, efficient, flexible, seamless, intuitive, "
        "minimal, several, many, various, appropriate, reasonable, 'as needed', etc, "
        "and/or or modern must be quantified in the same sentence with a number, unit "
        "or bound (the gate blocks on these, in any section)",
    ),
}


def shape_for(stage: str, section_id: str) -> str:
    """The literal template for a section, when one is defined (``""`` otherwise)."""
    return _SHAPES.get((stage.upper(), section_id), "")


# Write-tool calls the agent must make *after* writing a section so the BA ledger
# the gate checks is actually populated. Stated next to the section that creates
# the ids, because that is the only moment the agent knows them.
_RECORD_AFTER: dict[tuple[str, str], tuple[str, ...]] = {
    ("BA", "stories"): (
        "link_evidence(artifact_id=<each US-xxx>, source_type, source_ref) for EVERY US-xxx "
        "you wrote — the gate blocks when a requirement has no evidence",
    ),
    ("BA", "rules"): (
        "link_evidence(artifact_id=<each BR-xxx>, source_type, source_ref) for EVERY BR-xxx "
        "you wrote",
    ),
    ("BA", "business_case"): (
        "record_decision(decision, reason, decision_maker, related_ids=<OPT-n/BN-n ids>) for "
        "the option the business settles on",
    ),
    ("BA", "solution_assessment"): (
        "record_success_metric(name, baseline, target, method, review_point, metric_id=<SM-n>) "
        "for EVERY SM-n you wrote",
    ),
}

_RECORD_ALWAYS_BA = (
    "record_assumption(label=<the ASSUMPTION tag name>, text=<the assumption>) for every "
    "[ASSUMPTION:xxx] tag in this section — an unregistered tag blocks the gate",
)

_LENGTH_BUDGET = (
    "OUTPUT BUDGET — write 1,500–3,000 characters. Every check above passes inside "
    "that budget, so there is no reason to go longer; a section over "
    f"{MAX_SECTION_CHARS:,} characters is rejected and rewritten. Be dense: no "
    "repetition, no restating this brief, and no commentary about what you wrote — "
    "output only the section itself."
)


def record_after(stage: str, section_id: str) -> list[str]:
    """Tool calls the agent must make after this section (ledger population)."""
    lines = list(_RECORD_AFTER.get((stage.upper(), section_id), ()))
    if (stage or "").upper() == "BA":
        lines.append(_RECORD_ALWAYS_BA)
    return lines


def guardrails(stage: str, section: dict[str, Any]) -> list[str]:
    """The automated checks for one section, in plain language.

    Derived from the same spec ``check_section`` validates against, so the
    instruction the model receives cannot drift from what is actually enforced.
    """
    lines: list[str] = []
    for group in section.get("need") or []:
        alts = " or ".join(f'"{a}"' for a in group)
        lines.append(f"the text must contain {alts}")
    for rx, minimum in (section.get("ids") or []):
        lines.append(f"must contain at least {minimum} id(s) of the form "
                     f"{_sample_id(rx)}")
    for rx, minimum in (section.get("rows") or []):
        lines.append(f"must contain a markdown pipe table with at least {minimum} row(s) "
                     f"whose first cell is {_sample_id(rx)}")
    detail = str(section.get("detail") or "").strip()
    if detail:
        for part in detail.split(";"):
            part = part.strip().rstrip(".")
            if part:
                lines.append(part)
    lines.extend(_EXTRA_GUARDRAILS.get((stage.upper(), section["id"]), ()))
    lines.extend(_STAGE_GUARDRAILS.get(stage.upper(), ()))
    lines.append("the section must be at least 200 characters")
    return lines


def section_assignment(stage: str, section: dict[str, Any], done: list[str],
                       assigned_ids: dict[str, list[str]],
                       allocated: dict[str, list[str]] | None = None) -> str:
    """Brief fragment instructing exactly one section (orchestration, not a prompt edit).

    The required shape and the automated checks are generated from the same spec
    the validator uses, and the checks are stated as hard gates — a section that
    misses one is rejected and rewritten, so the model has no reason to treat
    them as advice.
    """
    others = [s for s in sections_for(stage) if s["id"] != section["id"]]
    lines = [
        "SECTION TASK — write ONLY this one section now (do not rewrite others):",
        f"Section: {section['title']} (id: {section['id']})",
        f"Completed sections so far: {done or 'none — this is the first'}",
    ]
    if assigned_ids:
        lines.append("Already-assigned IDs you MUST reuse verbatim (do not renumber): " +
                     ", ".join(f"{k}={v}" for k, v in assigned_ids.items() if v))
    allocated = allocated or {}
    if allocated:
        ids_line = "; ".join(f"{fam}: {', '.join(v)}"
                             for fam, v in allocated.items() if v)
        if ids_line:
            lines.append("Use EXACTLY these IDs (allocated by the harness — do not "
                         f"renumber, do not invent others): {ids_line}")
    shape = shape_for(stage, section["id"])
    if shape:
        lines.append("REQUIRED SHAPE — reproduce this structure exactly, filling in the "
                     "placeholders (keep the table columns and the per-item lines):")
        lines.append(shape)
    checks = guardrails(stage, section)
    if checks:
        lines.append("AUTOMATED CHECKS — this section is rejected and rewritten if any of "
                     "these fail, so satisfy every one of them:")
        lines.extend(f"- {c}" for c in checks)
    lines.append(_LENGTH_BUDGET)
    lines.append(
        "Call append_doc with doc_name for this stage, section_title exactly "
        f"'{section['title']}', and content_markdown containing ONLY this section, "
        "with complete tables and all required IDs, inside the output budget above. "
        "Do not paste other sections. Do not ask questions.")
    follow_up = record_after(stage, section["id"])
    if follow_up:
        # Measured: sequencing these ("after append_doc, then ...") makes the model
        # spend one turn per call — 4-5 model calls a pass. One explicit batching
        # instruction and it emits all of them in a single turn, with less reasoning.
        lines.append(
            "ONE TURN — emit append_doc AND every ledger call below in a SINGLE turn, "
            "together, without waiting for any tool result first. This pass must cost "
            "one turn, not one turn per call.")
        lines.append("Ledger calls to send in that same turn (the gate blocks without them):")
        lines.extend(f"- {c}" for c in follow_up)
    if others:
        lines.append("Remaining sections (NOT now, later passes): " +
                     ", ".join(s["title"] for s in others))
    return "\n".join(lines)
