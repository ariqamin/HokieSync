from __future__ import annotations

from src.db import Database
from src.models import ClassEntry
from src.utils.time_utils import normalize_days, validate_time_range


class ScheduleService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add_or_replace_class(self, entry: ClassEntry) -> None:
        entry.days = normalize_days(entry.days)
        if entry.start_time and entry.end_time:
            validate_time_range(entry.start_time, entry.end_time)
        self.db.add_class(entry)

    def edit_class(
        self,
        user_id: int,
        crn: str,
        days: str,
        start_time: str,
        end_time: str,
        location: str,
    ) -> ClassEntry | None:
        current = self.db.get_class(user_id, crn)
        if current is None:
            return None

        updated = ClassEntry(
            user_id=user_id,
            crn=current.crn,
            course_code=current.course_code,
            course_title=current.course_title,
            instructor=current.instructor,
            days=normalize_days(days),
            start_time=start_time,
            end_time=end_time,
            location=location,
            source="manual-edit",
        )
        validate_time_range(updated.start_time, updated.end_time)
        self.db.add_class(updated)
        return updated
