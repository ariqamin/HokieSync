from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.core.models import CourseRecord
from src.providers.helpers import course_major_tags, course_matches_query, infer_requirement_tags


class MockDataProvider:
    def __init__(self, catalog_path: Path):
        self.catalog_path = catalog_path
        self.courses: dict[str, CourseRecord] = {}
        self.last_refresh = "Never"
        self.last_error = "None"
        self._load()

    def _load(self):
        raw_items: list[dict[str, Any]] = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        self.courses = {}
        for item in raw_items:
            course = CourseRecord(
                crn=str(item["crn"]),
                course_code=item["course_code"],
                title=item["title"],
                instructor=item["instructor"],
                days=item["days"],
                start_time=item["start_time"],
                end_time=item["end_time"],
                location=item.get("location", ""),
                school=item.get("school", "Virginia Tech"),
                term=item.get("term", ""),
                major_tags=item.get("major_tags") or course_major_tags(item["course_code"], [item["course_code"][:2]]),
                requirement_tags=item.get("requirement_tags") or infer_requirement_tags(item["course_code"], item["title"]),
                open_seats=item.get("open_seats"),
                rmp_rating=item.get("rmp_rating"),
                avg_gpa=item.get("avg_gpa"),
                source="mock",
            )
            self.courses[course.crn] = course

    async def refresh(self):
        self._load()
        self.last_refresh = "OK"
        self.last_error = "None"

    async def get_course_by_crn(self, crn: str) -> CourseRecord | None:
        return self.courses.get(crn)

    async def list_courses_for_profile(self, major: str, school: str, term: str) -> list[CourseRecord]:
        major_upper = major.upper()
        result: list[CourseRecord] = []
        for course in self.courses.values():
            if school and course.school.lower() != school.lower():
                continue
            if term and course.term.lower() != term.lower():
                continue
            if any(tag.upper() in major_upper for tag in course.major_tags):
                result.append(course)
        return result

    async def search_courses(self, query: str, school: str = "Virginia Tech", term: str = "") -> list[CourseRecord]:
        result: list[CourseRecord] = []
        for course in self.courses.values():
            if school and course.school.lower() != school.lower():
                continue
            if term and course.term.lower() != term.lower():
                continue
            if course_matches_query(course, query):
                result.append(course)
        return sorted(result, key=lambda item: (item.course_code, item.start_time, item.crn))

    async def get_rmp_rating(self, instructor: str, school: str) -> float | None:
        for course in self.courses.values():
            if course.instructor.lower() == instructor.lower():
                return course.rmp_rating
        return None

    async def get_avg_gpa(self, course_code: str, instructor: str = "") -> float | None:
        for course in self.courses.values():
            if course.course_code.replace(" ", "").lower() == course_code.replace(" ", "").lower():
                return course.avg_gpa
        return None

    async def get_open_seats(self, crn: str) -> int | None:
        course = self.courses.get(crn)
        if course is None:
            return None
        return course.open_seats

    async def set_open_seats(self, crn: str, seats: int) -> bool:
        course = self.courses.get(crn)
        if course is None:
            return False
        course.open_seats = max(0, seats)
        return True
