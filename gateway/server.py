"""Agent Relay gateway.

Bridges the G2 glasses app (over a WebSocket) to a chat backend (Hermes by
default, LM Studio as the pluggable alternative) and Sogni for speech-to-text.
Audio and backend API keys never reach the browser layer — see the plan's
"Key design decision" section. Only this gateway's own address needs to be in
the glasses app's `app.json` network whitelist.

WebSocket protocol (glasses app is the client). Matches Even Terminal's
confirm-before-send flow: transcribe first, show it on the glasses, only
forward to the chat backend once the user confirms. Session management is
also WS-only, deliberately — a real device hit `TypeError: Failed to fetch`
on every REST call from inside the Even Hub WebView (the WS connection
itself works fine; something about that WebView's fetch/CORS handling
doesn't), so everything the glasses app needs goes over the one transport
confirmed to work.
  -> {"type": "set_active_session", "session_id": "..."}
  -> {"type": "transcribe", "session_id": "...", "audio_b64": "...", "sample_rate": 16000}
  <- {"type": "transcript", "session_id", "text"}
  -> {"type": "confirm_send", "session_id": "..."}   # sends the last transcript for this session
  <- {"type": "chat_reply", "session_id", "text"}
  -> {"type": "list_sessions"}
  <- {"type": "sessions", "sessions": [{"id","name"}, ...]}
  -> {"type": "create_session", "name": "..." (optional)}
  <- {"type": "session_created", "session": {"id","name"}}
  -> {"type": "delete_session", "session_id": "..."}
  <- {"type": "session_deleted", "session_id": "..."}
  -> {"type": "list_all_sessions"}
  <- {"type": "all_sessions", "sessions": [{"id","name"}, ...]}
  -> {"type": "attach_session", "session_id": "..."}
  <- {"type": "session_attached", "session_id": "..."}
  -> {"type": "get_recent_exchange", "session_id": "..."}
  <- {"type": "recent_exchange", "session_id", "transcript", "reply"}   # "" if no history yet
  <- {"type": "error", "message"}

REST endpoints below are kept only for manual/curl debugging — the glasses
app itself never calls them.
  GET    /api/sessions
  POST   /api/sessions        {"name": "..."}
  DELETE /api/sessions/{id}
  GET    /api/sessions/all
  POST   /api/sessions/{id}/attach
  GET    /health
"""

from __future__ import annotations

import base64
import json
import logging
import os

from aiohttp import web

from backends.base import ChatBackend
from backends.hermes import HermesBackend
from backends.lm_studio import LMStudioBackend
from session_registry import SessionRegistry
from stt_client import SogniSttClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-relay-gateway")


def build_backend() -> ChatBackend:
    kind = os.environ.get("BACKEND", "hermes").lower()
    if kind == "hermes":
        base_url = os.environ["HERMES_BASE_URL"]
        api_key = os.environ["HERMES_API_KEY"]
        registry = SessionRegistry(os.environ.get("SESSION_REGISTRY_PATH", "/data/sessions.json"))
        return HermesBackend(base_url=base_url, api_key=api_key, registry=registry)
    if kind == "lm_studio":
        base_url = os.environ["LM_STUDIO_BASE_URL"]
        model = os.environ.get("LM_STUDIO_MODEL")
        return LMStudioBackend(base_url=base_url, model=model)
    raise ValueError(f"Unknown BACKEND: {kind!r} (expected 'hermes' or 'lm_studio')")


def build_stt() -> SogniSttClient:
    return SogniSttClient(
        base_url=os.environ["SOGNI_BASE_URL"],
        api_key=os.environ["SOGNI_API_KEY"],
        path=os.environ.get("SOGNI_STT_PATH", "/transcribe"),
        engine=os.environ.get("SOGNI_ENGINE", "qwen3"),
        language=os.environ.get("SOGNI_LANGUAGE", "en"),
    )


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_list_sessions(request: web.Request) -> web.Response:
    backend: ChatBackend = request.app["backend"]
    sessions = await backend.list_sessions()
    return web.json_response([{"id": s.id, "name": s.name} for s in sessions])


async def handle_create_session(request: web.Request) -> web.Response:
    backend: ChatBackend = request.app["backend"]
    body = await request.json() if request.body_exists else {}
    session = await backend.create_session(name=body.get("name"))
    return web.json_response({"id": session.id, "name": session.name})


async def handle_delete_session(request: web.Request) -> web.Response:
    backend: ChatBackend = request.app["backend"]
    await backend.delete_session(request.match_info["session_id"])
    return web.json_response({"deleted": True})


