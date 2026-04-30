from __future__ import annotations

import asyncio
import re

import requests
from bs4 import BeautifulSoup

from src.core.models import CourseRecord
from src.providers.helpers import (
    course_major_tags,
    course_matches_query,
    infer_requirement_tags,
    subject_codes_for_major,
    subject_from_course_query,
)
from src.utils.time_utils import normalize_days


TIMETABLE_URL = "https://apps.es.vt.edu/ssb/HZSKVTSC.P_ProcRequest"
REQUEST_TIMEOUT_SECONDS = 30


class VTCatalogProvider:
    def __init__(self, preferred_term_label: str = "", preferred_term_year: str = ""):
        self.preferred_term_label = preferred_term_label
        self.preferred_term_year = preferred_term_year
        self.available = True
        self.last_refresh = "Never"
        self.last_error = "None"
        self.source_name = "VT timetable"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "user-agent": "kit-bot/1.0",
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )

    async def refresh(self):
        try:
            await asyncio.to_thread(self._check_timetable)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return
        self.last_refresh = "OK"
        self.last_error = "None"

    async def get_course_by_crn(self, crn: str, term: str = "", school: str = "Virginia Tech") -> CourseRecord | None:
        term_year = self._term_year(term)
        data = self._base_request_data(term_year=term_year, crn=crn)
        try:
            html = await asyncio.to_thread(self._request_timetable, data)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

        records = self._records_from_html(html, school, term or self.preferred_term_label, [])
        if not records:
            return None
        return records[0]

    async def list_courses_for_profile(self, major: str, school: str, term: str) -> list[CourseRecord]:
        term_year = self._term_year(term)
        subject_codes = subject_codes_for_major(major)
        collected: dict[str, CourseRecord] = {}

        for subject_code in subject_codes:
            data = self._base_request_data(term_year=term_year, subject=subject_code)
            try:
                html = await asyncio.to_thread(self._request_timetable, data)
            except Exception as exc:  # pragma: no cover - depends on live network
                self.last_error = str(exc)
                continue

            for record in self._records_from_html(html, school, term, subject_codes):
                collected[record.crn] = record

        if collected:
            self.last_refresh = "OK"
            self.last_error = "None"
        return sorted(collected.values(), key=lambda item: (item.course_code, item.start_time, item.crn))

    async def search_courses(self, query: str, school: str = "Virginia Tech", term: str = "") -> list[CourseRecord]:
        subject_code = subject_from_course_query(query) or self._subject_from_text(query)
        if not subject_code:
            return []

        course_number = self._course_number_from_text(query)
        term_year = self._term_year(term)
        data = self._base_request_data(term_year=term_year, subject=subject_code, course_number=course_number)
        try:
            html = await asyncio.to_thread(self._request_timetable, data)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return []

        subject_hints = subject_codes_for_major(subject_code)
        records = self._records_from_html(html, school, term, subject_hints)
        return [record for record in records if course_matches_query(record, query)]

    async def get_open_seats(self, crn: str, term: str = "") -> int | None:
        term_year = self._term_year(term)
        all_data = self._base_request_data(term_year=term_year, crn=crn)
        open_data = self._base_request_data(term_year=term_year, crn=crn, open_only=True)
        try:
            all_html = await asyncio.to_thread(self._request_timetable, all_data)
            if not self._records_from_html(all_html, "Virginia Tech", term, []):
                return None
            open_html = await asyncio.to_thread(self._request_timetable, open_data)
            return 1 if self._records_from_html(open_html, "Virginia Tech", term, []) else 0
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

    async def set_open_seats(self, crn: str, seats: int) -> bool:
        return False

    def _base_request_data(
        self,
        *,
        term_year: str | None = None,
        subject: str = "",
        course_number: str = "",
        crn: str = "",
        open_only: bool = False,
    ) -> dict[str, str]:
        return {
            "CAMPUS": "0",
            "TERMYEAR": term_year or self._term_year("") or "",
            "CORE_CODE": "AR%",
            "subj_code": subject or "%",
            "SCHDTYPE": "%",
            "CRSE_NUMBER": course_number,
            "crn": crn,
            "open_only": "on" if open_only else "",
            "sess_code": "%",
        }

    def _request_timetable(self, data: dict[str, str]) -> str:
        response = self.session.post(TIMETABLE_URL, data=data, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        text = response.text
        if "THERE IS AN ERROR WITH YOUR REQUEST" in text:
            raise RuntimeError("VT timetable rejected the search parameters.")
        if "There was a problem with your request" in text and "NO SECTIONS FOUND FOR THIS INQUIRY" not in text:
            message = self._extract_error_message(text)
            raise RuntimeError(message or "VT timetable returned an error.")
        return text

    def _check_timetable(self) -> None:
        response = self.session.get(TIMETABLE_URL, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()

    def _records_from_html(self, html: str, school: str, term: str, major_subjects: list[str]) -> list[CourseRecord]:
        if "NO SECTIONS FOUND FOR THIS INQUIRY" in html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        records: list[CourseRecord] = []
        seen: set[str] = set()
        for row in soup.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td", recursive=False)]
            if len(cells) < 12 or not cells[0].strip().isdigit():
                continue

            crn = cells[0].strip()
            if crn in seen:
                continue
            seen.add(crn)
            course_code = cells[1].replace("-", "").replace(" ", "").upper()
            title = cells[2].strip()
            instructor = cells[7].strip()
            if not instructor or instructor.upper() == "N/A":
                instructor = "TBA"
            days = normalize_days(cells[8])
            start_time, end_time, location = self._meeting_fields(cells)
            capacity = self._optional_int(cells[6])

            records.append(
                CourseRecord(
                    crn=crn,
                    course_code=course_code,
                    title=title,
                    instructor=instructor,
                    days=days,
                    start_time=start_time,
                    end_time=end_time,
                    location=location,
                    school=school,
                    term=term or self.preferred_term_label,
                    major_tags=course_major_tags(course_code, major_subjects or [self._subject_hint_from_code(course_code)]),
                    requirement_tags=infer_requirement_tags(course_code, title),
                    open_seats=None,
                    source=self.source_name,
                )
            )
        return records

    def _extract_error_message(self, html: str) -> str:
        match = re.search(r"<b class=red_msg><li>(.+?)</b>", html, re.IGNORECASE | re.DOTALL)
        if match is None:
            return ""
        return BeautifulSoup(match.group(1), "html.parser").get_text(" ", strip=True)

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
            "summer": "06",
            "summer i": "06",
            "summer 1": "06",
            "summer ii": "07",
            "summer 2": "07",
            "fall": "09",
            "winter": "12",
        }.get(season)
        if month is None:
            return None
        return f"{year}{month}"

    def _normalize_time(self, value: str) -> str:
        match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AP]M)\s*$", value, re.IGNORECASE)
        if match is None:
            return ""
        hour = int(match.group(1))
        minute = int(match.group(2))
        meridiem = match.group(3).upper()
        if meridiem == "PM" and hour != 12:
            hour += 12
        if meridiem == "AM" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute:02d}"

    def _meeting_fields(self, cells: list[str]) -> tuple[str, str, str]:
        if len(cells) >= 13:
            return self._normalize_time(cells[9]), self._normalize_time(cells[10]), cells[11].strip()

        start_time, end_time = self._normalize_time_range(cells[9])
        return start_time, end_time, cells[10].strip()

    def _normalize_time_range(self, value: str) -> tuple[str, str]:
        parts = re.split(r"\s*-\s*", value.strip(), maxsplit=1)
        if len(parts) != 2:
            return "", ""
        return self._normalize_time(parts[0]), self._normalize_time(parts[1])

    def _optional_int(self, value: str) -> int | None:
        try:
            return int(str(value).strip())
        except ValueError:
            return None

    def _subject_from_text(self, value: str) -> str:
        match = re.match(r"\s*([A-Za-z]{2,4})\b", value)
        if match is None:
            return ""
        return match.group(1).upper()

    def _course_number_from_text(self, value: str) -> str:
        match = re.search(r"\b(\d{4})\b", value)
        if match is None:
            return ""
        return match.group(1)

    def _subject_hint_from_code(self, course_code: str) -> str:
        letters = []
        for char in str(course_code):
            if char.isalpha():
                letters.append(char)
            elif letters:
                break
        return "".join(letters) or "CS"
