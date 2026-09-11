"""Conversation session persistence — one JSON file per session under data/sessions.

Requirement 7: every chat session survives restarts and is served back to the
chat UI through GET /api/sessions/{id}.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


class SessionStore:
    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{session_id}.json"

    def get(self, session_id: str) -> list[dict]:
        """Return the turn list for a session (empty list if unknown)."""
        path = self._path(session_id)
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("turns", [])
        except (json.JSONDecodeError, OSError):
            return []

    def append(self, session_id: str, turn: dict) -> list[dict]:
        turns = self.get(session_id)
        turns.append(turn)
        payload = {"id": session_id, "created": turns[0].get("ts") if turns else None, "updated": int(time.time()), "turns": turns}
        self._path(session_id).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return turns

    def seed(self, session_id: str, history: list[dict]) -> None:
        """Import client-provided history for a brand-new server-side session."""
        if self.get(session_id):
            return
        for turn in history[-50:]:
            turn = dict(turn)
            turn.setdefault("ts", int(time.time()))
            self.append(session_id, turn)

    def list(self) -> list[dict]:
        out = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                turns = data.get("turns", [])
                first_user = next((t["content"] for t in turns if t.get("role") == "user"), "New conversation")
                out.append({"id": data.get("id"), "title": (first_user[:60] + "…") if len(first_user) > 60 else first_user, "count": len(turns), "updated": data.get("updated")})
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def delete(self, session_id: str) -> None:
        self._path(session_id).unlink(missing_ok=True)

    def latest_turns(self, session_id: str, max_turns: int) -> list[dict]:
        """Most recent N complete turns (user+assistant pairs count as 2)."""
        return self.get(session_id)[-max_turns:]