"""BA ops tests — snapshot, sampling harness, tools, wiring, runbook."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))


def test_snapshot_steady_and_suggests_action():
    from ba_agent.ops import ops_snapshot

    s = ops_snapshot({})
    assert s["ok"] is True and s["stage"]["current"] == "S1"
    assert s["version"]["current"] == "v1.0"
    assert isinstance(s["suggested_action"], str) and s["suggested_action"]

    s2: dict = {}
    from ba_agent.managers import decision_manager as DM

    DM.request_approval(s2, "Go live")
    snap = ops_snapshot(s2)
    assert snap["hitl"]["pending_approvals"] == 1
    assert "AP-001" in snap["suggested_action"] or "approval" in snap["suggested_action"]


def test_snapshot_never_raises_and_marks_unavailable():
    from ba_agent.ops import ops_snapshot

    s = ops_snapshot({"brd": None, "project_context": None})
    assert s["ok"] is True  # degraded, not dead
    assert s["stage"]["current"] == "S1"


def test_sampling_queue_labels_accuracy():
    from ba_agent.eval import sampling as SM
    from ba_agent.eval.feedback_store import capture

    s: dict = {}
    assert SM.review_queue(s) == {"ok": True, "queue": [], "unlabeled": 0}
    assert SM.field_accuracy(s) == {"ok": True, "labeled": 0, "correct": 0,
                                    "accuracy": None}
    capture(s, "misclassified", "FR-007 filed as NFR", "v1.0", "FR-007")
    capture(s, "wrong", "vague SNAPSHOT wording in the system prompt", "v1.0", "")
    q = SM.review_queue(s)
    assert q["unlabeled"] == 2 and len(q["queue"]) == 2
    # Low-confidence first: 'wrong'+vague wording scores weakly.
    assert q["queue"][0]["confidence"] <= q["queue"][1]["confidence"]

    assert SM.record_label(s, "FB-001", "nope", "lead")["ok"] is False
    assert SM.record_label(s, "FB-999", "prompt", "lead")["ok"] is False
    assert SM.record_label(s, "FB-001", "classification_rule", "")["ok"] is False
    r = SM.record_label(s, "FB-001", "classification_rule", "lead")
    assert r == {"ok": True, "updated": False, "correct": True}
    assert SM.record_label(s, "FB-001", "prompt", "lead2")["correct"] is False  # relabel
    acc = SM.field_accuracy(s)
    assert acc["labeled"] == 1 and acc["accuracy"] == 0.0
    rep = SM.report(s)
    assert rep == {"ok": True, "unlabeled": 1, "labeled": 1, "field_accuracy": 0.0}


def test_sampling_labels_persist_across_resume():
    import shutil
    import uuid

    from ba_agent.eval.feedback_store import capture
    from ba_agent.eval.sampling import field_accuracy, record_label
    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace,
                                  load_project_state, persist_project_state)

    pid = f"_test_sampling_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s: dict = {}
    bind_workspace(s, pid)
    capture(s, "misclassified", "FR filed as NFR", "v1.0", "")
    record_label(s, "FB-001", "classification_rule", "lead")
    persist_project_state(s, ws)

    resumed: dict = {}
    bind_workspace(resumed, pid)
    load_project_state(resumed, ws)
    assert get_context(resumed)["ba_field_labels"][0]["feedback_id"] == "FB-001"
    assert field_accuracy(resumed)["accuracy"] == 1.0
    shutil.rmtree(ws.root, ignore_errors=True)


def test_ops_tools_and_agent_wiring():
    from ba_agent import agent as BA
    from ba_agent.eval import tools as T

    class FakeTC:
        def __init__(self):
            self.state: dict = {}

    tc = FakeTC()
    assert T.ba_ops_snapshot(tc)["ok"] is True
    assert T.sampling_review_queue(tc)["queue"] == []
    T.capture_ba_feedback(tc, "ambiguous", "criteria vague", "", "")
    q = T.sampling_review_queue(tc)
    assert q["unlabeled"] == 1
    assert T.record_field_label(tc, q["queue"][0]["feedback_id"],
                                "validation_rule", "lead")["ok"] is True
    rep = T.field_accuracy_report(tc)
    assert rep["labeled"] == 1 and rep["field_accuracy"] == 1.0
    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    for tool in ("ba_ops_snapshot", "sampling_review_queue",
                 "record_field_label", "field_accuracy_report"):
        assert tool in names


def test_runbook_exists_and_covers_ops():
    p = Path(MY_AGENTS, "ba_agent", "RUNBOOK.md")
    text = p.read_text()
    for section in ("STAGE TASK", "WAITING_FOR_HUMAN", "deploy_ba_version",
                    "field_accuracy", "ba_ops_snapshot", "pytest my_agents/tests/"):
        assert section in text, f"runbook missing: {section}"
