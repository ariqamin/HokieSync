from __future__ import annotations

from src.core.db import Database


class WatchService:
    def __init__(self, db: Database, provider) -> None:
        self.db = db
        self.provider = provider

    async def add_watch(self, user_id: int, course_or_crn: str, term: str = "", schedule_key: str = "current") -> tuple[bool, str]:
        crns = await self._resolve_crns(course_or_crn, term)
        if not crns:
            return False, "No matching CRN or course was found in the catalog source."

        watched: list[str] = []
        for crn in crns:
            seats = await self.provider.get_open_seats(crn, term=term)
            if seats is None:
                continue
            self.db.add_watch(user_id, crn, seats, schedule_key=schedule_key)
            watched.append(f"{crn} ({seats} open)")

        if not watched:
            return False, "No matching section had seat data available."
        return True, f"Now watching {len(watched)} section(s): {', '.join(watched[:8])}."

    async def _resolve_crns(self, course_or_crn: str, term: str = "") -> list[str]:
        query = course_or_crn.strip()
        if query.isdigit():
            return [query]

        matches = await self.provider.search_courses(query, term=term)
        return sorted({course.crn for course in matches if course.crn})

    async def remove_watch(self, user_id: int, course_or_crn: str, term: str = "", schedule_key: str = "current") -> tuple[bool, str]:
        crns = await self._resolve_crns(course_or_crn, term)
        if not crns and course_or_crn.strip().isdigit():
            crns = [course_or_crn.strip()]

        removed = [crn for crn in crns if self.db.remove_watch(user_id, crn, schedule_key=schedule_key)]
        if not removed:
            return False, f"{course_or_crn} was not in your {schedule_key} watchlist."
        return True, f"Stopped watching {len(removed)} section(s): {', '.join(removed[:8])}."

    async def add_watch_old(self, user_id: int, crn: str) -> tuple[bool, str]:
        seats = await self.provider.get_open_seats(crn)
        if seats is None:
            return False, "CRN not found in the catalog source."
        self.db.add_watch(user_id, crn, seats)
        return True, f"Now watching CRN {crn}. Current open seats: {seats}."
