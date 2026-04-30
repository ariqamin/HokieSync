from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path
from typing import Any

import requests

from src.db import Database
from src.models import GradeStat


COURSE_CODE_ALIASES = [
    "course code",
    "course_code",
    "course",
    "subject course",
]
SUBJECT_ALIASES = ["subject", "subject code", "subject_code"]
COURSE_NUMBER_ALIASES = ["course no.", "course no", "course_no", "course number", "course_number"]
TITLE_ALIASES = ["course title", "title", "course_title"]
INSTRUCTOR_ALIASES = ["instructor", "faculty", "professor"]
ACADEMIC_YEAR_ALIASES = ["academic year", "academic_year", "year"]
TERM_ALIASES = ["term", "semester"]
GPA_ALIASES = ["gpa", "avg gpa", "average gpa"]
A_ALIASES = ["a(%)", "a %", "a_pct", "a", "grade a", "grade_a"]
A_MINUS_ALIASES = ["a-(%)", "a- %", "a_minus_pct", "a-", "grade a negative", "grade_a_negative"]
B_PLUS_ALIASES = ["b+(%)", "b+ %", "b_plus_pct", "b+", "grade b positive", "grade_b_positive"]
B_ALIASES = ["b(%)", "b %", "b_pct", "b", "grade b", "grade_b"]


class GradeProvider:
    def __init__(
        self,
        db: Database,
        csv_path: Path | None = None,
        json_path: Path | None = None,
        request_url: str = "",
        headers: dict[str, Any] | None = None,
        cookies: dict[str, Any] | None = None,
    ):
        self.db = db
        self.csv_path = csv_path
        self.json_path = json_path
        self.request_url = request_url
        self.headers = headers or {}
        self.cookies = cookies or {}
        self.last_refresh = "Never"
        self.last_error = "None"

    async def refresh(self):
        rows: list[dict[str, Any]] = []
        configured = False

        if self.csv_path is not None and self.csv_path.exists():
            configured = True
            rows = await asyncio.to_thread(self._load_csv_rows, self.csv_path)
        elif self.json_path is not None and self.json_path.exists():
            configured = True
            rows = await asyncio.to_thread(self._load_json_rows, self.json_path)
        elif self.request_url:
            configured = True
            rows = await asyncio.to_thread(self._fetch_request_rows)

        if not configured:
            self.last_refresh = "Not configured"
            self.last_error = "None"
            return

        if not rows:
            self.last_error = "Grade source was configured, but no rows were returned"
            return

        stats = [self._row_to_grade_stat(row) for row in rows]
        stats = [item for item in stats if item is not None]
        if not stats:
            self.last_error = "Grade source was loaded, but no usable rows were found"
            return

        self.db.replace_grade_cache(stats)
        self.last_refresh = f"Loaded {len(stats)} grade rows"
        self.last_error = "None"

    async def get_grade_stat(self, course_code: str, instructor: str = "") -> GradeStat | None:
        return self.db.get_grade_summary(course_code, instructor)

    def _load_csv_rows(self, path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [dict(row) for row in reader]

    def _load_json_rows(self, path: Path) -> list[dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return self._extract_rows(payload)

    def _fetch_request_rows(self) -> list[dict[str, Any]]:
        response = requests.get(
            self.request_url,
            headers=self.headers,
            cookies=self.cookies,
            timeout=30,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError:
            self.last_error = "Grade request did not return JSON"
            return []
        return self._extract_rows(payload)

    def _extract_rows(self, payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if isinstance(payload, dict):
            for key in ("rows", "data", "results", "items", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
                if isinstance(value, dict):
                    nested = self._extract_rows(value)
                    if nested:
                        return nested

            for value in payload.values():
                nested = self._extract_rows(value)
                if nested:
                    return nested
        return []

    def _row_to_grade_stat(self, row: dict[str, Any]) -> GradeStat | None:
        normalized = {self._normalize_key(key): value for key, value in row.items()}

        course_code = self._first_value(normalized, COURSE_CODE_ALIASES)
        if not course_code:
            subject = self._first_value(normalized, SUBJECT_ALIASES)
            course_number = self._first_value(normalized, COURSE_NUMBER_ALIASES)
            if subject and course_number:
                course_code = f"{subject}{course_number}".replace(" ", "")

        gpa = self._float_value(normalized, GPA_ALIASES)
        if not course_code or gpa is None:
            return None

        title = self._first_value(normalized, TITLE_ALIASES)
        instructor = self._first_value(normalized, INSTRUCTOR_ALIASES)
        academic_year = self._first_value(normalized, ACADEMIC_YEAR_ALIASES)
        term = self._first_value(normalized, TERM_ALIASES)

        return GradeStat(
            course_code=course_code.replace(" ", "").upper(),
            title=title,
            instructor=instructor,
            academic_year=academic_year,
            term=term,
            gpa=gpa,
            a_pct=self._float_value(normalized, A_ALIASES),
            a_minus_pct=self._float_value(normalized, A_MINUS_ALIASES),
            b_plus_pct=self._float_value(normalized, B_PLUS_ALIASES),
            b_pct=self._float_value(normalized, B_ALIASES),
            raw_json=json.dumps(row),
        )

    def _normalize_key(self, key: str) -> str:
        return " ".join(str(key).replace("_", " ").lower().split())

    def _first_value(self, row: dict[str, Any], aliases: list[str]) -> str:
        for alias in aliases:
            value = row.get(alias)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _float_value(self, row: dict[str, Any], aliases: list[str]) -> float | None:
        value = self._first_value(row, aliases)
        if not value:
            return None
        value = value.replace("%", "").strip()
        try:
            return float(value)
        except ValueError:
            return None
            