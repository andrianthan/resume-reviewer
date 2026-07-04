"""Per-user conversation state for the Discord DM flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Stage(str, Enum):
    IDLE = "idle"
    AWAITING_RESUME = "awaiting_resume"
    AWAITING_MAJOR = "awaiting_major"
    AWAITING_YEAR = "awaiting_year"
    REVIEWING = "reviewing"
    DONE = "done"


@dataclass
class UserSession:
    user_id: int
    stage: Stage = Stage.IDLE
    resume_bytes: bytes | None = None
    resume_filename: str | None = None
    major: str | None = None
    class_year: str | None = None
    last_message_id: int | None = None
    error: str | None = None


class SessionStore:
    """In-memory session store. Replace with Redis/SQLite for multi-instance."""

    def __init__(self) -> None:
        self._store: dict[int, UserSession] = {}

    def get(self, user_id: int) -> UserSession:
        if user_id not in self._store:
            self._store[user_id] = UserSession(user_id=user_id)
        return self._store[user_id]

    def reset(self, user_id: int) -> None:
        self._store.pop(user_id, None)
