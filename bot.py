from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import load_settings
from src.db import Database
from src.models import ClassEntry, VALID_PRIVACY
from src.providers.mock_data import MockDataProvider
from src.services.free_time_service import FreeTimeService
from src.services.privacy_service import PrivacyService
from src.services.schedule_service import ScheduleService
from src.services.watch_service import WatchService
from src.utils.formatters import format_free_time, format_schedule, format_status, text_block
from src.utils.time_utils import normalize_days

# AI was used to brainstorm and determine which libraries would be most helpful for this project

# ChatGPT recommended to use a logger and a database to manage the various services of the bot

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("kit_bot")

settings = load_settings()
db = Database(settings.database_path)
provider = MockDataProvider(Path("data/sample_catalog.json"))
privacy_service = PrivacyService(db)
schedule_service = ScheduleService(db)
free_time_service = FreeTimeService(db)
watch_service = WatchService(db, provider)

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def build_entry_from_course(user_id: int, course) -> ClassEntry:
    return ClassEntry(
        user_id=user_id,
        crn=course.crn,
        course_code=course.course_code,
        course_title=course.title,
        instructor=course.instructor,
        days=normalize_days(course.days),
        start_time=course.start_time,
        end_time=course.end_time,
        location=course.location,
        source="catalog",
    )


def server_config(guild_id: int | None) -> dict:
    if guild_id is None:
        return {
            "enable_catalog": 1,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "last_catalog_refresh": "Never",
            "last_error": "None",
        }
    return db.get_server_config(guild_id)

# AI was used to account for any edge cases and exceptions for the various functions below
async def safe_send_dm(user_id: int, message: str) -> bool:
    user = await bot.fetch_user(user_id)
    try:
        await user.send(message)
        return True
    except discord.Forbidden:
        return False


class PrivacyTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        lowered = value.lower().strip()
        if lowered not in VALID_PRIVACY:
            raise app_commands.AppCommandError("Privacy must be one of: public, friends, private.")
        return lowered
