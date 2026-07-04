# resume-reviewer

Discord bot that scores resumes against major-specific rubrics.

**Status:** scaffolding only. Rubric packs live, evaluator not yet implemented.

## Layout

```
SCHEMA.md         — Pydantic schema + scoring formula (design doc)
rubrics/          — one JSON per major (consulting, marketing, ops-hr, supply-chain)
src/              — (TBD) evaluator.py + Discord bot
tests/            — (TBD)
```

## Rubric packs

| Major          | File                            | Source notes                              |
| -------------- | ------------------------------- | ----------------------------------------- |
| finance        | (see SCHEMA.md example)         | Hand-crafted v1 in parent project.        |
| consulting     | `rubrics/consulting.json`       | Synthesized from `research/rubric-sources/consulting.json` |
| marketing      | `rubrics/marketing.json`        | Synthesized from `research/rubric-sources/marketing.json`  |
| ops-hr         | `rubrics/ops-hr.json`           | Synthesized from `research/rubric-sources/ops-hr.json`     |
| supply-chain   | `rubrics/supply-chain.json`     | Synthesized from `research/rubric-sources/supply-chain.json` |

**Data caveat:** research JSONs built from documented recruiting consensus (WebSearch returned 400 + Reddit blocked during research). Re-derive frequencies from live JD pulls before production scoring — see SCHEMA.md "Sourcing caveat".

## Next steps

1. Implement `src/evaluator.py` per SCHEMA.md pseudo-code.
2. Wire Discord bot (`/resume-review major:<key> year:<year>`).
3. Decide LLM backend (Gemini vs Ollama).
4. Smoke-test against 5 sample resumes across majors.
5. Manual upstream PR if going external.

## Related

- Parent project: `job-board-aggregator/` — sibling under `~/projects/`.
- ApplyPilot-fork pattern reference: `~/projects/ApplyPilot-fork/`.
