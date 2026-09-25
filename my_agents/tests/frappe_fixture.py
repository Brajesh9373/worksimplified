"""Offline Frappe evidence fixture for flow tests.

Mirrors the exact shape the production tools write into
PROJECT_CONTEXT.frappe_state["evidence"], so flow tests exercise the real
frappe_gate criteria without touching a network.
"""

from __future__ import annotations

import re

from shared.traceability import extract_ids


def frappe_seed(*docs: str, plan: str = "") -> dict:
    """Minimal verified-implementation bundle that satisfies frappe_gate.

    Timeline/dependency counts are derived from the WBS plan so the fixture
    always matches what a correct run would have mapped into Frappe.
    """
    spec = "\n".join(d or "" for d in docs)
    traces = extract_ids(spec).get("FR", []) or ["FR-001"]
    try:
        from shared.sections import parse_wbs_rows
        rows = parse_wbs_rows(plan) if plan else []
    except Exception:
        rows = []
    dated = sum(1 for r in rows if r.get("start") and r.get("end"))
    deps = sum(1 for r in rows if re.search(r"T-\d+", r.get("deps", "") or ""))
    return {
        "frappe_project": "PROJ-TEST",
        "frappe_state": {
            "discovered": {
                "version": {"message": "15.0.0"},
                "logged_user": {"message": "Administrator"},
                "custom_doctypes": [],
                "workflows": [],
                "roles": [],
            },
            "evidence": [
                {"kind": "project", "name": "PROJ-TEST", "ok": True, "verified": True,
                 "tasks_created": max(60, len(rows)), "tasks_failed": 0,
                 "tasks_dated": dated, "deps_linked": deps, "traces": []},
                {"kind": "doctype", "name": "Travel Request", "ok": True, "verified": True,
                 "action": "created", "fields_added": ["destination", "amount"],
                 "table_ready": True, "traces": traces},
                {"kind": "doctype", "name": "Travel Expense Claim", "ok": True, "verified": True,
                 "action": "created", "fields_added": ["receipt", "amount"],
                 "table_ready": True, "traces": traces},
                {"kind": "workflow", "name": "Travel Approval", "ok": True, "verified": True,
                 "doctype": "Travel Request", "states": 3, "transitions": 2, "traces": traces},
                {"kind": "permissions", "name": "Travel Request:Approver", "ok": True,
                 "verified": True, "flags": {"read": 1, "write": 1}, "traces": traces},
                {"kind": "smoke_test", "name": "Travel Request", "ok": True, "created": "TR-0001",
                 "read_back": "TR-0001", "final_state": "Approved", "cleaned_up": True,
                 "traces": traces},
                {"kind": "smoke_test", "name": "Travel Expense Claim", "ok": True,
                 "created": "TEC-0001", "read_back": "TEC-0001", "cleaned_up": True,
                 "traces": traces},
                {"kind": "discovery", "name": "", "ok": True, "traces": []},
            ],
        },
    }


def seed_ba_v2(state: dict, brd: str, project: str = "fixture") -> dict:
    """Seed the BA v2 records a completed BA stage would hold (offline).

    The gate is fail-closed on the v2 contract (evidence per requirement,
    assumption statuses, DEC-n for approvals, plan, success metrics), so tests
    that drive straight to a gate must represent a BA that did its job rather
    than an empty ledger.
    """
    from ba_agent.managers import assumptions as ASM
    from ba_agent.managers import ba_plan as BP
    from ba_agent.managers import evidence as EV
    from ba_agent.managers import outcome as OC
    from shared.project_context import commit, get_context
    from shared.traceability import extract_ids

    ctx = get_context(state)
    if not isinstance(ctx.get("elicitation"), dict):
        ctx["elicitation"] = {"log": []}
    log = ctx["elicitation"].setdefault("log", [])
    if not log:
        log.append({"id": "EL-001", "q": f"What business problem triggers {project}?",
                    "a": "Policy breaches and slow reimbursement on travel and expense.",
                    "source": "user", "qid": "QB-001-Q1"})
    for fam in ("US", "BR"):
        for rid in extract_ids(brd).get(fam, []):
            EV.link(state, rid, "elicitation", "EL-001", "fixture requirement source",
                    brd=brd)
    for label in {m.strip().lower().replace(" ", "-")
                  for m in re.findall(r"\[ASSUMPTION:([^\]]+)\]", brd or "", re.IGNORECASE)}:
        ASM.record(state, label, f"{label} stated in the BRD")
    OC.record_metric(state, "Reimbursement cycle time", "10 business days",
                     "2 business days", "claim audit trail", "30 days after go-live")
    BP.seed(state)
    for item in BP.items(state):
        BP.update(state, item["id"], status="done")
    commit(state)
    return state


def seed_frappe(state: dict, *docs: str, plan: str = "", brd: str = "") -> dict:
    """Merge the evidence bundle into a state dict's project_context.

    Pass `brd` to also seed the BA v2 records (evidence links, assumptions,
    success metric, plan) so the fail-closed BA gate sees a completed BA stage.
    """
    from shared.project_context import commit, get_context
    bundle = frappe_seed(*docs, plan=plan)
    ctx = get_context(state)
    ctx["frappe_state"] = bundle["frappe_state"]
    try:
        state["frappe_project"] = bundle["frappe_project"]
    except Exception:
        pass
    commit(state)
    if brd:
        seed_ba_v2(state, brd)
    return state
