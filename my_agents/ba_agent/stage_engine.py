"""BA stage engine — S1..S7 as EXECUTING stages (prod).

This module drives BA execution; stage_tracker.py observes it (delegation is
one-way: tracker -> engine, single-sourcing all evidence).

Per pass the engine gives the agent exactly one stage assignment:
  STAGE TASK S<n> — <title>: <task>. Done when: <criteria>. Blockers: <...>.
Completion criteria are computed from pipeline truth (accepted sections +
validators), so blockers name the exact missing items. The formal gate stays
authoritative; stage blockers mirror its substance early (S5 self-check is
strictly stronger than the gate on AC depth).

Automation (no model voluntarism required):
- auto_batch(): entering S1/S2/S3 with gaps auto-builds one templated QB
  batch (<=5, deduped, once per stage per change-request).
- on_ba_gate_passed(): assembles the BA Package and persists it to the bound
  workspace + artifact snapshot + history (deterministic filename).

State keys: ba_stage_done/current/log (tracker-owned) + ba_stage_batched
(list of "<stage>:<cr>" markers). All persisted — see shared/workspace.
Every public function is total: never raises.
"""

from __future__ import annotations

from typing import Any

STAGES = ("S1", "S2", "S3", "S4", "S5", "S6", "S7")

TITLES = {
    "S1": "Understand Business",
    "S2": "Analyze Business",
    "S3": "Elicit Information",
    "S4": "Analyze Requirements",
    "S5": "Verify & Validate",
    "S6": "Manage Requirements",
    "S7": "Assess Solution",
}

FOCUS = {
    "S1": "establish problem, goals, initial scope (Objectives section)",
    "S2": "complete Scope IN/OUT, AS-IS vs TO-BE and the business case "
          "(capability gap + >=2 OPT-n options + risk of doing nothing)",
    "S3": "record stakeholders/actors; log elicitation Q&A, never re-ask",
    "S4": "write >=3 US-xxx (>=2 Given/When/Then each, MoSCoW-tagged) + >=3 atomic BR-xxx",
    "S5": "self-check the 8 quality attributes: complete, correct, consistent, cohesive, "
          "feasible, unambiguous, modifiable, testable",
    "S6": "keep IDs stable; link evidence per US/BR; record DEC-n decisions; register every "
          "ASSUMPTION with a status; tag every US/BR with a priority",
    "S7": "finish the solution assessment (coverage + 4 readiness dimensions + gaps + SM-n "
          "metrics) and meet the formal gate so the package is handoff-ready",
}

# Accepted BA sections evidencing S1..S4/S7 (S5..S7 are computed validators).
SECTION_GROUPS: dict[str, set[str]] = {
    "S1": {"objectives"},
    "S2": {"scope", "asis_tobe", "business_case"},
    "S3": {"stakeholders"},
    "S4": {"stories", "rules"},
    "S7": {"solution_assessment"},
}

# Stage -> prompt-fragment contract (verified by test: every BA_INSTRUCTION
# fragment is owned by >=1 stage; the agent's full instruction is unchanged —
# scoping happens per-pass via the STAGE TASK assignment below).
STAGE_FRAGMENTS: dict[str, tuple[str, ...]] = {
    "S1": ("FRAG_IDENTITY", "FRAG_PIPELINE", "FRAG_CONTEXT", "FRAG_DISCOVERY", "FRAG_PLAN",
           "FRAG_CONVERSATION"),
    "S2": ("FRAG_ANALYSIS", "FRAG_CONTEXT", "FRAG_BRD", "FRAG_BUSINESS_CASE"),
    "S3": ("FRAG_DISCOVERY", "FRAG_CONVERSATION"),
    # S4 owns the diagram spec: the flow diagram expresses these requirements.
    "S4": ("FRAG_REQUIREMENTS", "FRAG_RULES", "FRAG_ANALYSIS", "FRAG_DIAGRAM"),
    "S5": ("FRAG_QUALITY", "FRAG_BRD", "FRAG_QUALITY_ATTRS"),
    "S6": ("FRAG_TRACE", "FRAG_CHANGE", "FRAG_KNOWLEDGE", "FRAG_EVIDENCE", "FRAG_PLAN"),
    # S7 owns tool discipline + response format: save, summarize, hand off.
    "S7": ("FRAG_HANDOFF", "FRAG_TRACE", "FRAG_SAVE", "FRAG_TOOLS", "FRAG_RESPONSE",
           "FRAG_ASSESSMENT", "FRAG_OUTCOME", "FRAG_EVIDENCE", "FRAG_GENERATION_TRIGGER"),
}

