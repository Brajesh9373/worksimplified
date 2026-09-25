"""Deterministic quality gates — no LLM judgment about own output.

Each gate inspects the stage's actual artifacts (session state first,
CURRENT project workspace second) and returns
{stage, passed, missing[], warnings[], checked}. Missing items are actionable
strings the orchestrator feeds back for revision or escalates to a human.

Thresholds are documented per check and kept lenient on formatting
(case-insensitive keyword alternatives) but strict on structure.
"""

from __future__ import annotations

import re
from typing import Any

from .traceability import coverage_report, doc_texts, extract_ids

DIAGRAM_SUFFIX = "_diagram"


def _diagrams(state: dict[str, Any]) -> dict[str, dict]:
    """kind -> diagram metadata dict (first match wins)."""
    from .project_context import state_dict
    out: dict[str, dict] = {}
    for k, v in state_dict(state).items():
        if isinstance(k, str) and k.endswith(DIAGRAM_SUFFIX) and isinstance(v, dict):
            kind = str(v.get("kind", "")).lower()
            if kind and kind not in out:
                out[kind] = v
    return out


def _has(text: str, *alternatives: str) -> bool:
    t = text.lower()
    return any(a.lower() in t for a in alternatives)


def _count_rx(text: str, rx: str) -> int:
    return len(re.findall(rx, text, re.IGNORECASE))


class GateResult(dict):
    pass


def _result(stage: str, checks: list[tuple[str, bool, str, bool]]) -> dict:
    """checks: (name, passed, detail, required)."""
    missing = [f"{n}: {d}" for n, p, d, req in checks if req and not p]
    warnings = [f"{n}: {d}" for n, p, d, req in checks if not req and not p]
    return {
        "stage": stage,
        "passed": not missing,
        "missing": missing,
        "warnings": warnings,
        "checked": len(checks),
    }


def _doc(state: dict[str, Any], key: str) -> str:
    return doc_texts(state).get(key, "")


# ---------------- BA ----------------

def _ba_section_ok(state: dict[str, Any], section_id: str) -> tuple[bool, list[str]]:
    """Re-validate a BA section's production-grade contract from the BRD text.

    Acceptance happens in the harness, but the gate re-checks so a contract
    added after a section was written cannot be skipped (fail-closed).
    """
    try:
        from .harness import extract_section_text
        from .sections import check_section, sections_for

        title = next((s["title"] for s in sections_for("BA") if s["id"] == section_id), "")
        body = extract_section_text(_doc(state, "brd"), title) if title else ""
        if not body.strip():
            return False, [f"section '{section_id}' missing from the BRD"]
        r = check_section("BA", section_id, body)
        return bool(r["passed"]), list(r["missing"])
    except Exception as e:
        return False, [f"section '{section_id}' unverifiable ({str(e)[:80]})"]


