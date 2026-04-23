from __future__ import annotations

import asyncio

from src.models import CourseRecord
from src.providers.helpers import subject_codes_for_major, to_course_record_from_section

try:
    from pyvt import Timetable, TimetableError
except Exception:  # pragma: no cover - optional dependency
    Timetable = None
    TimetableError = Exception


class VTCatalogProvider:
    def __init__(self, preferred_term_label: str = "", preferred_term_year: str = ""):
        self.preferred_term_label = preferred_term_label
        self.preferred_term_year = preferred_term_year
        self.available = Timetable is not None
        self.last_refresh = "Never"
        self.last_error = "None"
        self.source_name = "pyvt"
        self.timetable = Timetable() if self.available else None

    async def refresh(self):
        if not self.available:
            self.last_error = "py-vt is not installed"
            return
        self.last_refresh = "OK"
        self.last_error = "None"

    async def get_course_by_crn(self, crn: str, term: str = "", school: str = "Virginia Tech") -> CourseRecord | None:
        if not self.available:
            return None

        term_year = self._term_year(term)

        try:
            section = await asyncio.to_thread(
                self.timetable.crn_lookup,
                crn,
                term_year,
                False,
            )
        except TimetableError as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

        if section is None:
            return None

        subject_hints = subject_codes_for_major(self._subject_hint_from_code(getattr(section, "code", "")))
        return to_course_record_from_section(section, school, term or self.preferred_term_label, subject_hints, self.source_name)

    async def list_courses_for_profile(self, major: str, school: str, term: str) -> list[CourseRecord]:
        if not self.available:
            return []

        term_year = self._term_year(term)
        subject_codes = subject_codes_for_major(major)
        collected: dict[str, CourseRecord] = {}

        for subject_code in subject_codes:
            try:
                sections = await asyncio.to_thread(
                    self.timetable.subject_lookup,
                    subject_code,
                    term_year,
                    False,
                )
            except TimetableError as exc:  # pragma: no cover - depends on live network
                self.last_error = str(exc)
                continue
            except Exception as exc:  # pragma: no cover - depends on live network
                self.last_error = str(exc)
                continue

            if not sections:
                continue

            for section in sections:
                record = to_course_record_from_section(section, school, term, subject_codes, self.source_name)
                if not record.crn:
                    continue
                collected[record.crn] = record

        if collected:
            self.last_refresh = "OK"
            self.last_error = "None"
        return list(collected.values())

    async def get_open_seats(self, crn: str, term: str = "") -> int | None:
        if not self.available:
            return None

        term_year = self._term_year(term)
        try:
            section_any = await asyncio.to_thread(self.timetable.crn_lookup, crn, term_year, False)
            if section_any is None:
                return None

            section_open = await asyncio.to_thread(self.timetable.crn_lookup, crn, term_year, True)
            if section_open is None:
                return 0

            capacity = getattr(section_open, "capacity", None)
            if isinstance(capacity, int) and capacity > 0:
                return capacity
            return 1
        except TimetableError as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

    async def set_open_seats(self, crn: str, seats: int) -> bool:
        return False

    def _term_year(self, term: str) -> str | None:
        label = term.strip() or self.preferred_term_label.strip()
        if self.preferred_term_year.strip():
            return self.preferred_term_year.strip()
        if not label:
            return None

        parts = label.split()
        if len(parts) < 2:
            return None

        season = " ".join(parts[:-1]).lower()
        year = parts[-1]
        month = {
            "spring": "01",
            "summer i": "06",
            "summer 1": "06",
            "summer ii": "07",
            "summer 2": "07",
            "fall": "09",
        }.get(season)
        if month is None:
            return None
        return f"{year}{month}"

    def _subject_hint_from_code(self, course_code: str) -> str:
        letters = []
        for char in str(course_code):
            if char.isalpha():
                letters.append(char)
            elif letters:
                break
        return "".join(letters) or "CS"
