"""Production-grade document fixtures (offline).

These encode the standard the gates enforce: WBS rows with owner/priority/
estimate/start/end/dependencies/trace, dated milestones, RAID with severity
and review dates, use cases with actor/pre/post conditions, error codes,
endpoints with response codes, schema with field types. They are mutually
consistent (coverage/traceability across documents).
"""

BRD = """# Business Requirements Document — Employee Travel and Expense

## Objectives and Executive Summary

Objective: deliver a travel and expense management capability on Frappe. Goal: control
travel spend and cut the reimbursement cycle. Success criteria: 100% of requests
policy-checked, every claim traceable to a receipt, and a reimbursement decision within
ten business days of claim submission.

## Scope IN and Scope OUT

Scope IN: travel request, approval workflow, booking references, expense claim, receipt
capture, reimbursement and spend dashboards. Every scope-in item is delivered in this
release and traced to at least one functional requirement.
Scope OUT: payroll processing, performance management, recruitment and fleet management.
The exclusions are deliberate and will be revisited only through a change request.

## Stakeholders and Actors

Stakeholders: employees, line managers, finance analysts, travel desk, compliance officer
and the executive sponsor. Actors and roles: Employee submits requests and claims; Line
Manager approves or rejects; Finance Analyst validates and reimburses; Travel
Administrator maintains policy data; Project Lead owns delivery and escalation.

## AS-IS and TO-BE Process

AS-IS: approvals by email and spreadsheets, no audit trail, receipts lost in inboxes, and
no reliable view of committed travel spend. Current process steps are manual and vary by
department.
TO-BE: Frappe workflow with policy validation at submission, duplicate detection on
claims, full audit logging and dashboards for committed versus actual spend.

## User Stories and Acceptance Criteria

US-001 [MoSCoW: Must] As an employee I want to submit a travel request so that I get approval before booking.
- AC-001 Given a logged-in employee when the request is complete then it is saved as Pending Approval.
- AC-002 Given missing mandatory fields when the employee submits then validation errors are shown.

US-002 [MoSCoW: Must] As a manager I want to approve or reject travel requests so that spend stays controlled.
- AC-001 Given a pending request when the manager approves then the state becomes Approved and the employee is notified.
- AC-002 Given a request above 1000 EUR when the manager rejects then the request is Cancelled with a reason.

US-003 [MoSCoW: Should] As a finance analyst I want to reimburse approved claims so that employees are paid accurately.
- AC-001 Given an approved claim when finance processes payment then the claim becomes Reimbursed and a payment record is created.
- AC-002 Given a claim with a missing receipt when finance reviews it then the claim is returned with error E-CL-002.

## Business Rules

BR-001 [MoSCoW: Must] Travel requests above 1000 EUR require manager approval before booking.
BR-002 [MoSCoW: Must] Hotel bookings must not exceed the grade cap of 180 EUR per night.
BR-003 [MoSCoW: Must] Reimbursement requires itemised receipts.
BR-004 [MoSCoW: Should] Duplicate claims must be rejected.

## Business Case and Solution Options

Capability gap: the organisation cannot enforce travel policy, cannot evidence approvals and
cannot report committed spend because approvals and receipts live in email and spreadsheets.

| Option | Approach | Benefit | Cost/Effort | Feasibility | Recommended |
|---|---|---|---|---|---|
| OPT-001 | Configure the travel and expense capability on the existing Frappe platform | Policy checks at submission and a full audit trail from day one | 6 person-weeks | High — the platform, workflow engine and reporting already exist | yes |
| OPT-002 | Buy a standalone SaaS expense product and integrate approvals | Faster initial rollout | 10 person-weeks plus annual licence | Medium — integration effort and a second data store to reconcile | no |

OPT-001 pro: no new platform contract and one data store for approvals and claims.
OPT-001 con: configuration work must be scheduled inside the current release.
OPT-002 pro: mature receipt OCR is available out of the box.
OPT-002 con: a second system of record and a recurring licence cost.

Feasibility summary: OPT-001 is technically feasible on the existing platform and
organisationally feasible with two configured approver roles; OPT-002 needs an interface
and an integration budget.

Quantified benefits: expected reimbursement cycle reduction from 10 business days to 2
business days, and policy-checked submissions rising from 0% today to 100%.

Risk of doing nothing: uncontrolled travel spend continues against a 2.4 million EUR
annual budget, with no audit evidence for 100% of approvals and continued duplicate
reimbursement.

Decision factors: total cost over three years, audit evidence, time to first release and
the number of systems of record. The business decides which factors dominate.

## Data Needs, Risks, Glossary and KPIs

Data entities and fields: Travel Request (destination, dates, amount), Expense Claim
(receipt number, amount), Payment (amount, claim reference).
Non-functional requirements: the system must notify the approver within 2 seconds of
submission, must keep approval history for 7 years, and must sustain 250 concurrent users
during business hours with 99.5% availability.
Risks: policy non-compliance on receipts; duplicate reimbursement; approval bottleneck.
Glossary: BRD, WBS, RACI, RAID. KPIs: approval cycle time, reimbursement cycle time,
policy violation rate.
Open questions: OQ-001 hotel cap per grade [ASSUMPTION:hotel-cap-per-grade]; OQ-002 flight
class thresholds [ASSUMPTION:flight-class-thresholds].

## Solution Assessment, Readiness and Gaps

Coverage of the recommended option OPT-001 against each requirement:

| Requirement | Solution element | Option | Coverage | Gap or note |
|---|---|---|---|---|
| US-001 | Travel Request DocType with policy validation | OPT-001 | Full | none |
| US-002 | Approval workflow with the Travel Approver role | OPT-001 | Full | none |
| US-003 | Expense Claim and receipt capture with payment record | OPT-001 | Partial | payment file export is out of scope for the first release |

Readiness:
- People: status Not ready, action name the two approver groups and confirm delegation rules.
- Process: status Partially ready, action publish the revised travel policy before the pilot.
- Technology: status Ready, action no new infrastructure is required on the existing platform.
- Training: status Not ready, action prepare a 30 minute employee briefing and a manager guide.

Solution gaps: payment file export for the finance back office is deferred, and no mobile
receipt capture is planned. Requirement-solution mismatch: none identified.

Business acceptance: the Finance Director accepts the solution against these criteria, with
the travel desk accountable for policy data.

Success metrics: SM-001 reimbursement cycle time (baseline 10 business days, target 2
business days, measured from the claim audit trail, reviewed 30 days after go-live);
SM-002 policy-checked submissions (baseline 0%, target 100%, measured from the Travel
Request audit trail, reviewed 30 days after go-live).
"""

