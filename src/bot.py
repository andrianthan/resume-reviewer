"""Discord bot entrypoint.

Flow:
1. Bot posts an embed with a "Review my resume" button in a configured
   channel (RESUME_REVIEW_CHANNEL_ID).
2. Member clicks button → bot DMs them.
3. Bot asks for PDF upload.
4. Bot asks for major (button row).
5. Bot runs evaluator, posts scored embed, deletes the resume from memory.

Note: scoring is uniform across class years (internship review — same bar
for sophomore and senior).
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
from .rate_limit import RateLimitStore
from .rubric_loader import list_majors
from .state import SessionStore, Stage, UserSession

log = logging.getLogger("resume-reviewer")

# ---------- Config ----------

DISCORD_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
REVIEW_CHANNEL_ID = int(os.environ.get("REVIEW_CHANNEL_ID", "0"))
MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB cap


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


START_REVIEW_COOLDOWN_SECONDS = _env_int(
    "START_REVIEW_COOLDOWN_SECONDS",
    _env_int("THREAD_CREATE_COOLDOWN_SECONDS", 30),
)
MAX_REVIEW_STARTS_PER_HOUR = _env_int(
    "MAX_REVIEW_STARTS_PER_HOUR",
    _env_int("MAX_THREAD_CREATES_PER_HOUR", 10),
)

# If RESUME_API_URL is set, route PDF processing through AWS Lambda.
# Otherwise, fall back to local evaluate() (deterministic-only without OpenRouter).
USE_REMOTE_API = bool(os.environ.get("RESUME_API_URL"))

MAJORS = list_majors()  # loaded once at import
YEARS = ["freshman", "sophomore", "junior", "senior", "grad"]

EMBED_COLOR_PRIMARY = 0x5B6CFF
EMBED_COLOR_SUCCESS = 0x2BB673
EMBED_COLOR_WARN = 0xE0A92B
DISCORD_FIELD_NAME_LIMIT = 256
REVIEW_FIELD_VALUE_LIMIT = 500
RATE_LIMITS = RateLimitStore(Path(__file__).resolve().parent.parent / "data" / "rate_limits.json")

# Module-level handle to the running client, set in on_ready. Avoids
# `message._state.client` (private) and `message.client` (doesn't exist)
# lookups that have been the source of multiple crashes in this flow.
_CLIENT: discord.Client | None = None


def _get_client() -> discord.Client:
    if _CLIENT is None:
        raise RuntimeError("bot client not initialized; on_ready hasn't fired")
    return _CLIENT


def _clip(text: object, limit: int) -> str:
    """Keep generated embed content inside Discord's hard field limits."""
    value = str(text).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


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
        sess: UserSession = _get_client()._store.get(interaction.user.id)  # type: ignore[attr-defined]
        if sess.stage != Stage.AWAITING_MAJOR:
            await interaction.response.send_message(
                "This review is no longer waiting for a major. Click the panel button to start a new review if needed.",
                ephemeral=True,
            )
            return

        sess.major = self.major
        # No year picker — bot scores uniformly across class years.
        # Default to "junior" internally so evaluator API contract holds.
        sess.class_year = "junior"
        sess.stage = Stage.REVIEWING
        await interaction.response.edit_message(
            content=f"Major: **{self.major}**. Running review…",
            view=None,
            embed=None,
        )
        await _run_review(interaction, sess)


class YearPickerView(discord.ui.View):
    """Class-year selection (DISABLED — kept as stub for backward compat).

    The bot scores uniformly across class years because the review is for
    internships, where a sophomore is held to the same bar as a senior.
    This class is a no-op stub so old serialized views don't crash on import.
    """

    def __init__(self, store: SessionStore, user_id: int) -> None:
        super().__init__(timeout=600)
        self.store = store
        self.user_id = user_id


# ---------- DM flow ----------

