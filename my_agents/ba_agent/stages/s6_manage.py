"""S6 — Requirements Management. IDs/versions/baseline/trace/change/evidence."""

STAGE_ID = "S6"
TITLE = "Requirements Management"
FRAGMENTS = ["FRAG_TRACE", "FRAG_CHANGE", "FRAG_KNOWLEDGE", "FRAG_EVIDENCE", "FRAG_PLAN"]

OUTPUT_CONTRACT = (
    "Controlled Requirement Set: versioned IDs, baseline, traceability, "
    "dependencies, evidence link per requirement, DEC-n decisions, registered "
    "assumption statuses, explicit priorities, change history, impact analysis, "
    "audience views."
)

# S6 change -> S4 re-analyze -> S5 revalidate (internal back-edge, not pipeline loop).
REWORK_LOOP = ("S6", "S4", "S5")
