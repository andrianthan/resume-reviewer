"""Deprecated thread-flow verifier.

The bot now uses the DM flow:

    panel button -> bot DM -> user uploads PDF in DM -> result in DM

Bots cannot realistically simulate a user's DM upload to another bot through
Discord's gateway, because bot-authored messages are ignored by this bot. Use:

    python -m pytest tests/test_bot_dm_flow.py -v

to verify the button-to-DM and DM-upload handlers in-process, and use
scripts/test_real_flow.py only to smoke-test evaluator + Discord DM delivery.
"""
from __future__ import annotations

import sys


def main():
    print(
        "This script verified the old private-thread flow and is no longer valid.\n"
        "Run: python -m pytest tests/test_bot_dm_flow.py -v\n"
        "Optional live DM delivery smoke test: DISCORD_BOT_TOKEN=... RECIPIENT_ID=... python scripts/test_real_flow.py",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