async def _begin_dm_flow(interaction: Interaction) -> None:
    """Click handler: open a DM and ask the user to upload their PDF."""
    user = interaction.user
    bot = _get_client()
    sess = bot._store.get(user.id)  # type: ignore[attr-defined]

    # Acknowledge the click first (must respond within 3s of interaction)
    await interaction.response.defer(ephemeral=True)

    if sess.stage in {Stage.AWAITING_RESUME, Stage.AWAITING_MAJOR, Stage.REVIEWING}:
        await interaction.followup.send(
            "You already have a resume review in progress. Check your DMs with me to continue.",
            ephemeral=True,
        )
        return

    allowed, message = RATE_LIMITS.check_review_start(
        user.id,
        cooldown_seconds=START_REVIEW_COOLDOWN_SECONDS,
        max_per_hour=MAX_REVIEW_STARTS_PER_HOUR,
    )
    if not allowed:
        await interaction.followup.send(message, ephemeral=True)
        return

    try:
        dm = await user.create_dm()
        await dm.send(
            embed=discord.Embed(
                title="📄 Upload your resume",
                description=(
                    "Send your **PDF** resume in this DM (max 5 MB).\n"
                    "After upload, you'll pick your major and get a scored review here.\n\n"
                    "🔒 Your resume is processed in-memory and discarded after the review."
                ),
                color=EMBED_COLOR_PRIMARY,
            )
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "I couldn't DM you. Enable DMs from server members, then click **Review my resume** again.",
            ephemeral=True,
        )
        return
    except discord.HTTPException as e:
        log.exception("create_dm failed: %s", e)
        await interaction.followup.send(f"Couldn't open a DM: {e}", ephemeral=True)
        return

    sess.resume_bytes = None
    sess.resume_filename = None
    sess.major = None
    sess.class_year = None
    sess.error = None
    sess.dm_channel_id = dm.id
    sess.stage = Stage.AWAITING_RESUME
    RATE_LIMITS.record_review_start(user.id)
    log.info("Started DM review flow for user_id=%s dm_channel_id=%s", user.id, dm.id)

    await interaction.followup.send(
        "✅ I sent you a DM. Upload your resume there to continue.",
        ephemeral=True,
    )


