"""S4 — Analyze Requirements. Extract/classify/decompose/consolidate/model."""

STAGE_ID = "S4"
TITLE = "Analyze Requirements"
FRAGMENTS = ["FRAG_REQUIREMENTS", "FRAG_RULES", "FRAG_ANALYSIS"]

OUTPUT_CONTRACT = (
    "Structured Requirements: business/stakeholder/functional requirements, "
    "NFRs, business rules (BR-xxx), user stories (US-xxx) with Given/When/Then "
    "acceptance criteria, dependencies, models. Glossary-first terminology."
)

MIN_STORIES = 3
MIN_RULES = 3
MIN_CRITERIA_PER_STORY = 2
