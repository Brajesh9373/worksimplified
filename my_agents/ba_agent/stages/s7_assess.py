"""S7 — Solution Assessment. Coverage/readiness/gaps/metrics before handoff."""

STAGE_ID = "S7"
TITLE = "Assess Solution"
FRAGMENTS = ["FRAG_HANDOFF", "FRAG_TRACE", "FRAG_ASSESSMENT", "FRAG_OUTCOME",
             "FRAG_EVIDENCE"]

OUTPUT_CONTRACT = (
    "Solution Assessment: requirement→solution coverage per US/FR against the "
    "recommended OPT-n, solution gaps and requirement-solution mismatches, "
    "organisational readiness (people/process/technology/training with actions), "
    "transition/migration/training needs, business acceptance, and SM-n outcome "
    "metrics with baseline, target, measurement method and review point. "
    "Handoff: BA PACKAGE → Project Agent."
)

NEEDED_INFO = [
    "coverage", "solution_gaps", "readiness_people", "readiness_process",
    "readiness_technology", "readiness_training", "transition", "acceptance",
    "success_metrics",
]
