"""BA Agent — Senior Business Analyst (org + field).

Real-world duties: stakeholder elicitation, AS-IS/TO-BE mapping, scope control,
BRD with user stories + acceptance criteria, business rules, RAID starter,
glossary/KPIs. Main output: BRD markdown + figma-like Flow Diagram bundle.

Phase 1 decomposition: instruction lives in prompts.py (split per 7-stage
engine fragments, assembled byte-identical to the locked prompt except the
build_diagram_bundle signature fix); stage contracts in stages/; state,
questions, requirements, trace, artifacts, decisions in managers/;
internal stage selection in orchestrator.py. The external delivery pipeline,
PROJECT_CONTEXT schema, gates, and tool set are untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.append(str(MY_AGENTS))

from google.adk import Agent  # noqa: E402
from google.genai import types  # noqa: E402

from shared.eng_tools import append_doc, build_diagram_bundle, list_outputs, save_doc, upload_document  # noqa: E402
from shared.meta_model import get_meta_model  # noqa: E402
from shared.orch_tools import (  # noqa: E402  (orchestration infra; prompt below untouched)
    create_change_request,
    get_project_status,
    get_stage_brief,
    read_output_file,
    record_elicitation,
    record_history_event,
)

from .eval.tools import (  # noqa: E402  (BA eval loop + package + HITL + ops)
    analyze_ba_feedback,
    approve_ba_candidate,
    assemble_ba_package,
    ba_eval_status,
    ba_ops_snapshot,
    build_audience_views,
    capture_ba_feedback,
    deploy_ba_version,
    field_accuracy_report,
    hitl_status,
    improvement_memory_report,
    log_stakeholder_answer,
    propose_ba_candidate,
    propose_question_batch,
    record_approval_decision,
    record_field_label,
    request_ba_approval,
    reuse_report_tool,
    run_ba_regression,
    sampling_review_queue,
    set_ba_regression,
)
from .ba_tools import (  # noqa: E402  (BA v2 + conversation: knowledge, plan, evidence…)
    analyze_change_impact,
    ask_user,
    knowledge_status,
    propose_generation,
    record_knowledge,
    ba_plan_status,
    evidence_report,
    impact_report,
    link_evidence,
    outcome_report,
    quality_report,
    record_assumption,
    record_ba_plan,
    record_decision,
    record_outcome_measurement,
    record_success_metric,
    resolve_contradiction,
    set_assumption_status,
    update_ba_plan_item,
)
from .prompts import BA_INSTRUCTION  # noqa: E402  (single source of truth)

# Re-export submodule API so team code can use ba_agent.<module> directly.
from . import ba_tools, eval, managers, orchestrator, prompts, stages  # noqa: E402,F401

root_agent = Agent(
    name="ba_agent",
    model=get_meta_model("ba"),
    description="Senior BA — elicits requirements, writes BRD, delivers figma-like flow diagram bundle.",
    instruction=BA_INSTRUCTION,
    tools=[save_doc, append_doc, build_diagram_bundle, list_outputs, upload_document,
           get_stage_brief, read_output_file, get_project_status,
           create_change_request, record_elicitation, record_history_event,
           capture_ba_feedback, analyze_ba_feedback, propose_ba_candidate,
           run_ba_regression, set_ba_regression, approve_ba_candidate,
           deploy_ba_version, ba_eval_status, assemble_ba_package,
           propose_question_batch, log_stakeholder_answer,
           request_ba_approval, record_approval_decision, hitl_status,
           ba_ops_snapshot, sampling_review_queue, record_field_label,
           field_accuracy_report, build_audience_views, reuse_report_tool,
           improvement_memory_report,
           # BA v2 — stage 2 / stage 7 artefacts, controlled requirements, monitoring
           record_decision, link_evidence, evidence_report,
           record_assumption, set_assumption_status,
           record_ba_plan, update_ba_plan_item, ba_plan_status,
           quality_report, resolve_contradiction,
           analyze_change_impact, impact_report,
           record_success_metric, record_outcome_measurement, outcome_report,
           # conversation / knowledge feeding
           record_knowledge, knowledge_status, ask_user, propose_generation],
    output_key="brd",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.3,
        # No output cap on purpose: a long section serialised into an append_doc
        # tool call was cut off at the old 16k limit and discarded as unparseable.
        # The provider's own ceiling is higher, so leaving it unset is the safe default.
    ),
)
