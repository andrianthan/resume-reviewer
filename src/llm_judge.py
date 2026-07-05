"""LLM category judge via OpenRouter (works with Gemini, Llama, Qwen, etc.).

For each rubric category, ask the model to score the resume section
on a 0..max scale and return evidence + red-flag hits + suggestions.

OpenRouter is OpenAI-compatible. Set OPENROUTER_API_KEY. The default
model is `google/gemini-2.5-flash` (cheap + fast). Override via
LLM_MODEL env var.
"""
from __future__ import annotations

import json
import os
from typing import Any

import requests

from .models import Category, ResumeSections

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"


def _api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set. See .env.example.")
    return key


def _model() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def judge_category(
    sections: ResumeSections,
    category: Category,
    max_score: float = 40.0,
    model: str | None = None,
) -> dict[str, Any]:
    """Score resume on one category. Returns dict with score/evidence/etc.

    Uses OpenRouter with a strict JSON prompt. Falls back to a
    deterministic stub if the API key is missing or the call fails.
    """
    use_model = model or _model()
    prompt = f"""You are a strict resume reviewer for the {category.label} category.

Rubric: {category.rubric}

Resume (raw text):
---
{sections.raw_text[:6000]}
---

Return JSON with EXACTLY these keys (no extra text, no markdown fence):
{{
  "score": <float 0..{max_score}>,
  "evidence": [<verbatim resume line(s) that drove the score, max 5>],
  "red_flags_hit": [<subset of red_flags that appear: {category.red_flags}>],
  "suggestions": [<actionable fix strings, max 3>]
}}

JSON:"""

    try:
        r = requests.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": use_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a strict resume reviewer. Always return valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=45,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception as e:  # noqa: BLE001
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": [f"(LLM judge unavailable: {e!r})"],
        }

    return {
        "score": float(max(0.0, min(max_score, data.get("score", 0)))),
        "evidence": list(data.get("evidence", []))[:5],
        "red_flags_hit": list(data.get("red_flags_hit", [])),
        "suggestions": list(data.get("suggestions", []))[:3],
    }
