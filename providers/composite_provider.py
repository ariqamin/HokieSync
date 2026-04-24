from __future__ import annotations

from src.models import CourseRecord


class CompositeProvider:
    def __init__(self, catalog_provider=None, rmp_provider=None, grade_provider=None, mock_provider=None):
        self.catalog_provider = catalog_provider
        self.rmp_provider = rmp_provider
        self.grade_provider = grade_provider
        self.mock_provider = mock_provider
        self.last_refresh = "Never"
        self.last_error = "None"

    async def refresh(self):
        errors: list[str] = []

        for current_provider in [self.catalog_provider, self.rmp_provider, self.grade_provider, self.mock_provider]:
            if current_provider is None:
                continue
            try:
                await current_provider.refresh()
            except Exception as exc:  # pragma: no cover - depends on live network
                errors.append(str(exc))

        self.last_refresh = "OK"
        self.last_error = " | ".join(errors) if errors else "None"

    async def get_course_by_crn(self, crn: str, school: str = "Virginia Tech", term: str = "") -> CourseRecord | None:
        course: CourseRecord | None = None

        if self.catalog_provider is not None:
            try:
                course = await self.catalog_provider.get_course_by_crn(crn, term=term, school=school)
            except TypeError:
                course = await self.catalog_provider.get_course_by_crn(crn)

        if course is None and self.mock_provider is not None:
            course = await self.mock_provider.get_course_by_crn(crn)

        if course is None:
            return None

        await self._enrich_course(course, school)
        return course

    async def list_courses_for_profile(self, major: str, school: str, term: str) -> list[CourseRecord]:
        courses: list[CourseRecord] = []

        if self.catalog_provider is not None:
            courses = await self.catalog_provider.list_courses_for_profile(major, school, term)

        if not courses and self.mock_provider is not None:
            courses = await self.mock_provider.list_courses_for_profile(major, school, term)

        for course in courses:
            await self._enrich_course(course, school)
        return courses

    async def get_rmp_rating(self, instructor: str, school: str) -> float | None:
        if self.rmp_provider is not None:
            rating = await self.rmp_provider.get_rating(instructor)
            if rating is not None:
                return rating.avg_rating

        if self.mock_provider is not None:
            return await self.mock_provider.get_rmp_rating(instructor, school)
        return None

    async def get_avg_gpa(self, course_code: str, instructor: str = "") -> float | None:
        if self.grade_provider is not None:
            stat = await self.grade_provider.get_grade_stat(course_code, instructor)
            if stat is not None:
                return stat.gpa

        if self.mock_provider is not None:
            return await self.mock_provider.get_avg_gpa(course_code, instructor)
        return None

    async def get_open_seats(self, crn: str, term: str = "") -> int | None:
        if self.catalog_provider is not None:
            try:
                seats = await self.catalog_provider.get_open_seats(crn, term=term)
            except TypeError:
                seats = await self.catalog_provider.get_open_seats(crn)
            if seats is not None:
                return seats

        if self.mock_provider is not None:
            return await self.mock_provider.get_open_seats(crn)
        return None

    async def set_open_seats(self, crn: str, seats: int) -> bool:
        if self.mock_provider is None:
            return False
        return await self.mock_provider.set_open_seats(crn, seats)

    async def _enrich_course(self, course: CourseRecord, school: str):
        if course.rmp_rating is None:
            course.rmp_rating = await self.get_rmp_rating(course.instructor, school)

        if course.avg_gpa is None:
            course.avg_gpa = await self.get_avg_gpa(course.course_code, course.instructor)
