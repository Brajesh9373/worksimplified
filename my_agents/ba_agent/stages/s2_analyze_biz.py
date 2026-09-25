"""S2 — Analyze Business. Need/capability gap/impact/options/feasibility/case."""

STAGE_ID = "S2"
TITLE = "Analyze Business"
FRAGMENTS = ["FRAG_ANALYSIS", "FRAG_CONTEXT", "FRAG_BUSINESS_CASE"]

OUTPUT_CONTRACT = (
    "Business Analysis / Solution Context: business need, current capability, "
    "capability gap, business impact, solution objectives, OPT-n options with "
    "pro/con and feasibility, quantified benefits, business case, explicit risk "
    "of doing nothing, decision factors, solution scope, risks, constraints."
)

NEEDED_INFO = [
    "business_need", "current_capability", "capability_gap",
    "business_impact", "solution_objectives", "options", "feasibility",
    "benefits", "risk_of_inaction", "decision_factors", "solution_scope", "risks",
]


def missing_info(known: dict) -> list[str]:
    return [k for k in NEEDED_INFO if not known.get(k)]
