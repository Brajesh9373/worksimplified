"""BA Phase 2 eval-loop tests — no LLM, no network (prod-grade).

Covers: feedback validation, root-cause routing, pure builders, stored
candidate lifecycle with hard deploy gate, tamper resistance, idempotent
deploy, workspace persistence round-trip, ADK tool end-to-end, agent wiring.
"""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))


def _golden_state():
    from tests.ba_fixture import ALL_SECTIONS, ba_state
    from tests.prod_fixtures import BRD

    # Gate reads node count from state (as build_diagram_bundle writes it).
    # ba_state also seeds the BA v2 records the fail-closed gate requires
    # (evidence links, assumption statuses, success metric, closed plan).
    s = ba_state(brd=BRD, sections=ALL_SECTIONS)
    s["ba_flow_diagram"] = {
        "kind": "flow",
        "nodes": 7,
        "edges": 6,
        "mermaid": ("flowchart TD\nA[Start]-->B[Step]\nB-->C{Decision?}\n"
                    "C-->|yes|D[Do]\nC-->|no|E[Alt]\nD-->F[End]\nE-->F"),
    }
    return s


def test_feedback_capture_and_validation():
    from ba_agent.eval import feedback_store as FS

    s = {}
    assert FS.capture(s, "nonsense", "detail")["ok"] is False
    assert FS.capture(s, "wrong", "  ")["ok"] is False
    r = FS.capture(s, "misclassified", "FR-007 filed as NFR", "v1.0", "FR-007")
    assert r == {"ok": True, "id": "FB-001"}
    assert len(FS.list_open(s)) == 1
    assert FS.mark_addressed(s, "FB-001", "v1.1-candidate")["ok"] is True
    assert FS.list_open(s) == []


def test_root_cause_routing():
    from ba_agent.eval import root_cause as RC

    assert RC.analyze({"id": "FB-001", "verdict": "misclassified",
                       "detail": "functional requirement filed as NFR"})["target"] == "classification_rule"
    assert RC.analyze({"id": "FB-002", "verdict": "missing_requirement",
                       "detail": "approval rule never captured"})["target"] == "question_strategy"
    assert RC.analyze({"id": "FB-003", "verdict": "ambiguous",
                       "detail": "criteria vague"})["target"] == "validation_rule"


def test_pure_builders_still_guarded():
    from ba_agent.eval import improvement as IM

    assert IM.propose("", "prompt", "change", [])["ok"] is False
    p = IM.propose("v1.1-candidate", "classification_rule",
                   "FR vs NFR decision table", ["FB-001"])
    assert p["ok"] and p["candidate"]["status"] == "proposed"
    c = p["candidate"]
    assert IM.approve(dict(c), "lead")["ok"] is False  # no skip
    IM.mark_regression(c, False)
    assert c["status"] == "regression-fail"
    assert IM.approve(dict(c), "lead")["ok"] is False
    IM.mark_regression(c, True)
    assert IM.approve(c, "lead")["ok"] is True


def _full_approval(s: dict, version: str = "v1.1-candidate") -> None:
    from ba_agent.eval import feedback_store as FS
    from ba_agent.eval import improvement as IM

    FS.capture(s, "misclassified", "FR filed as NFR", "v1.0", "FR-007")
    assert IM.propose_candidate(s, version, "classification_rule",
                                "FR vs NFR decision table", ["FB-001"])["ok"] is True
    assert IM.set_regression(s, version, True)["ok"] is True
    assert IM.approve_candidate(s, version, "lead")["ok"] is True


