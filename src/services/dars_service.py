from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO

from pypdf import PdfReader

try:
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR
except ImportError:  # pragma: no cover - optional OCR path
    np = None
    pdfium = None
    RapidOCR = None


COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,4})\s*[- ]?\s*(\d{4}|[1-9]XXX)\b")
LONG_TERM_RE = re.compile(r"^(20\d{2})(FA|SP|SU)$", re.IGNORECASE)
SHORT_TERM_RE = re.compile(r"^(\d{2})(FA|SP|SU)$", re.IGNORECASE)
HOURS_RE = re.compile(r"^\d+\.\d+$")

TERM_SEASONS = {
    "FA": "Fall",
    "SP": "Spring",
    "SU": "Summer",
}

MAJOR_HINTS = [
    ("computer science", "CS"),
    ("computerscience", "CS"),
    ("bscs", "CS"),
    ("computational modeling", "CMDA"),
    ("computer engineering", "CPE"),
    ("electrical engineering", "ECE"),
    ("mathematics", "MATH"),
    ("math", "MATH"),
    ("engineering", "ENGR"),
]

REQUIREMENT_KEYWORDS = {
    "systems": {"system", "systems", "operating", "network", "architecture", "organization", "assembly"},
    "software": {"software", "design", "engineering", "development", "testing", "project"},
    "theory": {"theory", "algorithm", "automata", "proof", "logic", "formal"},
    "math": {"math", "calculus", "linear", "probability", "statistics", "combinatorics"},
    "ai": {"ai", "artificial", "machine", "learning", "data", "intelligence"},
    "core": {"core", "foundation", "required"},
    "elective": {"elective", "choose", "select"},
}

UNMET_MARKERS = (
    "not complete",
    "still needed",
    "need ",
    "needs ",
    "required",
    "select from",
    "choose from",
    "incomplete",
    "no ",
)

COMPLETE_MARKERS = ("complete", "satisfied", "earned", "taken")


@dataclass(slots=True)
class DARSCourse:
    term_code: str
    term_label: str
    course_code: str
    title: str = ""
    status: str = ""
    hours: str = ""