PLAN = """# Project Plan — Travel and Expense (production-grade)

## Objective and Success Criteria

Objective: deliver the travel and expense capability end to end, from request capture
through reimbursement, on the existing Frappe platform. Success criteria: every
functional requirement implemented and verified in Frappe, UAT passed with no open
critical defects, and the go-live milestone met within the planned timeline.

## Work Breakdown Structure

| ID | Task | Owner Role | Priority | Estimate | Start | End | Dependencies | Trace |
|----|------|-----------|----------|----------|-------|-----|--------------|-------|
| T-001 | Travel Request DocType and form | Functional Consultant | P0 | 3d | 2026-09-21 | 2026-09-23 | - | US-001, BR-001 |
| T-002 | Approval workflow and notifications | Frappe Engineer | P0 | 2d | 2026-09-24 | 2026-09-25 | T-001 | US-002, BR-001 |
| T-003 | Expense Claim and receipt upload | Frappe Engineer | P1 | 4d | 2026-09-28 | 2026-10-01 | T-002 | US-003, BR-003 |
| T-004 | Duplicate and policy validation rules | Technical Architect | P1 | 2d | 2026-10-02 | 2026-10-05 | T-003 | BR-002, BR-003 |
| T-005 | Reimbursement automation and payment record | Functional Consultant | P1 | 3d | 2026-10-06 | 2026-10-08 | T-004 | US-003 |
| T-006 | Dashboards and KPI reporting | Technical Engineer | P2 | 2d | 2026-10-09 | 2026-10-12 | T-005 | FR-005 |

## Milestones, Dependencies and Critical Path

### Milestones

| Milestone | Date | Description |
|-----------|------|-------------|
| M1 Requirements signed off | 2026-09-19 | BRD and FRs approved |
| M2 Request module live | 2026-09-25 | T-001 and T-002 complete |
| M3 Claim module live | 2026-10-05 | T-003 and T-004 complete |
| M4 Go-live | 2026-10-13 | T-005 and T-006 complete, UAT passed |

### Dependencies

| Predecessor | Successor | Reason | Blocking |
|-------------|-----------|--------|----------|
| T-001 | T-002 | Workflow binds to the DocType | Yes |
| T-002 | T-003 | Claims start after the approval flow | Yes |
| T-004 | T-005 | Reimbursement depends on validations | Yes |

Critical path: T-001 → T-002 → T-003 → T-004 → T-005 → T-006 (16 working days).
Sprints: Sprint 1 (T-001, T-002), Sprint 2 (T-003, T-004), Sprint 3 (T-005, T-006).

## RACI and RAID Log

### RACI Matrix

| Role / Deliverable | Request | Approval | Claim | Reimbursement |
|--------------------|---------|----------|-------|---------------|
| Employee | R | I | R | I |
| Line Manager | C | A/R | I | I |
| Finance Analyst | I | C | A/R | A |
| Project Lead | A | C | C | C |

### RAID Log

| ID | Type | Description | Severity | Owner | Mitigation / Resolution | Review Date |
|----|------|-------------|----------|-------|------------------------|-------------|
| R-001 | Risk | Receipt fraud | High | Finance Analyst | Duplicate detection and random audit | 2026-10-01 |
| A-001 | Assumption | Hotel cap 180 EUR | Medium | Project Lead | Confirm policy with HR | 2026-09-22 |
| I-001 | Issue | Booking API latency | Medium | Technical Architect | Cache responses and queue retries | 2026-09-30 |
"""

