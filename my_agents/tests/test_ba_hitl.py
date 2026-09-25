"""HITL collaboration tests — batches, answers, assumptions, approvals, brief."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))


def test_batch_budget_and_topics():
    from ba_agent.managers.question_manager import build_batch

    s = {}
    r = build_batch(s, "S1", [f"Q{i}?" for i in range(8)])
    assert r["ok"] is True
    assert r["batch"]["id"] == "QB-001" and len(r["batch"]["questions"]) == 5
    assert all(q["topic"] in ("problem", "goals", "initial scope", "stakeholders")
               for q in r["batch"]["questions"])


def test_batch_never_repeats_log_or_pending():
    from ba_agent.managers.question_manager import build_batch
    from shared.project_context import get_context

    s = {}
    get_context(s)["elicitation"] = {"log": [{"q": "Volumes?", "a": "200/mo",
                                              "source": "user"}]}
    r = build_batch(s, "S2", ["Volumes?", "Owners?", "Volumes? "])
    assert r["ok"] is True
    assert [q["text"] for q in r["batch"]["questions"]] == ["Owners?"]
    # Second batch with only the pending question -> refused.
    assert build_batch(s, "S2", ["Owners?"])["ok"] is False
    assert build_batch(s, "S2", [])["ok"] is False


def test_answer_flow_completes_batch_and_feeds_log():
    from ba_agent.managers.question_manager import (answer, build_batch,
                                                    open_questions)
    from shared.project_context import get_context

    s = {}
    qids = [q["qid"] for q in build_batch(s, "S3", ["Happy path?", "Exceptions?"])["batch"]["questions"]]
    assert answer(s, "QB-999-Q1", "x")["ok"] is False  # unknown
    assert answer(s, qids[0], "  ")["ok"] is False  # empty
    assert answer(s, qids[0], "Order to cash")["ok"] is True
    assert answer(s, qids[0], "again")["ok"] is False  # double-answer refused
    assert len(open_questions(s)) == 1
    log = get_context(s)["elicitation"]["log"]
    assert log[-1] == {"id": "EL-001", "qid": qids[0], "q": "Happy path?",
                        "a": "Order to cash", "source": "user"}
    assert answer(s, qids[1], "None")["ok"] is True
    assert open_questions(s) == []
    assert get_context(s)["ba_question_batches"][0]["status"] == "complete"


def test_assumption_gaps_pairing():
    from ba_agent.managers.question_manager import assumption_gaps

    assert assumption_gaps("") == {"tags": [], "oqs": [], "unmirrored": []}
    brd = "Threshold 50k. [ASSUMPTION:threshold] See OQ-001. Also [ASSUMPTION:pending] noted."
    g = assumption_gaps(brd, {"OQ-001": "confirm threshold"})
    assert g["tags"] == ["pending", "threshold"] and g["oqs"] == ["001"]
    assert g["unmirrored"] == ["threshold"]  # pending covered by OQ-001


def test_approval_lifecycle_and_double_decide():
    from ba_agent.managers import decision_manager as DM
    from shared.project_context import get_context

    s = {}
    assert DM.request_approval(s, "  ")["ok"] is False
    ap = DM.request_approval(s, "Deploy v1.1", "candidate passed regression")["approval"]
    assert ap["id"] == "AP-001" and ap["status"] == "pending"
    assert DM.decide_approval(s, ap["id"], "maybe", "lead")["ok"] is False
    assert DM.decide_approval(s, ap["id"], "approve", "  ")["ok"] is False
    assert DM.decide_approval(s, "AP-999", "approve", "lead")["ok"] is False
    assert DM.decide_approval(s, ap["id"], "approve", "lead")["approval"]["status"] == "approved"
    assert DM.decide_approval(s, ap["id"], "reject", "lead2")["ok"] is False  # immutable
    assert DM.pending_approvals(s) == []
    hist = get_context(s)["history"]
    assert any(h.get("ref") == ap["id"] for h in hist)  # audited


def test_pending_fragment_and_never_raises():
    from ba_agent.managers.question_manager import build_batch, pending_fragment
    from ba_agent.managers import decision_manager as DM

    assert pending_fragment({}) == ""
    s = {}
    build_batch(s, "S1", ["Who approves?"])
    DM.request_approval(s, "Go live")
    frag = pending_fragment(s)
    assert "QB-001-Q1" in frag and "Who approves?" in frag
    assert "AP-001" in frag and "Go live" in frag


def test_hitl_keys_persist_across_resume():
    import shutil
    import uuid

    from ba_agent.managers import decision_manager as DM
    from ba_agent.managers.question_manager import answer, build_batch
    from shared.project_context import get_context
    from shared.workspace import (ProjectWorkspace, bind_workspace,
                                  load_project_state, persist_project_state)

    pid = f"_test_hitl_{uuid.uuid4().hex[:8]}"
    ws = ProjectWorkspace(pid)
    s: dict = {}
    bind_workspace(s, pid)
    qid = build_batch(s, "S2", ["Volumes?"])["batch"]["questions"][0]["qid"]
    answer(s, qid, "200/mo")
    DM.request_approval(s, "Pilot sign-off")
    persist_project_state(s, ws)

    resumed: dict = {}
    bind_workspace(resumed, pid)
    load_project_state(resumed, ws)
    ctx = get_context(resumed)
    assert ctx["ba_question_batches"][0]["status"] == "complete"
    assert ctx["ba_approvals"][0]["subject"] == "Pilot sign-off"
    assert ctx["elicitation"]["log"][-1]["a"] == "200/mo"
    shutil.rmtree(ws.root, ignore_errors=True)


def test_hitl_tools_end_to_end_and_agent_wiring():
    from ba_agent import agent as BA
    from ba_agent.eval import tools as T

    class FakeTC:
        def __init__(self):
            self.state: dict = {}

    tc = FakeTC()
    b = T.propose_question_batch(tc, "S1", "Who approves?; Volumes?")
    assert b["ok"] is True and len(b["batch"]["questions"]) == 2
    qid = b["batch"]["questions"][0]["qid"]
    assert T.log_stakeholder_answer(tc, qid, "CFO")["ok"] is True
    ap = T.request_ba_approval(tc, "Pilot", "ready")["approval"]
    assert T.record_approval_decision(tc, ap["id"], "approve", "sponsor")["ok"] is True
    st = T.hitl_status(tc)
    assert st["ok"] is True and len(st["open_questions"]) == 1
    assert st["pending_approvals"] == []
    names = sorted(getattr(t, "__name__", str(t)) for t in BA.root_agent.tools)
    for tool in ("propose_question_batch", "log_stakeholder_answer",
                 "request_ba_approval", "record_approval_decision", "hitl_status"):
        assert tool in names


def test_hooks_carry_stage_and_pending():
    import pathlib

    src = pathlib.Path(MY_AGENTS, "shared", "orch_nodes.py").read_text()
    # Single engine entry point carries stage progress + STAGE TASK + pending.
    assert "brief_extras" in src
    engine = pathlib.Path(MY_AGENTS, "ba_agent", "stage_engine.py").read_text()
    assert "pending_fragment" in engine and "auto_batch" in engine
