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
    import fitz  # type: ignore  # PyMuPDF
except ImportError as e:
    raise RuntimeError("PyMuPDF missing in Lambda package") from e

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
CATEGORY_MAX = {
    "impact": 40.0,
    "domain_fit": 25.0,
    "technical": 15.0,
    "format": 10.0,
    "brand": 10.0,
    "extras": 10.0,
}


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
    for s in skills:
        if needle in s or s in needle:
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


# --- Gemini judge (inline) ---

def _judge_category(sections_text: str, category: dict, max_score: float) -> dict:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": ["(LLM judge unavailable: GEMINI_API_KEY not set)"],
        }
    try:
        from google import genai  # type: ignore

        client = genai.Client(api_key=api_key)
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
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config={"temperature": 0.2, "response_mime_type": "application/json"},
        )
        data = json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        return {
            "score": 0.0,
            "evidence": [],
            "red_flags_hit": [],
            "suggestions": [f"(LLM judge error: {e!r})"],
        }
    return {
        "score": float(max(0.0, min(max_score, data.get("score", 0)))),
        "evidence": list(data.get("evidence", []))[:5],
        "red_flags_hit": list(data.get("red_flags_hit", [])),
        "suggestions": list(data.get("suggestions", []))[:3],
    }


# --- Main evaluation ---

def _evaluate(text: str, major: str, class_year: str, use_llm: bool) -> dict:
    rubric = load_rubric(major)
    skills = _extract_skills(text)

    skill_hits: dict[str, list[str]] = {"exact": [], "related": [], "transferable": []}
    for rule in rubric["skills"]:
        if _skill_matches(skills, rule):
            skill_hits[rule["tier"]].append(rule["name"])

    category_results: list[dict] = []
    for cat in rubric["categories"]:
        max_score = CATEGORY_MAX.get(cat["key"], 10.0)
        if use_llm:
            j = _judge_category(text, cat, max_score)
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
            tier_bonus = (
                len(skill_hits["exact"]) * 1.5
                + len(skill_hits["related"]) * 0.7
                + len(skill_hits["transferable"]) * 0.3
            )
            score = min(max_score, tier_bonus * (cat["weight"] * 100))
            category_results.append(
                {
                    "category_key": cat["key"],
                    "score": round(score, 2),
                    "max_score": max_score,
                    "evidence": [
                        f"Matched skills: {', '.join(skill_hits['exact'][:3]) or '(none)'}"
                    ],
                    "red_flags_hit": [],
                    "suggestions": [
                        f"Add more {cat['key']}-relevant bullets with quantified impact."
                    ],
                }
            )

    cat_by_key = {c["key"]: c for c in rubric["categories"]}
    raw = sum(
        r["score"] * cat_by_key[r["category_key"]]["weight"]
        for r in category_results
    )

    matched = _matched_domains(text, rubric["domains"])
    if matched:
        mean_mult = sum(d["multiplier"] for d in matched) / len(matched)
        domain_adj = min(raw * mean_mult, raw * 1.5)
    else:
        domain_adj = raw

    year_profile = next(p for p in rubric["class_years"] if p["year"] == class_year)
    year_adj = domain_adj if _meets_year_expectations(text, year_profile) else domain_adj * 0.85

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
        use_llm = bool(os.environ.get("GEMINI_API_KEY"))

        # Download PDF
        obj = s3_client.get_object(Bucket=s3_bucket, Key=s3_key)
        pdf_bytes = obj["Body"].read()

        # Delete from S3 immediately (privacy)
        try:
            s3_client.delete_object(Bucket=s3_bucket, Key=s3_key)
        except Exception as e:  # noqa: BLE001
            print(f"warn: S3 delete failed: {e!r}")

        # Extract text
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n\n".join(p.get_text() for p in doc)
        doc.close()

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
