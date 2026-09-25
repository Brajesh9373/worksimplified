"""The master requirements catalogue — what a mature BA collects, everywhere.

Drawn from four practices: BABOK/waterfall (BRD + sign-off), Agile (stories,
acceptance criteria, Definition of Ready), ERP/Frappe implementation
(process maps, gap analysis, migration, UAT), and agency discovery
(viability first, risk check, signed scope).

Each item carries:
  ask            the default interview question (one at a time, conversational)
  ask_overrides  per-project-type wording where it matters
  check          a *coded* fulfilled-criteria — the LLM never decides this
  applies        which project types the item is asked for

Groups A–G double as the nodes of the live coverage diagram.
"""

from __future__ import annotations

import re

PROJECT_TYPES = (
    "custom_software",
    "erp_frappe",
    "integration",
    "website",
    "mobile_app",
    "generic",
)

GROUPS = (
    {"id": "A", "label": "Viability", "desc": "Is this project real? Problem, goal, budget, deadline, approver."},
    {"id": "B", "label": "Business", "desc": "Objectives, scope in/out, stakeholders, as-is → to-be, value."},
    {"id": "C", "label": "Stakeholder", "desc": "Roles, user stories, acceptance criteria, priorities."},
    {"id": "D", "label": "Functional", "desc": "Features, rules, workflows, reports, integrations, data."},
    {"id": "E", "label": "Non-functional", "desc": "How well it must work: speed, security, compliance, hosting."},
    {"id": "F", "label": "Transition", "desc": "Migration, training, UAT, cutover and support."},
    {"id": "G", "label": "Governance", "desc": "Assumptions, risks, dependencies, change control, sign-off."},
)

_PLACEHOLDERS = re.compile(
    r"\btbd\b|\btodo\b|\bto be decided\b|\bnot sure\b|\bdon't know\b|\bna\b|\bn/a\b|^\s*[-.?…]*\s*$",
    re.IGNORECASE,
)
_ROLE_WORDS = {
    "manager", "admin", "administrator", "owner", "head", "lead", "user",
    "someone", "team", "management", "client", "vendor", "staff",
}


def _clean(text: str) -> str:
    return (text or "").strip()


def _lines(text: str) -> list[str]:
    return [ln.strip(" -•\t") for ln in _clean(text).splitlines() if ln.strip(" -•\t")]


def _bullets(text: str) -> list[str]:
    """Bullet/numbered lines, or comma-separated phrases when no newlines."""
    lines = [ln for ln in _lines(text) if re.match(r"^(\d+[.)]|[-*•])\s+\S", ln)]
    if lines:
        return lines
    if "\n" not in _clean(text):
        parts = [p.strip() for p in re.split(r"[,;]", _clean(text)) if len(p.strip()) > 2]
        if len(parts) >= 2:
            return parts
    return [ln for ln in _lines(text) if len(ln) > 2]


# ------------------------------------------------------------------ checks ---
# Each returns (fulfilled: bool, hint: str). Hint is shown to the LLM so the
# follow-up probe targets exactly what is missing.