AUTO_BATCH_STAGES = STAGES

# Templated elicitation questions per auto-batch stage ({project} slot).
# Templates instantiate the stage topics; the human's answers steer follow-ups.
QUESTION_TEMPLATES: dict[str, tuple[str, ...]] = {
    "S1": (
        "What business problem triggers the {project} effort?",
        "What goals must {project} achieve, and how is success measured?",
        "What is explicitly in scope for the first release of {project}?",
        "Who are the stakeholders and decision makers for {project}?",
    ),
    "S2": (
        "Walk through the current (AS-IS) process for {project} step by step.",
        "Where does the current process hurt most, and why does that pain occur?",
        "What business volumes (transactions/users per day/month) must {project} handle?",
        "Which systems are involved today, and what constraints bind {project}?",
    ),
    "S3": (
        "Describe the happy-path flow end to end for {project}.",
        "What alternate paths occur besides the happy path?",
        "What exceptions and rejections happen, and who handles each?",
        "Who owns each step, and where are approvals or decisions required?",
    ),
    "S4": (
        "For each user story, what is the acceptance detail that proves it is done?",
        "How should {project} requirements be prioritised (MoSCoW) and why?",
        "Which domain terms must the glossary define for {project}?",
        "What constraint or dependency would change a requirement's shape?",
    ),
    "S5": (
        "Which requirements are still ambiguous or not measurable for {project}?",
        "What value should replace each vague term (for example 'quickly')?",
        "Which statements conflict with each other?",
    ),
    "S6": (
        "Which requirement cannot be traced to a business need for {project}?",
        "What change is being requested, and which requirements does it affect?",
        "Which priority should change, and on what business basis?",
    ),
    "S7": (
        "Which requirement is not yet covered by the proposed solution for {project}?",
        "Is the organisation ready (people, process, technology, training)?",
        "What must transition or be migrated before go-live?",
        "Which success metric has no baseline or target yet?",
    ),
}

BATCHED_KEY = "ba_stage_batched"


def _done_sections(state: dict[str, Any]) -> set[str]:
    try:
        from shared.project_context import get_context

        done = get_context(state).get("sections_done_BA", [])
        return set(done) if isinstance(done, list) else set()
    except Exception:
        return set()


def _brd(state: dict[str, Any]) -> str:
    try:
        from shared.traceability import doc_texts

        return doc_texts(state).get("brd", "") or ""
    except Exception:
        v = state.get("brd", "")
        return v if isinstance(v, str) else ""


def evidence(state: dict[str, Any]) -> dict[str, bool]:
    """Per-stage completion from pipeline truth (never raises)."""
    brd = _brd(state)
    done = _done_sections(state)
    ok: dict[str, bool] = {
        sid: SECTION_GROUPS[sid] <= done for sid in ("S1", "S2", "S3", "S4")
    }
    # S5 — requirement quality (the 8 attributes; blocking findings only)
    try:
        from ba_agent.managers.quality_engine import report as _quality

        ok["S5"] = bool(ok["S4"]) and bool(_quality(brd)["ok"])
    except Exception:
        ok["S5"] = False
    # S6 — controlled requirement set: ids, evidence per US/BR, decisions for
    # approvals, registered assumptions, explicit priorities.
    try:
        from shared.traceability import extract_ids

        from ba_agent.managers import assumptions as ASM
        from ba_agent.managers import decision_manager as DM
        from ba_agent.managers import evidence as EV
        from ba_agent.managers.quality_engine import priorities_untagged

        ids = extract_ids(brd)
        ok["S6"] = (bool(ok["S4"]) and bool(ids.get("US")) and bool(ids.get("BR"))
                    and not EV.uncovered(state, brd)
                    and not ASM.gaps(state, brd)["missing"]
                    and not ASM.gaps(state, brd)["invalid_confirm"]
                    and not DM.decisions_missing_for_approvals(state)
                    and not priorities_untagged(brd))
    except Exception:
        ok["S6"] = False
    # S7 — solution assessment section + success metrics + the formal gate
    try:
        from ba_agent.managers import outcome as OC
        from shared.gates import ba_gate

        metrics = OC.report(state).get("metrics") or []
        ok["S7"] = (SECTION_GROUPS["S7"] <= done and bool(metrics)
                    and bool(ba_gate(state)["passed"]))
    except Exception:
        ok["S7"] = False
    return ok


