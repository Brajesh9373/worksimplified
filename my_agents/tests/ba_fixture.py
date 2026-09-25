"""Shared BA v2 test fixtures.

`ba_state()` builds the state a *completed* BA stage leaves behind: the BRD
(9 accepted sections), the flow diagram, and the v2 records the fail-closed
gate requires (evidence links per requirement, assumption statuses, a success
metric, a closed plan). Tests that only care about later stages use this
instead of hand-rolling a partial ledger.
"""

from __future__ import annotations

from typing import Any

from tests.prod_fixtures import BRD as FIXTURE_BRD

DIAG = {"kind": "flow", "nodes": 19, "edges": 16,
        "mermaid": "flowchart TD\nA{OK?}-->B", "mmd": "ba_flow_v2.mmd"}

ALL_SECTIONS = ["objectives", "scope", "asis_tobe", "business_case", "stakeholders",
                "stories", "rules", "datarisks", "solution_assessment"]

# No assumption tags / no vague wording / atomic rules — a clean document.
WEAK_BRD = FIXTURE_BRD + (
    "\n## Added weakness\n\nNFR-001 The system must be fast, scalable and secure.\n")


def _knowledge(ctx: dict) -> None:
    """Recorded BA knowledge (the state channel the package renders from)."""
    ctx["project"] = {"name": "Travel Expense"}
    ctx["business"] = {"need": "control travel spend", "problem": "manual approvals",
                       "drivers": "audit findings", "objectives": "cut cycle to 10 days",
                       "goals": "100% policy-checked"}
    ctx["stakeholders"] = {"sponsor": "CFO", "users": "employees"}
    ctx["actors"] = {"Employee": "submits requests"}
    ctx["processes"] = {"travel_request": "submit -> approve -> book"}
    ctx["requirements"] = {"BR-001": "manager approval above threshold"}
    ctx["business_rules"] = {"BR-001": "manager approval above threshold"}
    ctx["functional_requirements"] = {"FR-001": "submit travel request"}
    ctx["use_cases"] = {"UC-001": "submit request"}
    ctx["risks"] = {"R-1": "adoption delay"}
    ctx["dependencies"] = {"D-1": "SSO availability"}
    ctx["delivery"] = {"transition": "pilot with finance team"}
    ctx["open_questions"] = {"OQ-001": "grade cap value?"}
    ctx["decisions"] = {"approval_threshold": "1000 EUR"}
    ctx["history"] = [{"ts": "t", "type": "elicitation", "actor": "ba_agent",
                       "summary": "volumes 200/mo", "ref": ""}]


def ba_state(brd: str = "", sections: Any = (), diagram: bool = False,
             v2: bool = True, ws: str = "bafixture", knowledge: bool = True) -> dict:
    """A BA state dict, optionally with the full v2 record set + knowledge."""
    from shared.project_context import get_context

    s: dict = {}
    doc = brd if brd else FIXTURE_BRD
    if doc:
        s["brd"] = doc
    if diagram:
        s["ba_flow_diagram"] = dict(DIAG)
    ctx = get_context(s)
    ctx["sections_done_BA"] = list(sections)
    if ws:
        ctx["project_workspace_id"] = ws
    if knowledge:
        _knowledge(ctx)
    if v2 and doc:
        from tests.frappe_fixture import seed_ba_v2

        seed_ba_v2(s, doc)
    return s


