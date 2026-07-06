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


def test_review_start_cooldown_and_hourly_limit(tmp_path: Path) -> None:
    store = RateLimitStore(tmp_path / "rate_limits.json")
    user_id = 42

    ok, msg = store.check_review_start(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1000,
    )
    assert ok, msg
    store.record_review_start(user_id, now=1000)

    ok, msg = store.check_review_start(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1010,
    )
    assert not ok
    assert "wait" in (msg or "")

    ok, msg = store.check_review_start(
        user_id,
        cooldown_seconds=30,
        max_per_hour=2,
        now=1031,
    )
    assert ok, msg
    store.record_review_start(user_id, now=1031)

    ok, msg = store.check_review_start(
        user_id,
        cooldown_seconds=0,
        max_per_hour=2,
        now=1040,
    )
    assert not ok
    assert "hourly limit" in (msg or "")
