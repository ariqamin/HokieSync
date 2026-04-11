from __future__ import annotations
from dataclasses import dataclass




VALID_PRIVACY = {"public", "friends", "private"}
VALID_RECOMMENDATION_MODES = {"balanced", "easy", "professor"}
WEEKDAY_ORDER = ["M", "T", "W", "R", "F", "S", "U"]
WEEKDAY_NAMES = {
    "M": "Monday",
    "T": "Tuesday",
    "W": "Wednesday",
    "R": "Thursday",
    "F": "Friday",
    "S": "Saturday",
    "U": "Sunday",
}


@dataclass(slots = True)
class Profile:
    user_id: int
    major: str
    school: str
    term: str
    privacy: str
    requirements_text: str


@dataclass(slots = True)
class ClassEntry:
    user_id: int
    crn: str
    course_code: str
    course_title: str
    instructor: str
    days: str
    start_time: str
    end_time: str
    location: str
    source: str


@dataclass(slots = True)
class Recommendation:
    course_code: str
    title: str
    instructor: str
    crn: str
    days: str
    start_time: str
    end_time: str
    score: float
    label: str
    explanation: str
