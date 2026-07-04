"""Pydantic models matching docs/RESUME-RUBRIC-SCHEMA.md."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Tier = Literal["exact", "related", "transferable"]
ClassYear = Literal["freshman", "sophomore", "junior", "senior", "grad"]


class SkillRule(BaseModel):
    """One skill keyword + how it counts toward the rubric."""

    name: str
    tier: Tier
    weight: float = 1.0  # 0..2 multiplier on tier base


class DomainWeight(BaseModel):
    """Boost (or penalize) candidates with experience in a target domain."""

    industry: str
    multiplier: float = Field(ge=0.5, le=2.0)


class ClassYearProfile(BaseModel):
    """Per-year expectations."""

    year: ClassYear
    expected_sections: list[str]
    gpa_required_above: Optional[float] = None
    min_quantified_bullets: int = 0
    internship_required: bool = False


class Category(BaseModel):
    """One scoring category."""

    key: str
    label: str
    weight: float  # 0..1, all categories sum to 1.0
    rubric: str
    red_flags: list[str] = Field(default_factory=list)


class Rubric(BaseModel):
    """Top-level rubric for one major."""

    major: str
    label: str
    version: int = 1
    categories: list[Category]
    skills: list[SkillRule]
    domains: list[DomainWeight]
    class_years: list[ClassYearProfile]
    bonus_signals: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)

    def category(self, key: str) -> Category:
        for c in self.categories:
            if c.key == key:
                return c
        raise KeyError(f"category {key!r} not in rubric {self.major!r}")


class ResumeSections(BaseModel):
    """Structured resume extraction produced by LLM."""

    education: list[dict] = Field(default_factory=list)  # {school, gpa, major, grad_date}
    work: list[dict] = Field(default_factory=list)        # {company, role, dates, bullets}
    projects: list[dict] = Field(default_factory=list)
    leadership: list[dict] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    raw_text: str = ""


class CategoryResult(BaseModel):
    """Per-category scoring output."""

    category_key: str
    score: float
    max_score: float
    evidence: list[str] = Field(default_factory=list)
    red_flags_hit: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class Review(BaseModel):
    """Top-level review result."""

    major: str
    class_year: ClassYear
    final_score: float
    categories: list[CategoryResult]
    matched_domains: list[str] = Field(default_factory=list)
    year_profile: ClassYearProfile
    extracted: ResumeSections