async def handle_list_all_sessions(request: web.Request) -> web.Response:
    backend: ChatBackend = request.app["backend"]
    sessions = await backend.list_all_sessions()
    return web.json_response([{"id": s.id, "name": s.name} for s in sessions])


async def handle_attach_session(request: web.Request) -> web.Response:
    backend: ChatBackend = request.app["backend"]
    await backend.attach_session(request.match_info["session_id"])
    return web.json_response({"attached": True})


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    # heartbeat: periodic ping frames. Mobile networks/NATs aggressively kill
    # idle TCP connections (confirmed live — a real device's connection died
    # with no further reconnect ever reaching the server); regular traffic
    # both keeps the path alive and lets aiohttp detect a truly dead peer
    # faster than waiting on a read that will never come.
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    backend: ChatBackend = request.app["backend"]
    stt: SogniSttClient = request.app["stt"]
    active_session_id: str | None = None
    # Per-connection: the most recent transcript per session, held until the
    # user confirms it on the glasses (or a new transcribe supersedes it).
    pending_transcripts: dict[str, str] = {}

    logger.info("Glasses client connected")
    async for msg in ws:
        if msg.type != web.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
            msg_type = data.get("type")

            if msg_type == "set_active_session":
                active_session_id = data["session_id"]
                continue

            if msg_type == "transcribe":
                session_id = data.get("session_id") or active_session_id
                if not session_id:
                    raise ValueError("transcribe received with no active session")

                pcm = base64.b64decode(data["audio_b64"])
                sample_rate = data.get("sample_rate", 16000)

                text = await stt.transcribe(pcm, sample_rate=sample_rate)
                pending_transcripts[session_id] = text
                await ws.send_json(
                    {"type": "transcript", "session_id": session_id, "text": text}
                )
                continue

            if msg_type == "confirm_send":
                session_id = data.get("session_id") or active_session_id
                if not session_id:
                    raise ValueError("confirm_send received with no active session")
                text = pending_transcripts.pop(session_id, None)
                if text is None:
                    raise ValueError("confirm_send with no pending transcript for this session")

                reply = ""
                async for chunk in backend.send_message(session_id, text):
                    reply = chunk  # backends yield the full consolidated reply once
                await ws.send_json(
                    {"type": "chat_reply", "session_id": session_id, "text": reply}
                )
                continue

            if msg_type == "list_sessions":
                sessions = await backend.list_sessions()
                await ws.send_json(
                    {"type": "sessions", "sessions": [{"id": s.id, "name": s.name} for s in sessions]}
                )
                continue

            if msg_type == "create_session":
                session = await backend.create_session(name=data.get("name"))
                await ws.send_json(
                    {"type": "session_created", "session": {"id": session.id, "name": session.name}}
                )
                continue

            if msg_type == "delete_session":
                await backend.delete_session(data["session_id"])
                await ws.send_json({"type": "session_deleted", "session_id": data["session_id"]})
                continue

            if msg_type == "list_all_sessions":
                sessions = await backend.list_all_sessions()
                await ws.send_json(
                    {"type": "all_sessions", "sessions": [{"id": s.id, "name": s.name} for s in sessions]}
                )
                continue

            if msg_type == "attach_session":
                await backend.attach_session(data["session_id"])
                await ws.send_json({"type": "session_attached", "session_id": data["session_id"]})
                continue

            if msg_type == "get_recent_exchange":
                session_id = data["session_id"]
                exchange = await backend.get_recent_exchange(session_id)
                transcript, reply = exchange if exchange else ("", "")
                await ws.send_json(
                    {
                        "type": "recent_exchange",
                        "session_id": session_id,
                        "transcript": transcript,
                        "reply": reply,
                    }
                )
                continue

            logger.warning("Unknown message type: %s", msg_type)
        except Exception as exc:  # noqa: BLE001 - surface every failure to the client
            logger.exception("Error handling WS message")
            await ws.send_json({"type": "error", "message": str(exc)})

    logger.info("Glasses client disconnected")
    return ws


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app["backend"] = build_backend()
    app["stt"] = build_stt()
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ws", handle_ws)
    app.router.add_get("/api/sessions", handle_list_sessions)
    app.router.add_post("/api/sessions", handle_create_session)
    app.router.add_delete("/api/sessions/{session_id}", handle_delete_session)
    app.router.add_get("/api/sessions/all", handle_list_all_sessions)
    app.router.add_post("/api/sessions/{session_id}/attach", handle_attach_session)
    app.router.add_route("OPTIONS", "/{tail:.*}", lambda r: web.Response())
    return app


if __name__ == "__main__":
    web.run_app(
        create_app(),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "9091")),
    )
