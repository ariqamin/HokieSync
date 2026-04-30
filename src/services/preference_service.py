from __future__ import annotations

import re

from src.core.models import SchedulePreferences, VALID_RECOMMENDATION_MODES, WEEKDAY_NAMES, WEEKDAY_ORDER
from src.utils.time_utils import format_time, normalize_days


DAY_ALIASES = {
    "monday": "M",
    "mondays": "M",
    "mon": "M",
    "tuesday": "T",
    "tuesdays": "T",
    "tue": "T",
    "tues": "T",
    "wednesday": "W",
    "wednesdays": "W",
    "wed": "W",
    "thursday": "R",
    "thursdays": "R",
    "thu": "R",
    "thur": "R",
    "thurs": "R",
    "friday": "F",
    "fridays": "F",
    "fri": "F",
    "saturday": "S",
    "saturdays": "S",
    "sat": "S",
    "sunday": "U",
    "sundays": "U",
    "sun": "U",
}

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}


class PreferenceService:
    def parse_description(self, user_id: int, text: str) -> SchedulePreferences:
        raw_text = text.strip()
        lowered = raw_text.lower()

        window = self._extract_time_window(lowered)
        preferred_start = window[0] or self._extract_start_time(lowered)
        preferred_end = window[1] or self._extract_end_time(lowered)
        hard_time_window = bool(window[0] and window[1]) and self._has_any(
            lowered,
            ["all classes", "only classes", "must be", "have to be", "from", "between"],
        )

        avoid_days = self._extract_avoid_days(lowered)
        preferred_days = self._extract_preferred_days(lowered)
        avoid_friday = "F" in avoid_days or self._has_any(
            lowered,
            ["no friday", "avoid friday", "free friday", "fridays off", "friday off"],
        )
        if avoid_friday and "F" not in avoid_days:
            avoid_days = normalize_days(f"{avoid_days}F")

        avoid_early = self._has_any(
            lowered,
            [
                "no early",
                "avoid early",
                "not early",
                "sleep in",
                "late start",
                "nothing before 10",
                "nothing before 11",
            ],
        )
        avoid_late = self._has_any(
            lowered,
            [
                "avoid late",
                "no late",
                "done by",
                "finish by",
                "nothing after",
                "leave early",
            ],
        )
        compact_days = self._has_any(
            lowered,
            [
                "compact",
                "back to back",
                "few days on campus",
                "fewer days on campus",
                "stack classes",
                "group classes together",
            ],
        )

        max_days = self._extract_max_days(lowered)
        breaks_preference = self._extract_breaks_preference(lowered)
        min_avg_gpa = self._extract_min_avg_gpa(lowered)
        min_rmp_rating = self._extract_min_rmp_rating(lowered)
        target_courses = self._extract_target_courses(lowered)
        preferred_mode = self._extract_mode(lowered)

        notes = self._build_notes(
            preferred_start=preferred_start,
            preferred_end=preferred_end,
            hard_time_window=hard_time_window,
            avoid_days=avoid_days,
            preferred_days=preferred_days,
            compact_days=compact_days,
            max_days=max_days,
            breaks_preference=breaks_preference,
            min_avg_gpa=min_avg_gpa,
            min_rmp_rating=min_rmp_rating,
            target_courses=target_courses,
            preferred_mode=preferred_mode,
        )

        return SchedulePreferences(
            user_id=user_id,
            raw_text=raw_text,
            preferred_start=preferred_start,
            preferred_end=preferred_end,
            avoid_early=avoid_early,
            avoid_late=avoid_late,
            avoid_friday=avoid_friday,
            avoid_days=avoid_days,
            preferred_days=preferred_days,
            compact_days=compact_days,
            max_days=max_days,
            breaks_preference=breaks_preference,
            min_avg_gpa=min_avg_gpa,
            min_rmp_rating=min_rmp_rating,
            hard_time_window=hard_time_window,
            target_courses=target_courses,
            preferred_mode=preferred_mode,
            notes="; ".join(notes),
        )

    def _has_any(self, text: str, phrases: list[str]) -> bool:
        return any(phrase in text for phrase in phrases)

    def _extract_time_window(self, text: str) -> tuple[str, str]:
        patterns = [
            r"(?:from|between)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:to|and|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\s*(?:to|-)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            start = self._to_24_hour(match.group(1), match.group(2), match.group(3), role="start")
            end = self._to_24_hour(match.group(4), match.group(5), match.group(6), role="end")
            return start, end
        return "", ""

    def _extract_start_time(self, text: str) -> str:
        match = re.search(r"(?:after|start after|nothing before|no classes before)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if match is None:
            return ""
        return self._to_24_hour(match.group(1), match.group(2), match.group(3), role="start")

    def _extract_end_time(self, text: str) -> str:
        match = re.search(r"(?:done by|finish by|end by|nothing after|no classes after)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if match is None:
            return ""
        return self._to_24_hour(match.group(1), match.group(2), match.group(3), role="end")

    def _extract_avoid_days(self, text: str) -> str:
        days: list[str] = []
        for phrase in re.findall(r"(?:no|avoid|skip|free|off)\s+([a-z,\s/]+)", text):
            for day in self._days_from_text(phrase):
                if day not in days:
                    days.append(day)
        return normalize_days("".join(days))

    def _extract_preferred_days(self, text: str) -> str:
        match = re.search(r"(?:prefer|preferred|only|on)\s+([a-z,\s/]+?)(?:\s+(?:classes|schedule|if|with|and all|$)|$)", text)
        if match is None:
            return ""
        return normalize_days("".join(self._days_from_text(match.group(1))))

    def _days_from_text(self, text: str) -> list[str]:
        found: list[str] = []
        compact = re.sub(r"[^a-z]", "", text.lower())
        if "mwf" in compact:
            found.extend(["M", "W", "F"])
        if "tr" in compact or "tth" in compact:
            found.extend(["T", "R"])

        words = re.findall(r"[a-z]+", text.lower())
        for word in words:
            day = DAY_ALIASES.get(word)
            if day and day not in found:
                found.append(day)
        return found

    def _extract_max_days(self, text: str) -> int:
        patterns = [
            r"(?:no more than|max|maximum|at most)\s+(\d|one|two|three|four|five|six|seven)\s+days?",
            r"(\d|one|two|three|four|five|six|seven)\s+days?\s+(?:on campus|a week|max|maximum)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return min(max(self._number_value(match.group(1)), 1), 7)
        if "few days on campus" in text or "fewer days on campus" in text:
            return 3
        return 5

    def _extract_breaks_preference(self, text: str) -> str:
        if "long break" in text or "long breaks" in text:
            return "long"
        if "short break" in text or "short breaks" in text or "back to back" in text:
            return "short"
        return ""

    def _extract_min_avg_gpa(self, text: str) -> float:
        patterns = [
            r"(?:average\s+)?gpa\s*(?:is|should be|must be)?\s*(?:above|over|at least|>=|greater than)\s*(?:a\s*)?([0-4](?:\.\d+)?)",
            r"(?:above|over|at least|>=|greater than)\s*(?:a\s*)?([0-4](?:\.\d+)?)\s*(?:average\s+)?gpa",
            r"easy\s+schedule.*?([3-4]\.\d+)",
        ]
        return self._first_float(text, patterns, max_value=4.0)

    def _extract_min_rmp_rating(self, text: str) -> float:
        patterns = [
            r"(?:rmp|professor rating|professor score|rating)\s*(?:is|should be|must be)?\s*(?:above|over|at least|>=|greater than)\s*([0-5](?:\.\d+)?)",
            r"(?:above|over|at least|>=|greater than)\s*([0-5](?:\.\d+)?)\s*(?:rmp|professor rating|professor score|rating)",
        ]
        return self._first_float(text, patterns, max_value=5.0)

    def _extract_target_courses(self, text: str) -> int:
        match = re.search(r"(\d|one|two|three|four|five|six|seven)\s+(?:classes|courses)", text)
        if match is None:
            return 0
        return min(max(self._number_value(match.group(1)), 1), 7)

    def _extract_mode(self, text: str) -> str:
        if "easy" in text or "highest gpa" in text or "good grades" in text:
            return "easy"
        if "professor" in text or "best teacher" in text or "best instructor" in text:
            return "professor"
        if "balanced" in text:
            return "balanced"
        return ""

    def _first_float(self, text: str, patterns: list[str], max_value: float) -> float:
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            try:
                value = float(match.group(1))
            except ValueError:
                continue
            if 0 <= value <= max_value:
                return value
        return 0.0

    def _number_value(self, value: str) -> int:
        value = value.lower()
        if value.isdigit():
            return int(value)
        return NUMBER_WORDS.get(value, 0)

    def _to_24_hour(self, hour_text: str, minute_text: str | None, meridiem: str | None, role: str) -> str:
        hour = int(hour_text)
        minute = int(minute_text or "0")

        if meridiem is not None:
            meridiem = meridiem.lower()
            if meridiem == "pm" and hour != 12:
                hour += 12
            if meridiem == "am" and hour == 12:
                hour = 0
        elif role == "end" and 1 <= hour <= 7:
            hour += 12
        elif role == "start" and 1 <= hour <= 6:
            hour += 12

        return f"{hour:02d}:{minute:02d}"

    def _build_notes(
        self,
        *,
        preferred_start: str,
        preferred_end: str,
        hard_time_window: bool,
        avoid_days: str,
        preferred_days: str,
        compact_days: bool,
        max_days: int,
        breaks_preference: str,
        min_avg_gpa: float,
        min_rmp_rating: float,
        target_courses: int,
        preferred_mode: str,
    ) -> list[str]:
        notes: list[str] = []
        if preferred_start and preferred_end:
            label = "requires" if hard_time_window else "prefers"
            notes.append(f"{label} classes between {format_time(preferred_start)} and {format_time(preferred_end)}")
        elif preferred_start:
            notes.append(f"prefers classes starting after {format_time(preferred_start)}")
        elif preferred_end:
            notes.append(f"prefers to be done by {format_time(preferred_end)}")
        if avoid_days:
            names = ", ".join(WEEKDAY_NAMES[day] for day in avoid_days if day in WEEKDAY_NAMES)
            notes.append(f"avoids {names}")
        if preferred_days:
            names = ", ".join(WEEKDAY_NAMES[day] for day in preferred_days if day in WEEKDAY_NAMES)
            notes.append(f"prefers {names}")
        if compact_days:
            notes.append("prefers a compact schedule")
        if max_days < 5:
            notes.append(f"wants classes on no more than {max_days} day(s)")
        if breaks_preference:
            notes.append(f"prefers {breaks_preference} breaks")
        if min_avg_gpa:
            notes.append(f"wants schedule average GPA at least {min_avg_gpa:.2f}")
        if min_rmp_rating:
            notes.append(f"wants professor ratings at least {min_rmp_rating:.1f}/5")
        if target_courses:
            notes.append(f"wants {target_courses} course(s)")
        if preferred_mode in VALID_RECOMMENDATION_MODES:
            notes.append(f"implied recommendation mode: {preferred_mode}")
        return notes
