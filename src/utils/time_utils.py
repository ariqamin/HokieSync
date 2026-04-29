from __future__ import annotations

from datetime import datetime
from typing import Iterable

from src.models import WEEKDAY_NAMES, WEEKDAY_ORDER


TIME_FORMAT = "%H:%M"


def normalize_days(days: str) -> str:
    cleaned = "".join(ch for ch in days.upper() if ch in WEEKDAY_ORDER)
    ordered = [day for day in WEEKDAY_ORDER if day in cleaned]
    return "".join(ordered)


def parse_time(value: str) -> int:
    try:
        dt = datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError as exc:
        raise ValueError("Time must use 24-hour HH:MM format, for example 13:30.") from exc
    return dt.hour * 60 + dt.minute


def format_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def validate_time_range(start_time: str, end_time: str) -> None:
    start_minutes = parse_time(start_time)
    end_minutes = parse_time(end_time)
    if end_minutes <= start_minutes:
        raise ValueError("End time must be later than start time.")


def compress_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    sorted_ranges = sorted(ranges)
    if not sorted_ranges:
        return []

    merged: list[tuple[int, int]] = [sorted_ranges[0]]
    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def weekday_display(days: str) -> str:
    normalized = normalize_days(days)
    if not normalized:
        return "No meeting days"
    return ", ".join(WEEKDAY_NAMES[d] for d in normalized)
