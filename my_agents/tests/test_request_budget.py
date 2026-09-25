"""Tests for the request-size bound (shared.meta_model.trim_request_contents)."""
from __future__ import annotations

from google.genai import types


def _user(text):
    return types.Content(role="user", parts=[types.Part(text=text)])


def _model_call(call_id):
    return types.Content(role="model", parts=[types.Part(
        function_call=types.FunctionCall(id=call_id, name="read_output_file", args={}))])


def _tool_result(call_id, text):
    return types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=call_id, name="read_output_file", response={"content": text}))])


def _size(contents):
    return sum(len(str(c)) for c in contents)


def test_short_requests_are_untouched():
    from shared.meta_model import trim_request_contents

    contents = [_user("brief"), _user("hi")]
    assert trim_request_contents(contents, max_chars=100000) == contents


def test_oversized_request_keeps_the_brief_and_the_newest_turn():
    """A FRAPPE run grew 76k -> 154k -> 200k chars over six tool calls and the
    gateway stalled. The brief must always survive; old duplication goes."""
    from shared.meta_model import trim_request_contents

    brief = _user("B" * 40000)
    call = _model_call("1")
    result = _tool_result("1", "x" * 40000)
    ask = _model_call("2")
    contents = [brief, call, result, ask]

    out = trim_request_contents(contents, max_chars=50000)
    assert out[0] is brief
    assert _size(out) <= 50000
    assert out[-1] is ask


def test_oversized_tool_result_is_truncated_not_dropped():
    """Dropping an oversized result outright makes the model re-issue the same call
    forever (observed as a run of identical-size requests), so it is truncated and
    kept — the tool loop still advances."""
    from shared.meta_model import _MAX_CONTENT_CHARS, _cap_content

    result = _tool_result("1", "x" * (_MAX_CONTENT_CHARS + 5000))
    _cap_content(result)
    payload = result.parts[0].function_response.response["content"]
    assert len(payload) < _MAX_CONTENT_CHARS + 100
    assert payload.startswith("x") and "clipped" in payload
    assert result.parts[0].function_response is not None  # the turn survives

    big_text = _user("y" * (_MAX_CONTENT_CHARS + 5000))
    _cap_content(big_text)
    assert len(big_text.parts[0].text) < _MAX_CONTENT_CHARS + 100
    assert big_text.parts[0].text.endswith("[... clipped ...]")


def test_trim_never_leads_with_a_bare_tool_result():
    """Dropping a function call while keeping its response is rejected by the
    provider, so the pair has to travel together."""
    from shared.meta_model import trim_request_contents

    brief = _user("B" * 40000)
    call_a = _model_call("a")
    result_a = _tool_result("a", "x" * 9000)
    call_b = _model_call("b")
    result_b = _tool_result("b", "y" * 9000)
    out = trim_request_contents([brief, call_a, result_a, call_b, result_b],
                                max_chars=60000)
    assert out[0] is brief
    # the first non-brief content must be a model turn, never a tool result
    assert len(out) > 1
    assert getattr(out[1], "parts", None)
    assert out[1].parts[0].function_response is None
    assert out[1].parts[0].function_call is not None