def _nonempty(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if len(t) < int(params.get("min_len", 20)) or _PLACEHOLDERS.search(t):
        return False, f"need a concrete answer (at least ~{params.get('min_len', 20)} characters, no TBD)"
    return True, ""


_ARTICLES = {"the", "a", "an"}


def _named_person(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t):
        return False, "need an actual person's name"
    words = re.findall(r"[A-Za-z][A-Za-z.'-]*", t)
    if len(words) < 2:
        return False, "need a full name (first + last), not just a role"
    content = [w for w in words
               if w.lower() not in _ROLE_WORDS and w.lower() not in _ARTICLES]
    if not content:
        return False, "that is a role, not a person — need the person's name"
    return True, ""


def _has_number(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t):
        return False, "need a concrete figure, not TBD"
    if re.search(r"\d", t) or re.search(
            r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
            r"week|month|quarter|second|minute|hour|day|daily|asap|immediately|flexible)\b", t, re.IGNORECASE):
        return True, ""
    return False, "need a number, date or timeframe in the answer"


def _list_n(text: str, params: dict) -> tuple[bool, str]:
    n = int(params.get("n", 3))
    items = _bullets(text)
    if len(items) >= n and not _PLACEHOLDERS.search(_clean(text)):
        return True, ""
    return False, f"need at least {n} distinct items (list them, one per line)"


def _story(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t):
        return False, "need at least one real user story"
    stories = [ln for ln in _lines(text)
               if re.search(r"\bas a\b", ln, re.IGNORECASE)
               or (re.search(r"\bwant\w*\b|\bneed\w*\b", ln, re.IGNORECASE)
                   and re.search(r"\bso that\b|\bto\b", ln, re.IGNORECASE))]
    need = int(params.get("n", 2))
    if len(stories) >= need:
        return True, ""
    if stories:
        return False, f"good start — need at least {need} stories in 'As a … I want … so that …' form"
    return False, "need user stories in 'As a [role], I want [goal], so that [benefit]' form"


def _testable(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t) or len(t) < 20:
        return False, "need concrete, testable acceptance statements"
    markers = re.findall(
        r"\b(should|must|shall|will|can|verify|check|ensure|given|when|then|only if)\b",
        t, re.IGNORECASE)
    if len(markers) >= int(params.get("n", 2)):
        return True, ""
    return False, "phrase acceptance so it can be tested ('the system must …', 'given/when/then')"


def _process(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if len(t) < int(params.get("min_len", 60)) or _PLACEHOLDERS.search(t):
        return False, "describe the process step by step (who does what, in what order)"
    seq = re.findall(r"\b(first|then|next|after|before|finally|step \d)\b", t, re.IGNORECASE)
    if len(seq) >= 1 or len(_lines(text)) >= 3:
        return True, ""
    return False, "walk through the process in order, step by step"


def _rule(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t):
        return False, "need at least one concrete rule"
    if re.search(r"\b(if|when|unless|only|must|cannot|approve|limit|auto)\b", t, re.IGNORECASE) \
            and len(t) >= int(params.get("min_len", 30)):
        return True, ""
    return False, "state rules as conditions ('when X, the system must Y', approval limits, validations)"


def _choice(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if _PLACEHOLDERS.search(t):
        return False, "need a decision, not TBD"
    options = [o.lower() for o in params.get("options", [])]
    if any(o in t.lower() for o in options) and len(t) >= 10:
        return True, ""
    return False, f"pick one: {', '.join(options)} (or describe the decision)"


def _confirm_only(text: str, params: dict) -> tuple[bool, str]:
    t = _clean(text)
    if len(t) < int(params.get("min_len", 10)) or _PLACEHOLDERS.search(t):
        return False, "need a short substantive answer"
    return True, ""


_CHECKS = {
    "nonempty": _nonempty,
    "named_person": _named_person,
    "has_number": _has_number,
    "list_n": _list_n,
    "story": _story,
    "testable": _testable,
    "process": _process,
    "rule": _rule,
    "choice": _choice,
    "confirm_only": _confirm_only,
}


def check_item(item: dict, text: str) -> tuple[bool, str]:
    """Run an item's coded fulfilled-criteria. The LLM never decides this."""
    fn = _CHECKS[item["check"]]
    return fn(text, item.get("params", {}))


# ------------------------------------------------------------------- items ---

def _item(item_id: str, group: str, label: str, why: str, ask: str, check: str,
          params: dict | None = None, applies: list[str] | None = None,
          ask_overrides: dict | None = None) -> dict:
    return {"id": item_id, "group": group, "label": label, "why": why,
            "ask": ask, "ask_overrides": ask_overrides or {},
            "check": check, "params": params or {},
            "applies": applies or ["all"]}


ITEMS: dict[str, dict] = {}


def _add(item: dict) -> None:
    ITEMS[item["id"]] = item


# --- A. Viability: asked first, always, for every project type ----------------
_add(_item("a0", "A", "Project name",
           "Everything downstream is filed under this name — confirm it early.",
           "What should we call this project — a short working name?",
           "nonempty", {"min_len": 3}))
_add(_item("a1", "A", "Problem & pain",
           "Every engagement starts from the pain, never from features.",
           "In your own words — what is not working today, and what pain does it cause?",
           "nonempty", {"min_len": 20}))
_add(_item("a2", "A", "Goal & success metric",
           "Outcomes first: what does success look like, measurably?",
           "What would success look like? How will you know this worked — in numbers if possible?",
           "has_number", {"min_len": 10}))
_add(_item("a3", "A", "Budget range",
           "Agencies qualify viability before depth; no budget, no scope.",
           "What budget range are we working within?",
           "has_number"))
_add(_item("a4", "A", "Deadline & timeline",
           "Dates drive scope and phasing decisions.",
           "Is there a deadline or go-live date driving this? When?",
           "has_number"))
_add(_item("a5", "A", "Decision maker",
           "A role cannot sign off — the final approver must be a named person.",
           "Who is the one person who gives final approval? I need a name.",
           "named_person"))
_add(_item("a6", "A", "Hard constraints",
           "Technical, legal or policy limits shape everything downstream.",
           "Are there any hard limits I must respect — existing systems we must keep, policies, legal or compliance rules?",
           "nonempty", {"min_len": 10}))

# --- B. Business ---------------------------------------------------------------
_add(_item("b1", "B", "Objectives & KPIs",
           "Business objectives with measures, not technical goals.",
           "What are the 2–3 business objectives this must achieve, and how is each measured?",
           "list_n", {"n": 2}))
_add(_item("b2", "B", "Scope — in",
           "Explicit in-scope boundaries prevent scope creep.",
           "List what is definitely IN scope — the capabilities this project must deliver.",
           "list_n", {"n": 2}))
_add(_item("b3", "B", "Scope — out",
           "Out-of-scope is where disputes are born; name it now.",
           "And what is explicitly OUT of scope — things people might assume are included but are not?",
           "list_n", {"n": 1}))
_add(_item("b4", "B", "Stakeholders & RACI",
           "Incomplete stakeholder lists are a top cause of failed requirements.",
           "Who are the stakeholders — sponsor, users, operations, finance, IT? For each: name, role, and whether they approve, contribute, or just need informing.",
           "named_person"))
_add(_item("b5", "B", "As-is process",
           "You cannot design the future state without the current one.",
           "Walk me through how this works TODAY, step by step — who does what, in what order?",
           "process", {"min_len": 60}))
_add(_item("b6", "B", "To-be process",
           "The agreed future state, validated before anything is built.",
           "Now the future: how SHOULD it work once this is delivered? Step by step.",
           "process", {"min_len": 60}))
_add(_item("b7", "B", "Business case & value",
           "Why this is worth doing: value, cost of doing nothing.",
           "Why is this worth doing — what value does it create, or what does doing nothing cost?",
           "nonempty", {"min_len": 30}))

# --- C. Stakeholder ------------------------------------------------------------
_add(_item("c1", "C", "User roles & permissions",
           "Unclear roles surface in week three and detonate estimates.",
           "Who will actually use this, and what should each role be able to do (and not do)?",
           "list_n", {"n": 2},
           ask_overrides={"erp_frappe": "Which departments and roles will use the ERP (Sales, Purchase, Accounts, Stores…), and what should each role see and approve?"}))
_add(_item("c2", "C", "User stories",
           "Stories carry value ('so that') — tasks disguised as requirements do not.",
           "Give me the key user stories in this form: 'As a [role], I want [goal], so that [benefit]'. At least two.",
           "story", {"n": 2}))
_add(_item("c3", "C", "Acceptance criteria",
           "Shared definition of done per story — the basis of UAT.",
           "For those stories: how will we prove each one works? State acceptance so it can be tested ('the system must …', 'given/when/then').",
           "testable", {"n": 2}))
_add(_item("c4", "C", "Priorities (MoSCoW)",
           "Prioritization is what makes scope negotiable instead of infinite.",
           "Split the scope: Must have for go-live, Should have, Could have later, Won't have this time.",
           "choice", {"options": ["must", "should", "could", "won't", "wont", "moscow", "priority", "phase"]}))

# --- D. Functional ---------------------------------------------------------------
_add(_item("d1", "D", "Features & capabilities",
           "The numbered capability list the build is estimated against.",
           "List the features and capabilities the solution must have.",
           "list_n", {"n": 3}))
_add(_item("d2", "D", "Business rules",
           "Calculations, validations, authorizations — the logic layer.",
           "What are the business rules — calculations, validations, limits, approval thresholds? State them as conditions ('when X, the system must Y').",
           "rule", {"min_len": 30}))
_add(_item("d3", "D", "Workflows & approvals",
           "Multi-step flows with approval chains, especially for ERP.",
           "Which multi-step workflows need approvals — e.g. order → approval → invoice? Who approves at each step?",
           "nonempty", {"min_len": 30},
           ask_overrides={"erp_frappe": "Which approval workflows do you need — purchase approvals, leave approvals, multi-level authorizations? Who approves at each level and above what amount?"}))
_add(_item("d4", "D", "Reports & print formats",
           "Assumed reporting is a classic hidden requirement.",
           "What reports, dashboards, invoices or print formats must come out of the system, and which data goes on each?",
           "list_n", {"n": 1},
           ask_overrides={"erp_frappe": "Which reports and print formats are mandatory — GST reports, stock registers, quotation/invoice formats, management dashboards?"}))
_add(_item("d5", "D", "Notifications & alerts",
           "Often forgotten until UAT; cheap to capture now.",
           "Who must be notified about what — emails, SMS, WhatsApp alerts on which events?",
           "list_n", {"n": 1}))
_add(_item("d6", "D", "Integrations",
           "The integration nobody mentioned until mid-build — asked explicitly.",
           "What must this connect to — payment gateways, e-commerce, banks, e-invoicing, existing software, hardware like printers or scanners?",
           "list_n", {"n": 1},
           ask_overrides={"erp_frappe": "Which integrations are in play — payment gateways, bank statements, e-invoicing/e-way bills, e-commerce, biometric attendance, existing tools?"}))
_add(_item("d7", "D", "Data & masters",
           "Data requirements drive the model, migration and doctypes.",
           "What master data will live here — customers, suppliers, items, employees? What are the key fields for each, and roughly how many records?",
           "nonempty", {"min_len": 30},
           ask_overrides={"erp_frappe": "Which masters must be migrated — Chart of Accounts, Customers, Suppliers, Items, Warehouses, Price Lists, Employees? Rough record counts and where the data lives today (Excel, Tally, another system)?"}))

# --- E. Non-functional -------------------------------------------------------------
_add(_item("e1", "E", "Performance & scale",
           "Response times and user volumes, stated as numbers.",
           "How many users, and how fast must it feel — response times, transactions per day, data growth?",
           "has_number"))
_add(_item("e2", "E", "Security & access",
           "Authentication, authorization, audit trails.",
           "Security needs: logins and password policy, who can see or change what, and do you need an audit trail of who did what?",
           "nonempty", {"min_len": 20}))
_add(_item("e3", "E", "Compliance & legal",
           "Regulatory constraints nobody raised until audit time.",
           "Any compliance or legal constraints — GST, e-invoicing, data protection, industry regulations, data residency?",
           "nonempty", {"min_len": 10}))
_add(_item("e4", "E", "Usability & access",
           "Languages, devices, accessibility for real users.",
           "Who finds software hard to use here — languages needed, mobile vs desktop, accessibility needs?",
           "nonempty", {"min_len": 10}))
_add(_item("e5", "E", "Availability & backup",
           "Uptime expectations and recovery requirements.",
           "How critical is uptime — can it be down on weekends? And backups: how much data loss is acceptable, how fast must recovery be?",
           "nonempty", {"min_len": 15}))
_add(_item("e6", "E", "Hosting & infrastructure",
           "Where it runs constrains everything operational.",
           "Where should this run — your server, Frappe Cloud, a VPS? Any IT policies I must fit?",
           "choice", {"options": ["cloud", "server", "vps", "frappe cloud", "on-premise", "on premise", "host", "aws", "digitalocean", "hetzner"]},
           ask_overrides={"erp_frappe": "Where should the ERP run — Frappe Cloud, your own server, a VPS? Who manages backups and upgrades?"},
           applies=["custom_software", "erp_frappe", "integration", "website", "mobile_app", "generic"]))

# --- F. Transition: ERP, integration and migration-heavy jobs -----------------------
_F_APPLIES = ["erp_frappe", "integration", "custom_software", "generic"]
_add(_item("f1", "F", "Migration scope & sources",
           "Migration is the highest-risk ERP phase; scope it explicitly.",
           "What data moves into the new system — masters, opening balances, history? Where does it live today, and how clean is it?",
           "nonempty", {"min_len": 30},
           applies=_F_APPLIES,
           ask_overrides={"erp_frappe": "Migration scope: masters (Customers, Suppliers, Items…), opening balances, how much history? Source: Excel, Tally, legacy system? Known duplicates or messy data?"}))
_add(_item("f2", "F", "Training needs",
           "Role-based training before go-live, not after.",
           "Who needs training, by role — and in what language? Any training material that must exist (manuals, videos)?",
           "nonempty", {"min_len": 15},
           applies=_F_APPLIES))
_add(_item("f3", "F", "UAT approach",
           "Acceptance testing against the requirements, with named testers.",
           "Who will do acceptance testing, with real business scenarios? What does a passing UAT look like?",
           "named_person",
           applies=_F_APPLIES))
_add(_item("f4", "F", "Cutover & support",
           "Go-live plan and hypercare so day one is not day chaos.",
           "Go-live plan: big-bang or phased? And after go-live — how many weeks of priority support do you expect?",
           "nonempty", {"min_len": 15},
           applies=_F_APPLIES))

# --- G. Governance ---------------------------------------------------------------
_add(_item("g1", "G", "Assumptions",
           "Recorded assumptions are agreed facts; unrecorded ones are disputes.",
           "What are we assuming to be true — things you're taking for granted that I should write down?",
           "list_n", {"n": 1}))
_add(_item("g2", "G", "Risks",
           "Named risks get mitigations; unnamed ones get surprises.",
           "What could go wrong — top risks, and what would soften each one?",
           "list_n", {"n": 1}))
_add(_item("g3", "G", "Dependencies",
           "External dependencies own your timeline silently unless named.",
           "What do we depend on — other vendors, client inputs, hardware, licenses, approvals?",
           "list_n", {"n": 1}))
_add(_item("g4", "G", "Change control",
           "Out-of-scope arrives as 'obviously also…' — price it upfront.",
           "When new asks arrive mid-project, who approves them, knowing they add cost and time?",
           "named_person"))
_add(_item("g5", "G", "Acceptance & sign-off",
           "The Definition of Done for the whole project.",
           "What exactly makes this project DONE and accepted — whose signature, against what checklist?",
           "named_person"))
_add(_item("g6", "G", "Glossary",
           "Shared language prevents the most embarrassing defects.",
           "Any company-specific terms or abbreviations I must use correctly in the documents?",
           "confirm_only", {"min_len": 5}))

ORDER = ["a0", "a1", "a2", "a3", "a4", "a5", "a6",
         "b1", "b2", "b3", "b4", "b5", "b6", "b7",
         "c1", "c2", "c3", "c4",
         "d1", "d2", "d3", "d4", "d5", "d6", "d7",
         "e1", "e2", "e3", "e4", "e5", "e6",
         "f1", "f2", "f3", "f4",
         "g1", "g2", "g3", "g4", "g5", "g6"]

assert set(ORDER) == set(ITEMS), "catalogue ORDER/ITEMS mismatch"


def item_applies(item: dict, project_type: str | None) -> bool:
    """Whether an item is asked for a project type (pre-classification: A only)."""
    if project_type is None:
        return item["group"] == "A"
    return "all" in item["applies"] or (project_type or "generic") in item["applies"]


def question_for(item: dict, project_type: str | None) -> str:
    """Interview question, project-type wording where it matters."""
    if project_type and project_type in item["ask_overrides"]:
        return item["ask_overrides"][project_type]
    return item["ask"]