def ba_gate(state: dict[str, Any]) -> dict:
    doc = _doc(state, "brd")
    diags = _diagrams(state)
    flow = diags.get("flow", {})
    mermaid = str(flow.get("mermaid", ""))
    checks = [
        ("brd_exists", len(doc) > 1500, f"BRD length {len(doc)} chars (need >1500)", True),
        ("objective", _has(doc, "objective", "executive summary", "goal"), "no objectives/summary section", True),
        ("scope_in", _has(doc, "scope in", "in scope", "in-scope"), "no Scope IN", True),
        ("scope_out", _has(doc, "scope out", "out of scope", "out-of-scope"), "no Scope OUT", True),
        ("stakeholders", _has(doc, "stakeholder"), "no stakeholders", True),
        ("asis_tobe", _has(doc, "as-is", "asis") and _has(doc, "to-be", "tobe"), "need AS-IS and TO-BE", True),
        ("user_stories", len(extract_ids(doc)["US"]) >= 3, f"US ids found: {extract_ids(doc)['US'][:8]} (need >=3)", True),
        ("acceptance_criteria", _has(doc, "acceptance criteria", "given", "when", "then", "g/w/t"), "no acceptance criteria", True),
        ("business_rules", len(extract_ids(doc)["BR"]) >= 3, f"BR ids found: {extract_ids(doc)['BR'][:8]} (need >=3)", True),
        ("data_needs", _has(doc, "data", "entit", "field", "record"), "no data needs", True),
        ("risks", _has(doc, "risk"), "no risks", True),
        ("open_questions", _has(doc, "open question", "open_question", "assumption"), "open questions not explicitly represented", True),
        ("flow_diagram", bool(flow) and int(flow.get("nodes", 0)) >= 6, f"flow diagram nodes={flow.get('nodes', 0)} (need >=6)", True),
        ("flow_decision", ("{" in mermaid and "}" in mermaid) or _has(mermaid, "diamond", "decide", "valid?", "pass?"), "diagram needs >=1 decision node", True),
        ("assumption_ledger", bool(re.search(r"\[ASSUMPTION[:\s]", doc, re.IGNORECASE)), "assumptions lack [ASSUMPTION:xxx] ledger tags (warn-only)", False),
        ("oq_ledger", bool(re.search(r"\bOQ-\d+\b", doc)), "open questions lack OQ-xxx ids (warn-only)", False),
    ]

    # ---- BA v2 contract (fail-closed): stage 2, stage 7, quality, evidence,
    # decisions, assumptions, priorities, plan, contradictions, outcome metrics.

    ok2, miss2 = _ba_section_ok(state, "business_case")
    checks.append(("business_case", ok2,
                   "no production-grade business case: " + "; ".join(miss2[:3]), True))
    ok7, miss7 = _ba_section_ok(state, "solution_assessment")
    checks.append(("solution_assessment", ok7,
                   "no production-grade solution assessment: " + "; ".join(miss7[:3]), True))
    checks.append(("readiness", bool(re.search(
        r"people", doc, re.IGNORECASE)) and bool(re.search(r"training", doc, re.IGNORECASE)),
        "readiness must cover people, process, technology and training", False))

    try:
        from ba_agent.managers.quality_engine import blocking_missing, open_contradictions, priorities_untagged

        qm = blocking_missing(doc)
        checks.append(("quality_attributes", not qm,
                       "requirement quality: " + "; ".join(qm[:3]), False))
        untagged = priorities_untagged(doc)
        checks.append(("priorities_tagged", not untagged,
                       f"requirements without a priority tag: {', '.join(untagged[:8])}", False))
        open_c = open_contradictions(state)
        checks.append(("contradictions_resolved", not open_c,
                       "unresolved stakeholder contradictions: "
                       + ", ".join(c.get("id", "?") for c in open_c[:5]), False))
    except Exception as e:
        checks.append(("quality_attributes", False, f"quality engine unavailable: {str(e)[:80]}", False))
        checks.append(("priorities_tagged", False, "quality engine unavailable", False))
        checks.append(("contradictions_resolved", False, "quality engine unavailable", False))

    try:
        from ba_agent.managers import evidence as EV

        unc = EV.uncovered(state, doc)
        checks.append(("evidence_coverage", not unc,
                       f"requirements with no evidence link: {', '.join(unc[:8])}", False))
    except Exception as e:
        checks.append(("evidence_coverage", False, f"evidence manager unavailable: {str(e)[:80]}", False))

    try:
        from ba_agent.managers import assumptions as ASM

        g = ASM.gaps(state, doc)
        problems: list[str] = []
        if g["missing"]:
            problems.append(f"unregistered: {', '.join(g['missing'][:3])}")
        if g["invalid_confirm"]:
            problems.append(f"confirmed without evidence: {', '.join(g['invalid_confirm'][:3])}")
        if g["rejected_in_doc"]:
            problems.append(f"rejected but still in BRD: {', '.join(g['rejected_in_doc'][:2])}")
        checks.append(("assumptions_registered", g["ok"],
                       "assumption ledger: " + "; ".join(problems) if problems else "", False))
    except Exception as e:
        checks.append(("assumptions_registered", False, f"assumption manager unavailable: {str(e)[:80]}", False))

    try:
        from ba_agent.managers import decision_manager as DM

        missing_dec = DM.decisions_missing_for_approvals(state)
        checks.append(("decisions_recorded", not missing_dec,
                       f"approved requests with no DEC-n decision: {', '.join(missing_dec[:5])}", False))
    except Exception as e:
        checks.append(("decisions_recorded", False, f"decision manager unavailable: {str(e)[:80]}", False))

    try:
        from ba_agent.managers import ba_plan as BP

        BP.sync(state)          # close items whose stage is already complete
        rep = BP.report(state)
        checks.append(("ba_plan", rep["ok"], "BA plan: " + (rep["corrective"] or "ok"), False))
    except Exception as e:
        checks.append(("ba_plan", False, f"plan manager unavailable: {str(e)[:80]}", False))

    try:
        from ba_agent.managers import outcome as OC

        metrics = OC.report(state).get("metrics") or []
        checks.append(("outcome_metrics", bool(metrics),
                       "no SM-n success metric with baseline, target, method and review point", False))
    except Exception as e:
        checks.append(("outcome_metrics", False, f"outcome manager unavailable: {str(e)[:80]}", False))

    try:
        from ba_agent.managers import impact as IM

        open_cr = IM.open_without_impact(state)
        checks.append(("cr_impact_recorded", not open_cr,
                       f"open change requests with no impact analysis: {', '.join(open_cr[:5])}", False))
    except Exception as e:
        checks.append(("cr_impact_recorded", False, f"impact manager unavailable: {str(e)[:80]}", False))

    return _result("BA", checks)


