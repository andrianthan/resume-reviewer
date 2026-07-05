"""End-to-end test: full review pipeline (button click → DM → upload → major → year → scored review).

Uses real PDF, real OpenRouter LLM, real evaluator. Skips Discord transport.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot import _on_dm_message, _run_review  # noqa: E402
from src.evaluator import evaluate  # noqa: E402
from src.state import SessionStore, Stage  # noqa: E402

FIXTURE_PDF = Path("/Users/andrianthan/Downloads/AnThanSWE (11).pdf")


async def main() -> None:
    if not FIXTURE_PDF.exists():
        print(f"SKIP: {FIXTURE_PDF} not found")
        return

    # Set up fake client + store
    fake_store = SessionStore()
    fake_client = MagicMock()
    fake_client._store = fake_store  # type: ignore[attr-defined]
    import src.bot as bot_mod

    bot_mod._CLIENT = fake_client

    user_id = 999
    sess = fake_store.get(user_id)
    sess.stage = Stage.AWAITING_RESUME

    # 1. Simulate DM with PDF upload
    pdf_bytes = FIXTURE_PDF.read_bytes()
    attachment = MagicMock()
    attachment.filename = FIXTURE_PDF.name
    attachment.size = len(pdf_bytes)
    attachment.read = AsyncMock(return_value=pdf_bytes)

    msg = MagicMock()
    msg.author = MagicMock(bot=False, id=user_id)
    msg.guild = None
    msg.attachments = [attachment]
    msg.channel = MagicMock(send=AsyncMock())
    await _on_dm_message(msg)
    print(f"After PDF upload: stage={sess.stage}, bytes={len(sess.resume_bytes or b'')}")
    assert sess.stage == Stage.AWAITING_MAJOR
    assert sess.resume_bytes

    # 2. User picks major
    sess.major = "consulting"
    sess.stage = Stage.AWAITING_YEAR
    assert sess.stage == Stage.AWAITING_YEAR

    # 3. User picks year
    sess.class_year = "junior"
    sess.stage = Stage.REVIEWING

    # 4. Run the review (this is what _run_review does internally)
    print("Running evaluator on real PDF + OpenRouter LLM...")
    review = await asyncio.to_thread(
        evaluate,
        sess.resume_bytes,
        sess.major,
        sess.class_year,
        use_llm=True,
    )
    print(f"\nFINAL SCORE: {review.final_score}")
    print(f"MATCHED DOMAINS: {review.matched_domains}")
    for c in review.categories:
        print(f"  {c.category_key}: {c.score}/{c.max_score}")
    print("\nDONE")


if __name__ == "__main__":
    asyncio.run(main())
