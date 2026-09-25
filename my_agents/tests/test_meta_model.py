"""Per-agent model wiring: LLM_MODEL_<AGENT> overrides + escalation clones."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.append(str(MY_AGENTS))

from shared import meta_model as M  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("LLM_MODEL", "LLM_TEMPERATURE", "LLM_MODEL_BA", "LLM_TEMPERATURE_BA",
                "LLM_MODEL_FRAPPE", "LLM_MODEL_ESCALATION", "LLM_MODEL_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    yield


def _reload():
    return importlib.reload(M)


def test_global_default_when_no_overrides(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "global-model")
    m = _reload()
    assert m.agent_model("ba") == "global-model"
    assert m.agent_model() == "global-model"


def test_per_agent_model_overrides_global(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "global-model")
    monkeypatch.setenv("LLM_MODEL_PROJECT", "strong-model")
    m = _reload()
    assert m.agent_model("project") == "strong-model"
    assert m.agent_model("PROJECT") == "strong-model"      # case-insensitive key
    assert m.agent_model("ba") == "global-model"           # falls back


def test_per_agent_temperature_and_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.35")
    monkeypatch.setenv("LLM_TEMPERATURE_FRAPPE", "0.1")
    m = _reload()
    assert m.agent_temperature("frappe") == 0.1
    assert m.agent_temperature("ba") == 0.35


def test_bad_temperature_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    monkeypatch.setenv("LLM_TEMPERATURE_BA", "warm")
    m = _reload()
    assert m.agent_temperature("ba") == 0.2


def test_every_stage_agent_uses_its_own_key(monkeypatch):
    """Each stage agent must ask for its own model key, not the shared default."""
    monkeypatch.setenv("LLM_MODEL", "global-model")
    for agent, key in (("ba_agent", "ba"), ("project_agent", "project"),
                       ("functional_agent", "functional"),
                       ("technical_agent", "technical"), ("frappe_agent", "frappe")):
        monkeypatch.setenv(f"LLM_MODEL_{key.upper()}", f"{key}-model")
    for var in ("LLM_API_KEY", "LLM_API_BASE"):
        monkeypatch.setenv(var, "x")
    sys.path.insert(0, str(MY_AGENTS))
    for agent, key in (("ba_agent", "ba"), ("project_agent", "project"),
                       ("functional_agent", "functional"), ("technical_agent", "technical"),
                       ("frappe_agent", "frappe")):
        mod = importlib.import_module(f"{agent}.agent")
        mod = importlib.reload(mod)
        assert key in str(mod.root_agent.model.model), f"{agent} not wired to its own key"


def test_escalation_off_by_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL_ESCALATION", raising=False)
    sys.path.insert(0, str(MY_AGENTS))
    sys.path.insert(0, str(MY_AGENTS / "delivery_pipeline"))
    mod = importlib.import_module("delivery_pipeline.agent")
    mod = importlib.reload(mod)
    assert mod.build_escalation_agents() == {}
    assert mod.orchestrator.escalation_agents == {}


def test_escalation_clones_agents_without_touching_prompts(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_ESCALATION", "escalation-model")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_API_BASE", "http://x/v1")
    sys.path.insert(0, str(MY_AGENTS))
    sys.path.insert(0, str(MY_AGENTS / "delivery_pipeline"))
    mod = importlib.import_module("delivery_pipeline.agent")
    mod = importlib.reload(mod)
    esc = mod.build_escalation_agents()
    assert set(esc) == {"BA", "PROJECT", "FUNCTIONAL", "TECHNICAL", "FRAPPE"}
    base = mod.STAGE_AGENTS["BA"]
    clone = esc["BA"]
    assert "escalation-model" in str(clone.model.model)
    assert clone.instruction == base.instruction          # prompts unchanged
    assert [t.__name__ for t in clone.tools] == [t.__name__ for t in base.tools]
