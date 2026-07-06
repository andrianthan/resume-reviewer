"""Small persisted rate-limit store for Discord review flow."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def now_ts() -> float:
    return time.time()


def format_wait(seconds: float) -> str:
    seconds = max(1, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rem = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {rem}s" if rem else f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if minutes else f"{hours}h"


class RateLimitStore:
    """JSON-backed per-user counters.

    This is intentionally simple: one bot process, one small file, no external
    service. It survives bot restarts, which matters for open thread caps.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, Any] = {"users": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if isinstance(data, dict):
            self._data = data
            self._data.setdefault("users", {})

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

    def _user(self, user_id: int) -> dict[str, Any]:
        users = self._data.setdefault("users", {})
        user = users.setdefault(str(user_id), {})
        user.setdefault("open_threads", {})
        user.setdefault("thread_creates", [])
        user.setdefault("last_thread_create", 0.0)
        return user

    @staticmethod
    def _recent(values: list[Any], window_seconds: int, now: float) -> list[float]:
        cutoff = now - window_seconds
        out: list[float] = []
        for value in values:
            try:
                ts = float(value)
            except (TypeError, ValueError):
                continue
            if ts >= cutoff:
                out.append(ts)
        return out

    def get_open_threads(self, user_id: int) -> dict[int, float]:
        user = self._user(user_id)
        out: dict[int, float] = {}
        for thread_id, created_at in dict(user.get("open_threads", {})).items():
            try:
                out[int(thread_id)] = float(created_at)
            except (TypeError, ValueError):
                continue
        return out

    def add_open_thread(self, user_id: int, thread_id: int, created_at: float | None = None) -> None:
        user = self._user(user_id)
        user["open_threads"][str(thread_id)] = created_at or now_ts()
        self._save()

    def remove_open_thread(self, user_id: int, thread_id: int) -> None:
        user = self._user(user_id)
        user["open_threads"].pop(str(thread_id), None)
        self._save()

    def replace_open_threads(self, user_id: int, open_threads: dict[int, float]) -> None:
        user = self._user(user_id)
        user["open_threads"] = {str(k): v for k, v in open_threads.items()}
        self._save()

    def check_thread_create(
        self,
        user_id: int,
        *,
        cooldown_seconds: int,
        max_per_hour: int,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        ts = now or now_ts()
        user = self._user(user_id)
        last = float(user.get("last_thread_create") or 0.0)
        if cooldown_seconds > 0 and last and ts - last < cooldown_seconds:
            return False, f"Please wait {format_wait(cooldown_seconds - (ts - last))} before starting another review thread."

        recent = self._recent(list(user.get("thread_creates", [])), 3600, ts)
        user["thread_creates"] = recent
        if max_per_hour > 0 and len(recent) >= max_per_hour:
            oldest = min(recent)
            return False, f"You have hit the review-thread hourly limit. Try again in {format_wait(3600 - (ts - oldest))}."
        self._save()
        return True, None

    def record_thread_create(self, user_id: int, now: float | None = None) -> None:
        ts = now or now_ts()
        user = self._user(user_id)
        recent = self._recent(list(user.get("thread_creates", [])), 3600, ts)
        recent.append(ts)
        user["thread_creates"] = recent
        user["last_thread_create"] = ts
        self._save()
