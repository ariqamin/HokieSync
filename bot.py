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


@bot.tree.command(description="Edit meeting days and time for a class already in your schedule.")
async def editclass(
    interaction: discord.Interaction,
    crn: str,
    days: str,
    start_time: str,
    end_time: str,
    location: str = "",
) -> None:
    updated = schedule_service.edit_class(
        interaction.user.id,
        crn,
        days,
        start_time,
        end_time,
        location,
    )
    if updated is None:
        await interaction.response.send_message(text_block("Edit class", [f"CRN {crn} is not currently in your schedule."]))
        return
    await interaction.response.send_message(
        text_block(
            "Class updated",
            [
                f"CRN: {updated.crn}",
                f"Days: {updated.days}",
                f"Time: {updated.start_time}-{updated.end_time}",
                f"Location: {updated.location or '-'}",
            ],
        )
    )


@bot.tree.command(description="Remove a class from your schedule.")
async def removeclass(interaction: discord.Interaction, crn: str) -> None:
    removed = db.remove_class(interaction.user.id, crn)
    message = f"CRN {crn} was removed from your schedule." if removed else f"CRN {crn} is not currently listed in your schedule."
    await interaction.response.send_message(text_block("Remove class", [message]))


@bot.tree.command(description="View your saved schedule.")
async def myschedule(interaction: discord.Interaction) -> None:
    profile_data = db.get_profile(interaction.user.id)
    privacy = profile_data.privacy if profile_data else "friends"
    classes = db.list_classes(interaction.user.id)
    await interaction.response.send_message(format_schedule(interaction.user.display_name, classes, privacy))


@bot.tree.command(description="View another user's schedule if their privacy settings allow it.")
async def schedule(interaction: discord.Interaction, user: discord.Member) -> None:
    if not privacy_service.can_view_schedule(user.id, interaction.user.id):
        await interaction.response.send_message(text_block("Schedule", ["Not permitted."]))
        return
    profile_data = db.get_profile(user.id)
    privacy = profile_data.privacy if profile_data else "friends"
    classes = db.list_classes(user.id)
    await interaction.response.send_message(format_schedule(user.display_name, classes, privacy))


@bot.tree.command(description="Change your schedule privacy setting.")
async def privacy(interaction: discord.Interaction, setting: app_commands.Transform[str, PrivacyTransformer]) -> None:
    db.set_privacy(interaction.user.id, setting)
    await interaction.response.send_message(text_block("Privacy updated", [f"New setting: {setting}"]))


@bot.tree.command(description="Add a user to your schedule friends list.")
async def addfriend(interaction: discord.Interaction, user: discord.Member) -> None:
    if user.id == interaction.user.id:
        await interaction.response.send_message(text_block("Friends", ["You do not need to add yourself."]))
        return
    db.add_friend(interaction.user.id, user.id)
    await interaction.response.send_message(text_block("Friends", [f"{user.display_name} can now view your schedule when privacy is set to friends."]))


@bot.tree.command(description="Remove a user from your schedule friends list.")
async def removefriend(interaction: discord.Interaction, user: discord.Member) -> None:
    db.remove_friend(interaction.user.id, user.id)
    await interaction.response.send_message(text_block("Friends", [f"{user.display_name} was removed from your schedule friends list."]))

#
@bot.tree.command(description="Compute overlapping free time for up to three users.")
@app_commands.describe(
    user1="First user",
    user2="Second user",
    user3="Third user",
    start_time="24-hour HH:MM, default 09:00",
    end_time="24-hour HH:MM, default 18:00",
    weekdays_only="True to check Monday-Friday only",
    include_me="Include your own schedule in the overlap",
)
async def free(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: discord.Member | None = None,
    user3: discord.Member | None = None,
    start_time: str = "09:00",
    end_time: str = "18:00",
    weekdays_only: bool = True,
    include_me: bool = True,
) -> None:
    requested_users = [member for member in [user1, user2, user3] if member is not None]
    if include_me and all(member.id != interaction.user.id for member in requested_users):
        requested_users.insert(0, interaction.user)

    accessible_ids: list[int] = []
    excluded: list[str] = []
    for member in requested_users:
        if not privacy_service.can_view_schedule(member.id, interaction.user.id):
            excluded.append(f"{member.display_name}: not permitted")
            continue
        if not db.list_classes(member.id):
            excluded.append(f"{member.display_name}: no saved schedule")
            continue
        accessible_ids.append(member.id)

    if not accessible_ids:
        await interaction.response.send_message(text_block("Shared free time", ["No accessible schedules were available."]))
        return

    windows = free_time_service.compute(accessible_ids, start_time, end_time, weekdays_only)
    names = ", ".join(member.display_name for member in requested_users if member.id in accessible_ids)
    await interaction.response.send_message(format_free_time(f"Shared free time for {names}", windows, excluded))


