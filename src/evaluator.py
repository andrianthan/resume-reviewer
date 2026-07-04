"""Resume evaluator: orchestrates PDF extract + LLM judge + rubric scoring."""
from __future__ import annotations

import re
from typing import Iterable

from .models import (
    CategoryResult,
    ClassYearProfile,
    DomainWeight,
    ResumeSections,
    Review,
    Rubric,
    SkillRule,
)
from .pdf_extract import pdf_to_markdown
from .rubric_loader import load_rubric

# Map rubric max-score to each category — default 40 for impact, 25 for fit,
# 15 for technical, 10 for format, 10 for brand, 10 for extras. Override per
# rubric in future. (Matches finance example: 30+25+15+10+10+10 = 100.)
CATEGORY_MAX = {
    "impact": 40.0,
    "domain_fit": 25.0,
    "technical": 15.0,
    "format": 10.0,
    "brand": 10.0,
    "extras": 10.0,
}

# Tier base values for skill matching (deterministic partial scoring).
TIER_BASE = {"exact": 1.0, "related": 0.5, "transferable": 0.25}


# ---------- Section extraction (LLM-free heuristic) ----------

SECTION_PATTERNS = {
    "education": r"(?im)^\s*(education|academic)\s*$",
    "work": r"(?im)^\s*(experience|work\s+experience|professional\s+experience|employment)\s*$",
    "projects": r"(?im)^\s*(projects|selected\s+projects)\s*$",
    "leadership": r"(?im)^\s*(leadership|activities|extracurriculars|involvement)\s*$",
    "skills": r"(?im)^\s*(skills|technical\s+skills)\s*$",
}


def _split_sections(text: str) -> dict[str, list[str]]:
    """Naive: split resume text by all-caps section headers."""
    lines = text.splitlines()
    sections: dict[str, list[str]] = {k: [] for k in SECTION_PATTERNS}
    current: str | None = None
    for line in lines:
        matched = None
        for key, pat in SECTION_PATTERNS.items():
            if re.match(pat, line.strip()):
                matched = key
                break
        if matched:
            current = matched
            continue
        if current:
            sections[current].append(line)
    return sections


def _extract_skills(text: str) -> list[str]:
    """Pull comma-/newline-separated tokens from the Skills section, lower-cased."""
    sections = _split_sections(text)
    skills_blob = "\n".join(sections.get("skills", []))
    # Split on commas, bullets, newlines
    raw = re.split(r"[,•\n;]", skills_blob)
    cleaned = [s.strip(" -•\t").lower() for s in raw if s.strip()]
    # Dedup + drop ultra-short
    seen = set()
    out: list[str] = []
    for s in cleaned:
        if len(s) < 2:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def extract_sections(text: str) -> ResumeSections:
    """LLM-free resume extraction. Conservative: pulls skills + raw text only.

    For richer extraction (education.gpa, work.bullets, etc.) plug in an LLM
    pass before calling the evaluator. The current scorer only needs
    `skills` + `raw_text` for skill matching + LLM judge evidence.
    """
    return ResumeSections(
        skills=_extract_skills(text),
        raw_text=text,
    )


# ---------- Skill / domain matching (deterministic) ----------

def _skill_matches(skills: Iterable[str], rule: SkillRule) -> bool:
    needle = rule.name.lower()
    # Naive: substring match on any skill token. Skip overly-broad rules.
    for s in skills:
        if needle in s or s in needle:
            return True
    return False


def _matched_domains(
    sections: ResumeSections, domains: list[DomainWeight]
) -> list[DomainWeight]:
    text = sections.raw_text.lower()
    matched = []
    for d in domains:
        # Use the industry key as the needle
        if d.industry.lower().replace("_", " ") in text:
            matched.append(d)
    return matched


def _has_quantified_bullets(text: str) -> int:
    """Count bullet lines containing a number/percent/multiplier."""
    bullets = [l for l in text.splitlines() if l.strip().startswith(("•", "-", "●"))]
    quantified = sum(
        1 for b in bullets if re.search(r"\d", b)
    )
    return quantified