SPEC = """# Functional Specification — Travel and Expense (production-grade)

## Overview and Scope

Overview: this specification defines the functional behavior for travel requests,
approvals, expense claims and reimbursement, including validation rules, error codes and
the data model. Scope: all FRs trace to user stories and business rules from the BRD.

## Functional Requirements Catalog

| ID | Description | Priority | Source | Business Rules | Expected Behavior | Validations | Expected Outcome |
|----|-------------|----------|--------|----------------|-------------------|-------------|------------------|
| FR-001 | Create travel request | Must | US-001, BR-001 | BR-001 limit 1000 EUR; BR-002 hotel cap | Form persists a Travel Request | Mandatory fields, E-TR-001 missing field | Request in Pending Approval |
| FR-002 | Manager approval | Must | US-002, BR-001 | BR-001 | Workflow transition Approve or Reject | E-AP-002 invalid transition | State Approved with audit entry |
| FR-003 | Expense claim capture | Must | US-003, BR-003 | BR-003 receipts | Claim stores receipt items | E-CL-003 missing receipt | Claim in Pending Finance Review |
| FR-004 | Duplicate detection | Should | US-003, BR-003 | BR-003 | Reject duplicate receipt numbers | E-CL-004 duplicate receipt | Rejected with reason |
| FR-005 | Reimbursement processing | Should | US-003, BR-003 | BR-003 | Payment record created | E-PY-005 payment failure | Claim Reimbursed |

## Use Cases and Alternate Flows

### UC-001 Submit Travel Request

Actor: Employee. Precondition: employee is active and logged in. Postcondition: request
saved in Pending Approval.
Primary flow: fill form, submit, policy validation, notify manager.
Alternate flow: missing mandatory field shows E-TR-001; limit exceeded routes to Finance review.

### UC-002 Approve Travel Request

Actor: Line Manager. Precondition: request in Pending Approval. Postcondition: state
Approved or Rejected with reason.
Primary flow: open request, approve, notify employee and finance.
Alternate flow: reject with reason sets state Rejected; delegation to another approver is supported.

## Validations, Errors and Data Model

Validation rules and error codes: E-TR-001 mandatory field missing; E-AP-002 invalid
workflow transition; E-CL-003 missing receipt; E-CL-004 duplicate receipt; E-PY-005
payment failure.
Entities and attributes: Travel Request (destination varchar, amount decimal, start_date
date, primary key name), Expense Claim (receipt varchar, amount decimal, unique
receipt_no), Payment (amount decimal, index claim).

## Traceability Matrix

Traceability matrix mapping requirements to sources, design and tests:

| Requirement | Source | Design | Test |
|-------------|--------|--------|------|
| FR-001 | US-001, BR-001 | TECH-001 | TC-001 |
| FR-002 | US-002, BR-001 | TECH-002 | TC-002 |
| FR-003 | US-003, BR-003 | TECH-003 | TC-003 |
| FR-004 | US-003, BR-003 | TECH-003 | TC-004 |
| FR-005 | US-003, BR-003 | TECH-003 | TC-005 |
"""

