"""Hermes backend — proxies to a local Nous Research Hermes agent instance.

Every shape below was verified live against a running Hermes instance this
session (not guessed from docs):

  POST   /api/sessions            {"title": "..."}    -> {"session": {"id", "title", ...}}
  GET    /api/sessions            -> {"object": "list", "data": [{"id","title","preview",...}]}
  DELETE /api/sessions/{id}       -> {"deleted": true}   (see session_registry.py's docstring —
                                       confirmed live that the session still shows up in
                                       GET /api/sessions afterward; the local registry is
                                       what actually removes it from THIS app's list)
  GET    /api/sessions/{id}/messages -> {"data": [{"role","content","tool_calls",...}, ...]}
                                       (chronological; includes role: "tool" entries and
                                       empty-content assistant messages mid tool-call —
                                       confirmed live against a real multi-turn session)
  POST   /api/sessions/{id}/chat/stream   {"message": "..."}  -> SSE, named events:

    event: assistant.delta       {"delta": "...", ...}          # incremental token(s)
    event: tool.progress         {"tool_name": "_thinking", ...} # internal reasoning trace — NOT user-facing
    event: assistant.completed   {"content": "...", "completed": true, ...}  # final full text
    event: run.completed         {...}
    event: done                  {...}

We only surface `assistant.completed.content` to callers — the glasses HUD
renders one consolidated reply per turn, not a token stream (the BLE render
queue can't keep up with per-token writes; see even-g2-notes/docs/display.md).
`tool.progress` events (internal chain-of-thought) are never surfaced.

`GET /api/sessions` returns EVERY session on the Hermes account — confirmed
live this session it includes cron-job runs, Telegram chats, everything
(50+ in practice on a real account). `list_sessions()` filters this down to
only sessions created through this app, via `SessionRegistry` — otherwise
the glasses' session list would be unusable, buried under unrelated noise.

`list_all_sessions()` (the glasses' "Browse all sessions" screen, for
picking up something started elsewhere — a computer, another Hermes client)
drops only `source: "cron"` entries — those are automated one-off task runs,
never something a person would want to walk in and continue — and keeps
everything else (Telegram, CLI, unknown), most-recently-active first.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import aiohttp

from .base import ChatBackend, Session
from session_registry import SessionRegistry


class HermesBackend(ChatBackend):
    def __init__(self, base_url: str, api_key: str, registry: SessionRegistry):
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._registry = registry

    async def create_session(self, name: str | None = None) -> Session:
        payload = {"title": name} if name else {}
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{self._base_url}/api/sessions", json=payload, headers=self._headers
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        s = data["session"]
        self._registry.add(s["id"])
        return Session(id=s["id"], name=s.get("title") or "New session")

    async def _fetch_raw_sessions(self) -> list[dict]:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{self._base_url}/api/sessions", headers=self._headers
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return data["data"]

    @staticmethod
    def _display_name(s: dict) -> str:
        # Never fall back to the raw id — a brand-new session (no messages
        # yet, so no title or preview) would otherwise show as an ugly
        # "api_<hex>" string until Hermes auto-titles it from the first
        # exchange. Same fallback create_session() already uses.
        return s.get("title") or s.get("preview") or "New session"

    async def list_sessions(self) -> list[Session]:
        raw = await self._fetch_raw_sessions()
        owned_ids = self._registry.all_ids()
        return [
            Session(id=s["id"], name=self._display_name(s)) for s in raw if s["id"] in owned_ids
        ]

    async def list_all_sessions(self) -> list[Session]:
        raw = await self._fetch_raw_sessions()
        non_cron = [s for s in raw if s.get("source") != "cron"]
        non_cron.sort(key=lambda s: s.get("last_active") or s.get("started_at") or 0, reverse=True)
        return [Session(id=s["id"], name=self._display_name(s)) for s in non_cron]

    async def attach_session(self, session_id: str) -> None:
        self._registry.add(session_id)

    async def get_recent_exchange(self, session_id: str) -> tuple[str, str] | None:
        async with aiohttp.ClientSession() as http:
            async with http.get(
                f"{self._base_url}/api/sessions/{session_id}/messages", headers=self._headers
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        messages = data["data"]

        # Skip role "tool" entries and assistant messages that are pure
        # tool-call steps (empty content, finish_reason "tool_calls") — not
        # real user-facing replies. Doesn't try to pair them into one exact
        # turn; the most recent real message of each role is close enough
        # for a "here's where you left off" preview.
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user" and m["content"]),
            None,
        )
        last_assistant = next(
            (m["content"] for m in reversed(messages) if m["role"] == "assistant" and m["content"]),
            None,
        )
        if last_user is None and last_assistant is None:
            return None
        return (last_user or "", last_assistant or "")

    async def delete_session(self, session_id: str) -> None:
        self._registry.remove(session_id)
        async with aiohttp.ClientSession() as http:
            async with http.delete(
                f"{self._base_url}/api/sessions/{session_id}", headers=self._headers
            ) as resp:
                resp.raise_for_status()

    async def send_message(self, session_id: str, text: str) -> AsyncIterator[str]:
        url = f"{self._base_url}/api/sessions/{session_id}/chat/stream"
        async with aiohttp.ClientSession() as http:
            async with http.post(
                url, json={"message": text}, headers=self._headers
            ) as resp:
                resp.raise_for_status()
                event_name = None
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8").rstrip("\n")
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[len("data:") :].strip())
                        if event_name == "assistant.completed":
                            yield data["content"]
                            return
                    elif line == "":
                        event_name = None
