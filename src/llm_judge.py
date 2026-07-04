"""Gemini-based category judge.

For each rubric category, ask Gemini to score the resume section
on a 0..max scale and return evidence + red-flag hits + suggestions.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .models import Category, ResumeSections


def _client() -> Any:
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) not set. "
            "See .env.example."
        )
    from google import genai  # type: ignore

    return genai.Client(api_key=api_key)


def judge_category(
    sections: ResumeSections,
    category: Category,
    max_score: float = 40.0,
    model: str = "gemini-2.5-flash",
) -> dict[str, Any]:
    """Score resume on one category. Returns dict with score/evidence/etc.

    Uses Gemini with a strict JSON schema prompt. Falls back to a
    deterministic stub if the API key is missing or the call fails —
    the stub scores 0 and is logged so the caller can detect it.
    """
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
        client = _client()
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config={"temperature": 0.2, "response_mime_type": "application/json"},
        )
        data = json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": [f"(LLM judge unavailable: {e!r})"],
        }

    # Normalize
    return {
        "score": float(max(0.0, min(max_score, data.get("score", 0)))),
        "evidence": list(data.get("evidence", []))[:5],
        "red_flags_hit": list(data.get("red_flags_hit", [])),
        "suggestions": list(data.get("suggestions", []))[:3],
    }
