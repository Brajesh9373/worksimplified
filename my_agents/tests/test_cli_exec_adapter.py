"""CLI execution-adapter tests (no LLM, no network, no CLI subprocess).

Covers: per-stage engine resolution (api | cli, FRAPPE pinned), provider argv
construction, the BA record contract (extraction + execution through the real
managers), bounded retry, the CLI node's orchestrator contract, and the full
sectional BA stage driven end-to-end through the adapter with no pre-seeded
v2 ledger.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))
sys.path.insert(0, str(MY_AGENTS.parent / "src"))

from google.adk import Event, Workflow  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402

from ba_agent.managers.assumptions import assumptions as registered_assumptions  # noqa: E402
from ba_agent.managers.ba_plan import DEFAULTS as PLAN_DEFAULTS  # noqa: E402
from cli_exec import build_stage_agent, get_provider, is_cli_provider, stage_mode  # noqa: E402
from cli_exec.node import CliStageNode  # noqa: E402
from cli_exec.providers import CLIResult, CliExecError  # noqa: E402
from cli_exec.tools import apply_tool_calls, extract_tool_calls  # noqa: E402
from shared import sections as SEC  # noqa: E402
from shared.orch_nodes import DeliveryOrchestrator  # noqa: E402
from shared.workspace import ProjectWorkspace  # noqa: E402
from tests.ba_fixture import SECTION_BODIES, generation  # noqa: E402
from tests.frappe_fixture import seed_frappe  # noqa: E402
from tests.prod_fixtures import BRD, DESIGN, PLAN, SPEC  # noqa: E402

BA_TITLES = {s["id"]: s["title"] for s in SEC.sections_for("BA")}

_ALL_MODE_VARS = ["AGENT_EXEC_MODE", "CLI_PROVIDER", "CLI_MODEL", "CLI_BIN",
                  "CLI_PERMISSION_MODE", "CLI_AGENT", "CLI_RETRIES",
                  "CLI_RETRY_BACKOFF"] + [f"AGENT_EXEC_MODE_{s}"
                                          for s in ("BA", "PROJECT", "FUNCTIONAL",
                                                    "TECHNICAL", "FRAPPE")]

DIAGRAM_MD = """```mermaid
flowchart TD
  A[Start] --> B[Submit request]
  B --> C{Policy check}
  C -->|ok| D[Manager approval]
  C -->|fail| E[Return to employee]
  D --> F[Book travel]
  F --> G[End]
```"""

RICH_DIAGS = {
    "project_gantt_t_diagram": {"kind": "gantt", "nodes": 17, "edges": 0, "mermaid": "gantt"},
    "functional_t_diagram": {"kind": "functional", "nodes": 9, "edges": 8, "mermaid": "flowchart LR"},
    "tech_seq_t_diagram": {"kind": "sequence", "nodes": 6, "edges": 5, "mermaid": "sequenceDiagram"},
    "tech_arch_t_diagram": {"kind": "architecture", "nodes": 8, "edges": 7, "mermaid": "flowchart TB"},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_MODE_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLI_RETRY_BACKOFF", "0")


@pytest.fixture(autouse=True)
def _clean_workspaces():
    for name in ("cli_unit", "cli_e2e"):
        shutil.rmtree(MY_AGENTS / "projects" / name, ignore_errors=True)
    yield
    for name in ("cli_unit", "cli_e2e"):
        shutil.rmtree(MY_AGENTS / "projects" / name, ignore_errors=True)


# ------------------------------------------------------------- engine selection

def test_engine_defaults_to_api():
    assert stage_mode("BA") == "api"
    assert build_stage_agent("BA", "SENTINEL") == "SENTINEL"
    assert build_stage_agent("PROJECT", "SENTINEL") == "SENTINEL"


def test_engine_global_cli_switch(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_MODE", "cli")
    assert stage_mode("BA") == "cli" and stage_mode("PROJECT") == "cli"
    node = build_stage_agent("BA", "SENTINEL")
    assert isinstance(node, CliStageNode) and node.stage == "BA"
    assert node.name == "ba_cli_stage" and node.provider_name == "commandcode"


def test_engine_per_stage_override(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_MODE", "api")
    monkeypatch.setenv("AGENT_EXEC_MODE_PROJECT", "cli")
    assert stage_mode("BA") == "api" and stage_mode("PROJECT") == "cli"
    assert isinstance(build_stage_agent("PROJECT", None), CliStageNode)
    assert build_stage_agent("BA", "SENTINEL") == "SENTINEL"


def test_frappe_is_pinned_to_api(monkeypatch):
    """The Frappe stage makes real tool calls with side effects — a CLI cannot
    execute them, so the engine must stay api even when cli is requested."""
    monkeypatch.setenv("AGENT_EXEC_MODE", "cli")
    monkeypatch.setenv("AGENT_EXEC_MODE_FRAPPE", "cli")
    assert stage_mode("FRAPPE") == "api"
    assert build_stage_agent("FRAPPE", "SENTINEL") == "SENTINEL"


def test_unknown_provider_with_cli_engine_raises(monkeypatch):
    monkeypatch.setenv("AGENT_EXEC_MODE", "cli")
    monkeypatch.setenv("CLI_PROVIDER", "nope")
    with pytest.raises(CliExecError):
        build_stage_agent("BA", "SENTINEL")


# ------------------------------------------------------------------ providers

def test_commandcode_argv():
    p = get_provider("commandcode")
    argv = p.build_argv("PROMPT", Path("/ws"))
    assert argv[0] == "commandcode"
    assert argv[1] == "-p" and argv[2] == "PROMPT"
    assert "--output-format" in argv and "text" in argv
    assert "--skip-onboarding" in argv and "--no-session" in argv
    assert "--permission-mode" in argv and "standard" in argv  # read/write default
    assert "-m" not in argv
    assert p.build_argv("PROMPT", Path("/ws"), "the-model")[-2:] == ["-m", "the-model"]


def test_permission_mode_and_bin_overrides(monkeypatch):
    monkeypatch.setenv("CLI_PERMISSION_MODE", "plan")
    monkeypatch.setenv("CLI_BIN", "/opt/cmd")
    argv = get_provider("commandcode").build_argv("x", Path("/ws"))
    assert "plan" in argv and argv[0] == "/opt/cmd"


def test_opencode_argv_and_agent_flag(monkeypatch):
    monkeypatch.setenv("CLI_AGENT", "plan")
    argv = get_provider("opencode").build_argv("PROMPT", Path("/ws"), "prov/model")
    assert argv[:3] == ["opencode", "run", "PROMPT"]
    assert "--dir" in argv and "/ws" in argv
    assert argv[-4:] == ["-m", "prov/model", "--agent", "plan"]


def test_provider_aliases_and_unknown():
    assert is_cli_provider("command-code") and is_cli_provider("cmd")
    assert is_cli_provider("OpenCode")
    assert not is_cli_provider("adk") and not is_cli_provider("")
    with pytest.raises(CliExecError):
        get_provider("nope")


# --------------------------------------------------------------- tool contract

def test_extract_tool_calls_removes_block():
    reply = ('## Objectives and Executive Summary\n\nObjective: real body text.\n\n'
             '```json ba_tool_calls\n'
             '{"tool_calls": [{"name": "record_assumption", '
             '"arguments": {"label": "cap", "text": "cap is uncertain"}}]}\n```')
    text, calls = extract_tool_calls(reply)
    assert "ba_tool_calls" not in text and "record_assumption" not in text
    assert "Objective: real body text." in text
    assert calls == [{"name": "record_assumption",
                      "arguments": {"label": "cap", "text": "cap is uncertain"}}]


def test_extract_tool_calls_ignores_non_json_fences():
    text, calls = extract_tool_calls("## T\n\nbody\n\n```mermaid\nflowchart TD\n```")
    assert calls == [] and "flowchart TD" in text


def test_extract_tool_calls_accepts_bare_array_and_call_alias():
    reply = ('## T\n\nbody\n\n```ba_tool_calls\n'
             '[{"call": "record_assumption", "arguments": {"label": "a", "text": "b"}}]\n```')
    text, calls = extract_tool_calls(reply)
    assert "ba_tool_calls" not in text and "body" in text
    assert calls == [{"call": "record_assumption",
                      "arguments": {"label": "a", "text": "b"}}]


def test_apply_tool_calls_runs_real_managers():
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    res = apply_tool_calls(state, [
        {"name": "record_assumption",
         "arguments": {"label": "cap", "text": "the cap is uncertain"}},
        {"name": "record_success_metric",
         "arguments": {"name": "cycle", "baseline": "10 days", "target": "2 days",
                       "method": "audit trail", "review_point": "30 days"}},
        {"name": "record_open_question", "arguments": {"question": "who approves?"}},
        {"name": "not_a_tool", "arguments": {}},
    ])
    assert set(res["applied"]) == {"record_assumption", "record_success_metric",
                                   "record_open_question"}
    assert res["unknown"] == ["not_a_tool"]
    assert [a["label"] for a in registered_assumptions(state)] == ["cap"]
    assert state["project_context"]["success_metrics"][0]["id"] == "SM-001"


def test_apply_tool_calls_accepts_call_alias():
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    res = apply_tool_calls(state, [{"call": "record_assumption",
                                    "arguments": {"label": "alias", "text": "x"}}])
    assert res["applied"] == ["record_assumption"]


def test_apply_tool_calls_evidence_validated_against_brd():
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    ok = apply_tool_calls(state, [
        {"name": "record_evidence",
         "arguments": {"artifact_id": "US-001", "source_type": "document",
                       "source_ref": "BRD"}}], brd="US-001 the system shall do X.")
    assert "record_evidence" in ok["applied"]
    mixed = apply_tool_calls(state, [
        {"name": "record_evidence",
         "arguments": {"artifact_id": "US-999", "source_type": "document",
                       "source_ref": "BRD"}},
        {"name": "record_assumption", "arguments": {"label": "x", "text": "y"}},
    ], brd="US-001 the system shall do X.")
    assert "record_assumption" in mixed["applied"] and mixed["errors"]


# ---------------------------------------------------------------- the node

class FakeProvider:
    """In-process stand-in for a headless CLI (records prompts, returns text)."""

    name = "fake"

    def __init__(self, responder):
        self._responder = responder
        self.prompts: list[str] = []

    def build_argv(self, prompt, workspace, model=""):
        return ["fake", prompt]

    async def run(self, prompt, workspace, timeout=None, model=""):
        self.prompts.append(prompt)
        return CLIResult("fake", ["fake"], 0, self._responder(prompt), "", 0.01)


class FlakyProvider(FakeProvider):
    """Fails transiently for the first N calls, then succeeds."""

    def __init__(self, fail_times: int, success_text: str = "## T\n\nok body"):
        super().__init__(lambda p: success_text)
        self.fail_times = fail_times
        self.calls = 0

    async def run(self, prompt, workspace, timeout=None, model=""):
        self.calls += 1
        if self.calls <= self.fail_times:
            return CLIResult("flaky", ["flaky"], 7, "", "API server error", 0.01)
        return CLIResult("flaky", ["flaky"], 0, self._responder(prompt), "", 0.01)


def _collect(agen):
    async def _go():
        return [item async for item in agen]
    return asyncio.run(_go())


def _node(stage="BA"):
    return CliStageNode(name=f"{stage.lower()}_cli_stage", stage=stage,
                        provider_name="fake", rerun_on_resume=True)


def test_cli_node_returns_section_and_traces(monkeypatch):
    ws = ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    fake = FakeProvider(lambda p: "## Objectives and Executive Summary\n\n"
                                  "Objective and goal for the business. " * 20)
    monkeypatch.setattr("cli_exec.node.get_provider", lambda *a, **k: fake)

    out = _collect(_node().run_node_impl(
        ctx=SimpleNamespace(state=state),
        node_input="SECTION TASK — write ONLY this one section\nSection: Objectives "
                   "and Executive Summary (id: objectives)"))

    assert out and "Objective and goal for the business." in out[0]
    prompt = fake.prompts[0]
    assert "OVERRIDE" in prompt            # countermands the brief's ADK tool instructions
    assert "YOUR OUTPUT" in prompt
    assert "RECORDS — keep the BA ledger" in prompt  # BA record contract published
    assert "Section: Objectives and Executive Summary (id: objectives)" in prompt
    trace = ws.root / "execution" / "cli_BA.jsonl"
    assert trace.is_file() and '"provider": "fake"' in trace.read_text()
    assert any(h["type"] == "cli_pass" for h in state["project_context"]["history"])


def test_non_ba_stage_omits_record_contract(monkeypatch):
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    fake = FakeProvider(lambda p: "## WBS\n\n| ID | Task |\n| T-001 | x |\n" * 10)
    monkeypatch.setattr("cli_exec.node.get_provider", lambda *a, **k: fake)
    _collect(_node("PROJECT").run_node_impl(
        ctx=SimpleNamespace(state=state), node_input="Section: WBS (id: wbs)"))
    assert "RECORDS — keep the BA ledger" not in fake.prompts[0]
    assert "WorkSimplified Project Agent" in fake.prompts[0]


def test_cli_node_applies_tool_calls_and_strips_them(monkeypatch):
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    reply = ("## Business Rules\n\nBR-001 the rule body. " * 10 +
             "\n\n```json ba_tool_calls\n" +
             json.dumps({"tool_calls": [{"name": "record_assumption",
                                         "arguments": {"label": "r1",
                                                       "text": "rule may change"}}]}) +
             "\n```")
    monkeypatch.setattr("cli_exec.node.get_provider",
                        lambda *a, **k: FakeProvider(lambda p: reply))
    out = _collect(_node().run_node_impl(
        ctx=SimpleNamespace(state=state), node_input="Section: Business Rules (id: rules)"))
    assert out and "ba_tool_calls" not in out[0]
    assert [a["label"] for a in registered_assumptions(state)] == ["r1"]


def test_cli_node_retries_transient_failure(monkeypatch):
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    monkeypatch.setenv("CLI_RETRIES", "3")
    flaky = FlakyProvider(fail_times=2)
    monkeypatch.setattr("cli_exec.node.get_provider", lambda *a, **k: flaky)
    out = _collect(_node().run_node_impl(
        ctx=SimpleNamespace(state=state), node_input="Section: X (id: objectives)"))
    assert out and flaky.calls == 3


def test_cli_node_raises_after_retries_exhausted(monkeypatch):
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    monkeypatch.setenv("CLI_RETRIES", "2")
    flaky = FlakyProvider(fail_times=99)
    monkeypatch.setattr("cli_exec.node.get_provider", lambda *a, **k: flaky)
    with pytest.raises(CliExecError):
        _collect(_node().run_node_impl(
            ctx=SimpleNamespace(state=state), node_input="Section: X (id: scope)"))
    assert flaky.calls == 2


def test_cli_node_diagram_sets_state(monkeypatch):
    ProjectWorkspace("cli_unit")
    state = {"project_workspace_id": "cli_unit"}
    monkeypatch.setattr("cli_exec.node.get_provider",
                        lambda *a, **k: FakeProvider(lambda p: DIAGRAM_MD))
    _collect(_node().run_node_impl(
        ctx=SimpleNamespace(state=state),
        node_input='DIAGRAM TASK — call build_diagram_bundle with diagram_kind="flow"'))
    diagrams = {k: v for k, v in state.items() if k.endswith("_diagram")}
    assert diagrams, state.keys()
    diag = next(iter(diagrams.values()))
    assert diag["kind"] == "flow" and diag["nodes"] >= 6


# -------------------------------------------------------------- end to end

def _stage_stubs():
    def stub(node_input):
        txt = node_input if isinstance(node_input, str) else ""
        m = re.search(r"Handoff brief for (\w+)", txt)
        stage = m.group(1) if m else ""
        if stage == "PROJECT":
            return Event(state={"project_plan": PLAN,
                                **{k: v for k, v in RICH_DIAGS.items() if "gantt" in k}})
        if stage == "FUNCTIONAL":
            return Event(state={"functional_spec": SPEC,
                                **{k: v for k, v in RICH_DIAGS.items() if "functional" in k}})
        if stage == "TECHNICAL":
            return Event(state={"tech_design": DESIGN,
                                **{k: v for k, v in RICH_DIAGS.items() if "tech" in k}})
        if stage == "FRAPPE":
            return Event(state={"frappe_setup": "setup done", "frappe_project": "PROJ-TEST"})
        return Event(state={})
    return stub


def _run(pipe, start_text, initial_state, workspace_id):
    from google.genai import types
    ProjectWorkspace(workspace_id)
    base = dict(initial_state)
    base["project_workspace_id"] = workspace_id

    async def go():
        runner = InMemoryRunner(agent=pipe, app_name="pipe")
        sess = await runner.session_service.create_session(
            app_name="pipe", user_id="u1", state=base)
        events = []
        msg = types.Content(role="user", parts=[types.Part(text=start_text)])
        inv = None
        for _ in range(4):
            inv_id = interrupt_id = None
            kwargs = dict(user_id="u1", session_id=sess.id, new_message=msg)
            if inv:
                kwargs["invocation_id"] = inv
            async for ev in runner.run_async(**kwargs):
                events.append(ev)
                if getattr(ev, "long_running_tool_ids", None):
                    inv_id = ev.invocation_id
                    for fc in ev.get_function_calls():
                        interrupt_id = fc.id
            final = await runner.session_service.get_session(
                app_name="pipe", user_id="u1", session_id=sess.id)
            exc = final.state["project_context"]["execution"]
            if exc["workflow_status"] != "WAITING_FOR_HUMAN":
                await runner.close()
                return events, final
            if not (inv_id and interrupt_id
                    and final.state["project_context"].get("ba_final_review_pending")):
                await runner.close()
                return events, final
            msg = types.Content(role="user", parts=[types.Part(
                function_response=types.FunctionResponse(
                    id=interrupt_id, name="adk_request_input",
                    response={"result": "approve"}))])
            inv = inv_id
        await runner.close()
        return events, final
    return asyncio.run(go())


def _cli_responder(prompt: str) -> str:
    """A compliant CLI: the section plus the BA ledger records it decided."""
    if "DIAGRAM TASK" in prompt:
        return DIAGRAM_MD
    m = re.search(r"\(id:\s*(\w+)\)", prompt)
    sid = m.group(1) if m else ""
    body = SECTION_BODIES.get(sid, "No content. " * 30)
    section = f"## {BA_TITLES.get(sid, 'Section')}\n\n{body}"
    calls: list[dict] = []
    for rid in sorted(set(re.findall(r"\b(?:US|BR)-\d+\b", body))):
        calls.append({"name": "record_evidence",
                      "arguments": {"artifact_id": rid, "source_type": "document",
                                    "source_ref": "BRD", "note": "stated in the BRD"}})
    for i in range(1, len(PLAN_DEFAULTS) + 1):
        calls.append({"name": "update_plan_item",
                      "arguments": {"item_id": f"PL-{i:03d}", "status": "done"}})
    if sid == "solution_assessment":
        calls.append({"name": "record_success_metric",
                      "arguments": {"name": "Reimbursement cycle time",
                                    "baseline": "10 business days",
                                    "target": "2 business days",
                                    "method": "claim audit trail",
                                    "review_point": "30 days after go-live",
                                    "metric_id": "SM-001"}})
    block = "```json ba_tool_calls\n" + json.dumps({"tool_calls": calls}) + "\n```"
    return f"{section}\n\n{block}"


def test_sectional_ba_stage_runs_through_cli(monkeypatch):
    """The BA stage is produced entirely by the CLI adapter — sections via the
    prose path and the v2 ledger via the record contract — and the fail-closed
    gate passes with NO pre-seeded ledger."""
    fake = FakeProvider(_cli_responder)
    monkeypatch.setattr("cli_exec.node.get_provider", lambda *a, **k: fake)

    stubs = _stage_stubs()
    orch = DeliveryOrchestrator(name="orc", stage_agents={
        "BA": _node(), "PROJECT": stubs, "FUNCTIONAL": stubs,
        "TECHNICAL": stubs, "FRAPPE": stubs})
    pipe = Workflow(name="pipe", edges=[("START", orch)])

    seed = generation(seed_frappe({}, SPEC, DESIGN, plan=PLAN))  # no brd -> no ledger

    events, final = _run(pipe, "project_id:cli_e2e", seed, "cli_e2e")
    ctx = final.state["project_context"]

    assert ctx["execution"]["workflow_status"] == "SUCCESS", str(ctx["execution"])
    assert sorted(ctx.get("sections_done_BA", [])) == sorted(BA_TITLES)
    assert "on the existing platform" in final.state["brd"]
    assert ctx.get("evidence_links") and ctx.get("success_metrics")
    assert all(i["status"] == "done" for i in ctx.get("ba_plan", []))
    assert any(h["type"] == "cli_pass" for h in ctx["history"])
    assert (MY_AGENTS / "projects" / "cli_e2e" / "execution" / "cli_BA.jsonl").is_file()