@bot.tree.command(description="Watch a class and get a DM when a seat opens.")
async def watchclass(interaction: discord.Interaction, crn: str) -> None:
    success, message = await watch_service.add_watch(interaction.user.id, crn)
    await interaction.response.send_message(text_block("Watch class", [message]))
    if success:
        await safe_send_dm(interaction.user.id, text_block("Watch started", [message]))


@bot.tree.command(description="Remove a class from your watchlist.")
async def unwatchclass(interaction: discord.Interaction, crn: str) -> None:
    _, message = watch_service.remove_watch(interaction.user.id, crn)
    await interaction.response.send_message(text_block("Unwatch class", [message]))


@bot.tree.command(description="Admin command to configure catalog access and polling.")
@app_commands.checks.has_permissions(administrator=True)
async def config(
    interaction: discord.Interaction,
    enable_catalog: bool = True,
    poll_interval_seconds: app_commands.Range[int, 15, 3600] = 60,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(text_block("Config", ["This command must be used in a server."]))
        return
    db.update_server_config(
        interaction.guild_id,
        enable_catalog=int(enable_catalog),
        poll_interval_seconds=int(poll_interval_seconds),
    )
    watch_poll_loop.change_interval(seconds=poll_interval_seconds)
    await interaction.response.send_message(
        text_block(
            "Config updated",
            [
                f"Catalog source: {'on' if enable_catalog else 'off'}",
                f"Polling interval: {poll_interval_seconds} seconds",
            ],
        )
    )


@bot.tree.command(description="Admin command to view bot health and last refresh state.")
@app_commands.checks.has_permissions(administrator=True)
async def status(interaction: discord.Interaction) -> None:
    config = server_config(interaction.guild_id)
    lines = [
        "Health: operational",
        f"Catalog source enabled: {'yes' if config['enable_catalog'] else 'no'}",
        f"Polling interval: {config['poll_interval_seconds']} seconds",
        f"Catalog refresh: {config['last_catalog_refresh']}",
        f"Last error: {config['last_error']}",
    ]
    await interaction.response.send_message(format_status(lines))


@bot.tree.command(description="Admin helper to simulate seat openings in the mock catalog.")
@app_commands.checks.has_permissions(administrator=True)
async def simulateseats(interaction: discord.Interaction, crn: str, open_seats: app_commands.Range[int, 0, 999]) -> None:
    updated = await provider.set_open_seats(crn, open_seats)
    if not updated:
        await interaction.response.send_message(text_block("Simulate seats", [f"CRN {crn} was not found in the mock catalog."]))
        return
    await interaction.response.send_message(text_block("Simulate seats", [f"CRN {crn} now has {open_seats} open seats in the mock catalog."]))


@bot.tree.command(name="help", description="Show the bot commands and what they do.")
async def helpbot(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        text_block(
            "KIT bot help",
            [
                "/profile major school term - create or update your profile",
                "/addclass crn - add a class by CRN",
                "/editclass - edit the saved days and times for a class",
                "/removeclass crn - delete a class",
                "/myschedule - view your own schedule",
                "/schedule @user - view someone else's schedule if permitted",
                "/privacy public|friends|private - change who can see your schedule",
                "/addfriend and /removefriend - manage your friends-only list",
                "/free - compute mutual free time across users",
                "/watchclass crn and /unwatchclass crn - manage class alerts",
                "/config, /status, /simulateseats - admin commands",
            ],
        )
    )

# Chatgpt was used to write most of the logic for this code
@tasks.loop(seconds=settings.poll_interval_seconds)
async def watch_poll_loop() -> None:
    await bot.wait_until_ready()
    watches = db.list_watches()
    guild_id = settings.guild_id or next((guild.id for guild in bot.guilds), None)
    config = server_config(guild_id)
    watch_poll_loop.change_interval(seconds=int(config["poll_interval_seconds"]))

    try:
        await provider.refresh()
        if guild_id is not None:
            db.update_server_config(
                guild_id,
                last_catalog_refresh="Success",
                last_error="None",
            )

        for watch in watches:
            crn = watch["crn"]
            open_seats = await provider.get_open_seats(crn)
            if open_seats is None:
                continue
            notified_open = int(watch["notified_open"])
            last_known = int(watch["last_known_open_seats"])
            if open_seats > 0 and (notified_open == 0 or last_known == 0):
                dm_ok = await safe_send_dm(
                    int(watch["user_id"]),
                    text_block(
                        "Seat alert",
                        [
                            f"CRN {crn} now has {open_seats} open seat(s).",
                            "Use your registration portal quickly if you want the seat.",
                        ],
                    ),
                )
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, 1 if dm_ok else notified_open)
            elif open_seats == 0:
                db.update_watch_state(int(watch["user_id"]), crn, 0, 0)
            else:
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, notified_open)
    except Exception as exc:
        LOGGER.exception("Watch poll failed: %s", exc)
        if guild_id is not None:
            db.update_server_config(guild_id, last_error=str(exc))


def main() -> None:
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in first.")
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