# ---------------- PROJECT ----------------

def _wbs_rows(plan: str) -> list[list[str]]:
    """WBS task rows only: T-xxx rows inside a table whose header is a WBS header.

    Dependency tables also start rows with T-xxx ids — they are not tasks.
    """
    from .sections import looks_like_wbs_header  # lazy
    rows: list[list[str]] = []
    headers: list[str] = []
    for line in plan.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells or all(re.fullmatch(r"[-:\s]*", c or "") for c in cells):
            continue  # separator
        if len(cells) >= 2 and re.fullmatch(r"T-\d+", cells[0]):
            if looks_like_wbs_header(headers):
                rows.append(cells)
        else:
            headers = cells
    return rows


def project_gate(state: dict[str, Any]) -> dict:
    plan = _doc(state, "project_plan")
    diags = _diagrams(state)
    gantt = diags.get("gantt", {})
    rows = _wbs_rows(plan)
    from .sections import TRACE_ID_RX, detail_missing  # lazy: shared detail checks
    traced = [r for r in rows
              if re.search(TRACE_ID_RX, " | ".join(r), re.IGNORECASE)]
    wbs_det = [f"wbs: {m}" for m in detail_missing("PROJECT", "wbs", plan)]
    sched_det = [f"schedule: {m}" for m in detail_missing("PROJECT", "schedule", plan)]
    raid_det = [f"raci_raid: {m}" for m in detail_missing("PROJECT", "raci_raid", plan)]
    checks = [
        ("plan_exists", len(plan) > 1500, f"plan length {len(plan)} (need >1500)", True),
        ("wbs", len(rows) >= 5, f"WBS rows {len(rows)} (need >=5 with T-xxx ids)", True),
        ("task_trace", bool(rows) and len(traced) * 2 >= len(rows), f"{len(traced)}/{len(rows)} rows trace to US/BR/TECH/PMO (need >=half)", True),
        ("wbs_detail", not wbs_det, "WBS production detail — " + "; ".join(wbs_det[:4]), False),
        ("schedule_detail", not sched_det, "schedule production detail — " + "; ".join(sched_det[:3]), False),
        ("raid_detail", not raid_det, "RAID production detail — " + "; ".join(raid_det[:3]), False),
        ("dependencies", _has(plan, "dependenc"), "no dependencies", True),
        ("milestones", _has(plan, "milestone"), "no milestones", True),
        ("raci", _has(plan, "raci"), "no RACI", True),
        ("raid", _has(plan, "raid"), "no RAID log", True),
        ("sprints", _has(plan, "sprint", "phase", "phasing"), "no sprint phasing", True),
        ("critical_path", _has(plan, "critical path"), "no critical path", True),
        ("linked_to_brd", _has(plan, "brd") or bool(re.search(r"\b(BR|US)-\d+", plan)), "plan not linked to BRD (no BRD ref or BR-/US- ids)", True),
        ("gantt_diagram", bool(gantt) and int(gantt.get("nodes", 0)) >= 1, "no gantt diagram bundle recorded in state", False),
    ]
    return _result("PROJECT", checks)


# ---------------- FUNCTIONAL ----------------

