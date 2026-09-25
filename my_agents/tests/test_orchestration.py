"""Orchestration tests: context, gates, CRs, traceability, handoffs.

No LLM calls. Real ShopFloor _outputs/ docs are used where available.
Prompts-untouched guard asserts original instruction anchors are intact.
"""

import re
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from shared import change_requests as CR  # noqa: E402
from shared import gates  # noqa: E402
from shared import project_context as PC  # noqa: E402
from shared import traceability as TR  # noqa: E402

OUT = MY_AGENTS / "_outputs"
OLD_WS_ARTIFACTS = MY_AGENTS / "projects" / "shopfloor_o2c_20260917" / "artifacts"


def _doc(name_part):
    # Docs now live in the migrated project workspace (never global search).
    cands = [p for p in OLD_WS_ARTIFACTS.glob(f"*{name_part}*.md") if p.stat().st_size > 200]
    assert cands, f"missing fixture doc *{name_part}*.md in shopfloor workspace"
    return max(cands, key=lambda p: p.stat().st_size).read_text(encoding="utf-8")


# ---------- project_context ----------

def test_context_init_and_sections():
    s = {}
    ctx = PC.get_context(s)
    assert set(PC.SECTIONS) <= set(ctx)
    assert ctx["execution"]["workflow_status"] == "NOT_STARTED"
    PC.update_section(s, "risks", {"R-01": "erp api unknown"})
    assert PC.get_section(s, "risks") == {"R-01": "erp api unknown"}
    with pytest.raises(ValueError):
        PC.set_section(s, "nope", {})


def test_history_and_execution():
    s = {}
    PC.append_history(s, "gate_passed", "orchestrator", "BA gate")
    PC.set_execution(s, current_stage="PROJECT", workflow_status="RUNNING")
    assert PC.get_execution(s)["current_stage"] == "PROJECT"
    assert PC.get_context(s)["history"][-1]["type"] == "gate_passed"
    with pytest.raises(ValueError):
        PC.set_execution(s, bogus_field=1)


def test_snapshot_artifact():
    s = {}
    PC.snapshot_artifact(s, "BA", "brd", "/tmp/x.md", "ok")
    assert PC.get_context(s)["artifacts"]["BA"]["doc_key"] == "brd"


# ---------- change requests ----------

def test_cr_lifecycle_and_budget():
    s = {}
    cr = CR.create_cr(s, "technical", "functional", "FR-014 ambiguous",
                      ["FR-014"], ["T-023"], ["FunctionalSpec"], "blocks build")
    assert cr["id"] == "CR-001" and cr["status"] == "OPEN"
    assert cr["created_at"] and cr["resolved_at"] is None
    # idempotent duplicate
    dup = CR.create_cr(s, "technical", "functional", "FR-014 ambiguous",
                       ["FR-014"], ["T-023"], ["FunctionalSpec"])
    assert dup["id"] == "CR-001" and len(CR.open_crs(s)) == 1
    assert not CR.note_iteration(s, "CR-001")["budget_exceeded"]
    assert not CR.note_iteration(s, "CR-001")["budget_exceeded"]
    assert CR.note_iteration(s, "CR-001")["budget_exceeded"]  # 3rd pass
    resolved = CR.resolve_cr(s, "CR-001", "clarified")
    assert resolved["status"] == "RESOLVED" and resolved["resolved_at"]
    assert CR.open_crs(s) == []
    with pytest.raises(ValueError):
        CR.create_cr(s, "technical", "nowhere", "x")


# ---------- traceability ----------

def test_extract_ids_real_brd():
    brd = _doc("BRD_ShopFloor")
    ids = TR.extract_ids(brd)
    assert len(ids["BR"]) >= 5 and len(ids["US"]) >= 3


def test_coverage_real_docs():
    s = {"brd": _doc("BRD_ShopFloor"), "project_plan": _doc("ProjectPlan_ShopFloor"),
         "functional_spec": _doc("FunctionalSpec_ShopFloor"),
         "tech_design": _doc("TechDesign_ShopFloor")}
    rep = TR.coverage_report(s)
    assert rep["counts"]["BR"] >= 5 and rep["counts"]["FR"] >= 5
    assert rep["counts"]["T"] >= 10
    assert rep["uncovered_BR_no_FR"] == []  # aligned numbering convention
    chain = TR.chain_for(s, "T-004")
    assert chain["chain"]["BR"] and chain["chain"]["FR"]


# ---------- gates ----------

