"""Discord bot entrypoint.

Flow:
1. Bot posts an embed with a "Review my resume" button in a configured
   channel (RESUME_REVIEW_CHANNEL_ID).
2. Member clicks button → bot DMs them.
3. Bot asks for PDF upload.
4. Bot asks for major (button row).
5. Bot asks for class year (button row).
6. Bot runs evaluator, posts scored embed, deletes the resume from memory.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from pathlib import Path

# Load .env from repo root so the bot works under systemd / NSSM / Windows
# Service (which don't auto-load .env files).
try:
    from dotenv import load_dotenv

    _ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass  # python-dotenv not installed; fall back to os.environ only

import discord
from discord import Interaction

from .evaluator import evaluate
from .rubric_loader import list_majors
from .state import SessionStore, Stage, UserSession

log = logging.getLogger("resume-reviewer")

# ---------- Config ----------

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
REVIEW_CHANNEL_ID = int(os.environ.get("REVIEW_CHANNEL_ID", "0"))
MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB cap

# If RESUME_API_URL is set, route PDF processing through AWS Lambda.
# Otherwise, fall back to local evaluate() (deterministic-only without Gemini).
USE_REMOTE_API = bool(os.environ.get("RESUME_API_URL"))

MAJORS = list_majors()  # loaded once at import
YEARS = ["freshman", "sophomore", "junior", "senior", "grad"]

EMBED_COLOR_PRIMARY = 0x5B6CFF
EMBED_COLOR_SUCCESS = 0x2BB673
EMBED_COLOR_WARN = 0xE0A92B


# ---------- View / Components ----------

class StartReviewView(discord.ui.View):
    """Persistent view: button posted in the review channel."""

    def __init__(self) -> None:
        super().__init__(timeout=None)  # persistent

    @discord.ui.button(
        label="Review my resume",
        style=discord.ButtonStyle.primary,
        custom_id="resume_review:start",
        emoji="📝",
    )
    async def start_btn(
        self, interaction: Interaction, _: discord.ui.Button
    ) -> None:
        await _begin_dm_flow(interaction)


class MajorPickerView(discord.ui.View):
    """Major selection buttons in DM."""

    def __init__(self, store: SessionStore, user_id: int) -> None:
        super().__init__(timeout=600)
        self.store = store
        self.user_id = user_id
        for major in MAJORS:
            self.add_item(_MajorButton(major))

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


class _MajorButton(discord.ui.Button):
    def __init__(self, major: str) -> None:
        super().__init__(
            label=major,
            style=discord.ButtonStyle.secondary,
            custom_id=f"resume_review:major:{major}",
        )
        self.major = major

    async def callback(self, interaction: Interaction) -> None:
        sess: UserSession = interaction.client._store.get(interaction.user.id)  # type: ignore[attr-defined]
        sess.major = self.major
        sess.stage = Stage.AWAITING_YEAR
        view = YearPickerView(interaction.client._store, interaction.user.id)  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content=f"Major: **{self.major}**. Pick your class year:",
            view=view,
            embed=None,
        )


class YearPickerView(discord.ui.View):
    """Class-year selection buttons."""

    def __init__(self, store: SessionStore, user_id: int) -> None:
        super().__init__(timeout=600)
        self.store = store
        self.user_id = user_id
        for yr in YEARS:
            self.add_item(_YearButton(yr))


class _YearButton(discord.ui.Button):
    def __init__(self, year: str) -> None:
        super().__init__(
            label=year,
            style=discord.ButtonStyle.secondary,
            custom_id=f"resume_review:year:{year}",
        )
        self.year = year

    async def callback(self, interaction: Interaction) -> None:
        sess: UserSession = interaction.client._store.get(interaction.user.id)  # type: ignore[attr-defined]
        sess.class_year = self.year
        sess.stage = Stage.REVIEWING
        await interaction.response.edit_message(
            content="Got it. Running review…",
            view=None,
            embed=None,
        )
        await _run_review(interaction, sess)


# ---------- DM flow ----------

async def _begin_dm_flow(interaction: Interaction) -> None:
    user = interaction.user
    sess = interaction.client._store.get(user.id)  # type: ignore[attr-defined]

    # Dedupe: if we already started the flow for this user recently, don't
    # send a second "Upload your resume" DM — just confirm + send ephemeral.
    if sess.stage == Stage.AWAITING_RESUME:
        await interaction.response.send_message(
            f"📨 Already waiting for your PDF, {user.mention}. Check your DMs.",
            ephemeral=True,
        )
        return

    try:
        await user.send(
            embed=discord.Embed(
                title="📄 Upload your resume",
                description=(
                    "Send a **PDF** of your resume in this DM (max 5 MB).\n"
                    "After upload, you'll pick your major + class year.\n\n"
                    "🔒 Your resume is processed in-memory and discarded after the review."
                ),
                color=EMBED_COLOR_PRIMARY,
            )
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I can't DM you. Open your DMs and try again.",
            ephemeral=True,
        )
        return

    sess.stage = Stage.AWAITING_RESUME
    log.info("Sent DM upload prompt to user_id=%s", user.id)

    await interaction.response.send_message(
        f"✅ Check your DMs, {user.mention}.",
        ephemeral=True,
    )


async def _on_dm_message(message: discord.Message) -> None:
    """Handle PDF uploads + free-text in DM."""
    log.info(
        "DM msg from %s (%s): stage=%s attachments=%d",
        message.author,
        message.author.id,
        message.client._store.get(message.author.id).stage,  # type: ignore[attr-defined]
        len(message.attachments),
    )
    if message.author.bot:
        return
    if message.guild is not None:
        return  # DM only

    sess = message.client._store.get(message.author.id)  # type: ignore[attr-defined]
    if sess.stage != Stage.AWAITING_RESUME:
        # If they sent a PDF while not in the right state, give them a hint
        if message.attachments:
            await message.channel.send(
                embed=discord.Embed(
                    description=(
                        "I got your file, but I'm not in upload mode. "
                        "Click **Review my resume** in #resume-review-bot to start."
                    ),
                    color=EMBED_COLOR_WARN,
                )
            )
        return

    if not message.attachments:
        await message.channel.send(
            embed=discord.Embed(
                description="Please attach a **PDF** resume to this DM.",
                color=EMBED_COLOR_WARN,
            )
        )
        return

    att = message.attachments[0]
    if not att.filename.lower().endswith(".pdf"):
        await message.channel.send(
            embed=discord.Embed(
                description="Only **PDF** resumes are supported for now.",
                color=EMBED_COLOR_WARN,
            )
        )
        return
    if att.size > MAX_RESUME_BYTES:
        await message.channel.send(
            embed=discord.Embed(
                description=f"Resume too large (max {MAX_RESUME_BYTES // 1024 // 1024} MB).",
                color=EMBED_COLOR_WARN,
            )
        )
        return

    pdf_bytes = await att.read()
    sess.resume_bytes = pdf_bytes
    sess.resume_filename = att.filename
    sess.stage = Stage.AWAITING_MAJOR

    view = MajorPickerView(message.client._store, message.author.id)  # type: ignore[attr-defined]
    await message.channel.send(
        embed=discord.Embed(
            title="🎓 Pick your major",
            description="Which area should I review your resume against?",
            color=EMBED_COLOR_PRIMARY,
        ),
        view=view,
    )


async def _run_review(interaction: Interaction, sess: UserSession) -> None:
    """Run the evaluator and post the scored embed."""
    if not sess.resume_bytes or not sess.major or not sess.class_year:
        await interaction.followup.send(
            "Missing resume, major, or year. Restart by clicking the button again.",
            ephemeral=True,
        )
        sess.stage = Stage.IDLE
        return

    user = interaction.user
    try:
        if USE_REMOTE_API:
            # Upload to S3, invoke Lambda, await JSON
            from .aws_client import review_via_api, upload_pdf

            s3_key = await asyncio.to_thread(
                upload_pdf, sess.resume_bytes, user.id
            )
            review_data = await asyncio.to_thread(
                review_via_api, s3_key, sess.major, sess.class_year, user.id
            )
            review = _review_from_dict(review_data)
        else:
            # Local fallback — deterministic unless GEMINI_API_KEY is set
            review = await asyncio.to_thread(
                evaluate,
                sess.resume_bytes,
                sess.major,
                sess.class_year,
                use_llm=bool(os.environ.get("GEMINI_API_KEY")),
            )
    except Exception as e:  # noqa: BLE001
        log.exception("evaluate failed")
        sess.error = repr(e)
        await interaction.followup.send(
            embed=discord.Embed(
                title="❌ Review failed",
                description=f"```\n{e}\n```\nTry again or ping the bot owner.",
                color=0xCC3344,
            )
        )
        sess.stage = Stage.IDLE
        return

    embed = discord.Embed(
        title=f"📊 Resume Review — {sess.major.title()} / {sess.class_year.title()}",
        description=f"**Final score: `{review.final_score:.1f}`**",
        color=EMBED_COLOR_SUCCESS if review.final_score >= 70 else EMBED_COLOR_WARN,
    )
    if hasattr(review, "model_dump"):
        elapsed = (review.model_dump().get("elapsed_ms") if isinstance(review, object) else None)
        if elapsed:
            embed.set_footer(text=f"Processed in {elapsed}ms")
    for cat in review.categories:
        pct = (cat.score / cat.max_score * 100) if cat.max_score else 0
        body = ""
        if cat.evidence:
            body += "**Evidence:**\n" + "\n".join(f"• {e}" for e in cat.evidence[:3]) + "\n"
        if cat.red_flags_hit:
            body += "**Red flags:** " + ", ".join(cat.red_flags_hit) + "\n"
        if cat.suggestions:
            body += "**Suggestions:**\n" + "\n".join(f"→ {s}" for s in cat.suggestions[:2])
        embed.add_field(
            name=f"{cat.category_key} ({cat.score:.1f}/{cat.max_score})",
            value=body or "_—_",
            inline=False,
        )
    if review.matched_domains:
        embed.add_field(
            name="Matched domains",
            value=", ".join(review.matched_domains),
            inline=False,
        )

    # Send to DM
    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "I can't DM you the results. Open your DMs.",
            ephemeral=True,
        )

    # Cleanup
    sess.resume_bytes = None
    sess.resume_filename = None
    sess.stage = Stage.DONE


# ---------- Lifecycle ----------


def _review_from_dict(d: dict) -> "Review":  # type: ignore[name-defined]
    """Reconstruct a Review Pydantic model from Lambda JSON."""
    from .models import CategoryResult, ClassYearProfile, ResumeSections, Review

    return Review(
        major=d["major"],
        class_year=d["class_year"],
        final_score=d["final_score"],
        categories=[
            CategoryResult(
                category_key=c["category_key"],
                score=c["score"],
                max_score=c["max_score"],
                evidence=c.get("evidence", []),
                red_flags_hit=c.get("red_flags_hit", []),
                suggestions=c.get("suggestions", []),
            )
            for c in d["categories"]
        ],
        matched_domains=d.get("matched_domains", []),
        year_profile=ClassYearProfile(**d["year_profile"]),
        extracted=ResumeSections(raw_text=""),
    )

async def _post_panel(bot: discord.Client, channel_id: int) -> None:
    """Post the persistent review panel — ONCE per channel, then edit-in-place.

    Stores the panel message_id in `data/panel_state.json`. On reconnect
    (on_ready re-fires when Discord gateway resumes), edits the existing
    message instead of posting a new one — prevents panel spam every ~4 min.
    """
    if not channel_id:
        log.warning("REVIEW_CHANNEL_ID not set; skipping panel post.")
        return
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("REVIEW_CHANNEL_ID %s not found in cache.", channel_id)
        return

    embed = discord.Embed(
        title="📝 AKPsi Resume Reviewer",
        description=(
            "Click **Review my resume** to get scored feedback on your resume.\n\n"
            "• 5 majors supported: " + ", ".join(MAJORS) + "\n"
            "• Sophomore / Junior / Senior / etc. calibration\n"
            "• Evidence per category — not just a number\n"
            "🔒 Resume deleted after review."
        ),
        color=EMBED_COLOR_PRIMARY,
    )
    view = StartReviewView()

    state_path = Path(__file__).resolve().parent.parent / "data" / "panel_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    existing_id = state.get(str(channel_id))
    if existing_id:
        try:
            msg = await channel.fetch_message(int(existing_id))
            await msg.edit(embed=embed, view=view)
            log.info("Edited existing panel msg=%s in #%s", existing_id, channel)
            return
        except (discord.NotFound, discord.HTTPException):
            log.info("Existing panel msg=%s gone, posting new", existing_id)

    msg = await channel.send(embed=embed, view=view)
    state[str(channel_id)] = msg.id
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    log.info("Posted new panel msg=%s in #%s", msg.id, channel)


def make_client() -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True
    intents.messages = True
    intents.guilds = True

    client = discord.Client(intents=intents)
    client._store = SessionStore()  # type: ignore[attr-defined]

    @client.event
    async def on_ready() -> None:
        log.info("Logged in as %s (id=%s)", client.user, client.user.id)  # type: ignore[union-attr]
        # Register persistent views
        client.add_view(StartReviewView())  # type: ignore[arg-type]
        await _post_panel(client, REVIEW_CHANNEL_ID)

    @client.event
    async def on_message(message: discord.Message) -> None:
        if isinstance(message.channel, discord.DMChannel):
            await _on_dm_message(message)

    return client


def main() -> None:
    import sys

    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    log.info("bot starting; pid=%s", os.getpid())
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set. See .env.example.")
    client = make_client()
    log.info("client built, starting bot.run()")
    client.run(DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