DESIGN = """# Technical Design — Travel and Expense (production-grade)

## Architecture and Decisions

Architecture: Frappe/ERPNext app with custom DocTypes, the workflow engine, a REST
integration service and a background job queue.
Decision ADR-001: use Frappe workflows instead of custom state code (trade-off: less
flexibility, faster delivery, built-in audit).
Decision ADR-002: receipts stored as Frappe files with database metadata.

Requirements covered: FR-001, FR-002, FR-003, FR-004 and FR-005 map to TECH-001..TECH-003.

## API Contracts

| Endpoint | Method | Request | Response | Codes |
|----------|--------|---------|----------|-------|
| POST /api/resource/Travel%20Request | POST | destination, dates, amount | created doc name | 200, 400, 417 |
| GET /api/resource/Travel%20Request | GET | filters, fields | list of requests | 200 |
| PUT /api/resource/Travel%20Request/{name} | PUT | changed fields | updated doc | 200, 403, 404 |
| POST /api/method/frappe.model.workflow.apply_workflow | POST | doc, action | ok | 200, 417 |

## Data Schema

Table travel_request: name varchar primary key, destination varchar, start_date date,
end_date date, amount decimal, workflow_state varchar.
Table expense_claim: name varchar primary key, receipt_no varchar unique index, amount
decimal, claim_date date.
Table payment: name varchar primary key, claim varchar foreign key, amount decimal.

## NFRs, Security and Deployment

Performance: p95 API latency <= 300 ms at 200 RPS; receipt OCR <= 2 s per image.
Availability: 99.5% uptime, RPO 15 min, RTO 60 min.
Security: role-based access control, encryption at rest, audit logging on every write.
Deployment: CI/CD pipeline with a staged environment, database migration step and a
documented rollback procedure.

## Technical Build Tasks

| ID | Task | Estimate | Depends | Trace |
|----|------|----------|---------|-------|
| TECH-001 | Travel Request DocType and API | 3d | - | FR-001 |
| TECH-002 | Approval workflow and hooks | 2d | TECH-001 | FR-002 |
| TECH-003 | Claim capture, validation and payment | 4d | TECH-002 | FR-003, FR-004, FR-005 |

Sprints: Sprint 1 (TECH-001, TECH-002), Sprint 2 (TECH-003).
"""
