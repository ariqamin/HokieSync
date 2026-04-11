from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv



@dataclass(slots = True)
class Settings:
    discord_token: str
    guild_id: int | None
    database_path: Path
    poll_interval_seconds: int


def load_settings() -> Settings:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_value = os.getenv("DISCORD_GUILD_ID", "").strip()
    db_value = os.getenv("DATABASE_PATH", "data/kit_bot.db").strip()
    poll_seconds = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))

    return Settings(
        discord_token = token,
        guild_id = int(guild_value) if guild_value else None,
        database_path = Path(db_value),
        poll_interval_seconds = max(15, poll_seconds),
    )
