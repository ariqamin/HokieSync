from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(slots=True)
class Settings:
    discord_token: str
    guild_id: int | None
    database_path: Path
    poll_interval_seconds: int
    catalog_provider: str
    vt_term_year: str
    vt_preferred_term: str
    rmp_provider: str
    rmp_school_name: str
    rmp_school_id: str
    rmp_graphql_url: str
    rmp_auth_token: str
    grades_provider: str
    grades_csv_path: Path | None
    grades_json_path: Path | None
    grades_request_url: str
    grades_headers: dict[str, Any]
    grades_cookies: dict[str, Any]
    mock_catalog_path: Path


def _json_env(name: str) -> dict[str, Any]:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return {}

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return {}

    if isinstance(parsed, dict):
        return parsed
    return {}


def _path_env(name: str) -> Path | None:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return None
    return Path(raw_value)



def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_value = os.getenv("DISCORD_GUILD_ID", "").strip()
    database_path = Path(os.getenv("DATABASE_PATH", "data/kit_bot.db").strip())
    poll_seconds = max(15, int(os.getenv("POLL_INTERVAL_SECONDS", "60")))
    grades_provider = os.getenv("GRADES_PROVIDER", "auto").strip().lower()
    grades_csv_path = _path_env("GRADES_CSV_PATH")
    if grades_provider == "auto" and grades_csv_path is None:
        default_grades_csv = Path("data/imports/udc_grades.csv")
        if default_grades_csv.exists():
            grades_csv_path = default_grades_csv

    return Settings(
        discord_token=token,
        guild_id=int(guild_value) if guild_value else None,
        database_path=database_path,
        poll_interval_seconds=poll_seconds,
        catalog_provider=os.getenv("CATALOG_PROVIDER", "auto").strip().lower(),
        vt_term_year=os.getenv("VT_TERM_YEAR", "").strip(),
        vt_preferred_term=os.getenv("VT_PREFERRED_TERM", "").strip(),
        rmp_provider=os.getenv("RMP_PROVIDER", "auto").strip().lower(),
        rmp_school_name=os.getenv("RMP_SCHOOL_NAME", "Virginia Tech").strip(),
        rmp_school_id=os.getenv("RMP_SCHOOL_ID", "").strip(),
        rmp_graphql_url=os.getenv("RMP_GRAPHQL_URL", "https://www.ratemyprofessors.com/graphql").strip(),
        rmp_auth_token=os.getenv("RMP_AUTH_TOKEN", "dGVzdDp0ZXN0").strip(),
        grades_provider=grades_provider,
        grades_csv_path=grades_csv_path,
        grades_json_path=_path_env("GRADES_JSON_PATH"),
        grades_request_url=os.getenv("GRADES_REQUEST_URL", "").strip(),
        grades_headers=_json_env("GRADES_HEADERS_JSON"),
        grades_cookies=_json_env("GRADES_COOKIES_JSON"),
        mock_catalog_path=Path(os.getenv("MOCK_CATALOG_PATH", "data/sample_catalog.json").strip()),
    )