def functional_gate(state: dict[str, Any]) -> dict:
    spec = _doc(state, "functional_spec")
    ids = extract_ids(spec)
    cov = coverage_report(state)
    diags = _diagrams(state)
    func = diags.get("functional", {}) or diags.get("flow", {})
    defer = _has(spec, "defer")
    from .sections import detail_missing  # lazy: shared production-grade detail checks
    uc_det = [f"use_cases: {m}" for m in detail_missing("FUNCTIONAL", "use_cases", spec)]
    val_det = [f"validations_data: {m}" for m in detail_missing("FUNCTIONAL", "validations_data", spec)]
    checks = [
        ("spec_exists", len(spec) > 1500, f"spec length {len(spec)} (need >1500)", True),
        ("fr_ids", len(ids["FR"]) >= 5, f"FR ids: {ids['FR'][:10]} (need >=5)", True),
        ("fr_priority", _has(spec, "must", "should", "could", "moscow", "priority"), "FRs lack priority", True),
        ("fr_trace", bool(re.search(r"FR-\d+.*(US-|BR-)|(US-|BR-).*FR-\d+", spec, re.IGNORECASE | re.DOTALL)), "FRs lack source trace to US/BR", True),
        ("use_cases", len(ids["UC"]) >= 1, f"UC ids: {ids['UC'][:6]} (need >=1)", True),
        ("use_cases_detail", not uc_det, "; ".join(uc_det[:3]), False),
        ("val_validations_detail", not val_det, "; ".join(val_det[:3]), False),
        ("alternate_flows", _has(spec, "alternate", "alternative flow", "exception flow", "2a", "3a"), "no alternate flows", True),
        ("validations", _has(spec, "validation", "error", "e-v", "e-sb"), "no validations/errors", True),
        ("data_model", _has(spec, "entit", "table", "attribute", "relationship", "state"), "no data model", True),
        ("trace_matrix", _has(spec, "traceab", "trace matrix", "matrix"), "no traceability matrix", True),
        ("br_coverage", not cov["uncovered_BR_no_FR"] or defer, f"uncovered BRs: {cov['uncovered_BR_no_FR'][:8]} (or add defer note)", True),
        ("func_diagram", bool(func) and int(func.get("nodes", 0)) >= 7, f"functional diagram nodes={func.get('nodes', 0)} (need >=7)", True),
    ]
    return _result("FUNCTIONAL", checks)


# ---------------- TECHNICAL ----------------

def technical_gate(state: dict[str, Any]) -> dict:
    design = _doc(state, "tech_design")
    ids = extract_ids(design)
    cov = coverage_report(state)
    diags = _diagrams(state)
    seq, arch = diags.get("sequence", {}), diags.get("architecture", {})
    api_hits = 0
    try:
        from .sections import endpoint_count  # lazy: shared detector (both forms)
        api_hits = endpoint_count(design)
    except Exception:
        api_hits = len(re.findall(r"\b(GET|POST|PUT|PATCH|DELETE)\b\s+/\S*", design))
    from .sections import detail_missing  # lazy: shared production-grade detail checks
    api_det = [f"apis: {m}" for m in detail_missing("TECHNICAL", "apis", design)]
    data_det = [f"data: {m}" for m in detail_missing("TECHNICAL", "data", design)]
    checks = [
        ("design_exists", len(design) > 1500, f"design length {len(design)} (need >1500)", True),
        ("architecture", _has(design, "architect", "c4", "component", "service"), "no architecture", True),
        ("decisions", _has(design, "decision", "adr", "trade-off", "tradeoff", "chosen"), "no architecture decisions", True),
        ("apis", api_hits >= 3, f"API endpoints {api_hits} (need >=3 METHOD /path)", True),
        ("apis_detail", not api_det, "; ".join(api_det[:3]), False),
        ("data_schema", _has(design, "primary key", "schema", "table", "column", "varchar", "index"), "no data schema", True),
        ("data_detail", not data_det, "; ".join(data_det[:3]), False),
        ("security", _has(design, "secur", "auth", "rbac", "encrypt", "kms", "waf"), "security not addressed", True),
        ("nfrs", _has(design, "nfr", "performance", "p95", "latency", "sla", "observab"), "NFRs not addressed", True),
        ("deployment", _has(design, "deploy", "environment", "rollback", "ci/cd", "pipeline"), "deployment not addressed", True),
        ("sequence_diagram", bool(seq), "no sequence diagram bundle recorded in state", True),
        ("arch_diagram", bool(arch), "no architecture diagram bundle recorded in state", True),
        ("tech_tasks", len(ids["TECH"]) >= 1, f"TECH ids: {ids['TECH'][:8]} (need >=1)", True),
        ("fr_coverage", not cov["uncovered_FR_no_TECH"] or _has(design, "defer"), f"uncovered FRs: {cov['uncovered_FR_no_TECH'][:8]}", True),
    ]
    return _result("TECHNICAL", checks)


# ---------------- FRAPPE (production-grade implementation) ----------------

def _frappe_state(state: dict[str, Any]) -> dict:
    try:
        from .project_context import state_dict
        pc = state_dict(state).get("project_context", {})
        fs = pc.get("frappe_state", {}) if isinstance(pc, dict) else {}
        return fs if isinstance(fs, dict) else {}
    except Exception:
        return {}


