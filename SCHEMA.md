# Resume Reviewer Rubric Schema

Design doc for `akpsi-resume-reviewer` fork. Schema is rubric-agnostic —
each major (`finance`, `consulting`, `marketing`, `tech`, `ops-hr`, `general`)
fills its own JSON. Evaluator loads one rubric per review based on the
member's slash-command selection.

## Design goals

- **3-tier skill weight** — exact / related / transferable (Seeker pattern).
- **Domain-fit multiplier** — fintech > generic for finance roles, etc.
- **Class-year calibration** — sophomore ≠ senior expectations.
- **Evidence per category** — every score carries the resume line(s) that
  drove it, so the Discord bot can quote *why* not just *what*.
- **Red-flag list** — major-specific disqualifiers (missing GPA for IB,
  no quantified impact for consulting, etc).

## Schema (Pydantic)

```python
from typing import Literal
from pydantic import BaseModel, Field

Tier = Literal["exact", "related", "transferable"]
ClassYear = Literal["freshman", "sophomore", "junior", "senior", "grad"]

class SkillRule(BaseModel):
    """One skill keyword + how it counts toward the rubric."""
    name: str                          # "Python", "Excel", "Bloomberg"
    tier: Tier                         # exact / related / transferable
    weight: float = 1.0                # 0..2 multiplier on tier base

class DomainWeight(BaseModel):
    """Boost (or penalize) candidates with experience in a target domain."""
    industry: str                      # "fintech", "F100", "startup"
    multiplier: float = Field(ge=0.5, le=2.0)  # 0.5x .. 2.0x

class ClassYearProfile(BaseModel):
    """Per-year expectations. Sophomore: GPA matters less, proj demos ok.
    Senior: leadership + outcomes expected, GPA still in play."""
    year: ClassYear
    expected_sections: list[str]       # ["education","work","projects","leadership"]
    gpa_required_above: float | None    # None = don't penalize missing GPA
    min_quantified_bullets: int         # 0 for freshman, 5+ for senior
    internship_required: bool

class Category(BaseModel):
    """One scoring category (Impact, Clarity, Relevance, Format, Brand)."""
    key: str                           # "impact", "clarity", ...
    label: str                         # human label for Discord embed
    weight: float                      # 0..1, all categories sum to 1.0
    rubric: str                        # free-form instructions for LLM judge
    red_flags: list[str]               # bullets that auto-deduct

class Rubric(BaseModel):
    """Top-level rubric for one major."""
    major: str                         # "finance"
    label: str                         # "Finance / Banking"
    version: int = 1

    categories: list[Category]         # sum(weight) must == 1.0
    skills: list[SkillRule]            # 3-tier skill catalog
    domains: list[DomainWeight]        # industry-fit boosts
    class_years: list[ClassYearProfile]  # one per expected year

    bonus_signals: list[str]           # ["CFA L1", "IB internship", ...]
    disqualifiers: list[str]           # ["no_grad_date_listed", ...]

    target_roles: list[str]            # ["IB","PE","ER","S&T","AM"] — for Verify
```

## Scoring formula

```
category_score(category, resume) =
    judge_llm(resume, category.rubric)              # 0..100
  - red_flag_deductions(category.red_flags, resume)
  + bonus_signal_matches(bonus_signals, resume) * 5

raw_total = Σ (category_score(c) * category.weight)   # 0..100

domain_adjusted = raw_total * Σ(domain.multiplier for matched_domain) / N
                  # capped at 1.5x to prevent runaway

class_year_adjusted = (
    domain_adjusted
    if meets_year_expectations(class_years[year], resume)
    else domain_adjusted * 0.85                      # soft penalty
)

final = round(class_year_adjusted, 1)
```

## Evidence requirement

Each category output MUST include:
```python
class CategoryResult(BaseModel):
    category_key: str
    score: float
    evidence: list[str]    # resume lines quoted verbatim
    red_flags_hit: list[str]
    suggestions: list[str] # actionable fixes, e.g. "add quantified impact to Acme SWE role"
```

Bot DMs the user:
```
Impact (32/40)
  • "Spearheaded team projects" — vague, no metric
  • "Improved process efficiency by 20%" — solid
  ⚠ Red flag: no quantified outcome in first 3 bullets
  → Add a number to each leadership bullet
```

## Filled example — finance rubric

