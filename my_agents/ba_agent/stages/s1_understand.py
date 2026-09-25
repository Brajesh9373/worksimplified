"""S1 — Understand Business. Problem/goals/context/initial scope."""

STAGE_ID = "S1"
TITLE = "Understand Business"
FRAGMENTS = ["FRAG_IDENTITY", "FRAG_PIPELINE", "FRAG_CONTEXT", "FRAG_DISCOVERY"]

OUTPUT_CONTRACT = (
    "Business Understanding: problem, goals, objectives, business context, "
    "current situation, desired outcome, drivers, initial stakeholders, "
    "initial scope, assumptions, constraints."
)

NEEDED_INFO = [
    "problem", "goals", "objectives", "business_context",
    "current_situation", "desired_outcome", "stakeholders", "scope",
]


def missing_info(known: dict) -> list[str]:
    """Pure helper: which S1 fields are still empty."""
    return [k for k in NEEDED_INFO if not known.get(k)]
