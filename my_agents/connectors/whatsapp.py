"""WhatsApp channel — Python side of the Baileys bridge.

The Node sidecar (`whatsapp_bridge/`) emulates a WhatsApp Web session, pairs by
QR and shuttles messages over local HTTP:

    bridge  --POST /channels/whatsapp/inbound-->  this service
    this service  --POST {bridge}/send-->         bridge

Access control lives here, not in the bridge: with no allowlist configured the
channel **denies everyone** rather than answering strangers.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config
from .core import ChannelCore
from .telegram import chunk

log = logging.getLogger("connectors.whatsapp")


def normalize(number: str) -> str:
    """Digits only — WhatsApp/Baileys ids carry suffixes we do not want in a session id."""
    raw = str(number or "").split("@", 1)[0]
    return "".join(ch for ch in raw if ch.isdigit())


def is_allowed(number: str) -> bool:
    """Allowlist check. Unset allowlist = deny all (safe default)."""
    allowed = config.whatsapp_allowed_users()
    if allowed == "*":
        return True
    if not allowed:
        return False
    digits = normalize(number)
    return digits in allowed or str(number) in allowed


class WhatsAppBridge:
    """Client for the Node bridge's send endpoint."""

    name = "whatsapp"

    def __init__(self, *, url: str = "", client: httpx.AsyncClient | None = None):
        self._url = (url or config.whatsapp_bridge_url()).rstrip("/")
        self._client = client

    async def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30)
        return self._client

    async def send(self, to: str, text: str) -> bool:
        client = await self._client_or_new()
        ok = True
        for part in chunk(text):
            try:
                resp = await client.post(f"{self._url}/send",
                                         json={"to": to, "text": part})
                ok = ok and resp.status_code < 400
            except Exception as e:
                log.warning("whatsapp send failed: %s", e)
                return False
        return ok

    async def health(self) -> dict:
        client = await self._client_or_new()
        try:
            resp = await client.get(f"{self._url}/health")
            data = resp.json()
            return data if isinstance(data, dict) else {"ok": resp.status_code < 400}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    async def qr(self) -> dict:
        """The pairing code the bridge is currently showing, if any.

        Baileys rotates it until the phone scans it, so a caller reads this fresh
        rather than caching it.
        """
        client = await self._client_or_new()
        try:
            resp = await client.get(f"{self._url}/qr")
            if resp.status_code >= 400:
                return {"ok": False, "qr": "", "connected": False,
                        "error": f"bridge answered HTTP {resp.status_code}"}
            data = resp.json()
            if not isinstance(data, dict):
                return {"ok": False, "qr": "", "connected": False,
                        "error": "bridge sent an unexpected reply"}
            return {"ok": True, "qr": str(data.get("qr") or ""),
                    "connected": bool(data.get("connected")), "error": ""}
        except Exception as e:
            return {"ok": False, "qr": "", "connected": False, "error": str(e)[:200]}

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass


async def handle_inbound(core: ChannelCore, payload: dict[str, Any],
                         bridge: WhatsAppBridge | None = None) -> dict:
    """Handle one inbound WhatsApp message posted by the bridge.

    Returns ``{"ok": bool, "handled": bool, "reason": str}`` — never raises, so a
    malformed payload cannot take the webhook down.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "handled": False, "reason": "bad payload"}
    sender = str(payload.get("from") or payload.get("sender") or "").strip()
    text = str(payload.get("text") or payload.get("body") or "").strip()
    if not sender or not text:
        return {"ok": False, "handled": False, "reason": "missing from/text"}
    if not is_allowed(sender):
        log.info("whatsapp: denied %s", normalize(sender))
        return {"ok": True, "handled": False, "reason": "not allowed"}

    bridge = bridge or WhatsAppBridge()
    number = normalize(sender)

    async def send(reply: str) -> None:
        await bridge.send(number, reply)

    await core.submit(WhatsAppBridge.name, number, text, send=send)
    return {"ok": True, "handled": True, "reason": ""}