def _meets_year_expectations(
    sections: ResumeSections, profile: ClassYearProfile
) -> bool:
    text = sections.raw_text
    text_l = text.lower()
    section_hits = sum(
        1
        for sec in profile.expected_sections
        if any(re.match(p, line.strip()) for line in text.splitlines() for p in [SECTION_PATTERNS.get(sec, r"(?i)" + sec)])
    )
    if section_hits < len(profile.expected_sections) - 1:
        return False
    if profile.gpa_required_above is not None:
        gpa_match = re.search(r"gpa[:\s]+([0-9.]+)", text_l)
        if gpa_match and float(gpa_match.group(1)) < profile.gpa_required_above:
            return False
    if _has_quantified_bullets(text) < profile.min_quantified_bullets:
        return False
    if profile.internship_required and not re.search(
        r"(?i)\bintern(ship)?\b", text
    ):
        return False
    return True


# ---------- Evaluator entrypoint ----------

def evaluate(
    pdf_bytes: bytes | None = None,
    major: str = "finance",
    class_year: ClassYearProfile.__fields__["year"].annotation = "junior",  # type: ignore[attr-defined]
    *,
    text: str | None = None,
    use_llm: bool = False,
) -> Review:
    """Run full evaluation: extract → score categories → adjust → return Review.

    Pass either `pdf_bytes` (PDF upload path) or `text` (already-extracted
    text — used by tests and any upstream pipeline that pre-extracted).

    If use_llm=False, category scores are derived deterministically from
    skill matching only (useful for offline testing). If use_llm=True, each
    category is scored by Gemini (see llm_judge.py).
    """
    if pdf_bytes is None and text is None:
        raise ValueError("evaluate() requires pdf_bytes or text")
    if text is None:
        text = pdf_to_markdown(pdf_bytes)  # type: ignore[arg-type]
    rubric = load_rubric(major)
    sections = extract_sections(text)

    # Match skills
    skill_hits: dict[str, list[str]] = {"exact": [], "related": [], "transferable": []}
    for rule in rubric.skills:
        if _skill_matches(sections.skills, rule):
            skill_hits[rule.tier].append(rule.name)

    # Category scores
    category_results: list[CategoryResult] = []
    for cat in rubric.categories:
        max_score = CATEGORY_MAX.get(cat.key, 10.0)
        if use_llm:
            from .llm_judge import judge_category  # local import — optional dep

            j = judge_category(sections, cat, max_score=max_score)
            category_results.append(
                CategoryResult(
                    category_key=cat.key,
                    score=j["score"],
                    max_score=max_score,
                    evidence=j["evidence"],
                    red_flags_hit=j["red_flags_hit"],
                    suggestions=j["suggestions"],
                )
            )
        else:
            # Deterministic stub: blend skill hits into score
            tier_bonus = (
                len(skill_hits["exact"]) * 1.5
                + len(skill_hits["related"]) * 0.7
                + len(skill_hits["transferable"]) * 0.3
            )
            score = min(max_score, tier_bonus * (cat.weight * 100))
            category_results.append(
                CategoryResult(
                    category_key=cat.key,
                    score=round(score, 2),
                    max_score=max_score,
                    evidence=[
                        f"Matched skills: {', '.join(skill_hits['exact'][:3]) or '(none)'}"
                    ],
                    red_flags_hit=[],
                    suggestions=[
                        f"Add more {cat.key}-relevant bullets with quantified impact."
                    ],
                )
            )

    # Weighted raw total
    cat_by_key = {c.key: c for c in rubric.categories}
    raw = sum(r.score * cat_by_key[r.category_key].weight for r in category_results)

    # Domain adjustment
    matched_domains = _matched_domains(sections, rubric.domains)
    if matched_domains:
        mean_mult = sum(d.multiplier for d in matched_domains) / len(matched_domains)
        domain_adj = raw * mean_mult
        domain_adj = min(domain_adj, raw * 1.5)  # cap
    else:
        domain_adj = raw

    # Class-year profile lookup + adjustment
    year_profile = next(p for p in rubric.class_years if p.year == class_year)
    if _meets_year_expectations(sections, year_profile):
        year_adj = domain_adj
    else:
        year_adj = domain_adj * 0.85

    return Review(
        major=major,
        class_year=class_year,
        final_score=round(year_adj, 2),
        categories=category_results,
        matched_domains=[d.industry for d in matched_domains],
        year_profile=year_profile,
        extracted=sections,
    )
