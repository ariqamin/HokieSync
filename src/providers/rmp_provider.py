from __future__ import annotations

import asyncio
import base64
import json
import re
from difflib import SequenceMatcher

import requests

from src.core.db import Database
from src.core.models import ProfessorRating
from src.providers.helpers import normalize_professor_name


SCHOOL_SEARCH_QUERY = """
query NewSearchSchoolsQuery($query: SchoolSearchQuery!) {
  newSearch {
    schools(query: $query) {
      edges {
        node {
          id
          legacyId
          name
          city
          state
        }
      }
    }
  }
}
"""

TEACHER_SEARCH_QUERY = """
query TeacherSearchResultsPageQuery($query: TeacherSearchQuery!, $schoolID: ID, $includeSchoolFilter: Boolean!) {
  search: newSearch {
    teachers(query: $query, first: 8, after: "") {
      edges {
        node {
          id
          legacyId
          firstName
          lastName
          department
          avgDifficulty
          avgRating
          numRatings
          wouldTakeAgainPercent
        }
      }
    }
  }
  school: node(id: $schoolID) @include(if: $includeSchoolFilter) {
    id
  }
}
"""

KNOWN_SCHOOL_IDS = {
    "virginia tech": "U2Nob29sLTEzNDk=",
    "virginia polytechnic institute and state university": "U2Nob29sLTEzNDk=",
    "vt": "U2Nob29sLTEzNDk=",
}

COURSE_DEPARTMENT_HINTS = {
    "ACIS": ["accounting", "information systems"],
    "BIT": ["business information technology", "information technology"],
    "BMES": ["biomedical"],
    "CHEM": ["chemistry"],
    "COMM": ["communication"],
    "CS": ["computer science", "computer"],
    "CMDA": ["computational modeling", "data analytics", "mathematics", "statistics", "computer science"],
    "ECE": ["electrical", "computer engineering"],
    "ECON": ["economics"],
    "ENGL": ["english"],
    "FIN": ["finance"],
    "MATH": ["mathematics", "math"],
    "MGT": ["management"],
    "MKTG": ["marketing"],
    "PHYS": ["physics"],
    "PSYC": ["psychology"],
    "STAT": ["statistics", "stat"],
}


