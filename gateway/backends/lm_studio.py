"""LM Studio backend — secondary/pluggable, built after Hermes.

LM Studio exposes a standard OpenAI-compatible `/v1/chat/completions`
endpoint with no server-side session concept, so unlike `hermes.py` this
backend keeps conversation history itself, in memory, per session id.

Unverified against a live instance (LM Studio's Tailscale address is an open
item) — the wire format itself is the well-established OpenAI chat-completions
shape, not something specific to guess at.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import aiohttp

from .base import ChatBackend, Session


class LMStudioBackend(ChatBackend):
    def __init__(self, base_url: str, model: str | None = None):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._histories: dict[str, list[dict]] = {}
        self._titles: dict[str, str] = {}

    async def create_session(self, name: str | None = None) -> Session:
        session_id = f"lmstudio_{uuid.uuid4().hex[:12]}"
        self._histories[session_id] = []
        self._titles[session_id] = name or "New session"
        return Session(id=session_id, name=self._titles[session_id])

    async def list_sessions(self) -> list[Session]:
        return [Session(id=sid, name=self._titles[sid]) for sid in self._histories]

    async def delete_session(self, session_id: str) -> None:
        self._histories.pop(session_id, None)
        self._titles.pop(session_id, None)

    async def get_recent_exchange(self, session_id: str) -> tuple[str, str] | None:
        history = self._histories.get(session_id, [])
        last_user = next(
            (m["content"] for m in reversed(history) if m["role"] == "user"), None
        )
        last_assistant = next(
            (m["content"] for m in reversed(history) if m["role"] == "assistant"), None
        )
        if last_user is None and last_assistant is None:
            return None
        return (last_user or "", last_assistant or "")

    async def send_message(self, session_id: str, text: str) -> AsyncIterator[str]:
        history = self._histories.setdefault(session_id, [])
        history.append({"role": "user", "content": text})

        payload: dict = {"messages": history, "stream": False}
        if self._model:
            payload["model"] = self._model

        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{self._base_url}/v1/chat/completions", json=payload
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        reply = data["choices"][0]["message"]["content"]
        history.append({"role": "assistant", "content": reply})
        yield reply
