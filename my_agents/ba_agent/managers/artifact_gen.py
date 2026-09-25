"""Artifact generator — BRD 12-section contract + diagram bundle args.

Holds the canonical 12-section list and the corrected
build_diagram_bundle signature (diagram_name + title were missing from the
old prompt example). Actual file writes still go through
shared.eng_tools.save_doc/append_doc/build_diagram_bundle.
"""

from __future__ import annotations

BRD_SECTIONS = [
    "Executive Summary + Business Objectives",
    "Scope IN / OUT",
    "Stakeholders & RACI-lite",
    "AS-IS vs TO-BE",
    "Functional Scope / User Stories",
    "Business Rules",
    "Data Needs",
    "NFR Hints",
    "Assumptions / Constraints / Dependencies",
    "Risks + Mitigations",
    "Glossary + KPIs / Acceptance Metrics",
    "Open Questions",
]

DIAGRAM_DEFAULTS = {
    "diagram_name_prefix": "ba_flow_",
    "diagram_kind": "flow",
    "title_prefix": "Business Process Flow — ",
    "min_nodes": 6,
    "min_decisions": 1,
}


def diagram_params(project: str, mermaid: str) -> dict:
    """Build correct build_diagram_bundle kwargs for a BA flow diagram."""
    slug = "".join(c if c.isalnum() else "_" for c in project).strip("_") or "project"
    return {
        "diagram_name": f"{DIAGRAM_DEFAULTS['diagram_name_prefix']}{slug}",
        "diagram_kind": DIAGRAM_DEFAULTS["diagram_kind"],
        "mermaid": mermaid,
        "title": f"{DIAGRAM_DEFAULTS['title_prefix']}{slug}",
    }
