"""Shared LLM factory for all engineering agents (orchestration infra).

OpenAI-compatible endpoint (CommandCode gateway). Agent system prompts are
NOT defined here — this module only builds the model client.

Env overrides (also read from agent .env by ADK):
  LLM_API_BASE, LLM_API_KEY, LLM_MODEL
  Obsolete META_* names are still honored as fallback.
"""

from __future__ import annotations

import os
from typing import Any

from google.adk.models.lite_llm import LiteLlm

LLM_API_BASE = os.getenv(
    "LLM_API_BASE",
    os.getenv("META_API_BASE", "https://bedrock-mantle.us-east-1.api.aws/v1"),
)
LLM_API_KEY = os.getenv(
    "LLM_API_KEY",
    os.getenv(
        "META_API_KEY",
        "bedrock-api-key-YmVkcm9jay5hbWF6b25hd3MuY29tLz9BY3Rpb249Q2FsbFdpdGhCZWFyZXJUb2tlbiZYLUFtei1BbGdvcml0aG09QVdTNC1ITUFDLVNIQTI1NiZYLUFtei1DcmVkZW50aWFsPUFTSUFYWDROQ1BYSFVOQVZZRUIyJTJGMjAyNjA5MTglMkZ1cy1lYXN0LTElMkZiZWRyb2NrJTJGYXdzNF9yZXF1ZXN0JlgtQW16LURhdGU9MjAyNjA5MThUMDgyMTMyWiZYLUFtei1FeHBpcmVzPTQzMjAwJlgtQW16LVNlY3VyaXR5LVRva2VuPUlRb0piM0pwWjJsdVgyVmpFSGthQ1hWekxXVmhjM1F0TVNKR01FUUNJRXoyY3hacXRZNnE3SnBhbW53ZCUyQmc0NnVTMm11Q21yQ1NKbFZ6OUVObHUwQWlBRCUyRjczaVAxYXpwMkpDbU0ySDRSYTJNQ1BOckJBdFlJZXpiNmJ3TUMxQmppcnRBZ2hCRUFBYUREVXpNak16TkRrek5UVXdNeUlNMkhqSHNPaUNFR3RFbzNLUEtzb0MxZkhtd1Bja3clMkIzeTBWYmFPek5kZVFDRDB6cUhsY0lySDhjelFHcFc0ZnI4MGd2cENGYkJSQlJHWWZJRGhxN1d3clh1N2tlUkV1MGJKNmM2JTJGYVpBT2ZIWGw4eUM1SUkwSk03ckx0b0cwZ01MOVNlOXliNlFqMGY1bmZlNjdteVRXU250VVQ0cG42YllOd0pKaDdKYTBVb1lwTVltTlRHR0M5Y1NlZEFvOVpLejNWMHZkZCUyRm1PMUZURWM0UE4wRjQwTDZZb0RSRWJidlJxRXUxd1pjMm5IdnljTDFneW5nRGMzelhwZXFKNkNMJTJGZGkyTk5oaGpaTTZMTGlPQlFEdVQxaVVVVSUyQnNTMXNsMkxjemhlZVRuZiUyRjlhaVcwbjJHYlJzZmUyYWExUzdLUzVmU2FoelJycWI2WGI0MFdUellSWmpseTBwWiUyQnhIV25QRXN2ZTh4c3ZSczB5VndkRFJQekF3blBUTFNjdlAzNjBRJTJCRTlnajZXaDBPRkolMkJ3cWlMdVo4eU9xYnV1a1BQR3dwNFglMkZhQmxNdjZPZ1pBQ01WNUt3T0xRSUxJaXVWb250aG1qdkZwaWtzUyUyRnVNT1M4czlVR09wQUNubkszOXdZZWVrQSUyQjlrQTdXcmhKbkJPbldDUWtiJTJGTE93TDNFZTdsS3F6ckhRZVdaSlhuZTlOY1N2MHM5TTlqUHklMkJqZll5WlhTaGxOTjdqQWVBRnROc0U3akV6NVYwaXhLd1hYR1Y1ekclMkJHSjU4dW5DbU9hT043ckRsYmlDWGk0V3pXZ1J2WGZNbUZ2OVRSNTk1eVI5TWNVJTJGR1BsRUlSczUzWEVuTHRpMyUyRkMwNEglMkJRM2RwdmQ3YkJyJTJGdmgwSWRZZDFRYXd5aDVzVDYzTUFDRiUyRkFzbDlnbjRxMGFZM09pdEE0SiUyRms2Q01qamh3NUhxS3A4MFJueUF6diUyQklvZHBJZUxCMm1ReE1qRVNtQms0YU5sQWtCZmh1NVdiZThiYU13eTlISkxVdGFQUWJkeEFEaFdVJTJGMzFZaVRmMEJHYmJDb0Z6N0xTUDJrTDRhcFAzSWJOck1NazNzRWhqZ1plM0ElMkJlUFd0VmYwY0ZuQSUzRCZYLUFtei1TaWduYXR1cmU9MDdhZTcxN2Q4ODdlMTU3M2VkODM4MzE4MTJhMzZiMzNjNGJmMWNkMzFmZmI4ODQwNmE4NmZhZTgwMmJhNmU5NSZYLUFtei1TaWduZWRIZWFkZXJzPWhvc3QmVmVyc2lvbj0x",
    ),
)
LLM_MODEL = os.getenv(
    "LLM_MODEL",
    os.getenv("META_MODEL", "openai.gpt-oss-120b"),
)
# Raised for full BRD + migration flows. Env overrides allowed.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", os.getenv("META_MAX_TOKENS", "16000")))

