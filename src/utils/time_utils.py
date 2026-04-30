from __future__ import annotations

import re
from datetime import datetime
from typing import Iterable

from src.core.models import WEEKDAY_NAMES, WEEKDAY_ORDER


TIME_FORMAT = "%H:%M"


def normalize_days(days: str) -> str:
    upper_days = days.upper()
    if "ARR" in upper_days or "TBA" in upper_days or "ONLINE" in upper_days:
        return ""
    cleaned = "".join(ch for ch in upper_days if ch in WEEKDAY_ORDER)
    ordered = [day for day in WEEKDAY_ORDER if day in cleaned]
    return "".join(ordered)


def parse_time(value: str) -> int:
    cleaned = value.strip().lower().replace(" ", "")
    normal_match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)", cleaned)
    if normal_match is not None:
        hour = int(normal_match.group(1))
        minute = int(normal_match.group(2) or "0")
        meridiem = normal_match.group(3)
        if hour < 1 or hour > 12 or minute > 59:
            raise ValueError("Time must look like 9:00 AM or 9am.")
        if meridiem == "pm" and hour != 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        return hour * 60 + minute

    try:
        dt = datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError as exc:
        raise ValueError("Time must look like 9:00 AM or 9am.") from exc
    return dt.hour * 60 + dt.minute


def format_minutes(total_minutes: int) -> str:
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours:02d}:{minutes:02d}"


def format_time(value: str) -> str:
    if not value:
        return ""
    return format_minutes_normal(parse_time(value))


def format_minutes_normal(total_minutes: int) -> str:
    hours_24 = total_minutes // 60
    minutes = total_minutes % 60
    meridiem = "AM" if hours_24 < 12 else "PM"
    hours_12 = hours_24 % 12 or 12
    return f"{hours_12}:{minutes:02d} {meridiem}"


def format_time_range(start_time: str, end_time: str) -> str:
    if not start_time or not end_time:
        return "TBA"
    return f"{format_time(start_time)}-{format_time(end_time)}"


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
