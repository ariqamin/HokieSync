from __future__ import annotations

from collections import defaultdict

from src.core.db import Database
from src.core.models import WEEKDAY_ORDER
from src.utils.time_utils import compress_ranges, normalize_days, parse_time


class FreeTimeService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def compute(self, user_ids: list[int], start_time: str, end_time: str, weekdays_only: bool) -> dict[str, list[tuple[int, int]]]:
        bounds_start = parse_time(start_time)
        bounds_end = parse_time(end_time)
        if bounds_end <= bounds_start:
            raise ValueError("End time must be later than start time.")

        relevant_days = ["M", "T", "W", "R", "F"] if weekdays_only else WEEKDAY_ORDER
        busy: dict[int, dict[str, list[tuple[int, int]]]] = {}

        for user_id in user_ids:
            per_day: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for entry in self.db.list_classes(user_id):
                if not entry.days or not entry.start_time or not entry.end_time:
                    continue
                try:
                    class_start = parse_time(entry.start_time)
                    class_end = parse_time(entry.end_time)
                except ValueError:
                    continue
                clipped_start = max(bounds_start, class_start)
                clipped_end = min(bounds_end, class_end)
                if clipped_end <= clipped_start:
                    continue
                for day in normalize_days(entry.days):
                    if day in relevant_days:
                        per_day[day].append((clipped_start, clipped_end))
            busy[user_id] = {day: compress_ranges(ranges) for day, ranges in per_day.items()}

        free_windows: dict[str, list[tuple[int, int]]] = {}
        for day in relevant_days:
            occupied = []
            for user_id in user_ids:
                occupied.extend(busy[user_id].get(day, []))
            merged_occupied = compress_ranges(occupied)

            current = bounds_start
            free_ranges: list[tuple[int, int]] = []
            for start, end in merged_occupied:
                if current < start:
                    free_ranges.append((current, start))
                current = max(current, end)
            if current < bounds_end:
                free_ranges.append((current, bounds_end))
            free_windows[day] = free_ranges
        return free_windows
