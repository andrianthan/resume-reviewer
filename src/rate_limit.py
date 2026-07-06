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
    service. It survives bot restarts, which matters for review start caps.
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
        user.setdefault("review_starts", [])
        user.setdefault("last_review_start", 0.0)
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

    def check_review_start(
        self,
        user_id: int,
        *,
        cooldown_seconds: int,
        max_per_hour: int,
        now: float | None = None,
    ) -> tuple[bool, str | None]:
        ts = now or now_ts()
        user = self._user(user_id)
        last = float(user.get("last_review_start") or 0.0)
        if cooldown_seconds > 0 and last and ts - last < cooldown_seconds:
            return False, f"Please wait {format_wait(cooldown_seconds - (ts - last))} before starting another review."

        starts = list(user.get("review_starts", []))
        recent = self._recent(starts, 3600, ts)
        user["review_starts"] = recent
        if max_per_hour > 0 and len(recent) >= max_per_hour:
            oldest = min(recent)
            return False, f"You have hit the review hourly limit. Try again in {format_wait(3600 - (ts - oldest))}."
        self._save()
        return True, None

    def record_review_start(self, user_id: int, now: float | None = None) -> None:
        ts = now or now_ts()
        user = self._user(user_id)
        starts = list(user.get("review_starts", []))
        recent = self._recent(starts, 3600, ts)
        recent.append(ts)
        user["review_starts"] = recent
        user["last_review_start"] = ts
        self._save()