def current(state: dict[str, Any]) -> str:
    """First incomplete stage (S7 when all complete). Never raises."""
    try:
        ok = evidence(state)
        return next((s for s in STAGES if not ok.get(s)), "S7")
    except Exception:
        return "S1"


def blockers(state: dict[str, Any], stage: str = "") -> list[str]:
    """Exact missing items for a stage (default: current). Never raises."""
    try:
        st = stage or current(state)
        if evidence(state).get(st):
            return []
        brd = _brd(state)
        done = _done_sections(state)
        if st in SECTION_GROUPS and st != "S7":
            missing = sorted(SECTION_GROUPS[st] - done)
            return [f"section '{s}' not accepted" for s in missing]
        if st == "S5":
            from ba_agent.managers.quality_engine import blocking_missing

            return (blocking_missing(brd)[:5]
                    or ["self-check failing: requirement quality attributes"])
        if st == "S6":
            from shared.traceability import extract_ids

            from ba_agent.managers import assumptions as ASM
            from ba_agent.managers import decision_manager as DM
            from ba_agent.managers import evidence as EV
            from ba_agent.managers.quality_engine import priorities_untagged

            ids = extract_ids(brd)
            out = []
            if not ids.get("US"):
                out.append("no US-xxx in BRD")
            if not ids.get("BR"):
                out.append("no BR-xxx in BRD")
            unc = EV.uncovered(state, brd)
            if unc:
                out.append(f"requirements with no evidence link: {', '.join(unc[:6])}")
            g = ASM.gaps(state, brd)
            if g["missing"]:
                out.append(f"assumptions not registered: {', '.join(g['missing'][:6])}")
            if g["invalid_confirm"]:
                out.append(f"confirmed assumptions lacking evidence/decider: "
                           f"{', '.join(g['invalid_confirm'][:4])}")
            dm = DM.decisions_missing_for_approvals(state)
            if dm:
                out.append(f"approved requests with no DEC-n decision: {', '.join(dm[:4])}")
            untagged = priorities_untagged(brd)
            if untagged:
                out.append(f"requirements without a priority tag: {', '.join(untagged[:6])}")
            return out or ["traceability/evidence/decision gaps"]
        if st == "S7":
            out = [f"section '{s}' not accepted"
                   for s in sorted(SECTION_GROUPS["S7"] - done)]
            from shared.gates import ba_gate

            out += list(ba_gate(state).get("missing", []))[:5]
            try:
                from ba_agent.managers import outcome as OC

                if not OC.report(state).get("metrics"):
                    out.append("no SM-n success metric with baseline and target")
            except Exception:
                pass
            return out or ["gate failing"]
        return []
    except Exception:
        return ["blocker computation unavailable"]


def stage_assignment(state: dict[str, Any]) -> str:
    """One-pass STAGE TASK directive (never raises)."""
    try:
        st = current(state)
        bl = blockers(state, st)
        bl_txt = "; ".join(bl) if bl else "none — hold this stage's bar"
        return (f"STAGE TASK {st} — {TITLES[st]}: {FOCUS[st]}. "
                f"Done when: {FOCUS[st]}. Blockers: {bl_txt}. "
                f"Complete THIS stage before advancing; do not skip ahead.")
    except Exception:
        return "STAGE TASK unavailable; proceed with the assigned section."


def _active_cr(state: dict[str, Any]) -> str:
    try:
        from shared.project_context import get_execution

        return str(get_execution(state).get("active_change_request") or "")
    except Exception:
        return ""