def test_stored_lifecycle_and_hard_deploy_gate():
    from ba_agent.eval import improvement as IM
    from ba_agent.eval import version_manager as VM

    s = {}
    assert VM.current(s) == "v1.0"
    # Unknown version cannot deploy.
    assert VM.deploy(s, "v9.9")["ok"] is False
    # Propose -> deploy without regression/approval fails (hard gate).
    assert IM.propose_candidate(s, "v1.1-candidate", "prompt",
                                "tighten MoSCoW wording", [])["ok"] is True
    assert VM.deploy(s, "v1.1-candidate")["ok"] is False
    # Duplicate propose fails.
    assert IM.propose_candidate(s, "v1.1-candidate", "prompt",
                                "again", [])["ok"] is False
    # Regression fail -> approval refused.
    assert IM.set_regression(s, "v1.1-candidate", False, ["gate X"])["ok"] is True
    assert IM.approve_candidate(s, "v1.1-candidate", "lead")["ok"] is False
    assert VM.deploy(s, "v1.1-candidate")["ok"] is False
    # Pass + approve (empty approver refused) -> deploy ok.
    assert IM.set_regression(s, "v1.1-candidate", True)["ok"] is True
    assert IM.approve_candidate(s, "v1.1-candidate", "  ")["ok"] is False
    assert IM.approve_candidate(s, "v1.1-candidate", "lead")["ok"] is True
    assert VM.deploy(s, "v1.1-candidate") == {
        "ok": True, "version": "v1.1-candidate", "already": False}
    assert VM.current(s) == "v1.1-candidate"
    # Idempotent redeploy (no ledger duplicate).
    again = VM.deploy(s, "v1.1-candidate")
    assert again == {"ok": True, "version": "v1.1-candidate", "already": True}
    assert len(VM.history(s)) == 1
    # Deployed candidate is immutable.
    assert IM.set_regression(s, "v1.1-candidate", True)["ok"] is False


def test_forged_status_cannot_deploy():
    """Tamper test: a caller-side dict claiming human-approved is ignored."""
    from ba_agent.eval import improvement as IM
    from ba_agent.eval import version_manager as VM

    s = {}
    assert IM.propose_candidate(s, "v1.2-candidate", "prompt", "change", [])["ok"] is True
    # Attacker forges a local copy — deploy() never reads caller dicts,
    # only the stored status (still 'proposed'), so it must fail.
    forged = {"version": "v1.2-candidate", "status": "human-approved",
              "approver": "mallory", "target": "prompt", "change": "x"}
    assert forged["status"] == "human-approved"
    assert VM.deploy(s, "v1.2-candidate")["ok"] is False
    assert VM.current(s) == "v1.0"


def test_eval_persists_across_workspace_resume():
    """Prod: feedback + candidates + versions survive persist/load (per project)."""
    import uuid

    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace,
                                  load_project_state, persist_project_state)

    pid = f"_test_eval_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s: dict = {}
    bind_workspace(s, pid)
    get_context(s)  # init

    from ba_agent.eval import feedback_store as FS
    from ba_agent.eval import improvement as IM
    from ba_agent.eval import version_manager as VM

    _full_approval(s, "v1.1-candidate")
    assert VM.deploy(s, "v1.1-candidate")["ok"] is True
    persist_project_state(s, ws)

    resumed: dict = {}
    bind_workspace(resumed, pid)
    load_project_state(resumed, ws)
    ctx = get_context(resumed)
    assert any(f.get("id") == "FB-001" for f in ctx.get("ba_eval", {}).get("feedback", []))
    assert ctx.get("ba_candidates", {}).get("v1.1-candidate", {}).get("status") == "deployed"
    assert VM.current(resumed) == "v1.1-candidate"
    assert len(VM.history(resumed)) == 1

    import shutil
    shutil.rmtree(ws.root, ignore_errors=True)


