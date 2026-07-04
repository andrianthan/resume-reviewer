"""Load rubric JSON files into Pydantic Rubric models."""
from __future__ import annotations

import json
from pathlib import Path

from .models import Rubric

RUBRICS_DIR = Path(__file__).resolve().parent.parent / "rubrics"


def load_rubric(major: str) -> Rubric:
    """Load rubric for a major by key (e.g. 'finance', 'consulting')."""
    path = RUBRICS_DIR / f"{major}.json"
    if not path.exists():
        available = sorted(p.stem for p in RUBRICS_DIR.glob("*.json"))
        raise FileNotFoundError(
            f"rubric {major!r} not found. Available: {available}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return Rubric(**data)


def list_majors() -> list[str]:
    """Return sorted list of available rubric majors."""
    return sorted(p.stem for p in RUBRICS_DIR.glob("*.json"))
