"""Unit tests for the deterministic (LLM-free) evaluator path."""
from __future__ import annotations

import sys
from pathlib import Path

# Allow `import src.*` when run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.evaluator import extract_sections, evaluate  # noqa: E402
from src.rubric_loader import list_majors, load_rubric  # noqa: E402

FIXTURE = (
    "John Doe\njdoe@example.edu | linkedin.com/in/jdoe\n\n"
    "EDUCATION\n"
    "University of California, Berkeley\n"
    "B.S. Business Administration, GPA: 3.7\n"
    "Expected graduation: May 2027\n\n"
    "EXPERIENCE\n"
    "Goldman Sachs\n"
    "Investment Banking Summer Analyst\n"
    "- Built LBO model on $500M take-private deal; delivered to senior bankers\n"
    "- Pitched 3 live IPOs to coverage group; closed 1 lead-order book mandate\n"
    "- Analyzed comps across 40 public companies using Capital IQ + Excel\n\n"
    "AKPsi (Alpha Kappa Psi)\n"
    "Treasurer\n"
    "- Managed $50K chapter budget; reduced expenses by 12% YoY\n"
    "- Led 15-person exec board\n\n"
    "PROJECTS\n"
    "Stock Pitch Competition\n"
    "- Winner, 2025 Western Regional; pitched long TSLA, beat 80 teams\n\n"
    "SKILLS\n"
    "Excel (VLOOKUP, pivots, macros), Bloomberg Terminal, Capital IQ,\n"
    "Python (pandas), SQL, PowerPoint, Financial Modeling\n"
)


def test_list_majors() -> None:
    majors = list_majors()
    assert "consulting" in majors
    assert "marketing" in majors
    assert "ops-hr" in majors
    assert "supply-chain" in majors
    assert "tech" in majors


def test_load_consulting_rubric_shape() -> None:
    r = load_rubric("consulting")
    assert r.major == "consulting"
    assert len(r.categories) == 6
    assert abs(sum(c.weight for c in r.categories) - 1.0) < 1e-9
    assert len(r.class_years) == 4
    assert r.category("impact").weight == 0.30


def test_extract_skills() -> None:
    sections = extract_sections(FIXTURE)
    skills_lc = [s.lower() for s in sections.skills]
    assert any("excel" in s for s in skills_lc)
    assert any("bloomberg" in s for s in skills_lc)
    assert any("python" in s for s in skills_lc)


def test_evaluate_finance_deterministic() -> None:
    """Run deterministic (no-LLM) eval against consulting rubric on fixture."""
    review = evaluate(major="consulting", class_year="junior", text=FIXTURE, use_llm=False)
    assert review.major == "consulting"
    assert review.class_year == "junior"
    assert 0.0 <= review.final_score <= 100.0
    cats = {c.category_key: c for c in review.categories}
    assert set(cats) == {"impact", "domain_fit", "technical", "format", "brand", "extras"}
    for c in review.categories:
        assert 0.0 <= c.score <= c.max_score
    # Junior year requires internship - fixture has GS IB, should pass
    assert "investment_banking" in review.matched_domains


def test_evaluate_all_majors_dont_explode() -> None:
    for major in list_majors():
        review = evaluate(major=major, class_year="sophomore", text=FIXTURE, use_llm=False)
        assert review.final_score >= 0.0
        assert len(review.categories) >= 4  # every rubric has at least 4 categories
        assert 0.0 <= review.final_score <= 100.0
        assert round(sum(c.max_score for c in review.categories), 2) == 100.0
        # All category weights should sum to 1.0 (validated via Pydantic on load)


if __name__ == "__main__":
    test_list_majors()
    test_load_consulting_rubric_shape()
    test_extract_skills()
    test_evaluate_finance_deterministic()
    test_evaluate_all_majors_dont_explode()
    print("OK")