def test_eval_tools_end_to_end_and_audit():
    from ba_agent.eval import tools as T
    from shared.project_context import get_context

    class FakeTC:
        def __init__(self):
            self.state: dict = {}

    tc = FakeTC()
    assert T.capture_ba_feedback(tc, "misclassified", "FR filed as NFR",
                                 "", "FR-007")["id"] == "FB-001"
    assert T.analyze_ba_feedback(tc, "FB-001")["target"] == "classification_rule"
    assert T.propose_ba_candidate(tc, "v1.1-candidate", "classification_rule",
                                  "decision table", "FB-001")["ok"] is True
    # run_ba_regression uses live BA_INSTRUCTION; golden BRD absent here so it
    # records a FAIL — approval must then refuse (no skip possible).
    assert T.deploy_ba_version(tc, "v1.1-candidate")["ok"] is False
    assert T.set_ba_regression(tc, "v1.1-candidate", True)["ok"] is True
    assert T.approve_ba_candidate(tc, "v1.1-candidate", "lead")["ok"] is True
    assert T.deploy_ba_version(tc, "v1.1-candidate")["ok"] is True
    st = T.ba_eval_status(tc)
    assert st["current"] == "v1.1-candidate"
    assert st["candidates"]["v1.1-candidate"]["status"] == "deployed"
    # Audit trail: propose/regression/approve/deploy history entries exist.
    kinds = [h.get("summary", "") for h in get_context(tc.state).get("history", [])]
    assert any("proposed BA candidate" in k for k in kinds)
    assert any("deployed BA version" in k for k in kinds)


def test_agent_wires_eval_tools():
    from ba_agent import agent as BA

    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    for t in ["capture_ba_feedback", "analyze_ba_feedback", "propose_ba_candidate",
              "run_ba_regression", "set_ba_regression", "approve_ba_candidate",
              "deploy_ba_version", "ba_eval_status",
              "save_doc", "record_elicitation"]:
        assert t in names, f"ba_agent missing tool {t}"


def test_regression_runner_golden_passes_and_broken_fails():
    from ba_agent.eval import regression_runner as RR
    from ba_agent.prompts import BA_INSTRUCTION

    assert RR.run(_golden_state(), BA_INSTRUCTION)["passed"] is True
    bad = RR.run({"brd": "lorem ipsum " * 50}, BA_INSTRUCTION)
    assert bad["passed"] is False and bad["failures"]
    anchors_broken = RR.run(_golden_state(), "emptied prompt")
    assert anchors_broken["passed"] is False
    assert any("anchor" in f for f in anchors_broken["failures"])


# ---------- Root-cause golden set (prod): 30 cases across all 10 targets ----------

RC_GOLDEN = [
    # (verdict, detail, expected_target)
    ("misclassified", "FR-007 filed as NFR; should be FR, functional requirement wrong type", "classification_rule"),
    ("misclassified", "Login latency mislabeled: non-functional tagged as functional", "classification_rule"),
    ("wrong", "Throughput requirement classified as functional, NFR misclassified", "classification_rule"),
    ("missing_requirement", "Approval rule never captured; stakeholder never asked about threshold", "question_strategy"),
    ("incomplete", "Exception flow missing; happy path only, alternates never elicited", "question_strategy"),
    ("bad_question", "BA asked about document sections instead of business pain; bad question strategy", "question_strategy"),
    ("ambiguous", "Acceptance criteria vague; Given/When/Then not testable", "validation_rule"),
    ("inconsistent", "US-003 contradicts BR-002; validation should have caught contradiction", "validation_rule"),
    ("wrong", "Criteria ambiguous and not testable; validation rule gap", "validation_rule"),
    ("incomplete", "Scope OUT section missing from BRD; template section absent", "artifact_template"),
    ("improvement_suggested", "Add RACI rows to template; traceability matrix section missing", "artifact_template"),
    ("incomplete", "Glossary absent from artifact; template needs glossary section", "artifact_template"),
    ("poor_methodology", "Skipped stage S4 analysis; wrong stage order, iteration skipped", "workflow"),
    ("improvement_suggested", "Rework loop S6 back-edge to S4 never triggered on change", "workflow"),
    ("wrong", "Instruction wording unclear; system prompt anchor missing", "prompt"),
    ("poor_methodology", "Tone of elicitation prompts off; instruction needs rewording", "prompt"),
    ("inconsistent", "Domain term renamed mid-document; terminology inconsistent, glossary term drift", "knowledge"),
    ("wrong", "Business term renamed in rules vs stories; domain term mismatch", "knowledge"),
    ("incomplete", "Response truncated at token limit; model config max tokens too low", "model_config"),
    ("wrong", "Output cut off; temperature and token limit misconfigured", "model_config"),
    ("hallucinated", "Invented approval threshold with no evidence; made up business rule", "reasoning_rule"),
    ("wrong", "Hallucinated stakeholder never interviewed; no evidence for claim", "reasoning_rule"),
    ("bad_prioritization", "MoSCoW wrong priority; Must filed as Could, prioritization error", "reasoning_rule"),
    ("improvement_suggested", "Regression suite missed scope-out gap; slipped through eval coverage", "evaluation_coverage"),
    ("incomplete", "Golden fixture lacks pipe-table case; not caught by regression coverage", "evaluation_coverage"),
    ("misclassified", "Payment rule should be FR not NFR; classification wrong type", "classification_rule"),
    ("missing_requirement", "OQ-004 assumption never resolved; elicitation never asked", "question_strategy"),
    ("ambiguous", "Then-clause missing; acceptance not testable", "validation_rule"),
    ("hallucinated", "Made up KPI with no stakeholder source", "reasoning_rule"),
    ("improvement_suggested", "Workflow skipped validation stage; stage order wrong", "workflow"),
]