# A tool-calling stage re-sends its whole transcript on every round. On the live
# FRAPPE run the request grew 76k -> 154k -> 167k -> 189k -> 200k chars over six
# calls and the gateway then stalled with no response for the full 300s timeout,
# twice. The first content is the handoff brief and already carries every document
# in full, so the oldest middle turns are pure duplication and can be dropped.
# The bound is a stall guardrail, not a diet: a 90k budget proved too aggressive —
# the tool history got dropped and the agent re-did work instead of finishing.
# Requests up to ~155k completed fine; the gateway stalled around 200k.
MAX_REQUEST_CHARS = int(os.getenv("LLM_MAX_REQUEST_CHARS", "150000"))


def _content_chars(content: Any) -> int:
    try:
        return len(str(content))
    except Exception:
        return 0


def _is_tool_result(content: Any) -> bool:
    for part in (getattr(content, "parts", None) or []):
        if getattr(part, "function_response", None) is not None:
            return True
    return False


# Dropping an oversized tool result outright makes the model repeat the same call
# forever, so the result is truncated and kept: the loop can still progress.
_MAX_CONTENT_CHARS = int(os.getenv("LLM_MAX_CONTENT_CHARS", "40000"))


def _cap_content(content: Any, cap: int = _MAX_CONTENT_CHARS) -> None:
    """Truncate oversized text/response payloads inside one content, in place."""
    for part in (getattr(content, "parts", None) or []):
        text = getattr(part, "text", None)
        if isinstance(text, str) and len(text) > cap:
            try:
                part.text = text[:cap] + "\n[... clipped ...]"
            except Exception:
                pass
        response = getattr(part, "function_response", None)
        payload = getattr(response, "response", None) if response is not None else None
        if isinstance(payload, dict):
            for key, value in list(payload.items()):
                if isinstance(value, str) and len(value) > cap:
                    payload[key] = value[:cap] + "\n[... clipped ...]"


