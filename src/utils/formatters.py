from __future__ import annotations

import statistics
from typing import Iterable

from src.models import ClassEntry, Recommendation, SchedulePlan, SchedulePreferences, WEEKDAY_NAMES
from src.utils.time_utils import format_minutes, normalize_days


def text_block(title: str, lines: Iterable[str]) -> str:
    body = "\n".join(lines)
    return f"**{title}**\n```\n{body}\n```"


def format_schedule(owner_name: str, classes: list[ClassEntry], privacy: str) -> str:
    if not classes:
        return text_block(f"{owner_name}'s schedule", ["No classes saved.", f"Privacy: {privacy}"])

    lines = [f"Privacy: {privacy}", ""]
    lines.append(f"{'CRN':<8} {'Course':<12} {'Name':<28} {'Days':<6} {'Time':<13} {'Location':<16}")
    lines.append("-" * 93)
    for entry in classes:
        course = entry.course_code[:12]
        title = (entry.course_title or "-")[:28]
        if entry.start_time and entry.end_time:
            time_span = f"{entry.start_time}-{entry.end_time}"
        else:
            time_span = "TBA"
        location = (entry.location or "-")[:16]
        lines.append(f"{entry.crn:<8} {course:<12} {title:<28} {(entry.days or 'TBA'):<6} {time_span:<13} {location:<16}")
    return text_block(f"{owner_name}'s schedule", lines)


def format_preferences(preferences: SchedulePreferences | None) -> str:
    if preferences is None or not preferences.raw_text:
        return text_block("Schedule preferences", ["No schedule preferences saved yet."])

    lines = [f"Raw description: {preferences.raw_text}", ""]
    lines.append(f"Preferred start: {preferences.preferred_start or 'Not set'}")
    lines.append(f"Preferred end: {preferences.preferred_end or 'Not set'}")
    lines.append(f"Time window is strict: {'yes' if preferences.hard_time_window else 'no'}")
    lines.append(f"Avoid early classes: {'yes' if preferences.avoid_early else 'no'}")
    lines.append(f"Avoid late classes: {'yes' if preferences.avoid_late else 'no'}")
    lines.append(f"Avoid days: {preferences.avoid_days or ('F' if preferences.avoid_friday else 'None')}")
    lines.append(f"Preferred days: {preferences.preferred_days or 'No preference detected'}")
    lines.append(f"Compact schedule: {'yes' if preferences.compact_days else 'no'}")
    lines.append(f"Max days on campus: {preferences.max_days}")
    lines.append(f"Break style: {preferences.breaks_preference or 'No preference detected'}")
    lines.append(f"Minimum schedule GPA: {preferences.min_avg_gpa:.2f}" if preferences.min_avg_gpa else "Minimum schedule GPA: Not set")
    lines.append(
        f"Minimum professor rating: {preferences.min_rmp_rating:.1f}/5"
        if preferences.min_rmp_rating
        else "Minimum professor rating: Not set"
    )
    lines.append(f"Target course count: {preferences.target_courses or 'Flexible'}")
    lines.append(f"Suggested mode: {preferences.preferred_mode or 'No preference detected'}")
    if preferences.notes:
        lines.append("")
        lines.append(f"Parsed notes: {preferences.notes}")
    return text_block("Schedule preferences", lines)


def format_recommendations(profile_label: str, mode: str, recommendations: list[Recommendation]) -> str:
    if not recommendations:
        return text_block(
            "Recommendations",
            [
                f"Profile: {profile_label}",
                f"Mode: {mode}",
                "No recommendations available. Add a profile and make sure at least one data source is enabled.",
            ],
        )

    lines = [f"Profile: {profile_label}", f"Mode: {mode}", ""]
    for index, rec in enumerate(recommendations, start=1):
        meeting = _meeting_label(rec.days, rec.start_time, rec.end_time)
        lines.extend(
            [
                f"{index}. {rec.course_code} - {rec.title}",
                f"   {meeting} | CRN {rec.crn} | {rec.instructor}",
                f"   GPA {rec.avg_gpa:.2f} | RMP {_rating_label(rec.rmp_rating)} | {rec.label} ({rec.score:.1f})",
            ]
        )
        if rec.fit_notes:
            lines.append(f"   Fit: {rec.fit_notes}")
        lines.append("")
    lines.append("Run /planschedule when you want full schedule options from a plain-English request.")
    return text_block("Recommended courses", lines)


def format_schedule_plans(profile_label: str, mode: str, plans: list[SchedulePlan]) -> str:
    if not plans:
        return text_block(
            "Recommended schedules",
            [
                f"Profile: {profile_label}",
                f"Mode: {mode}",
                "No schedule plans could be built from the available recommendations.",
            ],
        )

    lines = [f"Profile: {profile_label}", f"Mode: {mode}", ""]
    for index, plan in enumerate(plans, start=1):
        lines.append(f"Option {index}: {plan.label} ({plan.score:.1f})")
        lines.append(f"  {plan.summary}")
        lines.append(f"  Avg GPA {plan.avg_gpa:.2f} | Avg RMP {_rating_label(plan.avg_rmp_rating)}")
        if plan.constraint_notes:
            lines.append(f"  Fit: {plan.constraint_notes}")
        lines.append("  Courses:")
        for course in plan.courses:
            title = course.title or "Untitled"
            meeting = _meeting_label(course.days, course.start_time, course.end_time)
            lines.append(f"  - {course.course_code} - {title}")
            detail = f"    {meeting} | {course.instructor} | GPA {course.avg_gpa:.2f}"
            if course.fit_notes:
                detail += f" | {course.fit_notes}"
            lines.append(detail)
        lines.append("")
    return text_block("Recommended schedules", lines)


