"""Telegram and WhatsApp channel adapters (offline, no network).

The Telegram Bot API and the WhatsApp bridge are both plain HTTP, so an
``httpx.MockTransport`` exercises the real adapter code without sockets.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from connectors import config  # noqa: E402
from connectors.telegram import TelegramBot, chunk  # noqa: E402
from connectors.whatsapp import WhatsAppBridge, handle_inbound, is_allowed, normalize  # noqa: E402

STATE = MY_AGENTS / "_outputs" / "connector_state_test"


class StubCore:
    """Records submissions and echoes a reply through the channel's send()."""

    def __init__(self):
        self.seen: list[tuple] = []

    async def submit(self, channel, external_id, text, send=None, attachments=None,
                     ack=True):
        self.seen.append((channel, external_id, text))
        if send is not None:
            await send(f"reply:{text}")


@pytest.fixture(autouse=True)
def _clean():
    shutil.rmtree(STATE, ignore_errors=True)
    yield
    shutil.rmtree(STATE, ignore_errors=True)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- telegram

def test_telegram_poll_forwards_and_replies():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        calls.append((request.url.path, body))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 7, "message": {"chat": {"id": 42}, "text": "hello"}}]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    core = StubCore()
    bot = TelegramBot(core, token="tok", api_base="https://tg.local",
                      client=_client(handler), offset_path=STATE / "offset.json")

    handled = asyncio.run(bot.poll_once(timeout=0))

    assert handled == 1
    assert core.seen == [("telegram", "42", "hello")]
    sent = [b for p, b in calls if p.endswith("/sendMessage")]
    assert sent and sent[0]["chat_id"] == 42 and sent[0]["text"] == "reply:hello"
    # offset advanced past the consumed update so it is never replayed
    assert json.loads((STATE / "offset.json").read_text())["offset"] == 8


def test_telegram_ignores_updates_without_text():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [
                {"update_id": 1, "message": {"chat": {"id": 9}}}]})
        return httpx.Response(200, json={"ok": True, "result": {}})

    core = StubCore()
    bot = TelegramBot(core, token="tok", api_base="https://tg.local",
                      client=_client(handler), offset_path=STATE / "o.json")
    assert asyncio.run(bot.poll_once(timeout=0)) == 0
    assert core.seen == []


def test_telegram_disabled_without_a_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    assert TelegramBot(StubCore(), token="").enabled is False
    assert TelegramBot(StubCore(), token="abc").enabled is True


def test_chunk_splits_long_replies():
    assert chunk("short", limit=100) == ["short"]
    parts = chunk(("word " * 400).strip(), limit=200)
    assert len(parts) > 1
    assert all(len(p) <= 200 for p in parts)
    assert "".join(p.replace(" ", "") for p in parts) == ("word" * 400)


# ---------------------------------------------------------------- whatsapp

def test_normalize_and_allowlist(monkeypatch):
    assert normalize("919812345678@s.whatsapp.net") == "919812345678"

    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    assert is_allowed("9198") is False            # unset = deny all

    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "919812345678")
    assert is_allowed("919812345678@s.whatsapp.net") is True
    assert is_allowed("4400000000000") is False

    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    assert is_allowed("anyone") is True


def test_whatsapp_inbound_forwards_and_replies(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    core = StubCore()
    sent: list[tuple[str, str]] = []

    class Bridge:
        async def send(self, to, text):
            sent.append((to, text))
            return True

    result = asyncio.run(handle_inbound(core, {"from": "919812345678@s.whatsapp.net",
                                               "text": "I need an ERP"}, Bridge()))

    assert result == {"ok": True, "handled": True, "reason": ""}
    assert core.seen == [("whatsapp", "919812345678", "I need an ERP")]
    assert sent == [("919812345678", "reply:I need an ERP")]


def test_whatsapp_denies_when_not_allowlisted(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ALLOWED_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    core = StubCore()

    result = asyncio.run(handle_inbound(core, {"from": "1555", "text": "hi"}, None))

    assert result["handled"] is False and result["reason"] == "not allowed"
    assert core.seen == []


def test_whatsapp_rejects_malformed_payload():
    core = StubCore()
    assert asyncio.run(handle_inbound(core, {"from": "", "text": ""}, None))["ok"] is False
    assert asyncio.run(handle_inbound(core, "not-a-dict", None))["ok"] is False


def test_whatsapp_bridge_client_posts_to_send():
    calls: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        return httpx.Response(200, json={"ok": True})

    bridge = WhatsAppBridge(url="http://bridge.local",
                            client=_client(handler))
    ok = asyncio.run(bridge.send("123", "hello there"))

    assert ok is True
    assert calls == [("/send", {"to": "123", "text": "hello there"})]
    assert config.message_chunk() > 0
