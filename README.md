# resume-reviewer

Discord bot that scores resumes against major-specific rubrics. Click a button → bot DMs the member → member uploads PDF → picks major → scored embed stays in DM.

**Status:** code complete, tested, ready to deploy. Needs Discord bot token + optional OpenRouter key + a channel to test in.

## Flow

1. Bot posts a persistent embed with **Review my resume** button in your chosen channel.
2. Member clicks → bot sends them a DM.
3. Member uploads PDF (max 5 MB) in that DM.
4. Bot asks for major (consulting / finance / marketing / ops-hr / supply-chain / tech).
5. Bot runs evaluator + posts scored embed in the DM with per-category evidence + suggestions.
6. Resume bytes zeroed in memory immediately after review.

## Setup

```bash
cd ~/projects/resume-reviewer
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill .env with your tokens (see below)
```

### Required credentials

| Variable              | Where to get it                                                                 |
| --------------------- | ------------------------------------------------------------------------------- |
| `DISCORD_BOT_TOKEN`   | https://discord.com/developers/applications → New App → Bot → Reset Token       |
| `REVIEW_CHANNEL_ID`   | Discord → enable Developer Mode → right-click channel → Copy ID                |
| `OPENROUTER_API_KEY`  | https://openrouter.ai/keys (optional — deterministic fallback if unset)         |

### Discord bot setup (one-time, in dev portal)

1. **New Application** → name it `AKPsi Resume Reviewer`.
2. **Bot** tab → reset token → copy into `.env`.
3. **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`, `Use Slash Commands` (slash not strictly required — button-only flow works without).
4. Open the generated URL → invite to your AKPsi server.
5. Enable **Message Content Intent** + **Server Members Intent** in Bot tab.

### Create the test channel (manual, one-time)

In your AKPsi Discord:

1. Right-click the parent category (e.g. `#akpsi-officers` or `#resources`) → **Create Channel**.
2. Name: `#resume-review-bot` (or whatever).
3. Privacy: **Private** — visible only to officers / bot testers.
4. Right-click the new channel → **Copy Channel ID** → paste into `REVIEW_CHANNEL_ID` in `.env`.

### Run

```bash
source .venv/bin/activate
python -m src.bot
```

Bot logs in, posts the panel embed in your channel. Click button to test the flow.

### Rate limits

Defaults are intentionally conservative:

| Variable | Default |
| --- | ---: |
| `START_REVIEW_COOLDOWN_SECONDS` | `30` |
| `MAX_REVIEW_STARTS_PER_HOUR` | `10` |

Set a limit to `0` to disable it. Runtime counters live in `data/rate_limits.json`.

## Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

Tests cover rubric loading, skill extraction, start-flow rate limits, and deterministic evaluation across all majors.

## Rubric packs

| Major        | File                            | Source                                       |
| ------------ | ------------------------------- | -------------------------------------------- |
| consulting   | `rubrics/consulting.json`       | Synthesized from Reddit + JD research        |
| finance      | `rubrics/finance.json`          | Synthesized from schema + finance research   |
| marketing    | `rubrics/marketing.json`        | Synthesized from Reddit + JD research        |
| ops-hr       | `rubrics/ops-hr.json`           | Synthesized from Reddit + JD research        |
| supply-chain | `rubrics/supply-chain.json`     | Synthesized from Reddit + JD research        |
| tech         | `rubrics/tech.json`             | Modeled from SWE hiring-agent criteria       |

**Caveat:** rubric data built from documented recruiting consensus (Reddit blocked during research). Re-derive from live JDs before production. See `SCHEMA.md` → "Sourcing caveat".

## Layout

```
src/
  __init__.py
  bot.py            # Discord client + DM upload/review flow
  evaluator.py      # PDF→text, skill match, domain/year adjust, scoring
  pdf_extract.py    # PyMuPDF wrapper
  llm_judge.py      # OpenRouter per-category judge (optional)
  rubric_loader.py  # Load rubric JSON → Pydantic
  state.py          # Per-user conversation state machine
  models.py         # Pydantic models matching SCHEMA.md
tests/
  test_evaluator.py
rubrics/
  consulting.json
  marketing.json
	  ops-hr.json
	  supply-chain.json
	  tech.json
SCHEMA.md           # Pydantic schema + scoring formula
requirements.txt
.env.example
```

## Next steps

1. Drop your `DISCORD_BOT_TOKEN` + `REVIEW_CHANNEL_ID` into `.env`.
2. Add `OPENROUTER_API_KEY` for real LLM-scored categories (else deterministic fallback).
3. `python -m src.bot` → test the flow end-to-end.
4. Re-run rubric research when WebFetch works to swap consensus data for live frequencies.
5. Manual upstream PR if you ever fork this externally.