def _meeting_label(days: str, start_time: str, end_time: str) -> str:
    day_label = days or "TBA"
    if start_time and end_time:
        return f"{day_label} {start_time}-{end_time}"
    return f"{day_label} TBA"


def _rating_label(value: float) -> str:
    if value <= 0:
        return "N/A"
    return f"{value:.1f}/5"


def format_free_time(title: str, windows: dict[str, list[tuple[int, int]]], excluded: list[str]) -> str:
    lines: list[str] = []
    if excluded:
        lines.append("Excluded users:")
        for item in excluded:
            lines.append(f"- {item}")
        lines.append("")

    if not windows:
        lines.append("No mutual free time windows were found.")
        return text_block(title, lines)

    for day_code, ranges in windows.items():
        day_name = WEEKDAY_NAMES[day_code]
        lines.append(f"{day_name}:")
        if not ranges:
            lines.append("  None")
            continue
        for start, end in ranges:
            lines.append(f"  {format_minutes(start)}-{format_minutes(end)}")
        lines.append("")

    return text_block(title, lines)


def sanitize_days(days: str) -> str:
    return normalize_days(days)


def format_udc_grade_rows(
    subject: str,
    course_number: str,
    rows: list[dict],
    total_rows: int,
    instructor: str = "",
    page: int = 0,
    page_size: int | None = None,
) -> str:
    course_code = f"{subject.upper()}{course_number}"
    if not rows:
        return text_block("UDC grades", [f"No UDC grade rows found for {course_code}."])

    page_size = page_size or len(rows)
    start_row = page * page_size + 1
    end_row = min(page * page_size + len(rows), total_rows)
    gpas = [float(row["gpa"]) for row in rows if row.get("gpa") not in {"", None}]
    avg_gpa = statistics.mean(gpas) if gpas else 0
    title = rows[0].get("course_title") or rows[0].get("title") or ""
    lines = [
        f"Course: {course_code} - {title}",
        f"Rows matched: {total_rows}",
        f"Page average GPA: {avg_gpa:.2f}",
        f"Showing rows: {start_row}-{end_row}",
    ]
    if instructor:
        lines.append(f"Instructor filter: {instructor}")
    lines.extend(["", f"{'Year':<8} {'Term':<7} {'Instructor':<18} {'GPA':<4} {'A%':<5} {'B+%':<5}"])
    lines.append("-" * 56)

    for row in rows:
        instructor_name = str(row.get("instructor") or "-")[:18]
        lines.append(
            f"{str(row.get('academic_year') or '-'):<8} "
            f"{str(row.get('term') or '-'):<7} "
            f"{instructor_name:<18} "
            f"{float(row.get('gpa') or 0):<4.2f} "
            f"{float(row.get('grade_a') or 0):<5.1f} "
            f"{float(row.get('grade_b_positive') or 0):<5.1f}"
        )

    return text_block("UDC grades", lines)


def format_udc_course_matches(subject: str, courses: list[list[str]]) -> str:
    subject_label = subject.upper() if subject.strip() else "all subjects"
    if not courses:
        return text_block("UDC grades", [f"No UDC courses found for {subject_label}."])

    lines = [f"Courses found for {subject_label}: {len(courses)}", ""]
    for course in courses[:20]:
        lines.append(f"{course[0]} {course[1]} - {course[2]}")
    if len(courses) > 20:
        lines.append("")
        lines.append("Showing first 20. Add course_number to view grade rows.")
    return text_block("UDC grades", lines)


def format_dars_import_result(
    major: str,
    school: str,
    term: str,
    requirements: str,
    missing_courses: list[str],
    completed_courses: list[str],
    credit_requirements: list[str],
    current_courses: list,
    planned_courses: list,
    warnings: list[str],
) -> str:
    lines = [
        f"Major: {major or 'Not detected'}",
        f"School: {school or 'Virginia Tech'}",
        f"Term: {term or 'Not set'}",
    ]
    if current_courses:
        preview = ", ".join(course.course_code for course in current_courses[:12])
        if len(current_courses) > 12:
            preview += f", and {len(current_courses) - 12} more"
        lines.append(f"Current in-progress courses detected: {preview}")
    if planned_courses:
        preview = ", ".join(course.course_code for course in planned_courses[:12])
        if len(planned_courses) > 12:
            preview += f", and {len(planned_courses) - 12} more"
        lines.append(f"Planned/future courses detected: {preview}")
    if missing_courses:
        preview = ", ".join(missing_courses[:12])
        if len(missing_courses) > 12:
            preview += f", and {len(missing_courses) - 12} more"
        lines.append(f"Missing/needed courses detected: {preview}")
    if credit_requirements:
        preview = ", ".join(credit_requirements[:8])
        if len(credit_requirements) > 8:
            preview += f", and {len(credit_requirements) - 8} more"
        lines.append(f"Unmet credit buckets detected: {preview}")
    if completed_courses:
        preview = ", ".join(completed_courses[:12])
        if len(completed_courses) > 12:
            preview += f", and {len(completed_courses) - 12} more"
        lines.append(f"Completed courses saved for recommendation filtering: {preview}")
    lines.append("")
    lines.append("No DARS PDF contents, personal header fields, or schedule entries were saved.")
    return text_block("DARS imported", lines)
