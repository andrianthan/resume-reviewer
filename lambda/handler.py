"""AWS Lambda handler: process resume PDF -> Review JSON.

Invoked by API Gateway HTTP API. Self-contained (does not import from
../src) for smaller deployment package + faster cold start.

Event shape (HTTP API v2):
    {
        "version": "2.0",
        "body": "{\"s3_bucket\":\"...\",\"s3_key\":\"...\",\"major\":\"...\",\"class_year\":\"...\"}",
        "headers": {...}
    }

Response:
    {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": "<Review JSON>"
    }
"""
from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any

# --- Imports: AWS + deps ---
try:
    import boto3  # type: ignore
except ImportError as e:
    raise RuntimeError("boto3 missing in Lambda package") from e

try:
    from pypdf import PdfReader
except ImportError as e:
    raise RuntimeError("pypdf missing in Lambda package") from e

# --- Rubric loader: inline to avoid package path issues ---

RUBRICS_DIR = Path(__file__).parent / "rubrics"


def load_rubric(major: str) -> dict[str, Any]:
    path = RUBRICS_DIR / f"{major}.json"
    if not path.exists():
        raise FileNotFoundError(f"rubric {major!r} not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# --- Skill matching (same heuristic as src/evaluator.py) ---

TIER_BASE = {"exact": 1.0, "related": 0.5, "transferable": 0.25}
SECTION_PATTERNS = {
    "education": r"(?im)^\s*(education|academic)\s*$",
    "work": r"(?im)^\s*(experience|work\s+experience|professional\s+experience|employment)\s*$",
    "projects": r"(?im)^\s*(projects|selected\s+projects)\s*$",
    "leadership": r"(?im)^\s*(leadership|activities|extracurriculars|involvement)\s*$",
    "skills": r"(?im)^\s*(skills|technical\s+skills)\s*$",
}
def _category_max(category: dict) -> float:
    return round(float(category["weight"]) * 100.0, 2)


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {k: [] for k in SECTION_PATTERNS}
    current: str | None = None
    for line in text.splitlines():
        matched = next(
            (k for k, pat in SECTION_PATTERNS.items() if re.match(pat, line.strip())),
            None,
        )
        if matched:
            current = matched
            continue
        if current:
            sections[current].append(line)
    return sections


def _extract_skills(text: str) -> list[str]:
    sections = _split_sections(text)
    blob = "\n".join(sections.get("skills", []))
    raw = re.split(r"[,•\n;]", blob)
    cleaned = [s.strip(" -•\t").lower() for s in raw if s.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for s in cleaned:
        if len(s) < 2 or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _skill_matches(skills: list[str], rule_name: str) -> bool:
    needle = str(rule_name).lower()
    short_needle = len(re.sub(r"[^a-z0-9]", "", needle)) <= 2
    for s in skills:
        haystack = s.lower()
        if short_needle:
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack):
                return True
            continue
        if needle in haystack:
            return True
    return False


def _matched_domains(text: str, domains: list[dict]) -> list[dict]:
    text_l = text.lower()
    out = []
    for d in domains:
        if d["industry"].lower().replace("_", " ") in text_l:
            out.append(d)
    return out


def _has_quantified_bullets(text: str) -> int:
    bullets = [l for l in text.splitlines() if l.strip().startswith(("•", "-", "●"))]
    return sum(1 for b in bullets if re.search(r"\d", b))


def _llm_judge_failed(result: dict) -> bool:
    return (
        float(result.get("score", 0.0)) == 0.0
        and not result.get("evidence")
        and any(str(s).startswith("(LLM judge") for s in result.get("suggestions", []))
    )


def _deterministic_category_result(
    cat: dict,
    max_score: float,
    skill_hits: dict[str, list[str]],
    skill_score: float,
    extra_suggestions: list[str] | None = None,
) -> dict:
    coverage = min(1.0, skill_score / 8.0)
    suggestions = list(extra_suggestions or [])
    suggestions.append(f"Add more {cat['key']}-relevant bullets with quantified impact.")
    return {
        "category_key": cat["key"],
        "score": round(max_score * coverage, 2),
        "max_score": max_score,
        "evidence": [
            f"Matched skills: {', '.join(skill_hits['exact'][:3]) or '(none)'}"
        ],
        "red_flags_hit": [],
        "suggestions": suggestions[:3],
    }


def _meets_year_expectations(text: str, profile: dict) -> bool:
    section_hits = sum(
        1
        for sec in profile["expected_sections"]
        if any(
            re.match(p, line.strip())
            for line in text.splitlines()
            for p in [SECTION_PATTERNS.get(sec, r"(?i)" + sec)]
        )
    )
    if section_hits < len(profile["expected_sections"]) - 1:
        return False
    if profile.get("gpa_required_above") is not None:
        m = re.search(r"gpa[:\s]+([0-9.]+)", text.lower())
        if m and float(m.group(1)) < profile["gpa_required_above"]:
            return False
    if _has_quantified_bullets(text) < profile.get("min_quantified_bullets", 0):
        return False
    if profile.get("internship_required") and not re.search(
        r"(?i)\bintern(ship)?\b", text
    ):
        return False
    return True


# --- LLM judge via OpenRouter (inline) ---

import urllib.request

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash"


def _judge_category(sections_text: str, category: dict, max_score: float) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": ["(LLM judge unavailable: OPENROUTER_API_KEY not set)"],
        }
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    prompt = (
        f"You are a strict resume reviewer for the {category['label']} category.\n\n"
        f"Rubric: {category['rubric']}\n\n"
        f"Resume (raw text):\n---\n{sections_text[:6000]}\n---\n\n"
        "Return JSON with EXACTLY these keys (no extra text, no markdown fence):\n"
        "{\n"
        '  "score": <float 0..' + str(max_score) + ">,\n"
        '  "evidence": [<verbatim resume line(s), max 5>],\n'
        f'  "red_flags_hit": [<subset of red_flags: {category.get("red_flags", [])}>],\n'
        '  "suggestions": [<actionable fix strings, max 3>]\n'
        "}"
    )
    try:
        body = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": "You are a strict resume reviewer. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        req = urllib.request.Request(
            OPENROUTER_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        result = json.loads(content)
    except Exception as e:  # noqa: BLE001
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": [f"(LLM judge error: {e!r})"],
        }
    return {
        "score": float(max(0.0, min(max_score, result.get("score", 0)))),
        "evidence": list(result.get("evidence", []))[:5],
        "red_flags_hit": list(result.get("red_flags_hit", [])),
        "suggestions": list(result.get("suggestions", []))[:3],
    }


