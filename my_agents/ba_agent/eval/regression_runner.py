"""Regression runner — deterministic checks a candidate must pass.

Runs (no LLM, no network):
1. shared.gates.ba_gate on a golden BRD state (prod fixture grade).
2. requirement_engine.validate_requirements minima.
3. Prompt anchor guard (PROMPT_ANCHORS for ba_agent).

Returns {passed, failures[]}. FAIL -> fix candidate; PASS -> human review.
"""

from __future__ import annotations

from typing import Any

BA_ANCHORS = ["Senior Business Analyst", "BUSINESS DISCOVERY OWNER",
              "5 Whys", "record_elicitation", "MoSCoW", "append_doc",
              "[ASSUMPTION:xxx]"]


def run(state: dict[str, Any], instruction_text: str) -> dict:
    """Run all regression checks. Pure/deterministic."""
    from ba_agent.managers.requirement_engine import validate_requirements
    from shared.gates import ba_gate
    from shared.traceability import doc_texts

    failures: list[str] = []
    gate = ba_gate(state)
    if not gate.get("passed"):
        failures.extend(gate.get("missing", []))
    brd = doc_texts(state).get("brd", "")
    req = validate_requirements(brd)
    if not req.get("ok"):
        failures.extend(req.get("missing", []))
    for a in BA_ANCHORS:
        if a not in (instruction_text or ""):
            failures.append(f"prompt anchor missing: {a!r}")
    return {"passed": not failures, "failures": failures}
