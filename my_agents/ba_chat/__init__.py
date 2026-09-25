"""Standalone BA chatbot — conversational requirements elicitation.

Zero ADK imports by design: plain Python + FastAPI + LiteLLM, talking to the
same OpenAI-compatible gateway (``LLM_API_BASE``/``LLM_API_KEY``/``LLM_MODEL``)
the pipeline uses. A deterministic state machine (``engine``) runs the
interview; the LLM only does language — questions, extraction, classification.
Coded validators in ``catalogue`` decide what counts as fulfilled.
"""
