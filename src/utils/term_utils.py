from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(slots=True)
class AcademicTerms:
    current: str
    next_main: str
    next_off: str


def academic_terms_for(today: date | None = None) -> AcademicTerms:
    current_date = today or date.today()
    year = current_date.year
    current_season = _current_season(current_date)

    if current_season == "Spring":
        return AcademicTerms(
            current=f"Spring {year}",
            next_main=f"Fall {year}",
            next_off=f"Summer {year}",
        )
    if current_season == "Summer":
        return AcademicTerms(
            current=f"Summer {year}",
            next_main=f"Fall {year}",
            next_off=f"Summer {year + 1}",
        )
    return AcademicTerms(
        current=f"Fall {year}",
        next_main=f"Spring {year + 1}",
        next_off=f"Summer {year + 1}",
    )


def choose_next_term(kind: str, today: date | None = None) -> str:
    terms = academic_terms_for(today)
    if kind.strip().lower() == "off":
        return terms.next_off
    return terms.next_main


def _current_season(current_date: date) -> str:
    if current_date.month <= 5:
        return "Spring"
    if current_date.month <= 7:
        return "Summer"
    return "Fall"
