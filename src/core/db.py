from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from src.core.models import ClassEntry, GradeStat, ProfessorRating, Profile, SchedulePreferences, VALID_SCHEDULE_KEYS
from src.utils.term_utils import academic_terms_for, choose_next_term


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self):
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                major TEXT NOT NULL,
                school TEXT NOT NULL,
                term TEXT NOT NULL,
                privacy TEXT NOT NULL DEFAULT 'friends',
                requirements_text TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS classes (
                user_id INTEGER NOT NULL,
                schedule_key TEXT NOT NULL DEFAULT 'current',
                crn TEXT NOT NULL,
                course_code TEXT NOT NULL,
                course_title TEXT NOT NULL,
                instructor TEXT NOT NULL,
                days TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'catalog',
                PRIMARY KEY (user_id, schedule_key, crn)
            );

            CREATE TABLE IF NOT EXISTS friends (
                owner_user_id INTEGER NOT NULL,
                friend_user_id INTEGER NOT NULL,
                PRIMARY KEY (owner_user_id, friend_user_id)
            );

            CREATE TABLE IF NOT EXISTS preferences (
                user_id INTEGER PRIMARY KEY,
                raw_text TEXT NOT NULL DEFAULT '',
                preferred_start TEXT NOT NULL DEFAULT '',
                preferred_end TEXT NOT NULL DEFAULT '',
                avoid_early INTEGER NOT NULL DEFAULT 0,
                avoid_late INTEGER NOT NULL DEFAULT 0,
                avoid_friday INTEGER NOT NULL DEFAULT 0,
                avoid_days TEXT NOT NULL DEFAULT '',
                preferred_days TEXT NOT NULL DEFAULT '',
                compact_days INTEGER NOT NULL DEFAULT 0,
                max_days INTEGER NOT NULL DEFAULT 5,
                breaks_preference TEXT NOT NULL DEFAULT '',
                min_avg_gpa REAL NOT NULL DEFAULT 0,
                min_rmp_rating REAL NOT NULL DEFAULT 0,
                hard_time_window INTEGER NOT NULL DEFAULT 0,
                target_courses INTEGER NOT NULL DEFAULT 0,
                preferred_mode TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS server_config (
                guild_id INTEGER PRIMARY KEY,
                enable_catalog INTEGER NOT NULL DEFAULT 1,
                enable_rmp INTEGER NOT NULL DEFAULT 1,
                enable_grades INTEGER NOT NULL DEFAULT 1,
                poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
                last_catalog_refresh TEXT NOT NULL DEFAULT 'Never',
                last_rmp_refresh TEXT NOT NULL DEFAULT 'Never',
                last_grades_refresh TEXT NOT NULL DEFAULT 'Never',
                last_error TEXT NOT NULL DEFAULT 'None'
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                user_id INTEGER NOT NULL,
                schedule_key TEXT NOT NULL DEFAULT 'current',
                crn TEXT NOT NULL,
                last_known_open_seats INTEGER NOT NULL DEFAULT 0,
                notified_open INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, schedule_key, crn)
            );

            CREATE TABLE IF NOT EXISTS professor_cache (
                cache_key TEXT PRIMARY KEY,
                professor_name TEXT NOT NULL,
                school_name TEXT NOT NULL,
                school_id TEXT NOT NULL DEFAULT '',
                avg_rating REAL NOT NULL,
                avg_difficulty REAL NOT NULL DEFAULT 0,
                num_ratings INTEGER NOT NULL DEFAULT 0,
                would_take_again REAL,
                raw_json TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS grade_cache (
                cache_key TEXT PRIMARY KEY,
                course_code TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                instructor TEXT NOT NULL DEFAULT '',
                academic_year TEXT NOT NULL DEFAULT '',
                term TEXT NOT NULL DEFAULT '',
                gpa REAL NOT NULL,
                a_pct REAL,
                a_minus_pct REAL,
                b_plus_pct REAL,
                b_pct REAL,
                raw_json TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self):
        self._ensure_columns(
            "profiles",
            {
                "active_schedule": "TEXT NOT NULL DEFAULT 'current'",
                "current_term": "TEXT NOT NULL DEFAULT ''",
                "next_term": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self.conn.execute("UPDATE profiles SET current_term=term WHERE current_term='' AND term<>''")
        self._migrate_classes_table()
        self._migrate_watchlist_table()
        self._ensure_columns(
            "preferences",
            {
                "avoid_days": "TEXT NOT NULL DEFAULT ''",
                "preferred_days": "TEXT NOT NULL DEFAULT ''",
                "min_avg_gpa": "REAL NOT NULL DEFAULT 0",
                "min_rmp_rating": "REAL NOT NULL DEFAULT 0",
                "hard_time_window": "INTEGER NOT NULL DEFAULT 0",
                "target_courses": "INTEGER NOT NULL DEFAULT 0",
                "preferred_mode": "TEXT NOT NULL DEFAULT ''",
            },
        )
    def _ensure_columns(self, table_name: str, columns: dict[str, str]):
        existing = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, definition in columns.items():
            if column_name in existing:
                continue
            self.conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")

    def _migrate_classes_table(self):
        columns = self.conn.execute("PRAGMA table_info(classes)").fetchall()
        column_names = {row["name"] for row in columns}
        pk_columns = [row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"]]
        if "schedule_key" in column_names and pk_columns == ["user_id", "schedule_key", "crn"]:
            return

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS classes_new (
                user_id INTEGER NOT NULL,
                schedule_key TEXT NOT NULL DEFAULT 'current',
                crn TEXT NOT NULL,
                course_code TEXT NOT NULL,
                course_title TEXT NOT NULL,
                instructor TEXT NOT NULL,
                days TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'catalog',
                PRIMARY KEY (user_id, schedule_key, crn)
            );
            """
        )
        schedule_expr = "schedule_key" if "schedule_key" in column_names else "'current'"
        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO classes_new (
                user_id, schedule_key, crn, course_code, course_title, instructor,
                days, start_time, end_time, location, source
            )
            SELECT user_id, {schedule_expr}, crn, course_code, course_title, instructor,
                   days, start_time, end_time, location, source
            FROM classes
            """
        )
        self.conn.execute("DROP TABLE classes")
        self.conn.execute("ALTER TABLE classes_new RENAME TO classes")

    def _migrate_watchlist_table(self):
        columns = self.conn.execute("PRAGMA table_info(watchlist)").fetchall()
        column_names = {row["name"] for row in columns}
        pk_columns = [row["name"] for row in sorted(columns, key=lambda item: item["pk"]) if row["pk"]]
        if "schedule_key" in column_names and pk_columns == ["user_id", "schedule_key", "crn"]:
            return

        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist_new (
                user_id INTEGER NOT NULL,
                schedule_key TEXT NOT NULL DEFAULT 'current',
                crn TEXT NOT NULL,
                last_known_open_seats INTEGER NOT NULL DEFAULT 0,
                notified_open INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, schedule_key, crn)
            );
            """
        )
        schedule_expr = "schedule_key" if "schedule_key" in column_names else "'current'"
        self.conn.execute(
            f"""
            INSERT OR IGNORE INTO watchlist_new (
                user_id, schedule_key, crn, last_known_open_seats, notified_open
            )
            SELECT user_id, {schedule_expr}, crn, last_known_open_seats, notified_open
            FROM watchlist
            """
        )
        self.conn.execute("DROP TABLE watchlist")
        self.conn.execute("ALTER TABLE watchlist_new RENAME TO watchlist")

    def _normalize_schedule_key(self, schedule_key: str) -> str:
        key = str(schedule_key or "").strip().lower()
        return key if key in VALID_SCHEDULE_KEYS else "current"

    def upsert_profile(self, user_id: int, major: str, school: str, term: str, next_term: str = ""):
        existing = self.get_profile(user_id)
        privacy = "friends"
        requirements = ""
        active_schedule = "current"
        existing_next_term = ""

        if existing is not None:
            privacy = existing.privacy
            requirements = existing.requirements_text
            active_schedule = self._normalize_schedule_key(existing.active_schedule)
            existing_next_term = existing.next_term

        self.conn.execute(
            """
            INSERT INTO profiles (
                user_id, major, school, term, privacy, requirements_text,
                active_schedule, current_term, next_term
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                major=excluded.major,
                school=excluded.school,
                term=excluded.term,
                current_term=excluded.current_term,
                next_term=excluded.next_term
            """,
            (
                user_id,
                major,
                school,
                term,
                privacy,
                requirements,
                active_schedule,
                term,
                next_term.strip() or existing_next_term,
            ),
        )
        self.conn.commit()

    def get_active_schedule(self, user_id: int) -> str:
        profile = self.get_profile(user_id)
        if profile is None:
            return "current"
        return self._normalize_schedule_key(profile.active_schedule)

    def set_active_schedule(self, user_id: int, schedule_key: str):
        key = self._normalize_schedule_key(schedule_key)
        existing = self.get_profile(user_id)
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO profiles (
                    user_id, major, school, term, privacy, requirements_text,
                    active_schedule, current_term, next_term
                )
                VALUES (?, '', 'Virginia Tech', '', 'friends', '', ?, '', '')
                """,
                (user_id, key),
            )
        else:
            self.conn.execute("UPDATE profiles SET active_schedule=? WHERE user_id=?", (key, user_id))
        self.conn.commit()

    def active_term_for_profile(self, profile: Profile | None) -> str:
        if profile is None:
            return ""
        active = self._normalize_schedule_key(profile.active_schedule)
        if active == "next":
            next_kind = "off" if profile.next_term.strip().lower().startswith("summer") else "main"
            return choose_next_term(next_kind)
        return academic_terms_for().current

    def update_requirements(self, user_id: int, requirements_text: str):
        existing = self.get_profile(user_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO profiles (user_id, major, school, term, privacy, requirements_text, current_term) VALUES (?, '', 'Virginia Tech', '', 'friends', ?, '')",
                (user_id, requirements_text),
            )
        else:
            self.conn.execute(
                "UPDATE profiles SET requirements_text=? WHERE user_id=?",
                (requirements_text, user_id),
            )
        self.conn.commit()

    def get_profile(self, user_id: int) -> Profile | None:
        row = self.conn.execute("SELECT * FROM profiles WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        return Profile(**dict(row))

    def set_privacy(self, user_id: int, privacy: str):
        existing = self.get_profile(user_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO profiles (user_id, major, school, term, privacy, requirements_text, current_term) VALUES (?, '', 'Virginia Tech', '', ?, '', '')",
                (user_id, privacy),
            )
        else:
            self.conn.execute(
                "UPDATE profiles SET privacy=? WHERE user_id=?",
                (privacy, user_id),
            )
        self.conn.commit()

    def save_preferences(self, user_id: int, **updates: Any):
        row = self.conn.execute("SELECT 1 FROM preferences WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            self.conn.execute("INSERT INTO preferences (user_id) VALUES (?)", (user_id,))

        if updates:
            columns = ", ".join(f"{key}=?" for key in updates)
            values = list(updates.values()) + [user_id]
            self.conn.execute(f"UPDATE preferences SET {columns} WHERE user_id=?", values)
        self.conn.commit()

    def get_preferences(self, user_id: int) -> SchedulePreferences | None:
        row = self.conn.execute("SELECT * FROM preferences WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None

        values = dict(row)
        values["avoid_early"] = bool(values["avoid_early"])
        values["avoid_late"] = bool(values["avoid_late"])
        values["avoid_friday"] = bool(values["avoid_friday"])
        values["compact_days"] = bool(values["compact_days"])
        values["hard_time_window"] = bool(values["hard_time_window"])
        return SchedulePreferences(**values)

    def add_friend(self, owner_user_id: int, friend_user_id: int):
        self.conn.execute(
            "INSERT OR IGNORE INTO friends (owner_user_id, friend_user_id) VALUES (?, ?)",
            (owner_user_id, friend_user_id),
        )
        self.conn.commit()

    def remove_friend(self, owner_user_id: int, friend_user_id: int):
        self.conn.execute(
            "DELETE FROM friends WHERE owner_user_id=? AND friend_user_id=?",
            (owner_user_id, friend_user_id),
        )
        self.conn.commit()

    def is_friend(self, owner_user_id: int, friend_user_id: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM friends WHERE owner_user_id=? AND friend_user_id=?",
            (owner_user_id, friend_user_id),
        ).fetchone()
        return row is not None

    def add_class(self, entry: ClassEntry):
        schedule_key = self._normalize_schedule_key(entry.schedule_key)
        self.conn.execute(
            """
            INSERT INTO classes (
                user_id, schedule_key, crn, course_code, course_title,
                instructor, days, start_time, end_time, location, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, schedule_key, crn) DO UPDATE SET
                course_code=excluded.course_code,
                course_title=excluded.course_title,
                instructor=excluded.instructor,
                days=excluded.days,
                start_time=excluded.start_time,
                end_time=excluded.end_time,
                location=excluded.location,
                source=excluded.source
            """,
            (
                entry.user_id,
                schedule_key,
                entry.crn,
                entry.course_code,
                entry.course_title,
                entry.instructor,
                entry.days,
                entry.start_time,
                entry.end_time,
                entry.location,
                entry.source,
            ),
        )
        self.conn.commit()

    def remove_dars_classes_for_term(self, user_id: int, term_code: str, schedule_key: str = "") -> int:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        cursor = self.conn.execute(
            """
            DELETE FROM classes
            WHERE user_id=?
              AND schedule_key=?
              AND ((source='dars' AND crn LIKE ?) OR source='dars-catalog')
            """,
            (user_id, key, f"DARS-{term_code}-%"),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_class(self, user_id: int, crn: str, schedule_key: str = "") -> ClassEntry | None:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        row = self.conn.execute(
            "SELECT * FROM classes WHERE user_id=? AND schedule_key=? AND crn=?",
            (user_id, key, crn),
        ).fetchone()
        if row is None:
            return None
        return ClassEntry(**dict(row))

    def list_classes(self, user_id: int, schedule_key: str = "") -> list[ClassEntry]:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        rows = self.conn.execute(
            "SELECT * FROM classes WHERE user_id=? AND schedule_key=? ORDER BY start_time, crn",
            (user_id, key),
        ).fetchall()
        return [ClassEntry(**dict(row)) for row in rows]

    def remove_class(self, user_id: int, crn: str, schedule_key: str = "") -> bool:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        cursor = self.conn.execute(
            "DELETE FROM classes WHERE user_id=? AND schedule_key=? AND crn=?",
            (user_id, key, crn),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def clear_schedule(self, user_id: int, schedule_key: str = "") -> int:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        cursor = self.conn.execute("DELETE FROM classes WHERE user_id=? AND schedule_key=?", (user_id, key))
        self.conn.commit()
        return cursor.rowcount

    def get_server_config(self, guild_id: int) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM server_config WHERE guild_id=?", (guild_id,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO server_config (guild_id) VALUES (?)",
                (guild_id,),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM server_config WHERE guild_id=?", (guild_id,)).fetchone()
        return dict(row)

    def update_server_config(self, guild_id: int, **updates: Any):
        self.get_server_config(guild_id)
        columns = ", ".join(f"{key}=?" for key in updates)
        values = list(updates.values()) + [guild_id]
        self.conn.execute(f"UPDATE server_config SET {columns} WHERE guild_id=?", values)
        self.conn.commit()

    def add_watch(self, user_id: int, crn: str, open_seats: int, schedule_key: str = ""):
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        self.conn.execute(
            """
            INSERT INTO watchlist (user_id, schedule_key, crn, last_known_open_seats, notified_open)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, schedule_key, crn) DO UPDATE SET
                last_known_open_seats=excluded.last_known_open_seats
            """,
            (user_id, key, crn, open_seats, 1 if open_seats > 0 else 0),
        )
        self.conn.commit()

    def remove_watch(self, user_id: int, crn: str, schedule_key: str = "") -> bool:
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        cursor = self.conn.execute(
            "DELETE FROM watchlist WHERE user_id=? AND schedule_key=? AND crn=?",
            (user_id, key, crn),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def list_watches(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT user_id, schedule_key, crn, last_known_open_seats, notified_open FROM watchlist ORDER BY schedule_key, crn"
        ).fetchall()

    def update_watch_state(self, user_id: int, crn: str, open_seats: int, notified_open: int, schedule_key: str = ""):
        key = self._normalize_schedule_key(schedule_key or self.get_active_schedule(user_id))
        self.conn.execute(
            "UPDATE watchlist SET last_known_open_seats=?, notified_open=? WHERE user_id=? AND schedule_key=? AND crn=?",
            (open_seats, notified_open, user_id, key, crn),
        )
        self.conn.commit()

    def cache_professor_rating(self, rating: ProfessorRating):
        cache_key = self._professor_cache_key(rating.professor_name, rating.school_name)
        self.conn.execute(
            """
            INSERT INTO professor_cache (
                cache_key, professor_name, school_name, school_id,
                avg_rating, avg_difficulty, num_ratings, would_take_again, raw_json, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(cache_key) DO UPDATE SET
                school_id=excluded.school_id,
                avg_rating=excluded.avg_rating,
                avg_difficulty=excluded.avg_difficulty,
                num_ratings=excluded.num_ratings,
                would_take_again=excluded.would_take_again,
                raw_json=excluded.raw_json,
                fetched_at=CURRENT_TIMESTAMP
            """,
            (
                cache_key,
                rating.professor_name,
                rating.school_name,
                rating.school_id,
                rating.avg_rating,
                rating.avg_difficulty,
                rating.num_ratings,
                rating.would_take_again,
                rating.raw_json,
            ),
        )
        self.conn.commit()

    def get_professor_rating(self, professor_name: str, school_name: str) -> ProfessorRating | None:
        cache_key = self._professor_cache_key(professor_name, school_name)
        row = self.conn.execute(
            "SELECT * FROM professor_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None

        values = dict(row)
        values.pop("cache_key", None)
        values.pop("fetched_at", None)
        return ProfessorRating(**values)

    def replace_grade_cache(self, items: list[GradeStat]):
        self.conn.execute("DELETE FROM grade_cache")
        for item in items:
            self.conn.execute(
                """
                INSERT INTO grade_cache (
                    cache_key, course_code, title, instructor, academic_year, term,
                    gpa, a_pct, a_minus_pct, b_plus_pct, b_pct, raw_json, fetched_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self._grade_cache_key(item.course_code, item.instructor, item.academic_year, item.term, item.gpa),
                    item.course_code,
                    item.title,
                    item.instructor,
                    item.academic_year,
                    item.term,
                    item.gpa,
                    item.a_pct,
                    item.a_minus_pct,
                    item.b_plus_pct,
                    item.b_pct,
                    item.raw_json,
                ),
            )
        self.conn.commit()

    def list_grade_stats(self, course_code: str, instructor: str = "") -> list[GradeStat]:
        course_key = course_code.replace(" ", "").upper()
        params: list[Any] = [course_key]
        query = "SELECT * FROM grade_cache WHERE REPLACE(UPPER(course_code), ' ', '') = ?"

        if instructor.strip():
            query += " AND LOWER(instructor) LIKE ?"
            params.append(f"%{instructor.strip().lower()}%")

        rows = self.conn.execute(query, params).fetchall()
        result: list[GradeStat] = []
        for row in rows:
            values = dict(row)
            values.pop("cache_key", None)
            values.pop("fetched_at", None)
            result.append(GradeStat(**values))
        return result

    def get_grade_summary(self, course_code: str, instructor: str = "") -> GradeStat | None:
        rows = self.list_grade_stats(course_code, instructor)
        if not rows and instructor:
            rows = self.list_grade_stats(course_code)
        if not rows:
            return None

        def avg(values: list[float | None]) -> float | None:
            numbers = [value for value in values if value is not None]
            if not numbers:
                return None
            return round(sum(numbers) / len(numbers), 2)

        return GradeStat(
            course_code=rows[0].course_code,
            title=rows[0].title,
            instructor=instructor or rows[0].instructor,
            academic_year="mixed",
            term="mixed",
            gpa=round(sum(row.gpa for row in rows) / len(rows), 2),
            a_pct=avg([row.a_pct for row in rows]),
            a_minus_pct=avg([row.a_minus_pct for row in rows]),
            b_plus_pct=avg([row.b_plus_pct for row in rows]),
            b_pct=avg([row.b_pct for row in rows]),
            raw_json=json.dumps([row.raw_json for row in rows if row.raw_json]),
        )

    def _professor_cache_key(self, professor_name: str, school_name: str) -> str:
        normalized_professor = " ".join(professor_name.lower().split())
        normalized_school = " ".join(school_name.lower().split())
        return f"{normalized_school}::{normalized_professor}"

    def _grade_cache_key(self, course_code: str, instructor: str, academic_year: str, term: str, gpa: float) -> str:
        normalized_code = course_code.replace(" ", "").upper()
        normalized_instructor = " ".join(instructor.lower().split())
        return f"{normalized_code}::{normalized_instructor}::{academic_year}::{term}::{gpa}"