# --- Main evaluation ---

def _evaluate(text: str, major: str, class_year: str, use_llm: bool) -> dict:
    rubric = load_rubric(major)
    skills = _extract_skills(text)

    skill_hits: dict[str, list[str]] = {"exact": [], "related": [], "transferable": []}
    skill_score = 0.0
    for rule in rubric["skills"]:
        if _skill_matches(skills, rule["name"]):
            skill_hits[rule["tier"]].append(rule["name"])
            skill_score += TIER_BASE[rule["tier"]] * float(rule.get("weight", 1.0))

    category_results: list[dict] = []
    for cat in rubric["categories"]:
        max_score = _category_max(cat)
        if use_llm:
            j = _judge_category(text, cat, max_score)
            if _llm_judge_failed(j):
                category_results.append(
                    _deterministic_category_result(
                        cat,
                        max_score,
                        skill_hits,
                        skill_score,
                        extra_suggestions=list(j.get("suggestions", []))[:1],
                    )
                )
                continue
            category_results.append(
                {
                    "category_key": cat["key"],
                    "score": j["score"],
                    "max_score": max_score,
                    "evidence": j["evidence"],
                    "red_flags_hit": j["red_flags_hit"],
                    "suggestions": j["suggestions"],
                }
            )
        else:
            category_results.append(
                _deterministic_category_result(cat, max_score, skill_hits, skill_score)
            )

    raw = sum(r["score"] for r in category_results)

    matched = _matched_domains(text, rubric["domains"])
    if matched:
        mean_mult = sum(d["multiplier"] for d in matched) / len(matched)
        domain_adj = min(raw * mean_mult, raw * 1.5)
    else:
        domain_adj = raw
    domain_adj = min(100.0, domain_adj)

    year_profile = next(p for p in rubric["class_years"] if p["year"] == class_year)
    year_adj = domain_adj

    return {
        "major": major,
        "class_year": class_year,
        "final_score": round(year_adj, 2),
        "categories": category_results,
        "matched_domains": [d["industry"] for d in matched],
        "year_profile": year_profile,
        "rubric_label": rubric["label"],
        "use_llm": use_llm,
        "skill_hits": skill_hits,
    }


# --- Lambda entrypoint ---

s3_client = boto3.client("s3")


def lambda_handler(event: dict, context: Any) -> dict:
    start = time.time()
    try:
        # HTTP API v2 puts body in event["body"] (string)
        body_raw = event.get("body") or "{}"
        body = json.loads(body_raw) if isinstance(body_raw, str) else body_raw

        s3_bucket = body["s3_bucket"]
        s3_key = body["s3_key"]
        major = body["major"]
        class_year = body["class_year"]
        use_llm = bool(os.environ.get("OPENROUTER_API_KEY"))

        # Download PDF
        obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
        pdf_bytes = obj["Body"].read()

        # Delete from S3 immediately (privacy)
        try:
            s3_client.delete_object(Bucket=s3_bucket, Key=s3_key)
        except Exception as e:  # noqa: BLE001
            print(f"warn: S3 delete failed: {e!r}")

        # Extract text
        reader = PdfReader(io.BytesIO(pdf_bytes))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)

        if not text.strip():
            return _resp(400, {"error": "PDF had no extractable text (scanned/image-only?)"})

        # Evaluate
        review = _evaluate(text, major, class_year, use_llm=use_llm)
        review["elapsed_ms"] = int((time.time() - start) * 1000)
        return _resp(200, review)

    except KeyError as e:
        return _resp(400, {"error": f"missing field: {e}"})
    except Exception as e:  # noqa: BLE001
        print(f"error: {e!r}")
        return _resp(500, {"error": repr(e)})


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
