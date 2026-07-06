from __future__ import annotations

import sys
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.bot as bot_mod  # noqa: E402
from src.bot import _begin_dm_flow, _on_dm_message  # noqa: E402
from src.rate_limit import RateLimitStore  # noqa: E402
from src.state import SessionStore, Stage  # noqa: E402


def _install_fake_client() -> MagicMock:
    store = SessionStore()
    client = MagicMock()
    client._store = store
    bot_mod._CLIENT = client
    return client


def test_begin_dm_flow_sends_upload_prompt_and_sets_session(tmp_path: Path) -> None:
    fake_client = _install_fake_client()
    old_rate_limits = bot_mod.RATE_LIMITS
    bot_mod.RATE_LIMITS = RateLimitStore(tmp_path / "rate_limits.json")

    # Set up panel reaction channel so the new reaction code path runs.
    import discord as _discord
    panel_channel = MagicMock(spec=_discord.TextChannel)
    panel_msg = MagicMock()
    panel_msg.add_reaction = AsyncMock()
    panel_channel.fetch_message = AsyncMock(return_value=panel_msg)
    fake_client.get_channel = MagicMock(return_value=panel_channel)
    bot_mod._PANEL_MESSAGE_IDS[bot_mod.REVIEW_CHANNEL_ID] = 999

    user = MagicMock()
    user.id = 123
    dm = MagicMock()
    dm.id = 456
    dm.send = AsyncMock()
    user.create_dm = AsyncMock(return_value=dm)

    interaction = MagicMock()
    interaction.user = user
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    asyncio.run(_begin_dm_flow(interaction))

    sess = fake_client._store.get(user.id)
    assert sess.stage == Stage.AWAITING_RESUME
    assert sess.dm_channel_id == dm.id
    dm.send.assert_awaited_once()
    panel_msg.add_reaction.assert_awaited_once_with("📝")
    interaction.followup.send.assert_awaited_with(
        "✅ I sent you a DM. Upload your resume there to continue.",
        ephemeral=True,
    )
    bot_mod.RATE_LIMITS = old_rate_limits
    bot_mod._CLIENT = None
    bot_mod._PANEL_MESSAGE_IDS.clear()


def test_on_dm_message_reads_pdf_and_prompts_for_major() -> None:
    fake_client = _install_fake_client()
    user_id = 123
    sess = fake_client._store.get(user_id)
    sess.stage = Stage.AWAITING_RESUME
    sess.dm_channel_id = 456

    attachment = MagicMock()
    attachment.filename = "resume.pdf"
    attachment.size = 42
    attachment.read = AsyncMock(return_value=b"%PDF-1.4 test")

    channel = MagicMock()
    channel.id = 456
    channel.send = AsyncMock()

    message = MagicMock()
    message.author = MagicMock(bot=False, id=user_id)
    message.guild = None
    message.attachments = [attachment]
    message.channel = channel

    asyncio.run(_on_dm_message(message, sess))

    assert sess.stage == Stage.AWAITING_MAJOR
    assert sess.resume_bytes == b"%PDF-1.4 test"
    assert sess.resume_filename == "resume.pdf"
    channel.send.assert_awaited_once()
    bot_mod._CLIENT = None