def _full_state():
    from tests.prod_fixtures import BRD, DESIGN, PLAN, SPEC
    st = {
        "brd": BRD,
        "project_plan": PLAN,
        "functional_spec": SPEC,
        "tech_design": DESIGN,
        "ba_flow_x_diagram": {"kind": "flow", "nodes": 19, "edges": 16,
                              "mermaid": "flowchart TD\nA{Valid?}-->B"},
        "project_gantt_x_diagram": {"kind": "gantt", "nodes": 17, "edges": 0, "mermaid": "gantt"},
        "functional_x_diagram": {"kind": "functional", "nodes": 9, "edges": 8, "mermaid": "flowchart LR"},
        "tech_seq_x_diagram": {"kind": "sequence", "nodes": 6, "edges": 5, "mermaid": "sequenceDiagram"},
        "tech_arch_x_diagram": {"kind": "architecture", "nodes": 8, "edges": 7, "mermaid": "flowchart TB"},
    }
    from tests.frappe_fixture import seed_frappe
    seed_frappe(st, st["functional_spec"], st["tech_design"], plan=st["project_plan"],
                brd=st["brd"])
    return st


def test_ba_gate_pass_and_fail():
    assert gates.ba_gate(_full_state())["passed"]
    # unbound empty session resolves NOTHING (isolation: no global fallback)
    fallback = gates.ba_gate({})
    assert not fallback["passed"]
    assert any(m.startswith("brd_exists:") for m in fallback["missing"])
    # a long but section-less doc fails text checks deterministically
    failed = gates.ba_gate({"brd": "lorem ipsum " * 300})
    assert not failed["passed"] and len(failed["missing"]) >= 10


def test_project_gate_pass_and_fail():
    assert gates.project_gate(_full_state())["passed"]
    failed = gates.project_gate({"project_plan": "lorem ipsum " * 300})
    assert not failed["passed"] and any(m.startswith("wbs:") for m in failed["missing"])


def test_functional_gate_pass():
    assert gates.functional_gate(_full_state())["passed"]


def test_technical_gate_pass():
    assert gates.technical_gate(_full_state())["passed"]


def test_final_validation_pass_and_fail():
    assert gates.final_validation(_full_state())["passed"]
    bad = gates.final_validation({})
    assert not bad["passed"]
    assert bad["sub_gates"]["BA"]["passed"] is False


# ---------- handoffs / status ----------

def test_brief_and_status():
    from shared import handoffs
    s = _full_state()
    brief = handoffs.build_brief(s, "FRAPPE")
    assert "Prior artifact: brd" in brief and "read_output_file" in brief
    PC.append_history(s, "gate_passed", "orchestrator", "BA gate")
    st = handoffs.status_summary(s)
    assert st["gates"]["BA"]["passed"] is True
    assert st["execution"]["workflow_status"] == "NOT_STARTED"


def test_cr_in_brief():
    from shared import handoffs
    s = _full_state()
    CR.create_cr(s, "technical", "functional", "ambiguous rule", ["FR-008"])
    brief = handoffs.build_brief(s, "FUNCTIONAL")
    assert "CR-001" in brief and "ambiguous rule" in brief


# ---------- orchestrator wiring (no LLM) ----------

def test_pipeline_wiring():
    from google.adk import Workflow
    from delivery_pipeline.agent import orchestrator, root_agent
    assert isinstance(root_agent, Workflow)
    assert set(orchestrator.stage_agents) == {"BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE"}
    assert orchestrator.rerun_on_resume is True


# ---------- prompts untouched guard ----------

PROMPT_ANCHORS = {
    # Anchors track the CURRENT managed prompts (owners edit prompts separately;
    # this guard fails loudly if a prompt is ever replaced/emptied).
    "ba_agent": ["Senior Business Analyst", "BUSINESS DISCOVERY OWNER",
                 "5 Whys", "record_elicitation", "MoSCoW", "append_doc",
                 "[ASSUMPTION:xxx]"],
    "project_agent": ["Project Engineer / Delivery Lead", "DELIVERY PLANNING OWNER"],
    "functional_agent": ["Functional Consultant", "FUNCTIONAL BEHAVIOR OWNER"],
    "technical_agent": ["Tech Lead / Solution Architect", "TECHNICAL DESIGN OWNER"],
    "frappe_agent": ["Frappe", "FRAPPE IMPLEMENTATION OWNER"],
}

INFRA_TOOLS = ["get_stage_brief", "read_output_file", "get_project_status",
               "create_change_request", "record_history_event"]
BA_EXTRA_TOOLS = ["record_elicitation"]


