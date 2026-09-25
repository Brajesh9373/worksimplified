"""Gateway client for the BA chatbot — LiteLLM, same triple as the pipeline.

Reads ``LLM_API_BASE`` / ``LLM_API_KEY`` / ``LLM_MODEL`` (plus the
``LLM_MODEL_BA`` override) at call time so the Connectors tab and ``start.sh``
can change them without a restart. The API key never leaves the server:
browsers only ever see the reply text.
"""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_BASE = "https://api.commandcode.ai/provider/v1"
DEFAULT_MODEL = "openai/deepseek/deepseek-v4.1-flash"


def settings() -> dict[str, str]:
    """Live LLM settings. Empty key means: tell the user to run start.sh."""
    return {"api_base": (os.getenv("LLM_API_BASE") or DEFAULT_BASE).strip(),
            "api_key": (os.getenv("LLM_API_KEY") or "").strip(),
            "model": (os.getenv("LLM_MODEL_BA") or os.getenv("LLM_MODEL")
                      or DEFAULT_MODEL).strip()}


def complete(messages: list[dict[str, Any]], *, temperature: float = 0.2,
             max_tokens: int = 800, timeout: int = 60) -> str:
    """One chat-completions call. Raises RuntimeError with a user-safe message."""
    cfg = settings()
    if not cfg["api_key"]:
        raise RuntimeError("no LLM API key — run ./start.sh and set one first")
    try:
        import litellm
    except Exception as exc:                                 # pragma: no cover
        raise RuntimeError(f"LLM library missing: {exc}") from exc
    try:
        response = litellm.completion(
            model=cfg["model"], api_base=cfg["api_base"], api_key=cfg["api_key"],
            temperature=temperature, max_tokens=max_tokens, messages=messages,
            num_retries=1, timeout=timeout)
        return ((response.choices[0].message.content) or "").strip()
    except Exception as exc:
        raise RuntimeError(f"the language service did not answer ({exc})") from exc


def extract_json(text: str) -> dict[str, Any]:
    """Parse the model's structured reply; repairs bare-prose wrappers."""
    t = (text or "").strip()
    if not t:
        return {}
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        try:
            data = json.loads(t[start:end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}
