"""End-to-end test: process a real PDF and post the scored embed to the
real resume-review-bot Discord channel via Papa AKPsi. Proves the
evaluator + OpenRouter + Discord embed delivery works on live infra.

Skips the DM flow (which requires andrianthan's user account to
interact) — focuses on the data path: PDF -> extract -> skill match ->
LLM -> embed -> live Discord message.
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
CHANNEL_ID = "1523086095947006204"  # #resume-review-bot


def post_embed(payload: dict) -> dict:
    r = requests.post(
        f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
        headers={
            "Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


async def main() -> None:
    if not os.environ.get("DISCORD_BOT_TOKEN"):
        print("DISCORD_BOT_TOKEN required")
        sys.exit(1)
    if not FIXTURE_PDF.exists():
        print(f"SKIP: {FIXTURE_PDF} not found")
        return

    pdf_bytes = FIXTURE_PDF.read_bytes()
    print(f"PDF: {len(pdf_bytes)} bytes")

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

    color = 0x2BB673 if review.final_score >= 70 else 0xE0A92B
    embed = {
        "title": f"📊 Resume Review — Consulting (internship)",
        "description": (
            f"**Final score: `{review.final_score:.1f}`**"
        ),
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
    embed["footer"] = {"text": "Processed by resume-reviewer · OpenRouter + Gemini 2.5 Flash"}

    payload = {
        "content": (
            "🧪 **Live end-to-end test** — your real PDF went through the "
            "full pipeline: extract → skill match → OpenRouter LLM judge → Discord embed."
        ),
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }
    msg = post_embed(payload)
    print(f"Posted to #{CHANNEL_ID}: message {msg['id']}")
    print("PASS — open Discord to view.")


if __name__ == "__main__":
    asyncio.run(main())
