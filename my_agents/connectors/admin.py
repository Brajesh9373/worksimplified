"""Connector setup for whoever runs the service.

    GET  /api/connectors              what each channel is set to (no secrets)
    POST /api/connectors/telegram     save the token/settings, restart the worker
    POST /api/connectors/whatsapp     save the bridge settings, re-point the client

Settings are written to the connectors dotenv file so they survive a restart, and
applied to the running process immediately.

Secrets are never returned. Telegram is identified by the username its token
resolves to via ``getMe`` — enough to confirm the right bot is connected without
handing the token back to the browser.

Note there is no login on this API: it configures the service for whoever can
reach it, which is the whole model of the loopback-bound service.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import config
from .core import ChannelCore
from .telegram import TelegramBot
from .whatsapp import WhatsAppBridge

log = logging.getLogger("connectors.admin")

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")
_TOKEN_RX = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")

BRIDGE_COMMAND = ("cd my_agents/connectors/whatsapp_bridge && npm install && "
                  "node index.js")


# --------------------------------------------------------------------- writing

def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Set ``KEY=value`` in a dotenv file, preserving every other line.

    An existing uncommented assignment is replaced in place; a key that is only
    documented as a comment is appended, so the file keeps its shape.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    if remaining:
        if out and out[-1].strip():
            out.append("")
        out.append("# set from the Connectors tab")
        out.extend(f"{key}={value}" for key, value in remaining.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(path)


def _apply(updates: dict[str, str]) -> None:
    """Persist to the dotenv file, then make it live for this process."""
    update_env_file(config.env_path(), updates)
    for key, value in updates.items():
        os.environ[key] = value


# ---------------------------------------------------------------- validation

def _as_bool(value: Any) -> bool | None:
    """True/False from the usual spellings, or None when it cannot be read."""
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    if not text:
        return None
    return None


def _token_error(token: str) -> str:
    if not token:
        return "the token is empty"
    if not _TOKEN_RX.match(token):
        return ("that does not look like a bot token — @BotFather sends it as "
                "digits:letters, paste the whole thing")
    return ""


def _bridge_url_error(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return "the bridge address is empty"
    if not text.startswith(("http://", "https://")):
        return "the bridge address must start with http:// or https://"
    return ""


def _allowed_error(value: str) -> str:
    text = (value or "").strip()
    if text in ("", "*"):
        return ""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    bad = [p for p in parts if not p.lstrip("+").isdigit()]
    if bad:
        return ("use digits with the country code, comma separated, or * for "
                "anyone — these are not numbers: " + ", ".join(bad[:3]))
    return ""


# ------------------------------------------------------------------ runtime

class ConnectorRuntime:
    """Owns the long-lived channel workers so the Connectors tab can apply a change.

    A Telegram bot reads its token when it is constructed, so saving a new token
    rebuilds the worker rather than mutating the running one; the WhatsApp client
    captures its bridge address the same way.
    """

    def __init__(self, core: ChannelCore, *, start_telegram: bool = True):
        self.core = core
        self._worker_wanted = start_telegram
        self.bridge = WhatsAppBridge()
        self.bot: TelegramBot | None = None
        self.task: asyncio.Task | None = None
        self.bot_username = ""

    @property
    def telegram_running(self) -> bool:
        return self.task is not None and not self.task.done()

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.bot is not None and self.bot.enabled)

    def start_telegram(self) -> None:
        if not self._worker_wanted:
            return
        self.bot = TelegramBot(self.core)
        self.task = asyncio.create_task(self.bot.run_forever())

    async def stop_telegram(self) -> None:
        if self.bot is not None:
            self.bot.stop()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
            self.task = None
        if self.bot is not None:
            await self.bot.aclose()
            self.bot = None

    async def restart_telegram(self) -> None:
        await self.stop_telegram()
        self.bot_username = ""
        self.start_telegram()

    def re_point_bridge(self) -> WhatsAppBridge:
        """A new bridge client after its address changes."""
        old, self.bridge = self.bridge, WhatsAppBridge()
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(old.aclose())
        except Exception:
            pass
        return self.bridge


# ------------------------------------------------------------------- status

def telegram_status(runtime: ConnectorRuntime) -> dict:
    """What Telegram is set to. The token is never included, only whether it is set."""
    token = config.telegram_token()
    return {
        "channel": "telegram",
        "configured": bool(token),
        "enabled": bool(config.telegram_enabled()),
        "live": runtime.telegram_running,
        "bot_username": runtime.bot_username,
        "poll_timeout": config.telegram_poll_timeout(),
    }


async def whatsapp_status(runtime: ConnectorRuntime) -> dict:
    """What WhatsApp is set to, plus whether its bridge is up and whether it is paired."""
    enabled = bool(config.whatsapp_enabled())
    allowed = config.whatsapp_allowed_users()
    # the channel being off does not mean the bridge is down: pairing happens before
    # the switch, so its state is still worth reporting
    health = await runtime.bridge.health()
    return {
        "channel": "whatsapp",
        "configured": bool(config.whatsapp_bridge_url()),
        "enabled": enabled,
        "linked": bool(health.get("connected")),
        "online": bool(health.get("ok")),
        "has_qr": bool(health.get("has_qr")),
        "bridge_url": config.whatsapp_bridge_url(),
        "allow_all": allowed == "*",
        "allowed_users": [] if allowed in (None, "*") else sorted(allowed or []),
        "bridge": health,
        "bridge_command": BRIDGE_COMMAND,
    }


# ------------------------------------------------------------------- routes

def qr_svg(text: str) -> str:
    """An SVG QR code for ``text``, or "" when no renderer is installed."""
    if not text:
        return ""
    try:
        import io

        import segno
    except Exception:
        return ""
    try:
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="svg", scale=4, border=2,
                                         xmldecl=False)
        return buf.getvalue().decode("utf-8")
    except Exception:
        log.warning("could not render a QR code", exc_info=True)
        return ""


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _bad(message: str, code: int = 422) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code)


def router(runtime: ConnectorRuntime) -> APIRouter:
    api = APIRouter(prefix="/api/connectors")

    @api.get("")
    async def status() -> JSONResponse:
        return JSONResponse({
            "telegram": telegram_status(runtime),
            "whatsapp": await whatsapp_status(runtime),
            "env_path": str(config.env_path()),
        })

    @api.post("/telegram")
    async def save_telegram(request: Request) -> JSONResponse:
        body = await _json(request)
        updates: dict[str, str] = {}

        if "token" in body:
            token = str(body.get("token") or "").strip()
            err = _token_error(token)
            if err:
                return _bad(err)
            updates["TELEGRAM_BOT_TOKEN"] = token
        elif _as_bool(body.get("clear_token")):
            updates["TELEGRAM_BOT_TOKEN"] = ""

        if "enabled" in body:
            value = _as_bool(body.get("enabled"))
            if value is None:
                return _bad("enabled must be true or false")
            updates["TELEGRAM_ENABLED"] = "1" if value else "0"

        if "poll_timeout" in body:
            try:
                seconds = int(str(body.get("poll_timeout")).strip())
            except Exception:
                return _bad("poll timeout must be a whole number of seconds")
            if not 1 <= seconds <= 60:
                return _bad("poll timeout must be between 1 and 60 seconds")
            updates["TELEGRAM_POLL_TIMEOUT"] = str(seconds)

        if not updates:
            return _bad("nothing to change", 400)

        _apply(updates)
        await runtime.restart_telegram()
        check = (await runtime.bot.get_me() if runtime.bot is not None
                 else {"ok": False, "username": "", "name": "",
                       "error": "the worker is not running in this process"})
        if check.get("ok"):
            runtime.bot_username = str(check.get("username") or "")
        log.info("telegram settings saved: %s (token check ok=%s)",
                 sorted(updates), check.get("ok"))
        return JSONResponse({"ok": True, "saved": sorted(updates), "check": check,
                             "telegram": telegram_status(runtime)})

    @api.post("/whatsapp")
    async def save_whatsapp(request: Request) -> JSONResponse:
        body = await _json(request)
        updates: dict[str, str] = {}

        if "enabled" in body:
            value = _as_bool(body.get("enabled"))
            if value is None:
                return _bad("enabled must be true or false")
            updates["WHATSAPP_ENABLED"] = "1" if value else "0"

        if "bridge_url" in body:
            url = str(body.get("bridge_url") or "").strip()
            err = _bridge_url_error(url)
            if err:
                return _bad(err)
            updates["WHATSAPP_BRIDGE_URL"] = url.rstrip("/")

        if "allowed_users" in body:
            allowed = str(body.get("allowed_users") or "").strip()
            err = _allowed_error(allowed)
            if err:
                return _bad(err)
            cleaned = ",".join(p.strip() for p in allowed.split(",") if p.strip())
            updates["WHATSAPP_ALLOWED_USERS"] = cleaned or "*"

        if not updates:
            return _bad("nothing to change", 400)

        _apply(updates)
        if "WHATSAPP_BRIDGE_URL" in updates:
            runtime.re_point_bridge()
        status = await whatsapp_status(runtime)
        log.info("whatsapp settings saved: %s", sorted(updates))
        return JSONResponse({"ok": True, "saved": sorted(updates),
                             "whatsapp": status})

    @api.get("/whatsapp/qr")
    async def whatsapp_qr() -> JSONResponse:
        """The current pairing code, rendered, so it can be scanned from the console.

        Pairing comes before switching the channel on, so this is offered whenever the
        bridge is up — showing the code does not accept any messages by itself. The
        code is still a credential (whoever scans it links their own device to the
        number) and this API has no login, so keep the service on loopback.
        """
        def answer(ok: bool, reason: str, message: str, qr: str = "") -> JSONResponse:
            svg = qr_svg(qr)
            data_url = ("data:image/svg+xml;base64,"
                        + base64.b64encode(svg.encode("utf-8")).decode("ascii")) if svg else ""
            return JSONResponse({"ok": ok and bool(data_url), "reason": reason,
                                 "message": message, "qr": qr, "svg": svg,
                                 "data_url": data_url})

        result = await runtime.bridge.qr()
        if not result.get("ok"):
            return answer(False, "unreachable",
                          "No bridge answering on " + config.whatsapp_bridge_url()
                          + " — start it and the code appears here instead of in the "
                            "terminal.")
        if result.get("connected"):
            return answer(True, "linked", "This number is linked.")
        code = str(result.get("qr") or "")
        if not code:
            return answer(False, "waiting",
                          "The bridge is up but has not produced a code yet — it "
                          "refreshes every few seconds.")
        if not qr_svg(code):
            return answer(False, "no-renderer",
                          "Install the 'segno' package to show the code here "
                          "(pip install segno), or scan it in the bridge's terminal.")
        return answer(True, "qr", "Point WhatsApp at this code.", code)

    return api
