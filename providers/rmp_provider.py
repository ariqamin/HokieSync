from __future__ import annotations

import asyncio
import json
from difflib import SequenceMatcher

import requests

from src.db import Database
from src.models import ProfessorRating
from src.providers.helpers import normalize_professor_name


SCHOOL_SEARCH_QUERY = """
query AutocompleteSearchQuery($query: String!) {
  autocomplete(query: $query) {
    schools {
      edges {
        node {
          id
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
query ProfessorRatingsQuery($text: String!, $schoolID: ID!) {
  newSearch {
    teachers(query: {text: $text, schoolID: $schoolID}) {
      edges {
        node {
          id
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
}
"""


class RMPProvider:
    def __init__(self, db: Database, graphql_url: str, auth_token: str, school_name: str, school_id: str = ""):
        self.db = db
        self.graphql_url = graphql_url
        self.auth_token = auth_token
        self.school_name = school_name
        self.school_id = school_id
        self.last_refresh = "Never"
        self.last_error = "None"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "authorization": f"Basic {self.auth_token}",
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": "kit-bot/1.0",
            }
        )

    async def refresh(self):
        await self._ensure_school_id()
        if self.school_id:
            self.last_refresh = "OK"
        elif not self.last_error:
            self.last_error = "No school ID available"

    async def get_rating(self, instructor: str) -> ProfessorRating | None:
        normalized_name = normalize_professor_name(instructor)
        if not normalized_name:
            return None

        cached = self.db.get_professor_rating(normalized_name, self.school_name)
        if cached is not None:
            return cached

        await self._ensure_school_id()
        if not self.school_id:
            return None

        try:
            results = await asyncio.to_thread(self._search_teachers, normalized_name)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return None

        selected = self._pick_best_teacher(normalized_name, results)
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
                    "text": text,
                    "schoolID": self.school_id,
                },
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        edges = payload.get("data", {}).get("newSearch", {}).get("teachers", {}).get("edges", [])
        return [edge.get("node", {}) for edge in edges]

    async def _ensure_school_id(self):
        if self.school_id:
            return

        try:
            school = await asyncio.to_thread(self._search_school, self.school_name)
        except Exception as exc:  # pragma: no cover - depends on live network
            self.last_error = str(exc)
            return

        if school is None:
            self.last_error = f"Could not find school '{self.school_name}' on RateMyProfessors"
            return

        self.school_id = str(school.get("id", ""))

    def _search_school(self, school_name: str) -> dict | None:
        response = self.session.post(
            self.graphql_url,
            json={
                "query": SCHOOL_SEARCH_QUERY,
                "variables": {
                    "query": school_name,
                },
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        edges = payload.get("data", {}).get("autocomplete", {}).get("schools", {}).get("edges", [])
        schools = [edge.get("node", {}) for edge in edges]
        if not schools:
            return None

        return max(
            schools,
            key=lambda item: SequenceMatcher(None, item.get("name", "").lower(), school_name.lower()).ratio(),
        )

    def _pick_best_teacher(self, instructor: str, results: list[dict]) -> dict | None:
        if not results:
            return None

        normalized_target = normalize_professor_name(instructor).lower()

        def score(item: dict) -> tuple[float, int, float]:
            candidate_name = self._teacher_full_name(item).lower()
            similarity = SequenceMatcher(None, candidate_name, normalized_target).ratio()
            num_ratings = int(item.get("numRatings") or 0)
            avg_rating = float(item.get("avgRating") or 0.0)
            return similarity, num_ratings, avg_rating

        best = max(results, key=score)
        similarity = score(best)[0]
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