```json
{
  "major": "finance",
  "label": "Finance / Banking",
  "version": 1,
  "categories": [
    { "key": "impact",        "label": "Quantified Impact",  "weight": 0.30,
      "rubric": "Score 0-40 based on % of bullets with numbers ($, %, x, count). Banking values deal size, AUM, model work, transaction volume.",
      "red_flags": ["no_quantified_bullets_in_work", "all_bullets_vague"] },
    { "key": "domain_fit",    "label": "Finance Domain Fit", "weight": 0.25,
      "rubric": "Score 0-25 on banking/finance relevance of work + coursework. Strong: deal team named, model build, coverage group. Weak: generic retail.",
      "red_flags": ["no_finance_work", "no_relevant_coursework"] },
    { "key": "technical",     "label": "Technical (Finance)","weight": 0.15,
      "rubric": "Score 0-15 on Excel, Bloomberg, CapIQ, Python/R, VBA, FactSet. Transferable: SQL, Tableau.",
      "red_flags": ["missing_excel"] },
    { "key": "format",        "label": "Format / ATS",        "weight": 0.10,
      "rubric": "Score 0-10 on 1-page (FT) / 2-page rule, clean sections, no tables/columns, .docx + .pdf export tested.",
      "red_flags": ["two_column_layout", "graphics_in_header"] },
    { "key": "brand",         "label": "Brand Consistency",   "weight": 0.10,
      "rubric": "Score 0-10 on LinkedIn headline + experience matching resume. Recruiter will cross-check.",
      "red_flags": ["linkedin_role_mismatch"] },
    { "key": "extras",        "label": "Extras & Leadership", "weight": 0.10,
      "rubric": "Score 0-10 on finance clubs (IBFA, AKPsi treasury), CFA L1, stock pitch comp, case comp win, leadership outside work.",
      "red_flags": [] }
  ],
  "skills": [
    { "name": "Excel (VLOOKUP/XLOOKUP/pivots)",   "tier": "exact",       "weight": 1.5 },
    { "name": "Excel (basic formulas)",            "tier": "exact",       "weight": 1.0 },
    { "name": "Bloomberg Terminal",                "tier": "exact",       "weight": 1.5 },
    { "name": "Capital IQ",                        "tier": "exact",       "weight": 1.2 },
    { "name": "FactSet",                           "tier": "exact",       "weight": 1.0 },
    { "name": "VBA",                               "tier": "exact",       "weight": 1.0 },
    { "name": "Python (pandas/numpy)",             "tier": "related",     "weight": 1.0 },
    { "name": "R",                                 "tier": "related",     "weight": 0.8 },
    { "name": "SQL",                               "tier": "related",     "weight": 0.9 },
    { "name": "Tableau",                           "tier": "transferable","weight": 0.5 },
    { "name": "PowerPoint",                        "tier": "exact",       "weight": 1.0 }
  ],
  "domains": [
    { "industry": "investment_banking", "multiplier": 1.4 },
    { "industry": "private_equity",     "multiplier": 1.4 },
    { "industry": "fintech",            "multiplier": 1.2 },
    { "industry": "asset_management",   "multiplier": 1.2 },
    { "industry": "equity_research",    "multiplier": 1.2 },
    { "industry": "consulting",         "multiplier": 1.0 },
    { "industry": "retail",             "multiplier": 0.7 },
    { "industry": "food_service",       "multiplier": 0.6 }
  ],
  "class_years": [
    { "year": "freshman",  "expected_sections": ["education","projects"], "gpa_required_above": null,  "min_quantified_bullets": 0, "internship_required": false },
    { "year": "sophomore", "expected_sections": ["education","work","projects","leadership"], "gpa_required_above": 3.2, "min_quantified_bullets": 3, "internship_required": false },
    { "year": "junior",    "expected_sections": ["education","work","projects","leadership"], "gpa_required_above": 3.4, "min_quantified_bullets": 5, "internship_required": true },
    { "year": "senior",    "expected_sections": ["education","work","leadership"], "gpa_required_above": 3.5, "min_quantified_bullets": 7, "internship_required": true }
  ],
  "bonus_signals": [
    "CFA Level I candidate/pass",
    "IB internship at BB/EB/MM",
    "Stock pitch competition winner",
    "AKPsi treasury / finance role",
    "Published equity research",
    "Founder of finance-related org"
  ],
  "disqualifiers": [
    "no_graduation_date",
    "gpa_below_3.0_and_no_explanation",
    "spelling_errors_in_firm_names"
  ],
  "target_roles": ["Investment Banking","Sales & Trading","Equity Research","Private Equity","Asset Management","Corporate Finance","FP&A","Wealth Management"]
}
```

