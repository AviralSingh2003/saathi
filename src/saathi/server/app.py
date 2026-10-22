"""ASGI app: the MCP server at /mcp plus a few plain HTTP routes.

Security for browser clients (per the Streamable HTTP spec): Origin and Host
validation via the SDK's TransportSecuritySettings, CORS limited to the UI
origin with Mcp-Session-Id exposed, and binding to 127.0.0.1 by default.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server.transport_security import TransportSecuritySettings

from saathi import config
from saathi.core import clock, db
from saathi.server.tools import build_server

ALLOWED_HEADERS = [
    "Authorization", "Content-Type", "Accept", "Last-Event-ID", "Mcp-Method",
    "Mcp-Name", "Mcp-Protocol-Version", "Mcp-Session-Id",
]


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "name": "saathi-mcp"})


async def dev_clock(request: Request) -> JSONResponse:
    """GET: simulated now. POST {"advance_minutes": n} or {"set": "HH:MM"} (DEMO_MODE only)."""
    if request.method == "POST":
        if not config.get().demo_mode:
            return JSONResponse({"error": "clock control is only available in DEMO_MODE"}, status_code=403)
        body = await request.json()
        if "set" in body:
            clock.set_time(str(body["set"]))
        elif "advance_minutes" in body:
            clock.advance(float(body["advance_minutes"]))
        elif body.get("reset"):
            clock.set_offset(0)
    return JSONResponse({"now": clock.iso(clock.now())})


def create_app() -> Starlette:
    settings = config.get()
    db.init()
    mcp = build_server()

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*"],
        allowed_origins=settings.ui_origins,
    )
    mcp_app = mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        transport_security=security,
        host=settings.mcp_host,
    )
    return Starlette(
        routes=[
            Route("/health", health),
            Route("/dev/clock", dev_clock, methods=["GET", "POST"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[Middleware(
            CORSMiddleware,
            allow_origins=settings.ui_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=ALLOWED_HEADERS,
            expose_headers=["Mcp-Session-Id"],
        )],
        lifespan=lifespan,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    settings = config.get()
    uvicorn.run(create_app(), host=settings.mcp_host, port=settings.mcp_port, log_level="warning")


if __name__ == "__main__":
    main()
