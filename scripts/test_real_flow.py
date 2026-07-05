"""End-to-end Discord test: process the user's actual PDF and DM the scored
embed to them via Papa AKPsi bot. Proves the full pipeline (PDF -> evaluate
-> OpenRouter -> Discord embed) works on live infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluator import evaluate  # noqa: E402

FIXTURE_PDF = Path("/Users/andrianthan/Downloads/AnThanSWE (11).pdf")
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
RECIPIENT_ID = os.environ.get("RECIPIENT_ID")  # andrianthan's user id
MAJOR = os.environ.get("MAJOR", "consulting")


def open_dm_channel() -> str:
    r = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"recipient_id": RECIPIENT_ID},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["id"]


def post_embed(channel_id: str, payload: dict) -> dict:
    r = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


async def main() -> None:
    if not BOT_TOKEN or not RECIPIENT_ID:
        print("DISCORD_BOT_TOKEN and RECIPIENT_ID required")
        sys.exit(1)
    if not FIXTURE_PDF.exists():
        print(f"SKIP: {FIXTURE_PDF} not found")
        return

    pdf_bytes = FIXTURE_PDF.read_bytes()
    print(f"PDF: {len(pdf_bytes)} bytes")

    # 1. Open DM
    dm_id = open_dm_channel()
    print(f"DM channel: {dm_id}")

    # 2. Run evaluator with real LLM
    review = await asyncio.to_thread(
        evaluate,
        pdf_bytes,
        MAJOR,
        "junior",
        use_llm=True,
    )
    print(f"FINAL SCORE: {review.final_score}")
    for c in review.categories:
        print(f"  {c.category_key}: {c.score}/{c.max_score}")

    # 3. Build embed
    color = 0x2BB673 if review.final_score >= 70 else 0xE0A92B
    embed = {
        "title": f"📊 Resume Review — {MAJOR.title()} (internship)",
        "description": f"**Final score: `{review.final_score:.1f}`**\n_Class-year calibration disabled — uniform bar for all years._",
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
    embed["footer"] = {"text": "Processed by resume-reviewer bot · OpenRouter + Gemini 2.5 Flash"}

    # 4. Post to DM
    payload = {
        "content": "🧪 **Test review** — full pipeline working end-to-end on live Discord. Your real PDF went through PDF extract → skill match → OpenRouter LLM judge → Discord embed.",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    msg = post_embed(dm_id, payload)
    print(f"Posted DM embed: {msg['id']} (channel {dm_id})")
    print("PASS")


if __name__ == "__main__":
    asyncio.run(main())