class RMPProvider:
    def __init__(self, db: Database, graphql_url: str, auth_token: str, school_name: str, school_id: str = ""):
        self.db = db
        self.graphql_url = graphql_url
        self.auth_token = auth_token
        self.school_name = school_name
        self.school_id = school_id
        self._school_lookup_attempted = bool(school_id)
        self.last_refresh = "Never"
        self.last_error = "None"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "authorization": f"Basic {self.auth_token}",
                "content-type": "application/json",
                "accept": "application/json",
                "accept-language": "en-US,en;q=0.5",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
            }
        )
        self.school_id = self._normalize_school_id(self.school_id)

    async def refresh(self):
        await self._ensure_school_id()
        if self.school_id:
            self.last_refresh = "OK"
            self.last_error = "None"
        elif not self.last_error:
            self.last_error = "No school ID available"

    async def get_rating(self, instructor: str, course_context: str = "") -> ProfessorRating | None:
        normalized_name = normalize_professor_name(instructor)
        if not normalized_name:
            return None

        cached = self.db.get_professor_rating(normalized_name, self.school_name)
        if cached is not None and self._cached_rating_matches_context(cached, course_context):
            return cached

        await self._ensure_school_id()
        if not self.school_id:
            return None

        try:
            results = await asyncio.to_thread(self._search_teachers, normalized_name)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

        selected = self._pick_best_teacher(normalized_name, results, course_context)
        if selected is None:
            return None

        rating = ProfessorRating(
            professor_name=self._teacher_full_name(selected),
            school_name=self.school_name,
            school_id=self.school_id,
            avg_rating=float(selected.get("avgRating") or 0.0),
            avg_difficulty=float(selected.get("avgDifficulty") or 0.0),
            num_ratings=int(selected.get("numRatings") or 0),
            would_take_again=self._optional_float(selected.get("wouldTakeAgainPercent")),
            raw_json=json.dumps(selected),
        )
        self.db.cache_professor_rating(rating)
        self.last_refresh = "OK"
        self.last_error = "None"
        return rating

    def _search_teachers(self, text: str) -> list[dict]:
        response = self.session.post(
            self.graphql_url,
            json={
                "query": TEACHER_SEARCH_QUERY,
                "variables": {
                    "query": {
                        "text": text,
                        "schoolID": self.school_id,
                        "fallback": True,
                        "departmentID": None,
                    },
                    "schoolID": self.school_id,
                    "includeSchoolFilter": True,
                },
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return []
        new_search = data.get("search") or data.get("newSearch") or {}
        if not isinstance(new_search, dict):
            return []
        teachers = new_search.get("teachers") or {}
        if not isinstance(teachers, dict):
            return []
        edges = teachers.get("edges") or []
        return [edge.get("node", {}) for edge in edges]

    async def _ensure_school_id(self):
        if self.school_id:
            return
        if self._school_lookup_attempted:
            return

        self._school_lookup_attempted = True

        known_id = KNOWN_SCHOOL_IDS.get(" ".join(self.school_name.lower().split()))
        if known_id:
            self.school_id = known_id
            self.last_error = "None"
            return

        try:
            school = await asyncio.to_thread(self._search_school, self.school_name)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return

        if school is None:
            self.last_error = f"Could not find school '{self.school_name}' on RateMyProfessors"
            return

        self.school_id = self._normalize_school_id(str(school.get("id") or school.get("legacyId") or ""))

    def _search_school(self, school_name: str) -> dict | None:
        response = self.session.post(
            self.graphql_url,
            json={
                "query": SCHOOL_SEARCH_QUERY,
                "variables": {
                    "query": {"text": school_name},
                },
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            return None
        search_payload = data.get("newSearch") or data.get("autocomplete") or {}
        if not isinstance(search_payload, dict):
            return None
        schools_payload = search_payload.get("schools") or {}
        if not isinstance(schools_payload, dict):
            return None
        edges = schools_payload.get("edges") or []
        schools = [edge.get("node", {}) for edge in edges]
        if not schools:
            return None

        return max(
            schools,
            key=lambda item: SequenceMatcher(None, item.get("name", "").lower(), school_name.lower()).ratio(),
        )

    def _pick_best_teacher(self, instructor: str, results: list[dict], course_context: str = "") -> dict | None:
        if not results:
            return None

        normalized_target = normalize_professor_name(instructor).lower()
        department_hints = self._department_hints(course_context)
        candidates = results
        if department_hints:
            candidates = [item for item in results if self._department_fit(item, department_hints) > 0]
            if not candidates:
                return None

        def score(item: dict) -> tuple[float, float, int, float]:
            candidate_name = self._teacher_full_name(item).lower()
            similarity = SequenceMatcher(None, candidate_name, normalized_target).ratio()
            department_fit = self._department_fit(item, department_hints)
            num_ratings = int(item.get("numRatings") or 0)
            avg_rating = float(item.get("avgRating") or 0.0)
            return similarity, department_fit, num_ratings, avg_rating

        best = max(candidates, key=score)
        similarity, *_ = score(best)
        if similarity < 0.55:
            return None
        return best

    def _teacher_full_name(self, item: dict) -> str:
        first = str(item.get("firstName") or "").strip()
        last = str(item.get("lastName") or "").strip()
        return normalize_professor_name(f"{first} {last}")

    def _optional_float(self, value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _normalize_school_id(self, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            return ""
        if value.isdigit():
            raw = f"School-{value}".encode("utf-8")
            return base64.b64encode(raw).decode("ascii")
        return value

    def _department_hints(self, course_context: str) -> list[str]:
        context = str(course_context or "").upper()
        match = re.search(r"\b([A-Z]{2,5})\s*\d{3,4}\b", context)
        subject = match.group(1) if match else context.strip()
        return COURSE_DEPARTMENT_HINTS.get(subject, [])

    def _department_fit(self, item: dict, hints: list[str]) -> float:
        if not hints:
            return 0.0
        department = str(item.get("department") or "").lower()
        if not department:
            return 0.0
        return 1.0 if any(hint in department for hint in hints) else 0.0

    def _cached_rating_matches_context(self, rating: ProfessorRating, course_context: str) -> bool:
        hints = self._department_hints(course_context)
        if not hints:
            return True
        try:
            raw = json.loads(rating.raw_json) if rating.raw_json else {}
        except ValueError:
            return False
        return self._department_fit(raw, hints) > 0
