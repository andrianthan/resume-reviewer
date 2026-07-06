"""Post the resume-reviewer panel embed into a channel.

Uses Papa AKPsi bot (admin in AKPsi guild) to drop the panel. Run once
to seed the channel; the full bot (src/bot.py) will re-post on startup.

Usage:
    DISCORD_BOT_TOKEN=... CHANNEL_ID=... python scripts/post_panel.py
"""
from __future__ import annotations

import os
import sys

import requests


def main() -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]
    channel_id = os.environ["CHANNEL_ID"]

    payload = {
        "embeds": [
            {
                "title": "📝 AKPsi Resume Reviewer",
                "description": (
                    "Click **Review my resume** to get scored feedback on your resume.\n\n"
                    "• 4 majors supported: consulting, marketing, ops-hr, supply-chain\n"
                    "• Feedback is sent privately by DM\n"
                    "• Evidence per category — not just a number\n"
                    "🔒 Resume deleted after review."
                ),
                "color": 0x5B6CFF,
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 1,
                        "label": "Review my resume",
                        "emoji": {"name": "📝"},
                        "custom_id": "resume_review:start",
                    }
                ],
            }
        ],
    }

    r = requests.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    if r.status_code >= 300:
        print(f"FAILED {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    data = r.json()
    print(f"Posted panel: message_id={data['id']} channel_id={data['channel_id']}")


if __name__ == "__main__":
    main()
