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


@bot.event
async def on_ready() -> None:
    LOGGER.info("Logged in as %s", bot.user)
    if settings.guild_id:
        guild = discord.Object(id=settings.guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        LOGGER.info("Synced %s guild command(s)", len(synced))
    else:
        synced = await bot.tree.sync()
        LOGGER.info("Synced %s global command(s)", len(synced))

    if not watch_poll_loop.is_running():
        watch_poll_loop.start()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    message = str(error)
    if not interaction.response.is_done():
        await interaction.response.send_message(text_block("Error", [message]), ephemeral=True)
    else:
        await interaction.followup.send(text_block("Error", [message]), ephemeral=True)


@bot.tree.command(description="Create or update your academic profile.")
@app_commands.describe(major="Example: CS", school="Example: Virginia Tech", term="Example: Fall 2026")
async def profile(interaction: discord.Interaction, major: str, school: str, term: str) -> None:
    db.upsert_profile(interaction.user.id, major.strip(), school.strip(), term.strip())
    await interaction.response.send_message(
        text_block(
            "Profile saved",
            [
                f"Major: {major.strip()}",
                f"School: {school.strip()}",
                f"Term: {term.strip()}",
            ],
        )
    )


@bot.tree.command(description="Add a class to your profile by CRN using the catalog source.")
@app_commands.describe(crn="Course reference number")
async def addclass(interaction: discord.Interaction, crn: str) -> None:
    config = server_config(interaction.guild_id)
    if not config["enable_catalog"]:
        await interaction.response.send_message(text_block("Add class", ["Catalog source is disabled by admin settings."]))
        return

    if db.get_class(interaction.user.id, crn):
        await interaction.response.send_message(text_block("Add class", [f"CRN {crn} is already in your schedule."]))
        return

    course = await provider.get_course_by_crn(crn)
    if course is None:
        await interaction.response.send_message(text_block("Add class", [f"CRN {crn} is invalid or unavailable."]))
        return

    entry = build_entry_from_course(interaction.user.id, course)
    schedule_service.add_or_replace_class(entry)
    await interaction.response.send_message(
        text_block(
            "Class added successfully",
            [
                f"CRN: {entry.crn}",
                f"Course: {entry.course_code} - {entry.course_title}",
                f"Instructor: {entry.instructor}",
                f"Days: {entry.days}",
                f"Time: {entry.start_time}-{entry.end_time}",
                f"Location: {entry.location or '-'}",
                "Use /myschedule to view your saved timetable.",
            ],
        )
    )
