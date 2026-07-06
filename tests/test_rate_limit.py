"""Unit tests for rate-limit state."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.rate_limit import RateLimitStore, format_wait  # noqa: E402


def test_format_wait() -> None:
    assert format_wait(9.9) == "9s"
    assert format_wait(60) == "1m"
    assert format_wait(125) == "2m 5s"
    assert format_wait(3600) == "1h"


def test_open_thread_tracking_persists(tmp_path: Path) -> None:
    path = tmp_path / "rate_limits.json"
    store = RateLimitStore(path)
    store.add_open_thread(1, 101, created_at=1000)
    store.add_open_thread(1, 102, created_at=1001)

    reloaded = RateLimitStore(path)
    assert reloaded.get_open_threads(1) == {101: 1000.0, 102: 1001.0}

    reloaded.remove_open_thread(1, 101)
    assert reloaded.get_open_threads(1) == {102: 1001.0}


def test_thread_create_cooldown_and_hourly_limit(tmp_path: Path) -> None:
    store = RateLimitStore(tmp_path / "rate_limits.json")
    user_id = 42

    ok, msg = store.check_thread_create(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1000,
    )
    assert ok, msg
    store.record_thread_create(user_id, now=1000)

    ok, msg = store.check_thread_create(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1010,
    )
    assert not ok
    assert "wait" in (msg or "")

    ok, msg = store.check_thread_create(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1031,
    )
    assert ok, msg
    store.record_thread_create(user_id, now=1031)

    ok, msg = store.check_thread_create(
        user_id,
        cooldown_seconds=0,
        max_per_hour=2,
        now=1040,
    )
    assert not ok
    assert "hourly limit" in (msg or "")
