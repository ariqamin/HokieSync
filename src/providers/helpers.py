from __future__ import annotations

import re

from src.core.models import CourseRecord


def normalize_course_code(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def subject_from_course_query(value: str) -> str:
    match = re.match(r"\s*([A-Za-z]{2,4})\s*[- ]?\s*\d{4}\b", value)
    if match is None:
        return ""
    return match.group(1).upper()


def course_matches_query(course: CourseRecord, query: str) -> bool:
    normalized_query = normalize_course_code(query)
    normalized_course = normalize_course_code(course.course_code)
    if normalized_query and normalized_query == normalized_course:
        return True

    lowered_query = " ".join(query.lower().split())
    if not lowered_query:
        return False
    title = " ".join(course.title.lower().split())
    code_with_space = re.sub(r"([A-Za-z]+)(\d+)", r"\1 \2", course.course_code).lower()
    return lowered_query in title or lowered_query == code_with_space


MAJOR_SUBJECT_MAP = {
    "CS": ["CS", "CMDA", "CPE", "MATH", "STAT"],
    "CPE": ["CPE", "CS", "ECE", "MATH"],
    "ECE": ["ECE", "MATH", "PHYS", "CS"],
    "CMDA": ["CMDA", "MATH", "STAT", "CS"],
    "MATH": ["MATH", "STAT", "CS"],
    "ENGR": ["ENGE", "MATH", "PHYS", "CS"],
}


def subject_codes_for_major(major: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z]+", major.upper())
    subjects: list[str] = []

    for token in tokens:
        if token in MAJOR_SUBJECT_MAP:
            for subject in MAJOR_SUBJECT_MAP[token]:
                if subject not in subjects:
                    subjects.append(subject)
            continue

        if len(token) >= 2 and token not in subjects:
            subjects.append(token)

    if not subjects:
        subjects.append(major.strip().upper() or "CS")

    return subjects[:6]


SYSTEMS_KEYWORDS = {"system", "operating", "network", "architecture", "organization", "assembly"}
SOFTWARE_KEYWORDS = {"software", "design", "engineering", "development", "testing", "project"}
THEORY_KEYWORDS = {"theory", "algorithm", "automata", "proof", "logic", "formal"}
MATH_KEYWORDS = {"math", "linear", "calculus", "probability", "statistics", "combinatorics"}
AI_KEYWORDS = {"ai", "machine", "learning", "data", "intelligence"}



def infer_requirement_tags(course_code: str, title: str) -> list[str]:
    text = f"{course_code} {title}".lower()
    normalized_code = course_code.replace(" ", "").upper()
    subject_match = re.match(r"([A-Za-z]+)", normalized_code)
    tags = ["elective", normalized_code.lower()]
    if subject_match is not None:
        tags.append(subject_match.group(1).lower())

    if any(word in text for word in SYSTEMS_KEYWORDS):
        tags.append("systems")
    if any(word in text for word in SOFTWARE_KEYWORDS):
        tags.append("software")
    if any(word in text for word in THEORY_KEYWORDS):
        tags.append("theory")
    if any(word in text for word in MATH_KEYWORDS):
        tags.append("math")
    if any(word in text for word in AI_KEYWORDS):
        tags.append("ai")

    course_number = _course_number(course_code)
    if 1000 <= course_number < 3000:
        tags.append("core")

    unique_tags: list[str] = []
    for tag in tags:
        if tag not in unique_tags:
            unique_tags.append(tag)
    return unique_tags



def course_major_tags(course_code: str, fallback_subjects: list[str]) -> list[str]:
    match = re.match(r"([A-Za-z]+)", course_code)
    if match is None:
        return fallback_subjects

    subject = match.group(1).upper()
    tags = [subject]
    for item in fallback_subjects:
        if item not in tags:
            tags.append(item)
    return tags



def normalize_professor_name(name: str) -> str:
    cleaned = name.replace("Professor", "").replace("Prof.", "").replace("Prof", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned



def to_course_record_from_section(section, school: str, term: str, major_subjects: list[str], source: str) -> CourseRecord:
    course_code = str(getattr(section, "code", "")).replace(" ", "")
    title = str(getattr(section, "name", "")).strip()
    instructor = normalize_professor_name(str(getattr(section, "instructor", "TBA"))) or "TBA"
    days = str(getattr(section, "days", "")).strip()
    start_time = str(getattr(section, "start_time", "")).strip()
    end_time = str(getattr(section, "end_time", "")).strip()
    location = str(getattr(section, "location", "")).strip()
    capacity = getattr(section, "capacity", None)

    open_guess: int | None = None
    if isinstance(capacity, int) and capacity > 0:
        open_guess = capacity

    return CourseRecord(
        crn=str(getattr(section, "crn", "")).strip(),
        course_code=course_code,
        title=title,
        instructor=instructor,
        days=days,
        start_time=start_time,
        end_time=end_time,
        location=location,
        school=school,
        term=term,
        major_tags=course_major_tags(course_code, major_subjects),
        requirement_tags=infer_requirement_tags(course_code, title),
        open_seats=open_guess,
        source=source,
    )



def _course_number(course_code: str) -> int:
    match = re.search(r"(\d{4})", course_code)
    if match is None:
        return 9999
    return int(match.group(1))
