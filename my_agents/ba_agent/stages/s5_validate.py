"""S5 — Verify & Validate. Mirrors gates.ba_gate as reasoning rules."""

STAGE_ID = "S5"
TITLE = "Verify and Validate"
FRAGMENTS = ["FRAG_QUALITY", "FRAG_BRD"]

OUTPUT_CONTRACT = "Validated Requirements: complete, correct, consistent, testable, aligned."

# Mirrors shared/gates.py ba_gate thresholds (kept in sync manually).
CHECKS = [
    "brd_exists_len>1500",
    "objective",
    "scope_in",
    "scope_out",
    "stakeholders",
    "asis_and_tobe",
    "user_stories>=3",
    "acceptance_criteria",
    "business_rules>=3",
    "data",
    "risk",
    "open_questions",
    "flow_nodes>=6",
    "decision_present",
]


def run_gate(state: dict) -> dict:
    """Deterministic gate via shared implementation (no LLM judgment)."""
    from shared.gates import ba_gate

    return ba_gate(state)
