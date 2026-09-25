"""State manager — typed views over shared PROJECT_CONTEXT (no new DB).

Maps the 8 architecture views onto existing project_context sections:
- Project Context -> project/business/stakeholders
- Requirements    -> requirements/business_rules/functional_requirements
- Decisions       -> decision_log/decisions
- Uncertainty     -> open_questions/risks/contradictions (+ elicitation log)
- Evidence        -> evidence_links/elicitation/history/artifacts
- Plan            -> ba_plan (+ stage progress)
- Quality         -> quality_findings
- Outcome         -> success_metrics/outcome_measurements
All helpers are pure functions over a plain dict (unit-testable, no ADK).
"""

from __future__ import annotations

from typing import Any

VIEW_TO_SECTIONS = {
    "context": ("project", "business", "stakeholders", "actors", "processes"),
    "requirements": ("requirements", "business_rules", "functional_requirements",
                     "use_cases", "priorities"),
    "decisions": ("decision_log", "decisions", "ba_approvals"),
    "uncertainty": ("open_questions", "risks", "contradictions", "assumptions", "elicitation"),
    "evidence": ("evidence_links", "elicitation", "history", "artifacts"),
    "plan": ("ba_plan",),
    "quality": ("quality_findings",),
    "outcome": ("success_metrics", "outcome_measurements", "solution_assessment"),
}


def _ctx(state: dict[str, Any]) -> dict[str, Any]:
    from shared.project_context import get_context

    return get_context(state)


def view(state: dict[str, Any], name: str) -> dict[str, Any]:
    """Return one architecture view as {section: value}."""
    ctx = _ctx(state)
    return {s: ctx.get(s) for s in VIEW_TO_SECTIONS[name]}


def is_new_project(state: dict[str, Any]) -> bool:
    """New when no BRD text and no recorded requirements/decisions."""
    from shared.traceability import doc_texts

    ctx = _ctx(state)
    has_doc = len(doc_texts(state).get("brd", "")) > 200
    has_req = bool(ctx.get("requirements")) or bool(ctx.get("business_rules"))
    has_dec = bool(ctx.get("decisions"))
    return not (has_doc or has_req or has_dec)
