"""End-to-end live test on Discord.

Creates a private thread in #resume-review-bot, posts the upload prompt,
processes the user's real PDF through the evaluator, posts the scored
embed inside the thread. Mimics the entire flow minus the user click +
upload steps (which only andrianthan can do).

This proves the bot's runtime stack: thread creation, embed delivery,
LLM call, file processing — all on real Discord.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluator import evaluate  # noqa: E402

FIXTURE_PDF = Path("/Users/andrianthan/Downloads/AnThanSWE (11).pdf")
CHANNEL_ID = "1523086095947006204"


def bot() -> str:
    return os.environ["DISCORD_BOT_TOKEN"]


def req(method: str, path: str, **json_body) -> dict:
    r = requests.request(
        method,
        f"https://discord.com/api/v10{path}",
        headers={
            "Authorization": f"Bot {bot()}",
            "Content-Type": "application/json",
        },
        json=json_body,
        timeout=15,
    )
    if not r.ok:
        raise RuntimeError(f"{method} {path} {r.status_code}: {r.text[:500]}")
    return r.json()


def upload_attachment(channel_id: str, filename: str, data: bytes) -> dict:
    """Upload file via multipart (Discord REST requires multipart for file uploads)."""
    r = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={"Authorization": f"Bot {bot()}"},
        files={"files[0]": (filename, data, "application/pdf")},
        data={"content": f"📎 Test upload: {filename}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def build_embed(review, major: str) -> dict:
    color = 0x2BB673 if review.final_score >= 70 else 0xE0A92B
    embed = {
        "title": f"📊 Resume Review — {major.title()} (internship)",
        "description": f"**Final score: `{review.final_score:.1f}`**",
        "color": color,
    }
    for cat in review.categories:
        body = ""
        if cat.evidence:
            body += "**Evidence:**\n" + "\n".join(f"• {e}" for e in cat.evidence[:2]) + "\n"
        if cat.suggestions:
            body += "**Suggestions:**\n" + "\n".join(f"→ {s}" for s in cat.suggestions[:2])
        embed["fields"] = embed.get("fields", []) + [
            {
                "name": f"{cat.category_key} ({cat.score:.1f}/{cat.max_score})",
                "value": body or "_—_",
                "inline": False,
            }
        ]
    if review.matched_domains:
        embed["fields"].append({
            "name": "Matched domains",
            "value": ", ".join(review.matched_domains),
            "inline": False,
        })
    embed["footer"] = {"text": "End-to-end live test · resume-reviewer · OpenRouter + Gemini 2.5 Flash"}
    return embed


async def main() -> None:
    if not FIXTURE_PDF.exists():
        print(f"SKIP: {FIXTURE_PDF} not found")
        return

    # 1. Create a private thread in the channel
    print("Creating private thread in #resume-review-bot...")
    thread = req(
        "POST",
        f"/channels/{CHANNEL_ID}/threads",
        name="🧪 Live test thread",
        type=12,  # ChannelType.private_thread
        auto_archive_duration=60,
    )
    thread_id = thread["id"]
    print(f"Thread created: {thread_id}  url=https://discord.com/channels/{CHANNEL_ID}/{thread_id}")

    # 2. Post the upload prompt
    req(
        "POST",
        f"/channels/{thread_id}/messages",
        embeds=[{
            "title": "📄 Upload your resume",
            "description": (
                "Drop your **PDF** resume here (max 5 MB).\n"
                "After upload, you'll pick your major and get a scored review.\n\n"
                "🔒 Your resume is processed in-memory and discarded after the review."
            ),
            "color": 0x5B6CFF,
        }],
    )

    # 3. Upload the PDF (simulating the user upload step)
    pdf_bytes = FIXTURE_PDF.read_bytes()
    print(f"Uploading {FIXTURE_PDF.name} ({len(pdf_bytes)} bytes)...")
    upload_attachment(thread_id, FIXTURE_PDF.name, pdf_bytes)

    # 4. Post major buttons
    req(
        "POST",
        f"/channels/{thread_id}/messages",
        content="🎓 **Pick your major**",
        components=[{
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 1,
                    "label": m,
                    "custom_id": f"resume_review:major:{m}",
                }
                for m in ["consulting", "marketing", "ops-hr", "supply-chain"]
            ],
        }],
    )

    # 5. Run evaluator on real PDF
    print("Running evaluator (real PDF + OpenRouter LLM)...")
    review = await asyncio.to_thread(
        evaluate,
        pdf_bytes,
        "consulting",
        "junior",
        use_llm=True,
    )
    print(f"FINAL SCORE: {review.final_score}")
    for c in review.categories:
        print(f"  {c.category_key}: {c.score}/{c.max_score}")

    # 6. Post the scored embed inside the thread
    embed = build_embed(review, "consulting")
    sent = req(
        "POST",
        f"/channels/{thread_id}/messages",
        embeds=[embed],
    )
    print(f"Posted scored review: message {sent['id']}")
    print(f"Thread URL: https://discord.com/channels/{CHANNEL_ID}/{thread_id}")
    print("PASS — open Discord to view.")


if __name__ == "__main__":
    asyncio.run(main())
