"""BA→Project handoff consumption tests — intake block rides PROJECT brief."""

import sys
from pathlib import Path

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from tests.prod_fixtures import BRD as _BRD

BRD = _BRD + "\nAssumption: caps reviewed yearly. [ASSUMPTION:cap-review]\n"


def _ba_state():
    from shared.project_context import get_context

    s = {"brd": BRD,
         "ba_flow_diagram": {"kind": "flow", "nodes": 19, "edges": 16,
                             "mermaid": "flowchart TD\nA{OK?}-->B"}}
    ctx = get_context(s)
    ctx["stakeholders"] = {"sponsor": "CFO"}
    ctx["decisions"] = {"threshold": "1000 EUR"}
    return s


def test_intake_reports_package_and_unresolved():
    from ba_agent.managers.package_assembler import project_intake

    inc = project_intake(_ba_state())
    assert inc["has_content"] is True
    assert "BA Package intake" in inc["markdown"]
    assert "do not invent" in inc["markdown"]
    # Sparse knowledge -> unresolved points named, not papered over.
    assert inc["unresolved"]  # business_need etc. unanswered from ctx
    for u in inc["unresolved"][:9]:
        assert u in inc["markdown"]


def test_intake_records_package_artifact_path():
    from ba_agent.managers.package_assembler import project_intake
    from shared.project_context import get_context

    s = _ba_state()
    get_context(s)["artifacts"] = {"BA": {"doc_key": "ba_package",
                                          "path": "/p/BA_PACKAGE_assembled.md"}}
    inc = project_intake(s)
    assert inc["package_path"] == "/p/BA_PACKAGE_assembled.md"
    assert "BA_PACKAGE_assembled.md" in inc["markdown"]


def test_project_brief_carries_intake_ba_brief_does_not():
    from shared import handoffs as HO

    s = _ba_state()
    project_brief = HO.build_brief(s, "PROJECT")
    assert "## BA Package intake" in project_brief
    assert "## Prior artifact: brd" in project_brief  # existing shape kept
    ba_brief = HO.build_brief(s, "BA")
    assert "## BA Package intake" not in ba_brief
    func_brief = HO.build_brief(s, "FUNCTIONAL")
    assert "## BA Package intake" not in func_brief


def test_legacy_state_brief_shape_unchanged():
    from shared import handoffs as HO

    before = HO.build_brief({}, "PROJECT")
    assert "## BA Package intake" not in before
    assert "## Traceability counts" in before  # existing sections intact


def test_intake_never_raises_and_pending_approvals_surfaced():
    from ba_agent.managers import decision_manager as DM
    from ba_agent.managers.package_assembler import project_intake

    assert project_intake({})["has_content"] is False
    assert project_intake({"brd": None})["markdown"] == ""
    s = _ba_state()
    DM.request_approval(s, "Pilot sign-off")
    inc = project_intake(s)
    assert inc["pending_approvals"] == ["AP-001"]
    assert "AP-001" in inc["markdown"]
