from __future__ import annotations

from dataclasses import dataclass, field


VALID_PRIVACY = {"public", "friends", "private"}
VALID_RECOMMENDATION_MODES = {"balanced", "easy", "professor"}
VALID_SCHEDULE_KEYS = {"current", "next"}
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


@dataclass(slots=True)
class Profile:
    user_id: int
    major: str
    school: str
    term: str
    privacy: str
    requirements_text: str
    active_schedule: str = "current"
    current_term: str = ""
    next_term: str = ""


@dataclass(slots=True)
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
    schedule_key: str = "current"


@dataclass(slots=True)
class CourseRecord:
    crn: str
    course_code: str
    title: str
    instructor: str
    days: str
    start_time: str
    end_time: str
    location: str
    school: str = "Virginia Tech"
    term: str = ""
    major_tags: list[str] = field(default_factory=list)
    requirement_tags: list[str] = field(default_factory=list)
    open_seats: int | None = None
    rmp_rating: float | None = None
    avg_gpa: float | None = None
    source: str = ""


@dataclass(slots=True)
class ProfessorRating:
    professor_name: str
    school_name: str
    school_id: str
    avg_rating: float
    avg_difficulty: float
    num_ratings: int
    would_take_again: float | None = None
    raw_json: str = ""


@dataclass(slots=True)
class GradeStat:
    course_code: str
    title: str
    instructor: str
    academic_year: str
    term: str
    gpa: float
    a_pct: float | None = None
    a_minus_pct: float | None = None
    b_plus_pct: float | None = None
    b_pct: float | None = None
    raw_json: str = ""


@dataclass(slots=True)
class Recommendation:
    course_code: str
    title: str
    instructor: str
    crn: str
    days: str
    start_time: str
    end_time: str
    rmp_rating: float
    avg_gpa: float
    score: float
    label: str
    explanation: str
    fit_notes: str = ""


@dataclass(slots=True)
class SchedulePreferences:
    user_id: int
    raw_text: str
    preferred_start: str
    preferred_end: str
    avoid_early: bool
    avoid_late: bool
    avoid_friday: bool
    avoid_days: str
    preferred_days: str
    compact_days: bool
    max_days: int
    breaks_preference: str
    min_avg_gpa: float
    min_rmp_rating: float
    hard_time_window: bool
    target_courses: int
    preferred_mode: str
    notes: str


@dataclass(slots=True)
class SchedulePlan:
    score: float
    label: str
    summary: str
    avg_gpa: float = 0.0
    avg_rmp_rating: float = 0.0
    constraint_notes: str = ""
    courses: list[Recommendation] = field(default_factory=list)
