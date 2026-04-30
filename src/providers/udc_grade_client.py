from __future__ import annotations

import base64
import json
from typing import Any

import brotli
import requests


PAGE_URL = "https://udc.vt.edu/irdata/data/courses/grades"
API_URL = "https://udc.vt.edu/api/irdata/data/courses/grades"


class UDCGradeClient:
    def __init__(self):
        self.session = requests.Session()
        self.csrf_token = ""

    def list_courses(self, subject: str = "", course_number: str = "") -> list[list[str]]:
        response = self.session.get(f"{API_URL}/course_no", timeout=60)
        response.raise_for_status()
        payload = decode_payload(response.json())
        courses = [item for item in payload if isinstance(item, list) and len(item) >= 3]

        subject = subject.strip().upper()
        course_number = course_number.strip()
        if subject:
            courses = [course for course in courses if str(course[0]).upper() == subject]
        if course_number:
            courses = [course for course in courses if str(course[1]) == course_number]
        return courses

    def fetch_course_rows(
        self,
        subject_code: str,
        course_number: str,
        title: str = "",
        page_size: int = 500,
    ) -> list[dict[str, Any]]:
        self.ensure_csrf_token()
        if not title:
            matches = self.list_courses(subject_code, course_number)
            if not matches:
                return []
            title = str(matches[0][2])

        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            payload = {
                "c": build_course_condition(subject_code, course_number, title),
                "l": page_size,
                "offset": offset,
                "o": "academic_year DESC",
            }
            response = self.session.post(
                API_URL,
                json=payload,
                headers={
                    "Referer": PAGE_URL,
                    "Origin": "https://udc.vt.edu",
                    "X-CSRFToken": self.csrf_token,
                },
                timeout=60,
            )
            response.raise_for_status()
            page_rows = decode_rows(response.json())
            rows.extend(normalize_row(row) for row in page_rows)
            if len(page_rows) < page_size:
                return rows
            offset += page_size

    def ensure_csrf_token(self):
        if self.csrf_token:
            return
        response = self.session.get(PAGE_URL, timeout=30)
        response.raise_for_status()
        token = self.session.cookies.get("csrftoken")
        if not token:
            raise RuntimeError("UDC did not provide a csrftoken cookie")
        self.csrf_token = token


def build_course_condition(subject_code: str, course_number: str, title: str) -> str:
    return "AND".join(
        [
            f"(\"subject_code\"='{escape_sql_value(subject_code)}')",
            f"(\"course_number\"='{escape_sql_value(course_number)}')",
            f"(\"course_title\"='{escape_sql_value(title)}')",
        ]
    )


def escape_sql_value(value: str) -> str:
    return str(value).replace("'", "''")


def decode_rows(payload: Any) -> list[dict[str, Any]]:
    payload = decode_payload(payload)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("schema"), list) and isinstance(payload.get("data"), list):
        schema = payload["schema"]
        return [dict(zip(schema, row)) for row in payload["data"]]
    return []


def decode_payload(payload: Any) -> Any:
    if not isinstance(payload, str):
        return payload
    compressed = base64.b64decode(payload)
    decompressed = brotli.decompress(compressed).decode("utf-8")
    return json.loads(decompressed)


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["course_code"] = f"{row.get('subject_code', '')}{row.get('course_number', '')}".replace(" ", "").upper()
    return normalized
