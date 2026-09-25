"""Configuration for the channel connectors.

Env-driven and total: every accessor returns a usable value and never raises, so
a missing variable degrades a channel instead of breaking the service. Values are
read at call time (not import time) so tests can monkeypatch the environment.

Naming follows the house convention in ``shared/meta_model.py``: a channel-specific
override wins over the generic name, which wins over the default.
"""

from __future__ import annotations

import os
from pathlib import Path

CONNECTORS_DIR = Path(__file__).resolve().parent

CHANNELS = ("telegram", "whatsapp", "web")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name, "") or default).strip()


def _env_any(names: tuple[str, ...], default: str = "") -> str:
    for n in names:
        v = _env(n)
        if v:
            return v
    return default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _bool(names: tuple[str, ...], default: bool = False) -> bool:
    v = _env_any(names).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


# ---------------------------------------------------------------- service

def adk_app_name() -> str:
    """The ADK app the connectors drive (the pipeline itself)."""
    return _env("ADK_APP_NAME", "delivery_pipeline")


def host() -> str:
    return _env("CONNECTORS_HOST", "127.0.0.1")


def port() -> int:
    # 8765+: 8000 and the neighbouring well-known ports are taken by other
    # services on the server, so the console starts high and walks up
    return _int("CONNECTORS_PORT", 8765)


def state_dir() -> Path:
    p = Path(_env("CONNECTORS_STATE_DIR", str(CONNECTORS_DIR / ".state")))
    return p


def env_path() -> Path:
    """The dotenv file the Connectors tab writes settings to."""
    return Path(_env("CONNECTORS_ENV_FILE", str(CONNECTORS_DIR / ".env")))


def session_db() -> Path:
    p = Path(_env("CONNECTORS_SESSION_DB", str(CONNECTORS_DIR / ".adk" / "session.db")))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def ack_text() -> str:
    return _env("CONNECTORS_ACK_TEXT", "Got it — working on that now.")


# ---------------------------------------------------------------- telegram

def telegram_token() -> str:
    """Bot token; empty means the long-poll worker idles (service still starts)."""
    return _env_any(("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"))


def telegram_api_base() -> str:
    return _env("TELEGRAM_API_BASE", "https://api.telegram.org")


def telegram_poll_timeout() -> int:
    return _int("TELEGRAM_POLL_TIMEOUT", 25)


def telegram_enabled() -> bool:
    return _bool(("TELEGRAM_ENABLED",), default=bool(telegram_token()))


# ---------------------------------------------------------------- whatsapp

def whatsapp_enabled() -> bool:
    return _bool(("WHATSAPP_ENABLED",), default=False)


def whatsapp_bridge_url() -> str:
    return _env("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:8081").rstrip("/")


def whatsapp_allowed_users() -> set[str] | str | None:
    """``"*"`` = allow all, a set = allowlist, ``None`` = deny all (safe default)."""
    raw = _env_any(("WHATSAPP_ALLOWED_USERS",))
    if raw == "*":
        return "*"
    if _bool(("WHATSAPP_ALLOW_ALL_USERS",), default=False):
        return "*"
    if not raw:
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def whatsapp_session_dir() -> Path:
    return Path(_env("WHATSAPP_SESSION_DIR", str(CONNECTORS_DIR / "whatsapp_bridge" / "session")))


def whatsapp_inbound_url() -> str:
    """Where the Node bridge posts inbound messages (our own service)."""
    return _env("WHATSAPP_INBOUND_URL",
                f"http://127.0.0.1:{port()}/channels/whatsapp/inbound")


# ---------------------------------------------------------------- limits

def message_chunk() -> int:
    """Max characters per outbound message (Telegram 4096, WhatsApp ~4096)."""
    return _int("CONNECTORS_CHUNK", 4000)


def upload_max_bytes() -> int:
    return _int("CONNECTORS_UPLOAD_MAX_BYTES", 5_000_000)


def upload_exts() -> tuple[str, ...]:
    """Extensions accepted at intake.

    Text-like files decode directly; `.pdf` and `.docx` are extracted with the
    optional `pypdf` / `python-docx` readers (see the connector README).
    """
    raw = _env("CONNECTORS_UPLOAD_EXTS",
               ".txt,.md,.markdown,.csv,.json,.log,.yaml,.yml,.pdf,.docx")
    return tuple(x.strip().lower() for x in raw.split(",") if x.strip())