def auto_batch(state: dict[str, Any]) -> dict:
    """Auto-build one templated QB batch on entering S1/S2/S3 with gaps.

    Once per stage per change-request; skipped when a batch is already open
    or the stage evidence is complete. Returns {batched, batch|reason}.
    Never raises.
    """
    try:
        from shared.project_context import commit, get_context

        st = current(state)
        if st not in AUTO_BATCH_STAGES:
            return {"batched": False, "reason": f"{st} needs no auto-batch"}
        if evidence(state).get(st):
            return {"batched": False, "reason": f"{st} complete — nothing to elicit"}
        ctx = get_context(state)
        markers = ctx.get(BATCHED_KEY)
        if not isinstance(markers, list):
            markers = []
            ctx[BATCHED_KEY] = markers
        marker = f"{st}:{_active_cr(state)}"
        if marker in markers:
            return {"batched": False, "reason": f"{st} already batched ({marker})"}
        from ba_agent.managers.question_manager import build_batch, open_questions

        if open_questions(state):
            return {"batched": False, "reason": "a batch is already open"}
        try:
            pname = (ctx.get("project", {}) or {}).get("name", "the project")
        except Exception:
            pname = "the project"
        cr = _active_cr(state)
        cands = [t.format(project=pname) for t in QUESTION_TEMPLATES[st]]
        if cr:
            # Change-scoped re-elicitation: identical text would (correctly)
            # match the answered log, so scope follow-ups to the change.
            cands = [f"[{cr}] {c}" for c in cands]
        r = build_batch(state, st, cands)
        if not r.get("ok"):
            return {"batched": False, "reason": r.get("error", "no questions")}
        markers.append(marker)
        del markers[:-20]
        commit(state)
        return {"batched": True, "batch": r["batch"]}
    except Exception as e:
        return {"batched": False, "reason": str(e)[:200]}


def brief_extras(state: dict[str, Any]) -> str:
    """Combined BA brief additions: stage progress + STAGE TASK + pending +
    auto-batch note + monitoring/quality signals. Single entry point for the
    orchestrator hook."""
    try:
        from ba_agent.stage_tracker import brief_fragment
        from ba_agent.managers.question_manager import pending_fragment

        # Plan (§28) and quality/contradiction findings are refreshed from truth
        # every pass — the agent never has to remember to seed them.
        try:
            from ba_agent.managers import ba_plan as BP
            from ba_agent.managers import quality_engine as QE

            BP.seed(state)
            BP.sync(state)
            QE.record_findings(state, _brd(state))
        except Exception:
            pass

        parts = [brief_fragment(state), stage_assignment(state)]
        pend = pending_fragment(state)
        if pend:
            parts.append(pend)
        try:
            from ba_agent.managers import ba_plan as BP

            var = BP.variance(state)
            if var:
                parts.append("Plan variance (close or waive):\n" + "\n".join(
                    f"- {v['id']} {v['title']} — {v['reason']}" for v in var[:5]))
        except Exception:
            pass
        try:
            from ba_agent.managers import quality_engine as QE

            open_c = QE.open_contradictions(state)
            if open_c:
                parts.append("Unresolved stakeholder contradictions (resolve before the gate):\n"
                             + "\n".join(f"- {c['id']} [{c['topic']}] {c['detail']} "
                                         f"(sources: {', '.join(c['sources'])})"
                                         for c in open_c[:5]))
        except Exception:
            pass
        auto = auto_batch(state)
        if auto.get("batched"):
            batch = auto["batch"]
            parts.append(f"Auto-batched {batch['id']} ({len(batch['questions'])} stakeholder "
                         f"questions for {batch['stage']}) — ask them now, at most 5 per turn.")
        return "\n".join(p for p in parts if p)
    except Exception:
        return ""


def on_ba_gate_passed(state: dict[str, Any]) -> dict:
    """Assemble + persist the BA Package after a BA gate pass (never raises)."""
    try:
        from shared.project_context import append_history, snapshot_artifact
        from shared.workspace import bound_workspace_id, ProjectWorkspace

        pid = ""
        try:
            pid = bound_workspace_id(state)
        except Exception:
            pid = ""
        if not pid:
            return {"saved": False, "error": "no bound workspace"}
        ws = ProjectWorkspace(pid, create=False)
        if not ws.exists():
            return {"saved": False, "error": f"workspace {pid} missing"}
        from ba_agent.managers.package_assembler import assemble

        pkg = assemble(state, pid)
        path = ws.save_artifact("BA_PACKAGE_assembled.md", pkg["markdown"])
        snapshot_artifact(state, "BA", "ba_package", str(path),
                          f"{pkg['items']} items, {len(pkg['missing'])} missing")
        append_history(state, "artifact_created", "ba_agent",
                       f"BA package ({pkg['items']} items, "
                       f"{len(pkg['missing'])} missing)", "ba_package")
        return {"saved": True, "path": str(path), "missing": pkg["missing"]}
    except Exception as e:
        return {"saved": False, "error": str(e)[:200]}