def test_prompts_untouched_and_infra_tools_present():
    import importlib
    for mod, anchors in PROMPT_ANCHORS.items():
        agent_mod = importlib.import_module(f"{mod}.agent")
        root = agent_mod.root_agent
        text = root.instruction
        for a in anchors:
            assert a in text, f"{mod} prompt anchor missing: {a!r}"
        tool_names = sorted(
            getattr(t, "__name__", getattr(t, "name", str(t))) for t in root.tools)
        for it in INFRA_TOOLS:
            assert it in tool_names, f"{mod} missing infra tool {it}"
        if mod == "ba_agent":
            for it in BA_EXTRA_TOOLS:
                assert it in tool_names, f"ba_agent missing infra tool {it}"


# ---------- BA accuracy infra: elicitation log, grounded briefs, ledger warnings ----------

def test_elicitation_section_and_recorder():
    from shared import orch_tools as OT
    assert "elicitation" in PC.SECTIONS
    s = {}

    class FakeTC:
        def __init__(self):
            self.state = s

    r = OT.record_elicitation(FakeTC(), "Who approves travel?", "Reporting manager", "user")
    assert r == {"ok": True, "entries": 1}
    assert PC.get_context(s)["elicitation"]["log"][0]["a"] == "Reporting manager"


def test_ba_brief_carries_prior_draft_and_elicitation():
    from shared import handoffs as HO
    s = {"brd": _doc("BRD_ShopFloor")}
    PC.get_context(s)["elicitation"] = {"log": [{"q": "Volumes?", "a": "200 trips/mo", "source": "user"}]}
    brief = HO.build_brief(s, "BA")
    assert "200 trips/mo" in brief
    assert "do not re-ask" in brief


def test_ba_gate_ledger_warnings():
    """Ledger gaps are warnings, never gate failures (fail-closed elsewhere)."""
    from tests.ba_fixture import ALL_SECTIONS, ba_state
    from tests.prod_fixtures import BRD

    good = ba_state(brd=BRD, sections=ALL_SECTIONS, diagram=True)
    assert gates.ba_gate(good)["passed"]
    stripped = re.sub(r"\[ASSUMPTION[^\]]*\]|OQ-\d+", "ITEM", BRD)
    g = gates.ba_gate(ba_state(brd=stripped, sections=ALL_SECTIONS, diagram=True))
    assert "assumption_ledger" in [w.split(":")[0] for w in g["warnings"]]
    assert "oq_ledger" in [w.split(":")[0] for w in g["warnings"]]
    assert g["passed"]  # warnings must not fail an otherwise good BRD


def test_read_output_file_is_capped(monkeypatch):
    """A tool read must not echo a whole document into the conversation. Unbounded
    reads grew a FRAPPE request 76k -> 154k -> 200k chars over six calls (the first
    tool round re-read the four documents already sitting in the brief) until the
    gateway stalled."""
    from shared import orch_tools as OT

    class FakeWS:
        project_id = "cap_test"
        size = OT._READ_CAP + 5000

        def read_artifact(self, name):
            return "x" * self.size

    monkeypatch.setattr(OT, "_ws_of", lambda tc: FakeWS())
    r = OT.read_output_file(None, "brd_assembled.md")
    assert r["ok"] and r["chars"] == OT._READ_CAP + 5000
    assert len(r["content"]) < r["chars"]
    assert "clipped" in r["content"] and "handoff brief" in r["content"]

    class SmallWS(FakeWS):
        def read_artifact(self, name):
            return "short doc"

    monkeypatch.setattr(OT, "_ws_of", lambda tc: SmallWS())
    assert OT.read_output_file(None, "x.md")["content"] == "short doc"


def test_uploaded_documents_get_a_larger_read_budget_than_stage_documents(monkeypatch):
    """A stage document is already in the brief in full, so clipping it is harmless.
    A document the customer handed over is the input material — clipping it to the
    same budget would hide most of what they provided."""
    from shared import orch_tools as OT

    class FakeWS:
        project_id = "cap_test"

        def read_artifact(self, name):
            return "y" * (OT._READ_CAP + 5000)

    monkeypatch.setattr(OT, "_ws_of", lambda tc: FakeWS())
    # inside the upload budget: returned whole, no clipping marker
    up = OT.read_output_file(None, "uploaded_notes_20260924_201144.md")
    assert OT._READ_CAP < OT._UPLOAD_READ_CAP
    assert len(up["content"]) == up["chars"] == OT._READ_CAP + 5000
    assert "clipped" not in up["content"]

    class BigWS(FakeWS):
        def read_artifact(self, name):
            return "y" * (OT._UPLOAD_READ_CAP + 5000)

    monkeypatch.setattr(OT, "_ws_of", lambda tc: BigWS())
    clipped = OT.read_output_file(None, "uploaded_notes_20260924_201144.md")
    assert len(clipped["content"]) < clipped["chars"]
    assert "clipped" in clipped["content"]
    # and it tells the reader how to get at the rest, unlike a stage document
    assert "narrower question" in clipped["content"]