def _frappe_evidence(fs: dict) -> list[dict]:
    ev = fs.get("evidence")
    return [e for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []


def _latest_by_object(ev: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for e in ev:
        latest[f"{e.get('kind', '')}:{e.get('name', '')}"] = e
    return latest


_IMPL_KINDS = ("doctype", "workflow", "role", "permissions")
_METH_RX = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s")


def frappe_gate(state: dict[str, Any]) -> dict:
    """Production-grade Frappe completion: implemented AND verified on the real site.

    Nothing here trusts a claim — every criterion reads recorded evidence
    (read-backs and smoke tests) that the tools wrote after real API calls.
    """
    fs = _frappe_state(state)
    ev = _frappe_evidence(fs)
    latest = _latest_by_object(ev)
    docs = doc_texts(state)
    spec = docs.get("functional_spec", "") or ""
    design = docs.get("tech_design", "") or ""
    plan = docs.get("project_plan", "") or ""
    defer = "defer" in (spec + design).lower()

    project = str(state.get("frappe_project", "") or "")
    proj_entry = next((e for k, e in latest.items() if str(e.get("kind")) == "project"), {})
    verified_names = {str(e.get("name")) for e in ev if e.get("ok") and e.get("verified")}
    from urllib.parse import unquote

    def _resolved(e: dict) -> bool:
        """A failure is open unless a later entry fixed the same object."""
        if e.get("ok"):
            return True
        if str(e.get("kind")) == "api":
            nm = unquote(str(e.get("name") or ""))
            # (a) an exploratory write to a specific object is resolved once
            #     that object is verified by a deterministic tool
            if any(vn and vn in nm for vn in verified_names):
                return True
            # (b) a failed write to a COLLECTION endpoint is resolved when the
            #     agent subsequently created objects successfully afterwards
            if re.search(r"/api/resource/[^/]+$", nm) and _METH_RX.search(nm):
                ts = str(e.get("ts") or "")
                return any(str(v.get("ts") or "") > ts for v in ev
                           if v.get("ok") and v.get("verified"))
        if str(e.get("kind")) == "permissions":
            # a failed permission write is resolved when the DocType itself is
            # (re)created/verified with its permissions afterwards
            dt = str(e.get("name") or "").split(":")[0]
            ts = str(e.get("ts") or "")
            return any(str(v.get("kind")) == "doctype" and str(v.get("name")) == dt
                       and v.get("ok") and v.get("verified")
                       and str(v.get("ts") or "") > ts for v in ev)
        return False

    open_failures = [k for k, e in latest.items() if not _resolved(e)]
    verified_objs = sorted({k for k, e in latest.items()
                            if e.get("ok") and e.get("verified")
                            and str(e.get("kind")) in _IMPL_KINDS})
    dt_entries = [e for k, e in latest.items() if str(e.get("kind")) == "doctype"]
    dt_verified = [e for e in dt_entries if e.get("ok") and e.get("verified")]
    # child tables cannot be smoke-tested standalone: their parent covers them
    dt_needing_smoke = [e for e in dt_verified if not e.get("istable")]
    smoke_ok = {str(e.get("name")) for k, e in latest.items()
                if str(e.get("kind")) == "smoke_test" and e.get("ok")}
    wf_verified = [e for k, e in latest.items()
                   if str(e.get("kind")) == "workflow" and e.get("ok") and e.get("verified")]
    traced = set()
    for e in ev:
        if e.get("ok"):
            for t in (e.get("traces") or []):
                traced.add(str(t))
    frs = set(extract_ids(spec).get("FR", []))
    uncovered_fr = sorted(frs - traced)
    t_ids = set(extract_ids(plan).get("T", []))
    tasks_created = int(proj_entry.get("tasks_created") or 0)
    need_tasks = max(1, len(t_ids) // 2) if t_ids else 0
    # the WBS timeline must actually reach Frappe (dates + dependencies), not
    # just exist in the markdown
    try:
        from .sections import parse_wbs_rows
        wbs_rows = parse_wbs_rows(plan)
    except Exception:
        wbs_rows = []
    wbs_dated = [r for r in wbs_rows if r.get("start") and r.get("end")]
    need_dated = len(wbs_dated)
    got_dated = int(proj_entry.get("tasks_dated") or 0)
    wbs_deps = [r for r in wbs_rows if re.search(r"T-\d+", r.get("deps", "") or "")]
    got_deps = int(proj_entry.get("deps_linked") or 0)
    need_deps = len(wbs_deps)

    checks = [
        ("frappe_project", bool(project) and bool(proj_entry.get("verified")),
         f"no verified Frappe project (state={project!r}, "
         f"evidence={'verified' if proj_entry.get('verified') else 'missing'})", True),
        # advisory: environment facts are nice to have and never worth re-running a
        # whole stage (which is how FRAPPE once spent 30 minutes on 21 attempts)
        ("env_discovered", bool(fs.get("discovered")),
         "Frappe environment not discovered (run discover_frappe_env)", False),
        ("objects_verified", len(verified_objs) >= 3,
         f"verified implementation objects {len(verified_objs)} (need >=3): {verified_objs[:6]}", True),
        ("doctypes_verified", bool(dt_verified) and all(str(e.get("name")) in smoke_ok
                                                        for e in dt_needing_smoke),
         f"doctype(s) without smoke-test evidence: "
         f"{[str(e.get('name')) for e in dt_needing_smoke if str(e.get('name')) not in smoke_ok][:5] or 'none implemented'}",
         True),
        ("workflows_verified", True if not re.search(r"approv", (spec + design), re.IGNORECASE)
         or defer else bool(wf_verified),
         "design requires approvals but no verified workflow exists", False),
        ("fr_traced", (not uncovered_fr) or defer,
         f"FRs with no implemented object: {uncovered_fr[:8]}", False),
        ("no_open_failures", not open_failures,
         f"unresolved failures: {open_failures[:6]}", False),
        ("tasks_created", tasks_created >= need_tasks,
         f"Frappe tasks created {tasks_created} (need >={need_tasks})", True),
        ("timeline_mapped", got_dated >= need_dated,
         f"Frappe tasks with start/end dates {got_dated} (WBS has {need_dated} dated tasks) — "
         "create the tasks from the WBS so the timeline lands in Frappe", False),
        ("dependencies_mapped", got_deps >= need_deps,
         f"Frappe tasks with linked dependencies {got_deps} (WBS has {need_deps} dependent tasks)", False),
    ]
    return _result("FRAPPE", checks)


# ---------------- FINAL VALIDATION ----------------

def final_validation(state: dict[str, Any]) -> dict:
    """All stage gates green + production-grade Frappe implementation + coverage clean."""
    subs = {
        "BA": ba_gate(state),
        "PROJECT": project_gate(state),
        "FUNCTIONAL": functional_gate(state),
        "TECHNICAL": technical_gate(state),
        "FRAPPE": frappe_gate(state),
    }
    cov = coverage_report(state)
    docs = doc_texts(state)
    from .sections import stub_markers  # lazy: content-quality guardrail
    stub_hits = {k: stub_markers(docs.get(k, ""))
                 for k in ("brd", "project_plan", "functional_spec", "tech_design")}
    stub_hits = {k: v for k, v in stub_hits.items() if v}
    checks = [
        ("all_gates", all(g["passed"] for g in subs.values()),
         f"failing gates: {[k for k, g in subs.items() if not g['passed']]}", True),
        ("frappe_stage", subs["FRAPPE"]["passed"],
         f"frappe gate: {subs['FRAPPE']['missing'][:4]}", True),
        ("no_stubs", not stub_hits,
         f"placeholder markers in documents: {stub_hits}", True),
        ("br_fully_covered", not cov["uncovered_BR_no_FR"],
         f"unvalidated BRs: {cov['uncovered_BR_no_FR'][:8]}", True),
        ("us_covered", not cov["uncovered_US"],
         f"unvalidated USs: {cov['uncovered_US'][:8]}", True),
        ("fr_covered", not cov["uncovered_FR_no_TECH"],
         f"unvalidated FRs: {cov['uncovered_FR_no_TECH'][:8]}", True),
    ]
    res = _result("VALIDATION", checks)
    res["sub_gates"] = {k: {"passed": g["passed"], "missing": g["missing"]} for k, g in subs.items()}
    res["coverage"] = cov["counts"]
    return res


GATES = {
    "BA": ba_gate,
    "PROJECT": project_gate,
    "FUNCTIONAL": functional_gate,
    "TECHNICAL": technical_gate,
    "FRAPPE": frappe_gate,
    "VALIDATION": final_validation,
}


def run_gate(stage: str, state: dict[str, Any]) -> dict:
    fn = GATES.get(stage.upper())
    if not fn:
        raise ValueError(f"Unknown gate stage: {stage}")
    return fn(state)
