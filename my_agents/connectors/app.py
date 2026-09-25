"""Connectors service — one FastAPI app fronting Telegram, WhatsApp and the web.

    python -m connectors.app

Runs beside `adk web` (which stays the Desk UI for inspecting generated
projects). The Telegram long-poll worker starts on lifespan; the WhatsApp bridge
is a separate Node process that posts into `/channels/whatsapp/inbound`.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import config
from .admin import ConnectorRuntime
from .admin import router as admin_router
from .core import ChannelCore
from .web import router as web_router
from .whatsapp import handle_inbound

log = logging.getLogger("connectors")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

_state: dict = {}


def build_app(core: ChannelCore | None = None, *, start_telegram: bool = True) -> FastAPI:
    """Build the service. ``core`` is injectable for tests."""
    core = core or ChannelCore()
    # the runtime owns the channel workers so the Connectors tab can apply a change
    # without a restart; it is also what /health reports on
    runtime = ConnectorRuntime(core, start_telegram=start_telegram)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.start_telegram()
        app.state.core = core
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.stop_telegram()
            await runtime.bridge.aclose()

    app = FastAPI(title="WorkSimplified connectors", lifespan=lifespan)
    app.include_router(web_router(core))
    app.include_router(admin_router(runtime))

    @app.get("/health")
    async def health() -> JSONResponse:
        bridge = runtime.bridge
        wa = await bridge.health() if config.whatsapp_enabled() else {"ok": None,
                                                                     "disabled": True}
        return JSONResponse({"ok": True,
                             "app": config.adk_app_name(),
                             "channels": {"web": True,
                                          "telegram": runtime.telegram_enabled,
                                          "whatsapp": bool(config.whatsapp_enabled())},
                             # the replay-ordering guard is advisory for this pipeline
                             # (it re-plans between passes); visible here so a stale
                             # process is obvious rather than a mystery hang
                             "replay_barrier": ("advisory"
                                                if os.getenv("ADK_REPLAY_BARRIER_ADVISORY")
                                                else "strict"),
                             "whatsapp_bridge": wa})

    @app.post("/channels/whatsapp/inbound")
    async def whatsapp_inbound(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        result = await handle_inbound(core, payload if isinstance(payload, dict) else {},
                                      runtime.bridge)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    return app


app = build_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.host(), port=config.port(), log_level="info")


if __name__ == "__main__":
    main()
