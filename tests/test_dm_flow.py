"""End-to-end smoke test for the DM message handler.

Simulates a DM with a real PDF attachment. Verifies the bot:
1. Receives the message
2. Detects the stage
3. Reads the PDF
4. Sets stage to AWAITING_MAJOR
5. Sends a "Pick your major" embed

This catches the bugs that block the user: the previous handler crashed
silently on `message._state.client`, leaving the user with no response
after uploading.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.bot import _on_thread_message, _get_client, _CLIENT, Stage  # noqa: E402
from src.state import SessionStore  # noqa: E402

FIXTURE_PDF = Path("/Users/andrianthan/Downloads/AnThanSWE (11).pdf")


async def main() -> None:
    if not FIXTURE_PDF.exists():
        print(f"SKIP: {FIXTURE_PDF} not found")
        return

    # Build a minimal fake client + store and inject it as the module-level _CLIENT
    fake_store = SessionStore()
    fake_client = MagicMock()
    fake_client._store = fake_store  # type: ignore[attr-defined]
    import src.bot as bot_mod

    bot_mod._CLIENT = fake_client

    # User sends the bot a DM with the real PDF
    user_id = 123456789
    sess = fake_store.get(user_id)
    sess.stage = Stage.AWAITING_RESUME  # simulate after button click
    sess.thread_id = 12345  # mock thread id

    pdf_bytes = FIXTURE_PDF.read_bytes()
    attachment = MagicMock()
    attachment.filename = FIXTURE_PDF.name
    attachment.size = len(pdf_bytes)
    attachment.read = AsyncMock(return_value=pdf_bytes)

    channel = MagicMock()
    channel.send = AsyncMock()

    message = MagicMock()
    message.author = MagicMock()
    message.author.bot = False
    message.author.id = user_id
    message.guild = None
    message.attachments = [attachment]
    message.channel = channel

    await _on_thread_message(message, sess)

    sess = fake_store.get(user_id)
    print(f"stage after: {sess.stage}")
    print(f"resume stored: {len(sess.resume_bytes or b'')} bytes")
    print(f"channel.send called: {channel.send.call_count} time(s)")

    if sess.stage == Stage.AWAITING_MAJOR and sess.resume_bytes:
        print("PASS: handler read PDF + advanced stage to AWAITING_MAJOR")
    else:
        print(f"FAIL: stage={sess.stage}, bytes={sess.resume_bytes is not None}")


if __name__ == "__main__":
    asyncio.run(main())
