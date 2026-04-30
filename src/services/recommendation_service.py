from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Protocol

from src.db import Database
from src.models import Recommendation, SchedulePlan, SchedulePreferences, VALID_RECOMMENDATION_MODES
from src.utils.time_utils import normalize_days, parse_time


DEFAULT_RMP = 3.5
DEFAULT_GPA = 3.0


@dataclass(slots=True)
class RequirementContext:
    tags: set[str]
    needed_courses: set[str]
    taken_courses: set[str]
    planned_courses: set[str]
    credit_buckets: set[str]


class Strategy(Protocol):
    def score(
        self,
        *,
        major_match: float,
        requirement_match: float,
        rmp: float,
        gpa: float,
        time_fit: float,
    ) -> float:
        ...


@dataclass(slots=True)
class BalancedStrategy:
    def score(self, *, major_match: float, requirement_match: float, rmp: float, gpa: float, time_fit: float) -> float:
        return major_match * 18 + requirement_match * 22 + rmp * 22 + gpa * 23 + time_fit * 15


@dataclass(slots=True)
class EasyStrategy:
    def score(self, *, major_match: float, requirement_match: float, rmp: float, gpa: float, time_fit: float) -> float:
        return major_match * 10 + requirement_match * 17 + rmp * 16 + gpa * 42 + time_fit * 15


@dataclass(slots=True)
class ProfessorFocusedStrategy:
    def score(self, *, major_match: float, requirement_match: float, rmp: float, gpa: float, time_fit: float) -> float:
        return major_match * 10 + requirement_match * 17 + rmp * 43 + gpa * 15 + time_fit * 15


