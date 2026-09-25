"""BA Phase 1 scaffold tests — no LLM, no network (mirrors my_agents/tests style)."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))


def test_prompt_anchors_preserved():
    from ba_agent.prompts import BA_INSTRUCTION

    for a in ["Senior Business Analyst", "BUSINESS DISCOVERY OWNER",
              "5 Whys", "record_elicitation", "MoSCoW", "append_doc",
              "[ASSUMPTION:xxx]", "flowchart TD"]:
        assert a in BA_INSTRUCTION, f"anchor missing: {a}"
    # Diagram signature fix must be present.
    assert "diagram_name=" in BA_INSTRUCTION
    assert "title=" in BA_INSTRUCTION


def test_agent_instruction_matches_prompts_and_tools_stable():
    from ba_agent import agent as BA

    assert BA.root_agent.instruction == BA.BA_INSTRUCTION
    assert BA.root_agent.name == "ba_agent"
    assert BA.root_agent.output_key == "brd"
    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    for t in ["save_doc", "append_doc", "build_diagram_bundle", "list_outputs",
              "get_stage_brief", "read_output_file", "get_project_status",
              "create_change_request", "record_elicitation", "record_history_event"]:
        assert t in names


def test_stage_contracts():
    from ba_agent.stages import (s1_understand, s2_analyze_biz, s3_elicit,
                                 s4_requirements, s5_validate, s6_manage, s7_assess)

    ids = [s1_understand.STAGE_ID, s2_analyze_biz.STAGE_ID, s3_elicit.STAGE_ID,
           s4_requirements.STAGE_ID, s5_validate.STAGE_ID,
           s6_manage.STAGE_ID, s7_assess.STAGE_ID]
    assert ids == ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
    assert s1_understand.missing_info({})  # all missing on empty
    assert s1_understand.missing_info({"problem": "x", "goals": "g",
                                       "objectives": "o", "business_context": "c",
                                       "current_situation": "s", "desired_outcome": "d",
                                       "stakeholders": "s", "scope": "s"}) == []
    assert s6_manage.REWORK_LOOP == ("S6", "S4", "S5")


def test_orchestrator_decisions():
    from ba_agent import orchestrator as O

    assert O.next_step("S3", missing=["approval process"])["action"] == "ask_human"
    assert O.next_step("S3", missing=[], confidence=0.9)["stage"] == "S4"
    r = O.next_step("S6", missing=[], confidence=1.0, change_detected=True)
    assert (r["action"], r["stage"]) == ("rework", "S4")
    assert O.next_step("S7")["stage"] == "S7"


def test_question_manager_no_repeat_and_budget():
    from ba_agent.managers import question_manager as QM
    from shared.project_context import get_context

    s = {}
    get_context(s)["elicitation"] = {"log": [{"q": "Who approves?", "a": "Mgr", "source": "user"}]}
    out = QM.filter_unasked(["Who approves?", "Volumes?", "  volumes?  ",
                             "Q3", "Q4", "Q5", "Q6", "Q7"], s)
    assert "Who approves?" not in out
    assert len(out) <= QM.MAX_PER_TURN
    assert QM.needs_human(["x"], 1.0) is True
    assert QM.needs_human([], 0.9) is False
    assert "[ASSUMPTION:" in QM.assumption_tag("threshold is 50k")


def test_requirement_engine_minima():
    from ba_agent.managers import requirement_engine as RE

    good = ""
    for i in (1, 2, 3):
        good += f"\nUS-00{i}: story {i}\nGiven x When y Then z\nGiven a When b Then c\n"
    for i in (1, 2, 3):
        good += f"\nBR-00{i}: rule {i}\n"
    r = RE.validate_requirements(good)
    assert r["ok"] and len(r["us"]) == 3 and len(r["br"]) == 3
    bad = RE.validate_requirements("US-001: one\nGiven x When y Then z\nBR-001: r")
    assert not bad["ok"] and bad["missing"]


def test_artifact_gen_contract():
    from ba_agent.managers import artifact_gen as AG

    assert len(AG.BRD_SECTIONS) == 12
    p = AG.diagram_params("Travel Expense", "flowchart TD\nA-->B")
    assert p["diagram_name"].startswith("ba_flow_")
    assert p["diagram_kind"] == "flow" and "title" in p and "mermaid" in p


def test_state_and_decision_managers():
    from ba_agent.managers import decision_manager as DM
    from ba_agent.managers import state_manager as SM

    s = {}
    assert SM.is_new_project(s) is True
    assert set(SM.view(s, "context")) >= {"project", "business", "stakeholders"}
    assert DM.record(s, "approval_threshold", "50k") == {"ok": True, "key": "approval_threshold"}
    assert DM.all_decisions(s)["approval_threshold"] == "50k"


def test_trace_engine_wraps_shared():
    from ba_agent.managers import trace_engine as TE

    s = {"brd": "US-001 BR-001", "functional_spec": "FR-001 US-001 BR-001"}
    cov = TE.coverage(s)
    assert cov["counts"]["US"] >= 1
    ch = TE.chain(s, "US-001")
    assert ch["seed"] == "US-001" and "BR" in ch["chain"]


# ---------- AC counter golden set (prod): every format must count exactly ----------

AC_GOLDEN = [
    # (name, block, expected_count)
    ("gwt_two_prose",
     "Given a logged-in employee When the request is complete Then it is saved.\n"
     "Given missing fields When submitted Then errors shown.", 2),
    ("gwt_bullets",
     "- Given a pending request When the manager approves Then state becomes Approved.\n"
     "- Given a pending request When the manager rejects Then state becomes Rejected.", 2),
    ("ac_codes",
     "US-001 story\n- AC-001 first criterion text\n- AC-002 second criterion text", 2),
    ("ac_table",
     "US-001 story\n| ID | Criterion |\n|----|-----------|\n"
     "| AC-001 | Given x Then y |\n| AC-002 | Given a Then b |\n| AC-003 | Given m Then n |", 3),
    ("gwt_table",
     "US-001 story\n| Given | When | Then |\n|---|---|---|\n"
     "| logged in | submit | saved |\n| guest | submit | rejected |", 2),
    ("numbered_acceptance",
     "US-001 story\nAcceptance criteria:\n1. Request saved as Pending Approval.\n"
     "2. Employee notified by email.", 2),
    ("single_criterion",
     "US-001 story\nGiven a logged-in employee When the request is complete Then it is saved.", 1),
    ("no_criteria",
     "US-001 story with no acceptance text at all, just narrative.", 0),
    ("mixed_ac_and_gwt",
     "US-001 story\n- AC-001 Given x When y Then z\n- AC-002 Given a When b Then c\n"
     "Note: extra context line without criteria.", 2),
    ("br_section_not_leaking",
     "US-001 story\nGiven x When y Then z\nGiven a When b Then c\n"
     "## Business Rules\nBR-001 must hold validated via separate process.", 2),
    ("scenario_headers",
     "US-001 story\nScenario: approve\nGiven pending When approve Then approved.\n"
     "Scenario: reject\nGiven pending When reject Then rejected.", 2),
    ("acceptance_with_numbers_no_cue_filtered",
     "US-001 story\n1. First stray numbered line.\n2. Second stray line.", 0),
]


def test_ac_counter_golden_exact():
    from ba_agent.managers.requirement_engine import count_criteria

    for name, block, expected in AC_GOLDEN:
        got = count_criteria(block)["count"]
        assert got == expected, f"{name}: got {got}, want {expected}"


def test_ac_counter_golden_accuracy_100():
    from ba_agent.managers.requirement_engine import count_criteria

    hits = sum(1 for _, b, e in AC_GOLDEN if count_criteria(b)["count"] == e)
    acc = hits / len(AC_GOLDEN)
    assert acc == 1.0, f"AC golden accuracy {acc:.2f} ({hits}/{len(AC_GOLDEN)})"


def test_validate_end_to_end_mixed_formats():
    from ba_agent.managers.requirement_engine import validate_requirements

    doc = (
        "US-001 approve requests\n- AC-001 Given pending When approve Then approved.\n"
        "- AC-002 Given pending When reject Then rejected.\n"
        "US-002 reimburse claims\n| ID | Criterion |\n|----|-----------|\n"
        "| AC-001 | claim paid |\n| AC-002 | duplicate rejected |\n"
        "US-003 dashboards\nAcceptance criteria:\n1. Spend visible.\n2. Export works.\n"
        "BR-001 approval required.\nBR-002 receipts required.\nBR-003 duplicates rejected.\n"
    )
    r = validate_requirements(doc)
    assert r["ok"] is True and r["per_story_counts"] == [2, 2, 2]
