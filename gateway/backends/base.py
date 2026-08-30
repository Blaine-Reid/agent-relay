"""Pluggable chat-backend interface.

Every backend gives the gateway three things: create a session, list
sessions, and send a message into a session and get the assistant's reply
(optionally as a stream of text chunks). `hermes.py` is the primary
implementation; `lm_studio.py` is the secondary one, built against the same
interface so the gateway's WS/session-proxy code doesn't care which is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class Session:
    id: str
    name: str


class ChatBackend(ABC):
    @abstractmethod
    async def create_session(self, name: str | None = None) -> Session: ...

    @abstractmethod
    async def list_sessions(self) -> list[Session]: ...

    @abstractmethod
    async def delete_session(self, session_id: str) -> None: ...

    async def list_all_sessions(self) -> list[Session]:
        """List every session this backend knows about, not just ones this
        app created — used by the glasses' "Browse all sessions" screen to
        pick up a session started elsewhere (a computer, another client).
        Default: same as list_sessions() — only backends with a wider
        session pool than their own registry (Hermes) need to override this.
        """
        return await self.list_sessions()

    async def attach_session(self, session_id: str) -> None:
        """Adopt an existing session (found via list_all_sessions) into this
        app's own session list. Default: no-op, since list_all_sessions()
        already only returns sessions this backend considers its own.
        """
        return None

    async def get_recent_exchange(self, session_id: str) -> tuple[str, str] | None:
        """The most recent (user message, assistant reply) pair, so entering
        an existing or attached session on the glasses can show where you
        left off instead of starting blank. Default: None (no history
        concept beyond what's already in the gateway's own memory).
        """
        return None

    @abstractmethod
    def send_message(self, session_id: str, text: str) -> AsyncIterator[str]:
        """Send a user message, yield assistant reply text incrementally.

        Implementations that don't support streaming should yield the full
        reply once. Callers should not assume any particular chunk size.
        """
        ...