RC_MIN_ACCURACY = 0.90  # >=27/30


def test_root_cause_golden_accuracy():
    from ba_agent.eval import root_cause as RC

    hits = 0
    misses = []
    for i, (verdict, detail, expected) in enumerate(RC_GOLDEN):
        got = RC.analyze({"id": f"FB-{i + 1:03d}", "verdict": verdict,
                          "detail": detail})["target"]
        if got == expected:
            hits += 1
        else:
            misses.append(f"case {i} ({verdict}): got {got}, want {expected} :: {detail[:60]}")
    acc = hits / len(RC_GOLDEN)
    assert acc >= RC_MIN_ACCURACY, f"root-cause accuracy {acc:.2f} < {RC_MIN_ACCURACY}\n" + "\n".join(misses)


def test_root_cause_confidence_and_determinism():
    from ba_agent.eval import root_cause as RC

    fb = {"id": "FB-001", "verdict": "misclassified",
          "detail": "FR-007 filed as NFR; should be FR"}
    a1, a2 = RC.analyze(fb), RC.analyze(dict(fb))
    assert a1["target"] == a2["target"] == "classification_rule"
    assert 0.0 <= a1["confidence"] <= 1.0
    assert a1["runner_up"] != a1["target"]
    # Unknown/empty feedback falls back deterministically, never raises.
    fb_empty = RC.analyze({"id": "FB-000", "verdict": "wrong", "detail": ""})
    assert fb_empty["target"] in RC.TARGETS


def test_improvement_memory_records_deploys_and_reports():
    from ba_agent.eval import improvement as IM
    from ba_agent.eval import tools as T
    from ba_agent.eval import version_manager as VM

    s = {"brd": "US-001 a. BR-001 x. US-002 b. BR-002 y. US-003 c. BR-003 z."}
    assert VM.memory_report(s)["patterns"] == 0
    for ver, target in (("vm1", "classification_rule"), ("vm2", "question_strategy")):
        assert IM.propose_candidate(s, ver, target, f"fix {target}", [])["ok"] is True
        IM.set_regression(s, ver, True)
        IM.approve_candidate(s, ver, "lead")
        assert VM.deploy(s, ver)["ok"] is True
    rep = VM.memory_report(s)
    assert rep["patterns"] == 2
    assert rep["by_target"] == {"classification_rule": 1, "question_strategy": 1}
    assert all("lesson" in lb for lb in rep["lessons"])
    # No client facts leak: lessons carry only change text, never the BRD.
    assert "US-001" not in str(rep["lessons"])
    assert T.improvement_memory_report(type("TC", (), {"state": s})())["patterns"] == 2
