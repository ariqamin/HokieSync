from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.providers.udc_grade_client import UDCGradeClient

CSV_FIELDS = [
    "course_code",
    "subject_code",
    "course_number",
    "course_title",
    "instructor",
    "academic_year",
    "term",
    "term_code_full",
    "course_ref_no",
    "credit_hours",
    "student_no",
    "withdraws",
    "gpa",
    "grade_a",
    "grade_a_negative",
    "grade_b_positive",
    "grade_b",
    "grade_b_negative",
    "grade_c_positive",
    "grade_c",
    "grade_c_negative",
    "grade_d_positive",
    "grade_d",
    "grade_d_negative",
    "grade_f",
]


def main():
    parser = argparse.ArgumentParser(description="Fetch Virginia Tech UDC grade distribution rows as CSV.")
    parser.add_argument("--subject", default="CS", help="Subject code to fetch, such as CS or MATH.")
    parser.add_argument("--course-number", default="", help="Optional course number, such as 3704.")
    parser.add_argument("--output", default="data/imports/udc_grades.csv", help="CSV output path.")
    parser.add_argument("--page-size", type=int, default=500, help="Rows to request per course page.")
    parser.add_argument("--max-courses", type=int, default=0, help="Optional safety limit while testing.")
    args = parser.parse_args()

    client = UDCGradeClient()
    courses = client.list_courses()
    selected_courses = select_courses(courses, args.subject, args.course_number)
    if args.max_courses:
        selected_courses = selected_courses[: args.max_courses]

    rows: list[dict[str, Any]] = []
    for index, course in enumerate(selected_courses, start=1):
        subject_code, course_number, title = course
        print(f"[{index}/{len(selected_courses)}] {subject_code} {course_number} - {title}")
        rows.extend(normalize_row(row) for row in client.fetch_course_rows(subject_code, course_number, title, args.page_size))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(output_path, rows)
    print(f"Wrote {len(rows)} rows to {output_path}")


def select_courses(courses: list[list[str]], subject: str, course_number: str) -> list[list[str]]:
    subject = subject.strip().upper()
    course_number = course_number.strip()
    selected = [course for course in courses if course[0].upper() == subject]
    if course_number:
        selected = [course for course in selected if str(course[1]) == course_number]
    if not selected:
        raise RuntimeError("No matching UDC courses found")
    return selected


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: row.get(field, "") for field in CSV_FIELDS}
    normalized["course_code"] = f"{row.get('subject_code', '')}{row.get('course_number', '')}".replace(" ", "").upper()
    return normalized


def write_csv(path: Path, rows: list[dict[str, Any]]):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