## Evaluator pseudo-code

```python
async def review(resume_pdf: bytes, major: str, class_year: ClassYear) -> Review:
    rubric = load_rubric(major)                          # JSON → Rubric
    md = pdf_to_markdown(resume_pdf)                     # PyMuPDF
    structured = extract_sections(md)                    # LLM → JSON Resume

    category_results = []
    for cat in rubric.categories:
        score, evidence, hits, fixes = await judge_category(
            structured, cat, rubric.skills
        )
        category_results.append(CategoryResult(
            category_key=cat.key, score=score,
            evidence=evidence, red_flags_hit=hits, suggestions=fixes
        ))

    raw = sum(r.score * cat.weight
              for r, cat in zip(category_results, rubric.categories))

    matched_domains = match_domains(structured.experience, rubric.domains)
    domain_adj = raw * mean(d.multiplier for d in matched_domains) if matched_domains else raw
    domain_adj = min(domain_adj, raw * 1.5)               # cap

    year_profile = next(p for p in rubric.class_years if p.year == class_year)
    year_adj = domain_adj if meets_expectations(structured, year_profile) else domain_adj * 0.85

    return Review(
        major=major,
        class_year=class_year,
        final_score=round(year_adj, 1),
        categories=category_results,
        matched_domains=[d.industry for d in matched_domains],
        year_profile=year_profile,
        extracted=structured   # ← bot shows this in Verify step
    )
```

## Discord bot flow

1. Member runs `/resume-review major:Finance year:Junior`
2. Bot replies with ephemeral: "Upload your resume PDF in this DM"
3. Member uploads → bot:
   - Parses PDF (PyMuPDF)
   - Extracts sections (LLM)
   - Runs review against rubric
   - **Verify step**: posts extracted skills/experience, member confirms/edits
   - Posts final scored report to DM (rich embed, one category per field)
4. Resume file deleted from disk immediately after review
5. Structured JSON kept only if member opts in ("save my profile for future reviews")

## Open questions

- LLM backend: Gemini (existing) vs Ollama (private for resume content)
- Where to host bot: same VPS as job-board, or new?
- Member auth: AKPsi roster verify (roster CSV upload by officer)?
- Multi-major picker: per-review slash arg, or persistent user setting (`/set-major`)?

## Filled rubric packs

Rubrics are stored as standalone JSON files alongside this doc. Evaluator
loads the file matching the member's `/resume-review major:<key>` selection.

| Major          | File                                                       | Notes                                                              |
| -------------- | ---------------------------------------------------------- | ------------------------------------------------------------------ |
| finance        | `docs/rubrics/finance.json` (see example above)            | Hand-crafted v1; reference for shape.                              |
| consulting     | `docs/rubrics/consulting.json`                             | MBB/Big 4 focus; GPA bar 3.6-3.7; pyramid principle + case prep.    |
| marketing      | `docs/rubrics/marketing.json`                              | Portfolio + metrics-led; GA4 + Google/Meta Ads; CPG weight 1.4.    |
| ops-hr         | `docs/rubrics/ops-hr.json`                                 | Discretion + volume-metrics; Workday/ATS; tech SaaS weight 1.4.    |
| supply-chain   | `docs/rubrics/supply-chain.json`                           | KPI + ERP-led; advanced Excel + SAP; CPG mfg weight 1.4.           |
| tech (fork)    | (upstream `hiring-agent`)                                  | Reuse existing hiring-agent rubric; no fork-local file needed.     |

### Sourcing caveat

All four non-finance rubrics were synthesized from
`research/rubric-sources/<domain>.json`, each built from a parallel
research agent pulling live JDs + Reddit + web guides. WebSearch returned
400 errors and WebFetch was blocked on Reddit + most corporate career
sites for the research sessions, so skill frequencies and Reddit quotes
were reconstructed from documented recruiting consensus rather than
freshly fetched text. Every research JSON carries a `notes` field
flagging this. **Before relying on rubric weights for high-stakes
scoring**, re-run research when WebFetch is restored to swap paraphrased
quotes for verbatim and re-derive frequencies from a larger live sample.

All 4 packs validate against the Pydantic schema: 6 categories, weights
sum to 1.0, 23-29 skills tiered exact/related/transferable, 8-9 domain
multipliers, 4 class-year profiles.