def test_frappe_prompt_drives_the_tools_its_gate_requires():
    """The FRAPPE gate checks evidence only these tools record. A run wrote a 1.5k
    setup document, called none of them, failed every blocking check and re-ran the
    stage until it was stopped. The mandatory sequence must name each tool, and each
    name must really exist on the agent — a prompt naming a tool that isn't wired up
    is exactly the drift that cost that time."""
    from frappe_agent.agent import FRAPPE_INSTRUCTION, root_agent

    start = FRAPPE_INSTRUCTION.index("MANDATORY EXECUTION SEQUENCE")
    end = FRAPPE_INSTRUCTION.index("CREDENTIALS", start)
    seq = FRAPPE_INSTRUCTION[start:end]

    required = {"discover_frappe_env", "inspect_doctype", "ensure_doctype", "smoke_test",
                "create_project_from_plan", "ensure_role", "ensure_permissions",
                "ensure_workflow", "seed_records", "save_doc"}
    unnamed = sorted(t for t in required if f"{t}(" not in seq)
    assert not unnamed, f"missing from the mandatory sequence: {unnamed}"

    available = set()
    for tool in root_agent.tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        available.add(str(name))
    unwired = sorted(t for t in required if t not in available)
    assert not unwired, f"prompt names tools the agent does not have: {unwired}"


def test_ba_brief_lists_the_documents_the_customer_handed_over():
    """Uploads are stored in the workspace and recorded as sources, but nothing else
    told the agent they existed — so an upload was stored and then ignored. The brief
    now names them, and the BA is told to cite them as evidence."""
    from shared import handoffs as HO
    from shared.project_context import get_context

    s = {"brd": _doc("BRD_ShopFloor")}
    get_context(s)["business"] = {"source_documents": [
        {"name": "interview_notes.txt",
         "file": "uploaded_interview_notes_20260924_201144.md", "chars": 2340}]}

    brief = HO.build_brief(s, "BA")
    assert "Documents you were given" in brief
    assert "uploaded_interview_notes_20260924_201144.md" in brief
    assert "2,340 chars" in brief
    assert "interview_notes.txt" in brief            # the original filename too
    assert "link_evidence" in brief                  # how to cite it
    assert "read_output_file" in brief               # and to actually read it

    # a later stage is told about them as well, without the BA-only citation line
    later = HO.build_brief(s, "TECHNICAL")
    assert "uploaded_interview_notes_20260924_201144.md" in later
    assert "link_evidence" not in later


def test_brief_without_uploads_has_no_documents_section():
    from shared import handoffs as HO

    brief = HO.build_brief({"brd": _doc("BRD_ShopFloor")}, "BA")
    assert "Documents you were given" not in brief


def test_agent_instructions_hold_no_state_template_variables():
    """ADK substitutes every {word} in an agent instruction from session state and
    raises KeyError when the name is absent. A literal `{name}` in the API-path
    example aborted technical_agent mid-run with
    "Context variable not found: `name` in agent 'technical_agent'".
    Instructions are static constants, so this is a build-time property."""
    from google.adk.utils.instructions_utils import (_TEMPLATE_VAR_PATTERN,
                                                     _is_valid_state_name)
    from ba_agent.agent import BA_INSTRUCTION
    from frappe_agent.agent import FRAPPE_INSTRUCTION
    from functional_agent.agent import FUNCTIONAL_INSTRUCTION
    from project_agent.agent import PROJECT_INSTRUCTION
    from technical_agent.agent import TECH_INSTRUCTION

    problems: list[str] = []
    for name, text in (("BA", BA_INSTRUCTION), ("PROJECT", PROJECT_INSTRUCTION),
                       ("FUNCTIONAL", FUNCTIONAL_INSTRUCTION),
                       ("TECHNICAL", TECH_INSTRUCTION), ("FRAPPE", FRAPPE_INSTRUCTION)):
        for m in _TEMPLATE_VAR_PATTERN.finditer(text or ""):
            var = m.group().strip("{}").strip()
            if _is_valid_state_name(var):
                problems.append(f"{name}: {m.group()!r} would resolve against session state")
    assert not problems, problems
