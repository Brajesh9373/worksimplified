"""Connectors tab API: status, saving, validation, and secret redaction.

Offline: the app is built with the Telegram worker off, so saving settings never
dials out. The dotenv target is redirected to a temp file so a test can never
clobber the real connectors/.env.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

MY_AGENTS = Path(__file__).resolve().parents[1]
if str(MY_AGENTS) not in sys.path:
    sys.path.insert(0, str(MY_AGENTS))

from connectors.admin import update_env_file  # noqa: E402
from connectors.app import build_app  # noqa: E402
from connectors.whatsapp import WhatsAppBridge  # noqa: E402

TOKEN = "123456789:AAF-abcdefghijklmnopqrstuvwxyz12"
_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ENABLED", "TELEGRAM_POLL_TIMEOUT",
             "WHATSAPP_ENABLED", "WHATSAPP_BRIDGE_URL", "WHATSAPP_ALLOWED_USERS")


class StubCore:
    async def ask(self, channel, external_id, text):
        return []

    async def bound_workspace(self, channel, external_id):
        return ""


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """Point settings at a throwaway file and a clean environment.

    The bridge address is a dead port so these tests never depend on a real bridge
    happening to be running on this machine.
    """
    path = tmp_path / "connectors.env"
    monkeypatch.setenv("CONNECTORS_ENV_FILE", str(path))
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # set last: _ENV_KEYS includes the bridge address, so clearing the environment
    # afterwards would put the default (and this machine's real bridge) back
    monkeypatch.setenv("WHATSAPP_BRIDGE_URL", "http://127.0.0.1:9")
    return path


@pytest.fixture()
def client(env_file):
    return TestClient(build_app(core=StubCore(), start_telegram=False))


def test_status_reports_both_channels_and_never_a_secret(client, env_file, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    body = client.get("/api/connectors").json()

    assert body["telegram"]["configured"] is True
    assert body["telegram"]["enabled"] is True
    # WhatsApp is off and unpaired here, and says so rather than guessing
    assert body["whatsapp"]["enabled"] is False
    assert body["whatsapp"]["online"] is False
    assert body["whatsapp"]["linked"] is False
    assert body["whatsapp"]["bridge_command"].endswith("node index.js")
    assert body["env_path"] == str(env_file)

    # the whole point: the token is never handed back to the browser
    assert TOKEN not in json.dumps(body)
    assert "token" not in body["telegram"]


def test_saving_a_telegram_token_writes_the_file_and_applies_it(client, env_file):
    resp = client.post("/api/connectors/telegram",
                       json={"token": TOKEN, "poll_timeout": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert "TELEGRAM_BOT_TOKEN" in body["saved"]
    assert TOKEN not in json.dumps(body)          # saved, never echoed
    assert body["telegram"]["configured"] is True
    assert body["telegram"]["poll_timeout"] == 30
    # the worker is off in this app, so it says so rather than claiming a live bot
    assert body["check"]["ok"] is False
    assert "not running" in body["check"]["error"]

    written = env_file.read_text()
    assert f"TELEGRAM_BOT_TOKEN={TOKEN}" in written
    assert "TELEGRAM_POLL_TIMEOUT=30" in written
    assert client.get("/api/connectors").json()["telegram"]["configured"] is True


def test_clearing_a_token_turns_it_off(client, env_file, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    resp = client.post("/api/connectors/telegram", json={"clear_token": True})
    assert resp.status_code == 200
    assert resp.json()["telegram"]["configured"] is False
    assert "TELEGRAM_BOT_TOKEN=\n" in env_file.read_text()


def test_telegram_rejects_a_token_that_is_not_one(client):
    resp = client.post("/api/connectors/telegram", json={"token": "hello"})
    assert resp.status_code == 422
    assert "bot token" in resp.json()["error"]


def test_telegram_validates_the_poll_timeout(client):
    for bad in ("soon", 0, 90):
        resp = client.post("/api/connectors/telegram", json={"poll_timeout": bad})
        assert resp.status_code == 422, bad
    assert "between 1 and 60" in resp.json()["error"]


def test_whatsapp_saves_and_reflects_its_settings(client, env_file):
    resp = client.post("/api/connectors/whatsapp",
                       json={"enabled": "yes", "bridge_url": "http://127.0.0.1:8081/",
                             "allowed_users": "919812345678, 919800000000"})
    assert resp.status_code == 200
    wa = resp.json()["whatsapp"]
    assert wa["enabled"] is True and wa["configured"] is True
    assert wa["bridge_url"] == "http://127.0.0.1:8081"      # trailing slash trimmed
    assert wa["allowed_users"] == ["919800000000", "919812345678"]
    assert wa["allow_all"] is False
    assert "WHATSAPP_BRIDGE_URL=http://127.0.0.1:8081" in env_file.read_text()


def test_whatsapp_allow_all(client, env_file):
    resp = client.post("/api/connectors/whatsapp", json={"allowed_users": "*"})
    wa = resp.json()["whatsapp"]
    assert wa["allow_all"] is True and wa["allowed_users"] == []
    assert "WHATSAPP_ALLOWED_USERS=*" in env_file.read_text()


def test_whatsapp_rejects_nonsense(client):
    assert client.post("/api/connectors/whatsapp",
                       json={"bridge_url": "127.0.0.1:8081"}).status_code == 422
    resp = client.post("/api/connectors/whatsapp",
                       json={"allowed_users": "call-me, 919812345678"})
    assert resp.status_code == 422
    assert "not numbers" in resp.json()["error"]


def test_an_empty_save_is_rejected(client):
    assert client.post("/api/connectors/telegram", json={}).status_code == 400
    assert client.post("/api/connectors/whatsapp", json={}).status_code == 400


def test_qr_reports_why_there_is_nothing_to_scan(client):
    """The endpoint answers "is there a code to scan right now", and says why not."""
    body = client.get("/api/connectors/whatsapp/qr").json()
    assert body["ok"] is False and body["reason"] == "unreachable"
    assert "No bridge answering" in body["message"]


def test_qr_renders_the_bridge_code(client, env_file, monkeypatch):
    """The code is rendered to an SVG here, so the page needs no QR library.

    Pairing is offered whether or not the channel is switched on — linking the number
    is the step *before* enabling it, so a disabled channel must still show its code.
    """
    async def fake_qr(self):
        return {"ok": True, "qr": "2@AbC-dEf/GhI=", "connected": False, "error": ""}

    monkeypatch.setattr(WhatsAppBridge, "qr", fake_qr)

    body = client.get("/api/connectors/whatsapp/qr").json()
    assert body["reason"] == "qr" and body["ok"] is True
    assert body["qr"] == "2@AbC-dEf/GhI="
    assert body["svg"].startswith("<svg")
    assert body["data_url"].startswith("data:image/svg+xml;base64,")


def test_qr_is_quiet_once_the_number_is_linked(client, env_file, monkeypatch):
    async def linked(self):
        return {"ok": True, "qr": "", "connected": True, "error": ""}

    monkeypatch.setattr(WhatsAppBridge, "qr", linked)
    body = client.get("/api/connectors/whatsapp/qr").json()
    assert body["reason"] == "linked" and body["ok"] is False
    assert body["data_url"] == "" and body["message"] == "This number is linked."


def test_qr_waits_while_the_bridge_has_not_produced_one(client, env_file, monkeypatch):
    async def waiting(self):
        return {"ok": True, "qr": "", "connected": False, "error": ""}

    monkeypatch.setattr(WhatsAppBridge, "qr", waiting)
    body = client.get("/api/connectors/whatsapp/qr").json()
    assert body["reason"] == "waiting" and body["ok"] is False
    assert "refreshes every few seconds" in body["message"]


def test_qr_svg_is_empty_without_a_renderer(monkeypatch):
    """No 'segno' installed must degrade to a message, not a crash."""
    import builtins

    from connectors import admin

    real_import = builtins.__import__

    def no_segno(name, *args, **kwargs):
        if name == "segno":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_segno)
    assert admin.qr_svg("anything") == ""


def test_update_env_file_preserves_comments_and_other_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# a comment\nTELEGRAM_BOT_TOKEN=old\nOTHER=kept\n",
                    encoding="utf-8")
    update_env_file(path, {"TELEGRAM_BOT_TOKEN": "new", "ADDED": "1"})
    text = path.read_text()
    assert "# a comment" in text            # documentation survives
    assert "OTHER=kept" in text
    assert "TELEGRAM_BOT_TOKEN=new" in text and "old" not in text
    assert "ADDED=1" in text and "# set from the Connectors tab" in text