# Section bodies a stub agent writes: production-grade enough to pass the
# fail-closed gate (>=2 criteria per story, priority tags, atomic rules, the
# stage-2 business case and the stage-7 solution assessment). Stubs that
# assemble a BRD from these exercise the real acceptance path.
SECTION_BODIES: dict[str, str] = {
    "objectives": (
        "Objective: deliver travel and expense management on the existing platform. "
        "Executive summary: approvals and claims move from email to one workflow. "
        "Goal: cut the reimbursement cycle and make every decision auditable. " * 4),
    "scope": (
        "Scope IN: travel request, approval workflow, expense claim, receipt capture and "
        "reimbursement reporting. Every in-scope item ships in this release. "
        "Scope OUT: payroll processing, fleet management and recruitment. " * 4),
    "stakeholders": (
        "Stakeholders: employees, line managers, finance analysts, travel desk and the "
        "executive sponsor. Actors and roles: Employee submits; Line Manager approves; "
        "Finance Analyst reimburses; Travel Administrator maintains policy data. " * 4),
    "asis_tobe": (
        "AS-IS: approvals by email and spreadsheets today, with no audit trail and no "
        "reliable view of committed spend; the current process varies by department. "
        "TO-BE: one workflow with policy checks at submission, duplicate detection, full "
        "audit logging and a committed-versus-actual spend report. " * 4),
    "business_case": (
        "Capability gap: the organisation cannot enforce travel policy or evidence "
        "approvals because they live in email.\n\n"
        "| Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended |\n"
        "|---|---|---|---|---|---|\n"
        "| OPT-001 | Configure the capability on the existing platform | Policy checks at "
        "submission and a complete audit trail | 6 person-weeks | High: the workflow and "
        "reporting already exist | yes |\n"
        "| OPT-002 | Buy a standalone expense product | Faster first rollout | 10 "
        "person-weeks | Medium: a second data store to reconcile | no |\n\n"
        "OPT-001 pro: one data store for approvals and claims. OPT-001 con: configuration "
        "work must fit the current release. OPT-002 pro: mature receipt capture. "
        "OPT-002 con: a recurring licence and an integration.\n\n"
        "Feasibility: OPT-001 is technically feasible on the existing platform. "
        "Benefits: reimbursement cycle from 10 business days to 2 business days and 100% "
        "policy-checked submissions.\n\n"
        "Risk of doing nothing: uncontrolled spend against a 2.4 million EUR budget with no "
        "audit evidence. Decision factors: three year cost, audit evidence and time to "
        "first release."),
    "stories": (
        "US-001 [MoSCoW: Must] As an employee I want to submit a travel request so that "
        "approval happens before booking.\n"
        "- AC-001 Given a complete request when the employee submits then it is saved as "
        "Pending Approval.\n"
        "- AC-002 Given a missing destination when the employee submits then error E-TR-001 "
        "is shown.\n\n"
        "US-002 [MoSCoW: Must] As a manager I want to approve a travel request so that "
        "spend stays controlled.\n"
        "- AC-001 Given a pending request when the manager approves then the state becomes "
        "Approved.\n"
        "- AC-002 Given a rejected request when the manager records a reason then the state "
        "becomes Cancelled.\n\n"
        "US-003 [MoSCoW: Should] As a finance analyst I want to reimburse an approved claim "
        "so that employees are paid.\n"
        "- AC-001 Given an approved claim when finance processes it then the claim becomes "
        "Reimbursed.\n"
        "- AC-002 Given a missing receipt when finance reviews the claim then it is "
        "returned with error E-CL-002."),
    "rules": (
        "BR-001 [MoSCoW: Must] Travel requests above 1000 EUR require manager approval "
        "before booking.\n"
        "BR-002 [MoSCoW: Must] Hotel bookings must not exceed the grade cap of 180 EUR per "
        "night.\n"
        "BR-003 [MoSCoW: Should] Reimbursement requires itemised receipts."),
    "datarisks": (
        "Data entities and fields: Travel Request (destination, dates, amount), Expense "
        "Claim (receipt number, amount) and Payment (amount, claim reference). "
        "Non-functional requirements: the system must notify the approver within 2 "
        "seconds, must retain approval history for 7 years and must sustain 250 "
        "concurrent users with 99.5% availability. "
        "Risks: policy non-compliance on receipts, duplicate reimbursement and approval "
        "bottleneck. Glossary: BRD, WBS, RACI, RAID. KPIs: approval cycle time and "
        "reimbursement cycle time. Open questions: OQ-001 hotel cap per grade and OQ-002 "
        "flight class thresholds. " * 2),
    "solution_assessment": (
        "Coverage of the recommended option OPT-001 against the requirements:\n\n"
        "| Requirement | Solution element | Option | Coverage | Gap or note |\n"
        "|---|---|---|---|---|\n"
        "| US-001 | Travel Request form with policy validation | OPT-001 | Full | none |\n"
        "| US-002 | Approval workflow with the approver role | OPT-001 | Full | none |\n"
        "| US-003 | Expense Claim and receipt capture | OPT-001 | Partial | payment file "
        "export is deferred |\n\n"
        "Readiness: People status Not ready, action confirm the approver groups. "
        "Process status Partially ready, action publish the revised policy. "
        "Technology status Ready, action no new infrastructure is required. "
        "Training status Not ready, action prepare a employee briefing and a manager "
        "guide.\n\n"
        "Solution gaps: payment file export is deferred. Requirement-solution mismatch: "
        "none identified. Business acceptance: the Finance Director accepts the solution "
        "against these criteria.\n\n"
        "Success metrics: SM-001 reimbursement cycle time (baseline 10 business days, "
        "target 2 business days, measured from the claim audit trail, reviewed 30 days "
        "after go-live)."),
}



def generation(seed: dict) -> dict:
    """Declare generation mode for pipeline tests.

    Conversation-first is the product default, so a test that exercises the
    pipeline machinery states its mode explicitly instead of relying on it.
    """
    seed["conversation"] = {"mode": "generation"}
    return seed