@dataclass(slots=True)
class DARSParseResult:
    major: str = ""
    school: str = "Virginia Tech"
    term: str = ""
    requirements_text: str = ""
    missing_courses: list[str] = field(default_factory=list)
    completed_courses: list[str] = field(default_factory=list)
    credit_requirements: list[str] = field(default_factory=list)
    current_courses: list[DARSCourse] = field(default_factory=list)
    planned_courses: list[DARSCourse] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DARSService:
    def __init__(self):
        self._ocr_engine = None

    def parse_pdf_bytes(self, payload: bytes, preferred_term: str = "") -> DARSParseResult:
        text = self.extract_text(payload)
        if not text.strip():
            ocr_text = self.extract_text_with_ocr(payload)
            if not ocr_text.strip():
                return DARSParseResult(
                    term=preferred_term,
                    warnings=[
                        "The PDF did not contain selectable text and OCR could not read enough content from it.",
                    ],
                )
            result = self.parse_text(ocr_text, preferred_term)
            result.warnings.append("Used OCR because the PDF did not contain selectable text.")
            return result

        result = self.parse_text(text, preferred_term)
        return result

    def extract_text(self, payload: bytes) -> str:
        reader = PdfReader(BytesIO(payload))
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks)

    def extract_text_with_ocr(self, payload: bytes) -> str:
        if pdfium is None or RapidOCR is None or np is None:
            return ""

        if self._ocr_engine is None:
            self._ocr_engine = RapidOCR()

        document = pdfium.PdfDocument(payload)
        chunks: list[str] = []
        try:
            for page in document:
                image = page.render(scale=2).to_pil()
                result, _ = self._ocr_engine(np.array(image))
                chunks.extend(self.ocr_result_to_lines(result or []))
        finally:
            document.close()
        return "\n".join(chunks)

    def ocr_result_to_lines(self, result: list) -> list[str]:
        items: list[tuple[float, float, str]] = []
        for item in result:
            if len(item) < 2:
                continue
            points, text = item[0], str(item[1]).strip()
            if not text:
                continue
            x_values = [point[0] for point in points]
            y_values = [point[1] for point in points]
            items.append((min(y_values), min(x_values), text))

        items.sort(key=lambda item: (round(item[0] / 10) * 10, item[1]))
        return [text for _, _, text in items]

    def parse_text(self, text: str, preferred_term: str = "") -> DARSParseResult:
        lines = [" ".join(line.split()) for line in text.splitlines()]
        lines = [line for line in lines if line]
        major = self.infer_major(lines)
        target_term = self.normalize_term_label(preferred_term) or self.infer_latest_in_progress_term(lines)
        missing_courses = self.find_missing_courses(lines)
        completed_courses = self.find_completed_courses(lines, missing_courses)
        credit_requirements = self.find_credit_requirements(lines)
        requirement_tokens = self.find_requirement_tokens(lines, missing_courses)
        current_courses, planned_courses = self.current_and_planned_courses(lines, target_term)

        if major and major.lower() not in {token.lower() for token in requirement_tokens}:
            requirement_tokens.insert(0, major)
        for course_code in missing_courses:
            token = f"need:{course_code}"
            if token not in requirement_tokens:
                requirement_tokens.append(token)
        for course_code in completed_courses:
            token = f"taken:{course_code}"
            if token not in requirement_tokens:
                requirement_tokens.append(token)
        for course in current_courses:
            token = f"taken:{course.course_code}"
            if token not in requirement_tokens:
                requirement_tokens.append(token)
        for course in planned_courses:
            token = f"planned:{course.course_code}"
            if token not in requirement_tokens:
                requirement_tokens.append(token)
        for requirement in credit_requirements:
            token = f"credit:{requirement}"
            if token not in requirement_tokens:
                requirement_tokens.append(token)

        warnings: list[str] = []
        if not major:
            warnings.append("Could not confidently infer a major from the DARS.")
        if not missing_courses and len(requirement_tokens) <= 1:
            warnings.append("Could not identify unmet requirement details from the DARS text.")
        if target_term and not current_courses:
            warnings.append(f"Could not identify any in-progress courses for {target_term} in the DARS course history.")

        return DARSParseResult(
            major=major,
            school="Virginia Tech",
            term=target_term,
            requirements_text=", ".join(requirement_tokens),
            missing_courses=missing_courses,
            completed_courses=completed_courses,
            credit_requirements=credit_requirements,
            current_courses=current_courses,
            planned_courses=planned_courses,
            warnings=warnings,
        )

    def infer_major(self, lines: list[str]) -> str:
        focused_lines: list[str] = []
        for index, line in enumerate(lines):
            if re.search(r"\b(major|degree|program|plan|college|department|bachelor)\b", line, re.IGNORECASE):
                focused_lines.extend(lines[max(0, index - 1) : index + 3])
        search_text = "\n".join(focused_lines + lines).lower()
        compact_text = re.sub(r"[^a-z0-9]", "", search_text)
        for phrase, major in MAJOR_HINTS:
            compact_phrase = re.sub(r"[^a-z0-9]", "", phrase)
            if phrase in search_text or compact_phrase in compact_text:
                return major
        return ""

    def find_missing_courses(self, lines: list[str]) -> list[str]:
        missing: list[str] = []
        for index, line in enumerate(lines):
            window = self.requirement_window(lines, index, 5)
            lowered = window.lower()
            if not self.looks_unmet(lowered):
                continue
            for subject, number in COURSE_CODE_RE.findall(window.upper()):
                code = f"{subject}{number}"
                if code not in missing:
                    missing.append(code)
        return missing[:30]

    def find_completed_courses(self, lines: list[str], missing_courses: list[str]) -> list[str]:
        completed: list[str] = []
        missing = set(missing_courses)
        complete_statuses = {"TR", "AP", "T", "RG", "RW", "PL"}
        for row in self.course_history_rows(lines):
            status = row.status.upper()
            if row.course_code in missing:
                continue
            if status == "IP":
                continue
            if status in complete_statuses or re.fullmatch(r"[ABCD][+-]?", status):
                if row.course_code not in completed:
                    completed.append(row.course_code)

        for index, line in enumerate(lines):
            window = self.requirement_window(lines, index, 4)
            lowered = window.lower()
            if self.looks_unmet(lowered):
                continue
            if not any(marker in lowered for marker in COMPLETE_MARKERS):
                continue
            for subject, number in COURSE_CODE_RE.findall(window.upper()):
                code = f"{subject}{number}"
                if code not in missing and code not in completed:
                    completed.append(code)
        return completed[:80]

    def find_credit_requirements(self, lines: list[str]) -> list[str]:
        requirements: list[str] = []
        for index, line in enumerate(lines):
            window = self.requirement_window(lines, index, 4)
            lowered = window.lower()
            if not self.looks_unmet(lowered):
                continue
            if "credit" not in lowered and "hour" not in lowered:
                continue

            amount = self.first_needed_credit_amount(window)
            label = self.credit_requirement_label(window)
            if amount:
                value = f"{label}:{amount}"
            else:
                value = label
            if value not in requirements:
                requirements.append(value)
        return requirements[:12]

    def first_needed_credit_amount(self, text: str) -> str:
        patterns = [
            r"(\d+(?:\.\d+)?)\s+(?:credits?|hours?)\s+(?:still\s+)?(?:needed|required)",
            r"(?:need|needs|needed|required)\s+(\d+(?:\.\d+)?)\s+(?:credits?|hours?)",
            r"select\s+(\d+(?:\.\d+)?)\s+(?:credits?|hours?)",
        ]
        lowered = text.lower()
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                return match.group(1).rstrip("0").rstrip(".")
        return ""

    def credit_requirement_label(self, text: str) -> str:
        lowered = text.lower()
        if "free elective" in lowered:
            return "free_electives"
        if "technical elective" in lowered or "tech elective" in lowered:
            return "technical_electives"
        if "pathway" in lowered:
            return "pathways"
        if "major" in lowered:
            return "major_credits"
        if "elective" in lowered:
            return "electives"
        return "credits"

    def requirement_window(self, lines: list[str], start_index: int, max_lines: int) -> str:
        chunk: list[str] = []
        for line in lines[start_index : start_index + max_lines]:
            lowered = line.lower()
            if chunk and (self.match_long_term_code(line) or "course history" in lowered):
                break
            chunk.append(line)
        return " ".join(chunk)

    def find_courses_for_term(self, lines: list[str], term_label: str) -> list[DARSCourse]:
        term_code = self.term_label_to_code(term_label)
        if not term_code:
            return []

        courses: list[DARSCourse] = []
        seen: set[str] = set()
        for row in self.course_history_rows(lines):
            if row.term_code != term_code:
                continue
            if row.status and row.status.upper() not in {"IP", "RG", "RW", "PL", "T", "TR"}:
                continue
            if row.course_code in seen:
                continue
            seen.add(row.course_code)
            courses.append(row)
        return courses

    def current_and_planned_courses(self, lines: list[str], target_term: str = "") -> tuple[list[DARSCourse], list[DARSCourse]]:
        current: list[DARSCourse] = []
        planned: list[DARSCourse] = []
        seen_current: set[str] = set()
        seen_planned: set[str] = set()
        rows = self.course_history_rows(lines)
        current_term_code = self.infer_current_coursework_term_code(rows) or self.current_calendar_term_code()
        target_term_code = self.term_label_to_code(target_term)

        for row in rows:
            status = row.status.upper()
            if status not in {"IP", "RG", "RW", "PL"}:
                continue

            is_planned = status == "PL"
            if row.term_code and current_term_code and self.term_code_sort_key(row.term_code) > self.term_code_sort_key(current_term_code):
                is_planned = True
            if (
                target_term_code
                and row.term_code == target_term_code
                and self.term_code_sort_key(row.term_code) > self.term_code_sort_key(current_term_code)
            ):
                is_planned = True

            if is_planned:
                if row.course_code not in seen_planned:
                    seen_planned.add(row.course_code)
                    planned.append(row)
                continue

            if row.course_code not in seen_current:
                seen_current.add(row.course_code)
                current.append(row)
        return current, planned

    def infer_current_coursework_term_code(self, rows: list[DARSCourse]) -> str:
        term_codes = sorted(
            {
                row.term_code
                for row in rows
                if row.term_code and row.status.upper() in {"IP", "RG", "RW"}
            },
            key=self.term_code_sort_key,
        )
        if not term_codes:
            return ""
        return term_codes[0]

    def term_code_sort_key(self, term_code: str) -> tuple[int, int]:
        match = LONG_TERM_RE.match(term_code.strip().upper())
        if not match:
            return (9999, 9)
        year, season = match.groups()
        order = {"SP": 1, "SU": 2, "FA": 3}
        return (int(year), order.get(season.upper(), 9))

    def current_calendar_term_code(self) -> str:
        today = date.today()
        if today.month >= 9:
            season = "FA"
        elif today.month >= 6:
            season = "SU"
        else:
            season = "SP"
        return f"{today.year}{season}"

    def course_history_rows(self, lines: list[str]) -> list[DARSCourse]:
        rows: list[DARSCourse] = []
        for index, line in enumerate(lines):
            term_code = self.match_long_term_code(line)
            if not term_code:
                continue

            chunk: list[str] = []
            for candidate in lines[index + 1 : index + 10]:
                if self.match_long_term_code(candidate):
                    break
                chunk.append(candidate)

            course_code = self.first_course_code(chunk)
            if not course_code:
                continue

            status = self.first_status(chunk)
            hours = self.first_hours(chunk)
            title = self.title_from_chunk(chunk, course_code, status, hours)
            rows.append(
                DARSCourse(
                    term_code=term_code,
                    term_label=self.term_code_to_label(term_code),
                    course_code=course_code,
                    title=title,
                    status=status,
                    hours=hours,
                )
            )
        return rows

    def first_course_code(self, lines: list[str]) -> str:
        for line in lines:
            match = COURSE_CODE_RE.search(line.upper())
            if match:
                return f"{match.group(1)}{match.group(2)}"
        return ""

    def first_status(self, lines: list[str]) -> str:
        for line in lines:
            token = line.strip().upper()
            if token in {"IP", "TR", "AP", "RG", "RW", "PL", "T"}:
                return token
            if re.fullmatch(r"[ABCDF][+-]?", token):
                return token
        return ""

    def first_hours(self, lines: list[str]) -> str:
        for line in lines:
            cleaned = line.strip().replace("°", "0").replace("*", "")
            if HOURS_RE.match(cleaned):
                return cleaned
        return ""

    def title_from_chunk(self, lines: list[str], course_code: str, status: str, hours: str) -> str:
        title_lines: list[str] = []
        start_index = 0
        if status:
            for index, line in enumerate(lines):
                if line.strip().upper() == status.upper():
                    start_index = index + 1
                    break

        normalized_code = f"{course_code[:-4]} {course_code[-4:]}"
        for line in lines[start_index:]:
            stripped = line.strip()
            upper = stripped.upper()
            if self.is_course_history_footer(stripped):
                break
            if SHORT_TERM_RE.match(upper):
                continue
            if COURSE_CODE_RE.search(upper):
                continue
            if status and upper == status.upper():
                continue
            if hours and stripped.replace("°", "0").replace("*", "") == hours:
                continue
            if stripped.upper() == normalized_code.upper():
                continue
            if re.fullmatch(r"[ABCDF][+-]?", upper):
                continue
            if stripped:
                title_lines.append(stripped)
        title = " ".join(title_lines).strip()
        return re.sub(r"^(?:[0O]{2,}[A-Z]?|\d+\.\d+)\s+", "", title).strip()

    def is_course_history_footer(self, line: str) -> bool:
        lowered = line.lower()
        if re.fullmatch(r"\d+/\d+", line.strip()):
            return True
        return any(
            marker in lowered
            for marker in [
                "legend",
                "completed course",
                "in progress course",
                "planned course",
                "copyright",
                "privacy policy",
                "selfservice version",
                "licensed to virginia",
            ]
        )

    def infer_latest_in_progress_term(self, lines: list[str]) -> str:
        term_codes: list[str] = []
        for row in self.course_history_rows(lines):
            if row.status.upper() == "IP" and row.term_code not in term_codes:
                term_codes.append(row.term_code)
        if not term_codes:
            return ""
        return self.term_code_to_label(sorted(term_codes)[-1])

    def match_long_term_code(self, value: str) -> str:
        match = LONG_TERM_RE.match(value.strip().upper())
        if not match:
            return ""
        return f"{match.group(1)}{match.group(2)}"

    def normalize_term_label(self, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            return ""
        long_code = self.match_long_term_code(value)
        if long_code:
            return self.term_code_to_label(long_code)
        short_match = SHORT_TERM_RE.match(value.upper())
        if short_match:
            year = f"20{short_match.group(1)}"
            return self.term_code_to_label(f"{year}{short_match.group(2)}")

        parts = value.split()
        if len(parts) < 2:
            return value
        season = parts[0].capitalize()
        year = parts[-1]
        if season.lower() in {"fall", "spring", "summer"} and re.fullmatch(r"20\d{2}", year):
            return f"{season} {year}"
        return value

    def term_label_to_code(self, value: str) -> str:
        label = self.normalize_term_label(value)
        if not label:
            return ""
        parts = label.split()
        if len(parts) != 2:
            return ""
        season_code = {season.lower(): code for code, season in TERM_SEASONS.items()}.get(parts[0].lower())
        if not season_code or not re.fullmatch(r"20\d{2}", parts[1]):
            return ""
        return f"{parts[1]}{season_code}"

    def term_code_to_label(self, term_code: str) -> str:
        match = LONG_TERM_RE.match(term_code.strip().upper())
        if not match:
            return term_code
        year, season_code = match.groups()
        return f"{TERM_SEASONS.get(season_code.upper(), season_code.upper())} {year}"

    def find_requirement_tokens(self, lines: list[str], missing_courses: list[str]) -> list[str]:
        tokens: list[str] = []
        for code in missing_courses:
            if code not in tokens:
                tokens.append(code)
            subject = re.match(r"[A-Z]+", code)
            if subject and subject.group(0) not in tokens:
                tokens.append(subject.group(0))

        unmet_text_parts: list[str] = []
        for index, line in enumerate(lines):
            window = self.requirement_window(lines, index, 3)
            lowered = window.lower()
            if self.looks_unmet(lowered):
                unmet_text_parts.append(lowered)
        unmet_text = " ".join(unmet_text_parts) if unmet_text_parts else " ".join(lines).lower()

        for token, keywords in REQUIREMENT_KEYWORDS.items():
            if any(keyword in unmet_text for keyword in keywords) and token not in tokens:
                tokens.append(token)

        if not tokens:
            tokens.extend(["core", "elective"])
        return tokens[:40]

    def looks_unmet(self, text: str) -> bool:
        has_unmet_marker = any(marker in text for marker in UNMET_MARKERS)
        if not has_unmet_marker:
            return False
        if "not complete" in text or "incomplete" in text:
            return True
        if "still needed" in text:
            return True
        return not any(marker in text for marker in COMPLETE_MARKERS)
