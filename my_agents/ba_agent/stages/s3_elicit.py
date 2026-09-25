"""S3 — Elicit Information. Targeted questions + human answers + validation."""

STAGE_ID = "S3"
TITLE = "Elicit Information"
FRAGMENTS = ["FRAG_DISCOVERY"]

OUTPUT_CONTRACT = (
    "Verified Business Information: gaps closed via targeted questions, "
    "document analysis, rules, exceptions, assumptions, constraints, "
    "dependencies, contradictions resolved."
)

QUESTION_BUDGET_PER_TURN = 5


def missing_info(gaps: dict) -> list[str]:
    """gaps: {topic: bool answered}. Return unanswered topics."""
    return [k for k, v in gaps.items() if not v]