def trim_request_contents(contents: list, max_chars: int = MAX_REQUEST_CHARS) -> list:
    """Keep the first content (the brief) plus the newest tail that fits the budget.

    Trims in whole turns and never starts the tail on a bare tool result: the
    provider rejects a function response whose function call was dropped.
    """
    if not contents:
        return contents
    if len(contents) < 4 or sum(_content_chars(c) for c in contents) <= max_chars:
        return contents
    head, body = contents[0], list(contents[1:])
    budget = max_chars - _content_chars(head)
    if budget <= 0:
        return [head]
    tail: list = []
    used = 0
    for content in reversed(body):
        n = _content_chars(content)
        if used + n > budget:
            break
        tail.append(content)
        used += n
    tail.reverse()
    # a tool result may never lead: keep its function call with it (the provider
    # rejects a function response whose function call was dropped)
    while tail and _is_tool_result(tail[0]):
        idx = body.index(tail[0])
        if idx == 0:
            tail.pop(0)
            break
        tail.insert(0, body[idx - 1])
    if len(tail) >= len(body):
        return contents
    return [head] + tail


class BoundedLiteLlm(LiteLlm):
    """LiteLlm that keeps each outgoing request inside a character budget.

    Truncate first (keeps every turn, so the tool loop still advances), then drop
    only the oldest middle turns if the request is somehow still oversized.
    """

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        try:
            contents = list(getattr(llm_request, "contents", None) or [])
            if len(contents) > 1:
                for content in contents[1:]:      # the brief (head) stays whole
                    _cap_content(content)
            if sum(_content_chars(c) for c in contents) > MAX_REQUEST_CHARS:
                contents = trim_request_contents(contents, MAX_REQUEST_CHARS)
            llm_request.contents = contents
        except Exception:
            pass
        async for event in super().generate_content_async(llm_request, stream=stream):
            yield event


LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", os.getenv("META_TEMPERATURE", "0.3")))

# Per-agent overrides: LLM_MODEL_<AGENT> / LLM_TEMPERATURE_<AGENT>
# (e.g. LLM_MODEL_PROJECT, LLM_TEMPERATURE_FRAPPE), falling back to LLM_MODEL.
AGENTS = ("ba", "project", "functional", "technical", "frappe", "escalation")


def agent_model(agent: str = "") -> str:
    """Resolve the model id for one agent (per-agent env, then global default)."""
    if agent:
        override = os.getenv(f"LLM_MODEL_{agent.upper()}")
        if override:
            return override
    return LLM_MODEL


def agent_temperature(agent: str = "") -> float:
    """Resolve the sampling temperature for one agent."""
    if agent:
        override = os.getenv(f"LLM_TEMPERATURE_{agent.upper()}")
        if override:
            try:
                return float(override)
            except ValueError:
                pass
    return LLM_TEMPERATURE


def model_string(agent: str = "") -> str:
    """The provider-qualified model id LiteLLM needs (single source of truth).

    Model-string routing for OpenAI-compatible gateways (LiteLLM sends
    everything after the first "/" as the remote model name, and needs a "/" to
    detect the provider):
    - no slash (openai.gpt-oss-120b) -> "openai/<id>" (Bedrock Mantle style)
    - slash IDs (openai/auto, deepseek/...) -> used as-is (NIM / gateway style)
    """
    model_id = agent_model(agent)
    return model_id if "/" in model_id else f"openai/{model_id}"


def get_meta_model(agent: str = "") -> LiteLlm:
    """Return a LiteLlm client pointed at the configured endpoint.

    Pass the agent key ("ba", "project", "functional", "technical", "frappe",
    "escalation") to honor per-agent model/temperature overrides; omit it for
    the global default. Agent instructions live with the agents, not here.

    NOTE: do NOT pass max_tokens/max_completion_tokens here — ADK maps
    generate_content_config.max_output_tokens -> max_completion_tokens
    automatically, and passing both triggers LiteLLM's mutual-exclusion error.
    (Name kept as get_meta_model so agent modules need no changes.)
    """
    return BoundedLiteLlm(
        model=model_string(agent),
        api_base=LLM_API_BASE,
        api_key=LLM_API_KEY,
        temperature=agent_temperature(agent),
        num_retries=2,
        timeout=300,
    )
