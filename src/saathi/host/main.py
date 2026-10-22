"""Web host: serves the elder and family pages and bridges the browser to the host agent.

WebSocket protocol (/ws), browser -> host:
  {"type": "hello", "person_id": "meera"}
  {"type": "utterance", "text": "..."}
host -> browser:
  {"type": "reply", "text": "...", "proactive": bool, "escalated": bool, "model": "mock|bedrock|emergency|emergency-check"}
  {"type": "card", "tool": "...", "uri": "ui://...", "input": {...}, "result": {...}}
  {"type": "frame", "direction": "out|in", "message": {...}}   # host agent tool calls, for the inspector
  {"type": "remembered", "facts": ["..."]}                       # facts Saathi kept after reflecting on the turn
  {"type": "error", "text": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from saathi import config
from saathi.config import REPO_ROOT
from saathi.host.agent import Host, TurnResult
from saathi.seed import demo_family_token

log = logging.getLogger("saathi.host")
WEB_DIR = REPO_ROOT / "web"


def _people() -> list[dict]:
    raw = os.environ.get("SAATHI_PEOPLE", "meera:Meera,raj:Raj")
    people = []
    for item in raw.split(","):
        pid, _, name = item.partition(":")
        if pid.strip():
            people.append({"person_id": pid.strip(), "name": (name or pid).strip()})
    return people


def _server_base(settings: config.Settings) -> str:
    return settings.mcp_url.rsplit(settings.mcp_path, 1)[0] if settings.mcp_path else settings.mcp_url


async def _wait_for_server(settings: config.Settings, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while True:
            try:
                if (await client.get(f"{_server_base(settings)}/health", timeout=2)).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError(f"Saathi MCP server not reachable at {settings.mcp_url}")
            await asyncio.sleep(0.5)


def create_app() -> FastAPI:
    settings = config.get()
    state: dict = {}

    async def digest_loop() -> None:
        """The family's evening summary goes out whether or not anyone is at the device."""
        while True:
            await asyncio.sleep(settings.poll_seconds)
            try:
                await asyncio.to_thread(state["host"].call, "send_due_digests", {})
            except Exception:  # keep trying on the next tick
                log.exception("digest check failed")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await _wait_for_server(settings)
        state["host"] = await asyncio.to_thread(Host, settings)
        state["reflect_locks"] = {}
        log.info("Host agent connected to %s (mode: %s)", settings.mcp_url,
                 "mock" if settings.mock or not settings.bedrock_model_id else "bedrock")
        digests = asyncio.create_task(digest_loop())
        yield
        digests.cancel()
        state["host"].close()

    app = FastAPI(title="Saathi host (simulated Alexa+)", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    async def index():
        return RedirectResponse("/elder.html")

    @app.get("/family", include_in_schema=False)
    async def family_shortcut():
        """Token-free family link for the demo (some ways of opening a browser drop the query string)."""
        if not settings.demo_mode:
            return RedirectResponse("/family.html")
        return RedirectResponse(f"/family.html?token={demo_family_token()}")

    @app.get("/phone", include_in_schema=False)
    async def phone_shortcut():
        """Token-free link to "Priya's phone" for the demo."""
        if not settings.demo_mode:
            return RedirectResponse("/inbox.html")
        return RedirectResponse(f"/inbox.html?token={demo_family_token()}")

    @app.get("/api/inbox")
    async def inbox_messages(token: str = ""):
        """Everything Saathi has sent this family member (emails and texts), newest first."""
        from saathi.core import SaathiError, people
        from saathi.host import inbox

        if not token and settings.demo_mode:
            token = demo_family_token()
        try:
            contact = await asyncio.to_thread(people.contact_by_token, token)
        except SaathiError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        messages = await asyncio.to_thread(inbox.messages_for, contact)
        live = {"email": "ses" if settings.live_ses else "smtp" if settings.live_smtp else None,
                "text": "sns" if settings.live_sns else None}
        return JSONResponse({"contact_name": contact["name"], "email": contact["email"],
                             "phone": contact["phone"], "live": live, "messages": messages})

    @app.get("/config.js", include_in_schema=False)
    async def config_js():
        cfg = {
            "mcpUrl": settings.mcp_url,
            "mode": "mock" if settings.mock or not settings.bedrock_model_id else "bedrock",
            "demoMode": settings.demo_mode,
            "people": _people(),
            "lang": "en-IN",
        }
        if settings.demo_mode:
            cfg["demoFamilyToken"] = demo_family_token()  # demo data only; never exposed outside DEMO_MODE
        return PlainTextResponse(f"window.SAATHI_CONFIG = {json.dumps(cfg)};", media_type="application/javascript")

    @app.get("/api/clock")
    async def get_clock():
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{_server_base(settings)}/dev/clock", timeout=5)
        return JSONResponse(r.json())

    @app.post("/api/clock")
    async def set_clock(body: dict):
        if not settings.demo_mode:
            return JSONResponse({"error": "clock control is only available in DEMO_MODE"}, status_code=403)
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{_server_base(settings)}/dev/clock", json=body, timeout=5)
        return JSONResponse(r.json(), status_code=r.status_code)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        host: Host = state["host"]
        current = {"person_id": None}
        send_lock = asyncio.Lock()

        async def send(payload: dict) -> None:
            async with send_lock:
                await ws.send_text(json.dumps(payload))

        async def deliver(result: TurnResult, person_id: str) -> None:
            # Tag everything with its person, so a reply that lands after the speaker
            # changes is filed under the right conversation.
            for frame in result.frames:
                await send({"type": "frame", "person_id": person_id, **frame})
            for card in result.cards:
                await send({"type": "card", "person_id": person_id, **card})
            if result.reply:
                await send({"type": "reply", "person_id": person_id, "text": result.reply,
                            "proactive": result.proactive, "escalated": result.escalated, "model": result.model})

        async def poll_loop() -> None:
            while True:
                await asyncio.sleep(settings.poll_seconds)
                person_id = current["person_id"]
                if not person_id:
                    continue
                try:
                    for result in await host.poll(person_id):
                        await deliver(result, person_id)
                except Exception as exc:  # keep polling; surface the problem
                    log.exception("poll failed")
                    await send({"type": "error", "text": f"Poll failed: {exc}"})

        background: set[asyncio.Task] = set()

        async def reflect_in_background(person_id: str, text: str, result: TurnResult) -> None:
            """Reflection (memory, impressions, concern texts to family) never holds up the conversation.
            One person's reflections run in order; the elder only ever sees "Remembered" lines."""
            lock = state["reflect_locks"].setdefault(person_id, asyncio.Lock())
            async with lock:
                try:
                    frames, outcome = await host.reflect_after(person_id, text, result)
                    for frame in frames:
                        await send({"type": "frame", "person_id": person_id, **frame})
                    remembered = outcome.get("remembered", [])
                    if remembered:
                        await send({"type": "remembered", "person_id": person_id,
                                    "facts": [m["fact"] for m in remembered]})
                except Exception:  # the socket may have closed; reflection itself is already stored
                    log.debug("background reflection could not report back", exc_info=True)

        poller = asyncio.create_task(poll_loop())
        try:
            while True:
                message = json.loads(await ws.receive_text())
                kind = message.get("type")
                if kind == "hello":
                    current["person_id"] = message.get("person_id")
                    await send({"type": "ready", "person_id": current["person_id"]})
                elif kind == "utterance" and current["person_id"]:
                    text = (message.get("text") or "").strip()
                    if not text:
                        continue
                    person_id = current["person_id"]
                    try:
                        result = await host.handle_utterance(person_id, text, reflect=False)
                        await deliver(result, person_id)
                        if not (result.escalated or result.checking):
                            background.add(asyncio.create_task(reflect_in_background(person_id, text, result)))
                            for task in [t for t in background if t.done()]:
                                background.discard(task)
                    except Exception as exc:
                        log.exception("turn failed")
                        await send({"type": "error", "text": f"Something went wrong: {exc}"})
                elif kind == "poll_now" and current["person_id"]:
                    person_id = current["person_id"]
                    for result in await host.poll(person_id):
                        await deliver(result, person_id)
        except WebSocketDisconnect:
            pass
        finally:
            poller.cancel()

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpx2", "mcp", "strands"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # Expected every turn: the system prompt is refreshed, and AgentSkills re-appends its skill list.
    logging.getLogger("strands.vended_plugins.skills.agent_skills").setLevel(logging.ERROR)
    settings = config.get()
    uvicorn.run(create_app(), host=settings.ui_host, port=settings.ui_port, log_level="warning")


if __name__ == "__main__":
    main()