class RecommendationService:
    def __init__(self, db: Database, provider):
        self.db = db
        self.provider = provider

    def choose_strategy(self, mode: str) -> Strategy:
        if mode == "easy":
            return EasyStrategy()
        if mode == "professor":
            return ProfessorFocusedStrategy()
        return BalancedStrategy()

    def effective_mode(self, requested_mode: str, preferences: SchedulePreferences | None) -> str:
        if requested_mode in VALID_RECOMMENDATION_MODES and requested_mode != "balanced":
            return requested_mode
        if preferences is not None and preferences.preferred_mode in VALID_RECOMMENDATION_MODES:
            return preferences.preferred_mode
        return requested_mode if requested_mode in VALID_RECOMMENDATION_MODES else "balanced"

    async def recommend(
        self,
        user_id: int,
        mode: str,
        enable_rmp: bool,
        enable_grades: bool,
        limit: int = 5,
    ) -> list[Recommendation]:
        profile = self.db.get_profile(user_id)
        if profile is None or not profile.major or not profile.school or not self.db.active_term_for_profile(profile):
            return []

        preferences = self.db.get_preferences(user_id)
        effective_mode = self.effective_mode(mode, preferences)
        recommendations = await self._build_recommendations(user_id, effective_mode, enable_rmp, enable_grades)
        return recommendations[:limit]

    async def recommend_schedules(
        self,
        user_id: int,
        mode: str,
        enable_rmp: bool,
        enable_grades: bool,
    ) -> list[SchedulePlan]:
        preferences = self.db.get_preferences(user_id)
        effective_mode = self.effective_mode(mode, preferences)
        candidates = await self._build_recommendations(user_id, effective_mode, enable_rmp, enable_grades)
        plans = self._build_schedule_plans(candidates[:18], preferences, strict=True)
        if plans:
            return plans

        relaxed_candidates = await self._build_recommendations(
            user_id,
            effective_mode,
            enable_rmp,
            enable_grades,
            respect_hard_preferences=False,
        )
        return self._build_schedule_plans(relaxed_candidates[:18], preferences, strict=False)

    def _build_schedule_plans(
        self,
        candidates: list[Recommendation],
        preferences: SchedulePreferences | None,
        *,
        strict: bool,
    ) -> list[SchedulePlan]:
        sizes = self._candidate_schedule_sizes(preferences, len(candidates))
        plans: list[SchedulePlan] = []
        for size in sizes:
            for combo in combinations(candidates, size):
                option = list(combo)
                if self._has_internal_conflict(option):
                    continue

                constraint_notes = self._plan_constraint_notes(option, preferences, strict=strict)
                if constraint_notes is None:
                    continue

                avg_gpa = self._average([course.avg_gpa for course in option])
                avg_rmp = self._average([course.rmp_rating for course in option])
                score = self._plan_score(option, preferences, avg_gpa, avg_rmp)
                plans.append(
                    SchedulePlan(
                        score=score,
                        label=self._plan_label(score),
                        summary=self._plan_summary(option, preferences),
                        avg_gpa=avg_gpa,
                        avg_rmp_rating=avg_rmp,
                        constraint_notes=constraint_notes,
                        courses=option,
                    )
                )

        if not plans and candidates:
            greedy = self._greedy_schedule(candidates, sizes[0] if sizes else 1)
            if greedy:
                avg_gpa = self._average([course.avg_gpa for course in greedy])
                avg_rmp = self._average([course.rmp_rating for course in greedy])
                plans.append(
                    SchedulePlan(
                        score=self._plan_score(greedy, preferences, avg_gpa, avg_rmp),
                        label="Best available",
                        summary=self._plan_summary(greedy, preferences),
                        avg_gpa=avg_gpa,
                        avg_rmp_rating=avg_rmp,
                        constraint_notes=self._plan_constraint_notes(greedy, preferences, strict=False) or "best available non-conflicting schedule",
                        courses=greedy,
                    )
                )

        plans.sort(key=lambda item: (item.score, len(item.courses)), reverse=True)
        return plans[:3]

    async def _build_recommendations(
        self,
        user_id: int,
        mode: str,
        enable_rmp: bool,
        enable_grades: bool,
        respect_hard_preferences: bool = True,
    ) -> list[Recommendation]:
        profile = self.db.get_profile(user_id)
        if profile is None or not profile.major or not profile.school or not self.db.active_term_for_profile(profile):
            return []

        term = self.db.active_term_for_profile(profile)
        preferences = self.db.get_preferences(user_id)
        strategy = self.choose_strategy(mode)
        requirements = self._requirement_context(profile.requirements_text)
        saved_classes = self.db.list_classes(user_id)
        all_saved_classes = self._all_saved_classes(user_id)
        saved_crns = {entry.crn for entry in all_saved_classes}
        saved_course_codes = {self._normalized_course_code(entry.course_code) for entry in all_saved_classes}
        blocked_course_codes = saved_course_codes.union(requirements.taken_courses).union(requirements.planned_courses)
        courses = await self._candidate_courses(
            profile.major,
            profile.school,
            term,
            requirements,
            enrich_rmp=enable_rmp,
            enrich_grades=enable_grades,
        )

        recommendations: list[Recommendation] = []
        for course in courses:
            if course.crn in saved_crns:
                continue
            course_key = self._normalized_course_code(course.course_code)
            if course_key in blocked_course_codes:
                continue
            if self._conflicts_with_saved_schedule(course.days, course.start_time, course.end_time, saved_classes):
                continue
            if respect_hard_preferences and self._course_violates_hard_preferences(course.days, course.start_time, course.end_time, preferences):
                continue

            major_match = self._major_match(profile.major, course.major_tags)
            requirement_match = self._requirement_match(requirements, course.course_code, course.requirement_tags)
            rmp_raw = course.rmp_rating if enable_rmp and course.rmp_rating is not None else DEFAULT_RMP
            gpa_raw = course.avg_gpa if enable_grades and course.avg_gpa is not None else DEFAULT_GPA
            rmp_score = max(0.0, min(rmp_raw / 5.0, 1.0))
            gpa_score = max(0.0, min(gpa_raw / 4.0, 1.0))
            time_fit = self._time_fit(course.days, course.start_time, course.end_time, preferences)

            final_score = strategy.score(
                major_match=major_match,
                requirement_match=requirement_match,
                rmp=rmp_score,
                gpa=gpa_score,
                time_fit=time_fit,
            )
            if course_key in requirements.needed_courses:
                final_score += 14.0
            elif requirements.needed_courses and any(tag.lower().replace(" ", "") in requirements.tags for tag in course.requirement_tags):
                final_score += 4.0
            fit_notes = self._fit_notes(course.days, course.start_time, course.end_time, rmp_raw, gpa_raw, preferences)
            fit_notes = self._append_requirement_fit_notes(fit_notes, course_key, course.requirement_tags, requirements)
            recommendations.append(
                Recommendation(
                    course_code=course.course_code,
                    title=course.title,
                    instructor=course.instructor,
                    crn=course.crn,
                    days=normalize_days(course.days),
                    start_time=course.start_time,
                    end_time=course.end_time,
                    rmp_rating=rmp_raw,
                    avg_gpa=gpa_raw,
                    score=final_score,
                    label=self._label_for_score(final_score),
                    explanation=self._build_explanation(
                        mode=mode,
                        course_code=course.course_code,
                        needed_courses=requirements.needed_courses,
                        credit_buckets=requirements.credit_buckets,
                        major_tags=course.major_tags,
                        requirement_tags=course.requirement_tags,
                        rmp_raw=rmp_raw,
                        gpa_raw=gpa_raw,
                        preferences=preferences,
                        time_fit=time_fit,
                    ),
                    fit_notes=fit_notes,
                )
            )

        recommendations.sort(key=lambda item: item.score, reverse=True)
        return recommendations

    def _all_saved_classes(self, user_id: int):
        classes = []
        seen: set[tuple[str, str]] = set()
        for schedule_key in ["current", "next"]:
            for entry in self.db.list_classes(user_id, schedule_key):
                key = (entry.schedule_key, entry.crn)
                if key in seen:
                    continue
                seen.add(key)
                classes.append(entry)
        return classes

    async def _candidate_courses(
        self,
        major: str,
        school: str,
        term: str,
        requirements: RequirementContext,
        *,
        enrich_rmp: bool,
        enrich_grades: bool,
    ):
        collected = {
            course.crn: course
            for course in await self.provider.list_courses_for_profile(
                major,
                school,
                term,
                enrich_rmp=enrich_rmp,
                enrich_grades=enrich_grades,
            )
        }

        for course_code in sorted(requirements.needed_courses):
            matches = await self.provider.search_courses(
                course_code,
                school=school,
                term=term,
                enrich_rmp=enrich_rmp,
                enrich_grades=enrich_grades,
            )
            for course in matches:
                if self._normalized_course_code(course.course_code) == course_code:
                    collected[course.crn] = course

        return sorted(collected.values(), key=lambda item: (self._candidate_priority(item, requirements), item.course_code, item.start_time, item.crn))

    def _candidate_priority(self, course, requirements: RequirementContext) -> int:
        course_key = self._normalized_course_code(course.course_code)
        if course_key in requirements.needed_courses:
            return 0
        normalized_tags = {tag.lower().replace(" ", "") for tag in course.requirement_tags}
        if requirements.credit_buckets and "elective" in normalized_tags:
            return 1
        if requirements.tags.intersection(normalized_tags):
            return 2
        return 3

    def _major_match(self, major: str, tags: list[str]) -> float:
        major_upper = major.upper()
        if any(tag.upper() in major_upper for tag in tags):
            return 1.0
        return 0.45

    def _requirement_context(self, requirements_text: str) -> RequirementContext:
        tags: set[str] = set()
        needed_courses: set[str] = set()
        taken_courses: set[str] = set()
        planned_courses: set[str] = set()
        credit_buckets: set[str] = set()
        for raw_token in requirements_text.split(","):
            token = raw_token.strip()
            normalized = token.lower().replace(" ", "")
            if not normalized:
                continue
            if normalized.startswith("need:"):
                needed_courses.add(self._normalized_course_code(normalized.removeprefix("need:")))
                continue
            if normalized.startswith("taken:"):
                taken_courses.add(self._normalized_course_code(normalized.removeprefix("taken:")))
                continue
            if normalized.startswith("planned:"):
                planned_courses.add(self._normalized_course_code(normalized.removeprefix("planned:")))
                continue
            if normalized.startswith("credit:"):
                credit_buckets.add(normalized.removeprefix("credit:"))
                tags.add("elective")
                continue
            tags.add(normalized)
        return RequirementContext(
            tags=tags,
            needed_courses=needed_courses,
            taken_courses=taken_courses,
            planned_courses=planned_courses,
            credit_buckets=credit_buckets,
        )

    def _requirement_match(self, requirement_context: RequirementContext, course_code: str, tags: list[str]) -> float:
        course_key = self._normalized_course_code(course_code)
        if course_key in requirement_context.needed_courses:
            return 1.0
        if not requirement_context.tags and not requirement_context.needed_courses:
            return 0.65
        normalized_tags = {tag.lower().replace(" ", "") for tag in tags}
        if requirement_context.tags.intersection(normalized_tags):
            return 1.0
        if requirement_context.credit_buckets and "elective" in normalized_tags:
            return 0.85
        if requirement_context.needed_courses:
            needed_subjects = {self._subject_from_course_code(code).lower() for code in requirement_context.needed_courses}
            if needed_subjects.intersection(normalized_tags):
                return 0.55
        return 0.4

    def _append_requirement_fit_notes(
        self,
        fit_notes: str,
        course_code: str,
        requirement_tags: list[str],
        requirements: RequirementContext,
    ) -> str:
        notes = [note for note in fit_notes.split(", ") if note]
        normalized_tags = {tag.lower().replace(" ", "") for tag in requirement_tags}
        if course_code in requirements.needed_courses:
            notes.insert(0, "DARS required course")
        elif requirements.credit_buckets and "elective" in normalized_tags:
            notes.insert(0, "helps remaining elective credits")

        unique_notes: list[str] = []
        for note in notes:
            if note not in unique_notes:
                unique_notes.append(note)
        return ", ".join(unique_notes)

    def _normalized_course_code(self, course_code: str) -> str:
        return course_code.replace(" ", "").upper()

    def _subject_from_course_code(self, course_code: str) -> str:
        letters = []
        for char in str(course_code).upper():
            if char.isalpha():
                letters.append(char)
            elif letters:
                break
        return "".join(letters)

    def _time_fit(self, days: str, start_time: str, end_time: str, preferences: SchedulePreferences | None) -> float:
        if preferences is None:
            return 0.75

        score = 0.75
        normalized_days = normalize_days(days)
        start_minutes = self._safe_parse_time(start_time)
        end_minutes = self._safe_parse_time(end_time)

        if start_minutes is not None and preferences.preferred_start:
            preferred_start = parse_time(preferences.preferred_start)
            score += 0.12 if start_minutes >= preferred_start else -0.18

        if end_minutes is not None and preferences.preferred_end:
            preferred_end = parse_time(preferences.preferred_end)
            score += 0.12 if end_minutes <= preferred_end else -0.18

        if start_minutes is not None and preferences.avoid_early and start_minutes < 10 * 60:
            score -= 0.18

        if end_minutes is not None and preferences.avoid_late and end_minutes > 16 * 60:
            score -= 0.18

        if preferences.avoid_days and set(normalized_days).intersection(preferences.avoid_days):
            score -= 0.25

        if preferences.preferred_days and set(normalized_days).issubset(set(preferences.preferred_days)):
            score += 0.08

        if preferences.compact_days:
            score += 0.1 if len(normalized_days) <= 2 else -0.05

        return max(0.0, min(score, 1.0))

    def _fit_notes(
        self,
        days: str,
        start_time: str,
        end_time: str,
        rmp_rating: float,
        avg_gpa: float,
        preferences: SchedulePreferences | None,
    ) -> str:
        if preferences is None:
            return ""

        notes: list[str] = []
        normalized_days = normalize_days(days)
        if preferences.min_avg_gpa and avg_gpa >= preferences.min_avg_gpa:
            notes.append("meets GPA goal")
        if preferences.min_rmp_rating and rmp_rating >= preferences.min_rmp_rating:
            notes.append("meets professor-rating goal")
        if preferences.preferred_start and preferences.preferred_end and self._within_window(start_time, end_time, preferences):
            notes.append("inside time window")
        if preferences.avoid_days and not set(normalized_days).intersection(preferences.avoid_days):
            notes.append("avoids blocked days")
        if preferences.compact_days and len(normalized_days) <= 2:
            notes.append("compact meeting pattern")
        return ", ".join(notes)

    def _build_explanation(
        self,
        *,
        mode: str,
        course_code: str,
        needed_courses: set[str],
        credit_buckets: set[str],
        major_tags: list[str],
        requirement_tags: list[str],
        rmp_raw: float,
        gpa_raw: float,
        preferences: SchedulePreferences | None,
        time_fit: float,
    ) -> str:
        parts = [f"{course_code} matches major tags {', '.join(major_tags) or 'none'}"]
        if self._normalized_course_code(course_code) in needed_courses:
            parts.append("it appears as an unmet DARS course requirement")
        elif credit_buckets and any(tag.lower() == "elective" for tag in requirement_tags):
            parts.append("it can help with remaining elective or credit requirements")
        else:
            parts.append(f"lines up with requirement tags {', '.join(requirement_tags) or 'none'}")
        parts.extend(
            [
                f"has a professor score of {rmp_raw:.1f}/5",
                f"and an average GPA of {gpa_raw:.2f}",
            ]
        )

        if preferences is not None and preferences.raw_text:
            if time_fit >= 0.8:
                parts.append("it fits the saved schedule preferences well")
            elif time_fit < 0.6:
                parts.append("it only partially fits the saved schedule preferences")
            if preferences.min_avg_gpa:
                parts.append(f"the planner will try to keep full schedule GPA at least {preferences.min_avg_gpa:.2f}")

        if mode == "easy":
            parts.append("Easy mode gives extra weight to grading history.")
        elif mode == "professor":
            parts.append("Professor mode gives extra weight to instructor quality.")
        else:
            parts.append("Balanced mode weighs requirements, grading history, professor quality, and schedule fit.")

        return "; ".join(parts)

    def _label_for_score(self, score: float) -> str:
        if score >= 84:
            return "Strong match"
        if score >= 74:
            return "Good fit"
        return "Possible fit"

    def _course_violates_hard_preferences(
        self,
        days: str,
        start_time: str,
        end_time: str,
        preferences: SchedulePreferences | None,
    ) -> bool:
        if preferences is None:
            return False
        normalized_days = normalize_days(days)
        if preferences.avoid_days and set(normalized_days).intersection(preferences.avoid_days):
            return True
        if preferences.hard_time_window and not self._within_window(start_time, end_time, preferences):
            return True
        return False

    def _within_window(self, start_time: str, end_time: str, preferences: SchedulePreferences) -> bool:
        if not preferences.preferred_start or not preferences.preferred_end:
            return True
        start_minutes = self._safe_parse_time(start_time)
        end_minutes = self._safe_parse_time(end_time)
        if start_minutes is None or end_minutes is None:
            return False
        return start_minutes >= parse_time(preferences.preferred_start) and end_minutes <= parse_time(preferences.preferred_end)

    def _conflicts_with_saved_schedule(self, days: str, start_time: str, end_time: str, saved_classes) -> bool:
        normalized_days = normalize_days(days)
        start_minutes = self._safe_parse_time(start_time)
        end_minutes = self._safe_parse_time(end_time)
        if not normalized_days or start_minutes is None or end_minutes is None:
            return False

        for entry in saved_classes:
            existing_days = normalize_days(entry.days)
            existing_start = self._safe_parse_time(entry.start_time)
            existing_end = self._safe_parse_time(entry.end_time)
            if not existing_days or existing_start is None or existing_end is None:
                continue
            if not set(normalized_days).intersection(existing_days):
                continue
            if start_minutes < existing_end and end_minutes > existing_start:
                return True
        return False

    def _has_internal_conflict(self, courses: list[Recommendation]) -> bool:
        course_codes: set[str] = set()
        for course in courses:
            course_code = self._normalized_course_code(course.course_code)
            if course_code in course_codes:
                return True
            course_codes.add(course_code)

        for left_index, left_course in enumerate(courses):
            for right_course in courses[left_index + 1 :]:
                if not set(left_course.days).intersection(right_course.days):
                    continue
                left_start = self._safe_parse_time(left_course.start_time)
                left_end = self._safe_parse_time(left_course.end_time)
                right_start = self._safe_parse_time(right_course.start_time)
                right_end = self._safe_parse_time(right_course.end_time)
                if None in {left_start, left_end, right_start, right_end}:
                    continue
                if left_start < right_end and left_end > right_start:
                    return True
        return False

    def _greedy_schedule(self, candidates: list[Recommendation], target_size: int) -> list[Recommendation]:
        selected: list[Recommendation] = []
        for candidate in candidates:
            option = selected + [candidate]
            if self._has_internal_conflict(option):
                continue
            selected.append(candidate)
            if len(selected) >= target_size:
                break
        return selected

    def _candidate_schedule_sizes(self, preferences: SchedulePreferences | None, candidate_count: int) -> list[int]:
        if candidate_count <= 0:
            return []
        if preferences is not None and preferences.target_courses:
            return [min(preferences.target_courses, candidate_count)]
        largest = min(4, candidate_count)
        return list(range(largest, 0, -1))

    def _plan_constraint_notes(self, courses: list[Recommendation], preferences: SchedulePreferences | None, *, strict: bool) -> str | None:
        if preferences is None:
            return "no saved preferences"

        notes: list[str] = []
        misses: list[str] = []
        distinct_days = {day for course in courses for day in course.days}
        avg_gpa = self._average([course.avg_gpa for course in courses])
        avg_rmp = self._average([course.rmp_rating for course in courses])

        if preferences.target_courses and len(courses) != preferences.target_courses:
            if strict:
                return None
            misses.append(f"{len(courses)} of {preferences.target_courses} requested courses")
        if preferences.max_days and len(distinct_days) > preferences.max_days:
            if strict:
                return None
            misses.append(f"{len(distinct_days)} campus days exceeds {preferences.max_days}")
        if preferences.min_avg_gpa and avg_gpa < preferences.min_avg_gpa:
            if strict:
                return None
            misses.append(f"average GPA {avg_gpa:.2f} below {preferences.min_avg_gpa:.2f}")
        if preferences.min_rmp_rating and avg_rmp < preferences.min_rmp_rating:
            if strict:
                return None
            misses.append(f"average RMP {avg_rmp:.1f} below {preferences.min_rmp_rating:.1f}")
        if preferences.hard_time_window and any(not self._within_window(course.start_time, course.end_time, preferences) for course in courses):
            if strict:
                return None
            misses.append(f"some classes outside {preferences.preferred_start}-{preferences.preferred_end}")
        if preferences.avoid_days and any(set(course.days).intersection(preferences.avoid_days) for course in courses):
            if strict:
                return None
            misses.append("uses a blocked day")

        if preferences.min_avg_gpa and avg_gpa >= preferences.min_avg_gpa:
            notes.append(f"average GPA {avg_gpa:.2f} clears {preferences.min_avg_gpa:.2f}")
        if preferences.min_rmp_rating and avg_rmp >= preferences.min_rmp_rating:
            notes.append(f"average RMP {avg_rmp:.1f} clears {preferences.min_rmp_rating:.1f}")
        if preferences.preferred_start and preferences.preferred_end and all(self._within_window(course.start_time, course.end_time, preferences) for course in courses):
            notes.append(f"inside {preferences.preferred_start}-{preferences.preferred_end}")
        if preferences.max_days and len(distinct_days) <= preferences.max_days:
            notes.append(f"{len(distinct_days)} day(s) on campus")
        if preferences.avoid_days and all(not set(course.days).intersection(preferences.avoid_days) for course in courses):
            notes.append("avoids blocked days")
        if misses:
            notes.append("best effort: " + "; ".join(misses))
        return ", ".join(notes) or "fits saved preferences"

    def _plan_score(
        self,
        courses: list[Recommendation],
        preferences: SchedulePreferences | None,
        avg_gpa: float,
        avg_rmp: float,
    ) -> float:
        if not courses:
            return 0.0

        base = sum(course.score for course in courses) / len(courses)
        distinct_days = len({day for course in courses for day in course.days})
        bonus = len(courses) * 1.5

        if preferences is not None:
            if preferences.compact_days:
                bonus += 6.0 if distinct_days <= 3 else -3.0
            if preferences.max_days and distinct_days <= preferences.max_days:
                bonus += 4.0
            if preferences.avoid_days and all(not set(course.days).intersection(preferences.avoid_days) for course in courses):
                bonus += 4.0
            if preferences.min_avg_gpa and avg_gpa >= preferences.min_avg_gpa:
                bonus += min(6.0, (avg_gpa - preferences.min_avg_gpa) * 8 + 3)
            if preferences.min_rmp_rating and avg_rmp >= preferences.min_rmp_rating:
                bonus += min(5.0, (avg_rmp - preferences.min_rmp_rating) * 4 + 2)

        return round(base + bonus, 1)

    def _plan_label(self, score: float) -> str:
        if score >= 88:
            return "Best overall"
        if score >= 78:
            return "Strong option"
        return "Backup option"

    def _plan_summary(self, courses: list[Recommendation], preferences: SchedulePreferences | None) -> str:
        distinct_days = sorted({day for course in courses for day in course.days})
        pieces = [f"{len(courses)} course(s) across {len(distinct_days)} day(s)"]
        if preferences is not None:
            if preferences.avoid_days and all(not set(course.days).intersection(preferences.avoid_days) for course in courses):
                pieces.append("keeps blocked days open")
            if preferences.compact_days and len(distinct_days) <= 3:
                pieces.append("stays compact")
            if preferences.target_courses and len(courses) == preferences.target_courses:
                pieces.append("matches requested course count")
        return ", ".join(pieces)

    def _average(self, values: list[float]) -> float:
        if not values:
            return 0.0
        return round(sum(values) / len(values), 2)

    def _safe_parse_time(self, value: str) -> int | None:
        if not value:
            return None
        try:
            return parse_time(value)
        except ValueError:
            return None
