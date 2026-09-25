"""BA chatbot gateway client: JSON mode with fallback, clean key errors."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from ba_chat import llm


def _response(text):
    class Message:
        content = text

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    return Response()


def test_json_mode_falls_back_when_gateway_rejects_it(monkeypatch):
    calls = []

    def fake(**kwargs):
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise Exception("unsupported parameter")
        return _response("hi")

    monkeypatch.setattr("litellm.completion", fake)
    monkeypatch.setenv("LLM_API_KEY", "k")
    assert llm.complete([{"role": "user", "content": "hi"}], json_mode=True) == "hi"
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


def test_json_mode_used_when_supported(monkeypatch):
    monkeypatch.setattr("litellm.completion",
                        lambda **kwargs: _response('{"a": 1}'))
    monkeypatch.setenv("LLM_API_KEY", "k")
    out = llm.complete([{"role": "user", "content": "hi"}], json_mode=True)
    assert llm.extract_json(out) == {"a": 1}


def test_missing_key_is_a_clean_error(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    with pytest.raises(RuntimeError, match="start.sh"):
        llm.complete([{"role": "user", "content": "hi"}])


def test_gateway_failure_is_user_safe(monkeypatch):
    def boom(**kwargs):
        raise Exception("connection reset")

    monkeypatch.setattr("litellm.completion", boom)
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(RuntimeError, match="did not answer"):
        llm.complete([{"role": "user", "content": "hi"}])


def test_extract_json_repairs_wrappers():
    assert llm.extract_json('{"reply": "x"}') == {"reply": "x"}
    assert llm.extract_json('Sure! {"reply": "x", "value": ""} done')["reply"] == "x"
    assert llm.extract_json("just prose") == {}
    assert llm.extract_json("") == {}
