"""Tracks which Hermes sessions this app created.

`GET /api/sessions` on a real Hermes instance returns EVERY session on the
account — cron jobs, Telegram chats, everything, 50+ in practice. Showing
that on a 6-row glasses list would bury actual voice-chat sessions under
unrelated noise. This is a small JSON-file-backed registry of session ids
this app created, so `HermesBackend.list_sessions()` can filter down to just
those. Persisted to a Docker volume so it survives gateway restarts.

Also works around a real Hermes API quirk confirmed live this session:
`DELETE /api/sessions/{id}` returns `{"deleted": true}` but the session still
appears in `GET /api/sessions` afterward (archived: false, hidden: false,
message_count reset to 0). Removing the id from this registry is what
actually makes it disappear from *this app's* session list, regardless of
that upstream behavior.
"""

from __future__ import annotations

import json
import os
import threading


class SessionRegistry:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        self._ids: set[str] = set()
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path) as f:
                self._ids = set(json.load(f))

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(sorted(self._ids), f)

    def add(self, session_id: str) -> None:
        with self._lock:
            self._ids.add(session_id)
            self._save()

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._ids.discard(session_id)
            self._save()

    def contains(self, session_id: str) -> bool:
        return session_id in self._ids

    def all_ids(self) -> set[str]:
        return set(self._ids)