async def _on_dm_message(message: discord.Message, sess: "UserSession") -> None:
    """Handle PDF uploads inside a DM."""
    log.info(
        "dm msg from %s (%s): stage=%s attachments=%d",
        message.author,
        message.author.id,
        sess.stage,
        len(message.attachments),
    )

    if sess.stage != Stage.AWAITING_RESUME:
        if message.attachments:
            await message.channel.send(
                embed=discord.Embed(
                    description=(
                        "I got your file, but I'm not in upload mode. "
                        "Click **Review my resume** in #resume-review-bot to start a new review."
                    ),
                    color=EMBED_COLOR_WARN,
                )
            )
        return

    if not message.attachments:
        await message.channel.send(
            embed=discord.Embed(
                description="Please attach a **PDF** resume in this DM.",
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

    bot = _get_client()
    pdf_bytes = await att.read()
    sess.resume_bytes = pdf_bytes
    sess.resume_filename = att.filename
    sess.stage = Stage.AWAITING_MAJOR

    view = MajorPickerView(bot._store, message.author.id)  # type: ignore[attr-defined]
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
            # Local fallback is deterministic unless OpenRouter is configured.
            use_llm = bool(os.environ.get("OPENROUTER_API_KEY"))
            review = await asyncio.to_thread(
                evaluate,
                sess.resume_bytes,
                sess.major,
                sess.class_year,
                use_llm=use_llm,
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

    # Build score breakdown: total max from categories
    total_max = sum(cat.max_score for cat in review.categories)
    # Color thresholds: 80+ green, 60-80 yellow, <60 red
    color = (
        EMBED_COLOR_SUCCESS
        if review.final_score >= 80
        else EMBED_COLOR_WARN
        if review.final_score >= 60
        else 0xCC3344
    )
    embed = discord.Embed(
        title=f"📊 Resume Review — {sess.major.title()}",
        description=(
            f"**Final score: `{review.final_score:.1f} / 100`**\n"
            f"_Sum of {len(review.categories)} category scores (max {total_max:.0f})._\n"
            "Higher = stronger resume for this role. 80+ is competitive for top internships."
        ),
        color=color,
    )
    if hasattr(review, "model_dump"):
        elapsed = (review.model_dump().get("elapsed_ms") if isinstance(review, object) else None)
        if elapsed:
            embed.set_footer(text=f"Processed in {elapsed}ms")
    for cat in review.categories:
        pct = (cat.score / cat.max_score * 100) if cat.max_score else 0
        body_parts = []
        if cat.evidence:
            body_parts.append(
                "**Evidence:**\n"
                + "\n".join(f"• {_clip(e, 160)}" for e in cat.evidence[:2])
            )
        if cat.red_flags_hit:
            body_parts.append("**Red flags:** " + _clip(", ".join(cat.red_flags_hit), 220))
        if cat.suggestions:
            body_parts.append(
                "**Suggestions:**\n"
                + "\n".join(f"→ {_clip(s, 160)}" for s in cat.suggestions[:2])
            )
        body = "\n".join(body_parts)
        embed.add_field(
            name=_clip(
                f"{cat.category_key} ({cat.score:.1f}/{cat.max_score}) — {pct:.0f}%",
                DISCORD_FIELD_NAME_LIMIT,
            ),
            value=_clip(body or "_—_", REVIEW_FIELD_VALUE_LIMIT),
            inline=False,
        )
    if review.matched_domains:
        embed.add_field(
            name="Matched domains",
            value=", ".join(review.matched_domains),
            inline=False,
        )

    # Post the review in the user's DM. If Discord rejects the embed, do not
    # claim success silently.
    posted_to_dm = False
    post_error: str | None = None
    if sess.dm_channel_id:
        try:
            channel = await _get_client().fetch_channel(sess.dm_channel_id)  # type: ignore[attr-defined]
            if isinstance(channel, discord.DMChannel):
                await channel.send(embed=embed)
                posted_to_dm = True
            else:
                post_error = f"Fetched channel was {type(channel).__name__}, not DMChannel"
                log.warning("review post skipped for dm_channel_id=%s: %s", sess.dm_channel_id, post_error)
        except (discord.NotFound, discord.HTTPException) as e:
            post_error = repr(e)
            log.exception("failed to post review embed to dm_channel_id=%s", sess.dm_channel_id)
    else:
        post_error = "session had no dm_channel_id"

    if posted_to_dm:
        await interaction.followup.send("✅ Done. Your review is posted above.")
    else:
        await interaction.followup.send(
            content=(
                "⚠️ I generated the review, but couldn't post it in DM. "
                f"Showing it here instead. `{post_error}`"
            ),
            embed=embed,
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
            f"• {len(MAJORS)} majors supported: " + ", ".join(MAJORS) + "\n"
            "• Same scoring standard for all class years (internship review)\n"
            "• Evidence per category — not just a number\n"
            "🔒 Feedback is delivered privately by DM."
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
        global _CLIENT
        _CLIENT = client
        log.info("Logged in as %s (id=%s)", client.user, client.user.id)  # type: ignore[union-attr]
        # Register persistent views
        client.add_view(StartReviewView())  # type: ignore[arg-type]
        await _post_panel(client, REVIEW_CHANNEL_ID)

    @client.event
    async def on_message(message: discord.Message) -> None:
        # Dedupe: discord.py occasionally fires on_message twice for the same
        # id (heartbeat / reconnect retries). Track seen ids per session.
        seen = getattr(client, "_seen_msg_ids", None)
        if seen is None:
            seen = set()
            client._seen_msg_ids = seen  # type: ignore[attr-defined]
        if message.id in seen:
            return
        seen.add(message.id)
        if len(seen) > 1000:
            client._seen_msg_ids = set(list(seen)[-500:])  # type: ignore[attr-defined]

        if message.author.bot:
            return
        if message.guild is not None:
            return
        if not isinstance(message.channel, discord.DMChannel):
            return

        sess = client._store.get(message.author.id)  # type: ignore[attr-defined]
        if sess.dm_channel_id and sess.dm_channel_id != message.channel.id:
            return
        await _on_dm_message(message, sess)

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
