from __future__ import annotations

import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.config import load_settings
from src.db import Database
from src.models import ClassEntry, VALID_PRIVACY, VALID_RECOMMENDATION_MODES
from src.providers.composite_provider import CompositeProvider
from src.providers.grade_provider import GradeProvider
from src.providers.mock_data import MockDataProvider
from src.providers.rmp_provider import RMPProvider
from src.providers.vt_catalog import VTCatalogProvider
from src.services.free_time_service import FreeTimeService
from src.services.preference_service import PreferenceService
from src.services.privacy_service import PrivacyService
from src.services.recommendation_service import RecommendationService
from src.services.schedule_service import ScheduleService
from src.services.watch_service import WatchService
from src.utils.formatters import (
    format_free_time,
    format_preferences,
    format_recommendations,
    format_schedule,
    format_schedule_plans,
    format_status,
    text_block,
)
from src.utils.time_utils import normalize_days


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("kit_bot")

settings = load_settings()
db = Database(settings.database_path)
mock_provider = MockDataProvider(settings.mock_catalog_path)

catalog_provider = None
if settings.catalog_provider in {"auto", "pyvt"}:
    vt_provider = VTCatalogProvider(settings.vt_preferred_term, settings.vt_term_year)
    if settings.catalog_provider == "pyvt" or vt_provider.available:
        catalog_provider = vt_provider

rmp_provider = None
if settings.rmp_provider != "none":
    rmp_provider = RMPProvider(
        db=db,
        graphql_url=settings.rmp_graphql_url,
        auth_token=settings.rmp_auth_token,
        school_name=settings.rmp_school_name,
        school_id=settings.rmp_school_id,
    )

grade_provider = None
if settings.grades_provider != "none":
    grade_provider = GradeProvider(
        db=db,
        csv_path=settings.grades_csv_path,
        json_path=settings.grades_json_path,
        request_url=settings.grades_request_url,
        headers=settings.grades_headers,
        cookies=settings.grades_cookies,
    )

provider = CompositeProvider(
    catalog_provider=catalog_provider,
    rmp_provider=rmp_provider,
    grade_provider=grade_provider,
    mock_provider=mock_provider,
)

privacy_service = PrivacyService(db)
schedule_service = ScheduleService(db)
free_time_service = FreeTimeService(db)
watch_service = WatchService(db, provider)
recommendation_service = RecommendationService(db, provider)
preference_service = PreferenceService()

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
        source=course.source or "catalog",
    )


def server_config(guild_id: int | None) -> dict:
    if guild_id is None:
        return {
            "enable_catalog": 1,
            "enable_rmp": 1,
            "enable_grades": 1,
            "poll_interval_seconds": settings.poll_interval_seconds,
            "last_catalog_refresh": "Never",
            "last_rmp_refresh": "Never",
            "last_grades_refresh": "Never",
            "last_error": "None",
        }

    return db.get_server_config(guild_id)


async def safe_send_dm(user_id: int, message: str) -> bool:
    try:
        user = await bot.fetch_user(user_id)
        await user.send(message)
        return True
    except (discord.Forbidden, discord.NotFound):
        return False


class PrivacyTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        lowered = value.lower().strip()
        if lowered not in VALID_PRIVACY:
            raise app_commands.AppCommandError("Privacy must be one of: public, friends, private.")
        return lowered


class RecommendModeTransformer(app_commands.Transformer):
    async def transform(self, interaction: discord.Interaction, value: str) -> str:
        lowered = value.lower().strip()
        if lowered not in VALID_RECOMMENDATION_MODES:
            raise app_commands.AppCommandError("Mode must be one of: balanced, easy, professor.")
        return lowered


def source_status_lines() -> list[str]:
    lines = []

    if catalog_provider is None:
        lines.append("Catalog provider: mock fallback only")
    else:
        lines.append(f"Catalog provider: {catalog_provider.source_name}")
        lines.append(f"Catalog refresh: {catalog_provider.last_refresh}")
        lines.append(f"Catalog error: {catalog_provider.last_error}")

    if rmp_provider is None:
        lines.append("RMP provider: disabled")
    else:
        school_id = rmp_provider.school_id or "not resolved yet"
        lines.append(f"RMP provider: graphql ({settings.rmp_school_name}, school id {school_id})")
        lines.append(f"RMP refresh: {rmp_provider.last_refresh}")
        lines.append(f"RMP error: {rmp_provider.last_error}")

    if grade_provider is None:
        lines.append("Grades provider: disabled")
    else:
        grade_mode = "none"
        if grade_provider.csv_path is not None:
            grade_mode = f"csv ({grade_provider.csv_path})"
        elif grade_provider.json_path is not None:
            grade_mode = f"json ({grade_provider.json_path})"
        elif grade_provider.request_url:
            grade_mode = "request"
        lines.append(f"Grades provider: {grade_mode}")
        lines.append(f"Grades refresh: {grade_provider.last_refresh}")
        lines.append(f"Grades error: {grade_provider.last_error}")

    return lines


@bot.event
async def on_ready():
    LOGGER.info("Logged in as %s", bot.user)

    if settings.guild_id:
        guild = discord.Object(id=settings.guild_id)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        LOGGER.info("Synced %s guild command(s)", len(synced))
    else:
        synced = await bot.tree.sync()
        LOGGER.info("Synced %s global command(s)", len(synced))

    await provider.refresh()

    if not watch_poll_loop.is_running():
        watch_poll_loop.start()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    message = str(error)
    if not interaction.response.is_done():
        await interaction.response.send_message(text_block("Error", [message]), ephemeral=True)
        return

    await interaction.followup.send(text_block("Error", [message]), ephemeral=True)


@bot.tree.command(description="Create or update your academic profile.")
@app_commands.describe(major="Example: CS", school="Example: Virginia Tech", term="Example: Fall 2026")
async def profile(interaction: discord.Interaction, major: str, school: str, term: str):
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


@bot.tree.command(description="Save optional requirement keywords for recommendations.")
@app_commands.describe(requirements="Comma-separated tags like core, systems, math, elective")
async def setrequirements(interaction: discord.Interaction, requirements: str):
    db.update_requirements(interaction.user.id, requirements.strip())
    await interaction.response.send_message(text_block("Requirements saved", [requirements.strip() or "None"]))


@bot.tree.command(description="Describe your ideal schedule in natural language.")
@app_commands.describe(description="Example: no classes before 10am, done by 3pm, avoid Friday, compact schedule")
async def describeprefs(interaction: discord.Interaction, description: str):
    preferences = preference_service.parse_description(interaction.user.id, description)
    db.save_preferences(
        interaction.user.id,
        raw_text=preferences.raw_text,
        preferred_start=preferences.preferred_start,
        preferred_end=preferences.preferred_end,
        avoid_early=int(preferences.avoid_early),
        avoid_late=int(preferences.avoid_late),
        avoid_friday=int(preferences.avoid_friday),
        compact_days=int(preferences.compact_days),
        max_days=preferences.max_days,
        breaks_preference=preferences.breaks_preference,
        notes=preferences.notes,
    )
    await interaction.response.send_message(format_preferences(db.get_preferences(interaction.user.id)))


@bot.tree.command(description="View the schedule preferences you have saved.")
async def myprefs(interaction: discord.Interaction):
    preferences = db.get_preferences(interaction.user.id)
    await interaction.response.send_message(format_preferences(preferences))


@bot.tree.command(description="Add a class to your profile by CRN using the catalog source.")
@app_commands.describe(crn="Course reference number")
async def addclass(interaction: discord.Interaction, crn: str):
    config = server_config(interaction.guild_id)
    if not config["enable_catalog"]:
        await interaction.response.send_message(text_block("Add class", ["Catalog source is disabled by admin settings."]))
        return

    if db.get_class(interaction.user.id, crn):
        await interaction.response.send_message(text_block("Add class", [f"CRN {crn} is already in your schedule."]))
        return

    profile_data = db.get_profile(interaction.user.id)
    school = "Virginia Tech"
    term = settings.vt_preferred_term
    if profile_data is not None:
        if profile_data.school:
            school = profile_data.school
        if profile_data.term:
            term = profile_data.term

    course = await provider.get_course_by_crn(crn, school=school, term=term)
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
                f"Source: {entry.source}",
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
):
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
async def removeclass(interaction: discord.Interaction, crn: str):
    removed = db.remove_class(interaction.user.id, crn)
    if removed:
        message = f"CRN {crn} was removed from your schedule."
    else:
        message = f"CRN {crn} is not currently listed in your schedule."

    await interaction.response.send_message(text_block("Remove class", [message]))


@bot.tree.command(description="View your saved schedule.")
async def myschedule(interaction: discord.Interaction):
    profile_data = db.get_profile(interaction.user.id)
    privacy = "friends"
    if profile_data is not None:
        privacy = profile_data.privacy

    classes = db.list_classes(interaction.user.id)
    await interaction.response.send_message(format_schedule(interaction.user.display_name, classes, privacy))


@bot.tree.command(description="View another user's schedule if their privacy settings allow it.")
async def schedule(interaction: discord.Interaction, user: discord.Member):
    if not privacy_service.can_view_schedule(user.id, interaction.user.id):
        await interaction.response.send_message(text_block("Schedule", ["Not permitted."]))
        return

    profile_data = db.get_profile(user.id)
    privacy = "friends"
    if profile_data is not None:
        privacy = profile_data.privacy

    classes = db.list_classes(user.id)
    await interaction.response.send_message(format_schedule(user.display_name, classes, privacy))


@bot.tree.command(description="Change your schedule privacy setting.")
async def privacy(interaction: discord.Interaction, setting: app_commands.Transform[str, PrivacyTransformer]):
    db.set_privacy(interaction.user.id, setting)
    await interaction.response.send_message(text_block("Privacy updated", [f"New setting: {setting}"]))


@bot.tree.command(description="Add a user to your schedule friends list.")
async def addfriend(interaction: discord.Interaction, user: discord.Member):
    if user.id == interaction.user.id:
        await interaction.response.send_message(text_block("Friends", ["You do not need to add yourself."]))
        return

    db.add_friend(interaction.user.id, user.id)
    await interaction.response.send_message(
        text_block(
            "Friends",
            [f"{user.display_name} can now view your schedule when privacy is set to friends."],
        )
    )


@bot.tree.command(description="Remove a user from your schedule friends list.")
async def removefriend(interaction: discord.Interaction, user: discord.Member):
    db.remove_friend(interaction.user.id, user.id)
    await interaction.response.send_message(
        text_block(
            "Friends",
            [f"{user.display_name} was removed from your schedule friends list."],
        )
    )


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
):
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


@bot.tree.command(description="Recommend courses using your profile and ranking mode.")
async def recommend(
    interaction: discord.Interaction,
    mode: app_commands.Transform[str, RecommendModeTransformer] = "balanced",
):
    config = server_config(interaction.guild_id)
    recommendations = await recommendation_service.recommend(
        interaction.user.id,
        mode,
        enable_rmp=bool(config["enable_rmp"]),
        enable_grades=bool(config["enable_grades"]),
    )

    profile_data = db.get_profile(interaction.user.id)
    profile_label = "No profile"
    if profile_data is not None:
        profile_label = f"{profile_data.major} | {profile_data.school} | {profile_data.term}"

    warning_lines: list[str] = []
    if not config["enable_rmp"]:
        warning_lines.append("Warning: professor-rating source is disabled, so neutral defaults were used.")
    if not config["enable_grades"]:
        warning_lines.append("Warning: grade-history source is disabled, so neutral defaults were used.")

    message = format_recommendations(profile_label, mode, recommendations)
    if warning_lines:
        message += "\n" + text_block("Source warnings", warning_lines)
    await interaction.response.send_message(message)


@bot.tree.command(description="Build full schedule options from the recommendation list.")
async def recommendschedule(
    interaction: discord.Interaction,
    mode: app_commands.Transform[str, RecommendModeTransformer] = "balanced",
):
    config = server_config(interaction.guild_id)
    plans = await recommendation_service.recommend_schedules(
        interaction.user.id,
        mode,
        enable_rmp=bool(config["enable_rmp"]),
        enable_grades=bool(config["enable_grades"]),
    )

    profile_data = db.get_profile(interaction.user.id)
    profile_label = "No profile"
    if profile_data is not None:
        profile_label = f"{profile_data.major} | {profile_data.school} | {profile_data.term}"

    await interaction.response.send_message(format_schedule_plans(profile_label, mode, plans))


@bot.tree.command(description="Explain why a recommendation was produced.")
async def whyrecommend(interaction: discord.Interaction, rank: int):
    item = db.get_recommendation(interaction.user.id, rank)
    if item is None:
        await interaction.response.send_message(
            text_block(
                "Why this?",
                ["No cached recommendation found for that rank. Run /recommend first."],
            )
        )
        return

    await interaction.response.send_message(
        text_block(
            f"Why recommendation #{rank}",
            [
                f"Course: {item['course_code']} - {item['title']}",
                item["explanation"],
            ],
        )
    )


@bot.tree.command(description="Watch a class and get a DM when a seat opens.")
async def watchclass(interaction: discord.Interaction, crn: str):
    success, message = await watch_service.add_watch(interaction.user.id, crn)
    await interaction.response.send_message(text_block("Watch class", [message]))
    if success:
        await safe_send_dm(interaction.user.id, text_block("Watch started", [message]))


@bot.tree.command(description="Remove a class from your watchlist.")
async def unwatchclass(interaction: discord.Interaction, crn: str):
    _, message = watch_service.remove_watch(interaction.user.id, crn)
    await interaction.response.send_message(text_block("Unwatch class", [message]))


@bot.tree.command(description="Upload a UDC grade export as CSV or JSON.")
@app_commands.checks.has_permissions(administrator=True)
async def uploadgrades(interaction: discord.Interaction, file: discord.Attachment):
    if grade_provider is None:
        await interaction.response.send_message(text_block("Upload grades", ["Grade provider is disabled in this build."]))
        return

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".csv", ".json"}:
        await interaction.response.send_message(text_block("Upload grades", ["Please upload a CSV or JSON file."]))
        return

    uploads_dir = Path("data/imports")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    destination = uploads_dir / file.filename
    await file.save(destination)

    if suffix == ".csv":
        grade_provider.csv_path = destination
        grade_provider.json_path = None
    else:
        grade_provider.json_path = destination
        grade_provider.csv_path = None

    await grade_provider.refresh()
    await interaction.response.send_message(
        text_block(
            "Upload grades",
            [
                f"Saved: {destination}",
                f"Refresh result: {grade_provider.last_refresh}",
                f"Last error: {grade_provider.last_error}",
            ],
        )
    )


@bot.tree.command(description="Admin command to re-run all data source refresh steps.")
@app_commands.checks.has_permissions(administrator=True)
async def refreshsources(interaction: discord.Interaction):
    await provider.refresh()
    await interaction.response.send_message(format_status(source_status_lines()))


@bot.tree.command(description="Admin command to enable or disable data sources and polling.")
@app_commands.checks.has_permissions(administrator=True)
async def config(
    interaction: discord.Interaction,
    enable_catalog: bool = True,
    enable_rmp: bool = True,
    enable_grades: bool = True,
    poll_interval_seconds: app_commands.Range[int, 15, 3600] = 60,
):
    if interaction.guild_id is None:
        await interaction.response.send_message(text_block("Config", ["This command must be used in a server."]))
        return

    db.update_server_config(
        interaction.guild_id,
        enable_catalog=int(enable_catalog),
        enable_rmp=int(enable_rmp),
        enable_grades=int(enable_grades),
        poll_interval_seconds=int(poll_interval_seconds),
    )
    watch_poll_loop.change_interval(seconds=poll_interval_seconds)
    await interaction.response.send_message(
        text_block(
            "Config updated",
            [
                f"Catalog source: {'on' if enable_catalog else 'off'}",
                f"Professor ratings: {'on' if enable_rmp else 'off'}",
                f"Grade history: {'on' if enable_grades else 'off'}",
                f"Polling interval: {poll_interval_seconds} seconds",
            ],
        )
    )


@bot.tree.command(description="Admin command to view bot health and last refresh state.")
@app_commands.checks.has_permissions(administrator=True)
async def status(interaction: discord.Interaction):
    config = server_config(interaction.guild_id)
    lines = [
        "Health: operational",
        f"Catalog source enabled: {'yes' if config['enable_catalog'] else 'no'}",
        f"Professor ratings enabled: {'yes' if config['enable_rmp'] else 'no'}",
        f"Grade history enabled: {'yes' if config['enable_grades'] else 'no'}",
        f"Polling interval: {config['poll_interval_seconds']} seconds",
        f"Catalog refresh: {config['last_catalog_refresh']}",
        f"RMP refresh: {config['last_rmp_refresh']}",
        f"Grades refresh: {config['last_grades_refresh']}",
        f"Last error: {config['last_error']}",
    ]
    lines.extend([""] + source_status_lines())
    await interaction.response.send_message(format_status(lines))


@bot.tree.command(description="Show which live or fallback data sources this build is using.")
async def sourceinfo(interaction: discord.Interaction):
    await interaction.response.send_message(format_status(source_status_lines()))


@bot.tree.command(description="Admin helper to simulate seat openings in the mock catalog.")
@app_commands.checks.has_permissions(administrator=True)
async def simulateseats(interaction: discord.Interaction, crn: str, open_seats: app_commands.Range[int, 0, 999]):
    updated = await provider.set_open_seats(crn, open_seats)
    if not updated:
        await interaction.response.send_message(text_block("Simulate seats", [f"CRN {crn} was not found in the mock catalog."]))
        return

    await interaction.response.send_message(
        text_block(
            "Simulate seats",
            [f"CRN {crn} now has {open_seats} open seats in the mock catalog."],
        )
    )


@bot.tree.command(name="help", description="Show the bot commands and what they do.")
async def helpbot(interaction: discord.Interaction):
    await interaction.response.send_message(
        text_block(
            "KIT bot help",
            [
                "/profile major school term - create or update your profile",
                "/setrequirements - save requirement keywords like core, systems, math",
                "/describeprefs - describe your ideal schedule in plain English",
                "/myprefs - view your saved schedule preferences",
                "/addclass crn - add a class by CRN",
                "/editclass - edit the saved days and times for a class",
                "/removeclass crn - delete a class",
                "/myschedule - view your own schedule",
                "/schedule @user - view someone else's schedule if permitted",
                "/privacy public|friends|private - change who can see your schedule",
                "/addfriend and /removefriend - manage your friends-only list",
                "/free - compute mutual free time across users",
                "/recommend [balanced|easy|professor] - get ranked class suggestions",
                "/recommendschedule - build full schedule options from recommendations",
                "/whyrecommend rank - explain a recommendation",
                "/watchclass crn and /unwatchclass crn - manage class alerts",
                "/uploadgrades file - upload a UDC export as CSV or JSON",
                "/refreshsources and /sourceinfo - inspect data-source state",
                "/config, /status, /simulateseats - admin commands",
            ],
        )
    )


@tasks.loop(seconds=settings.poll_interval_seconds)
async def watch_poll_loop():
    await bot.wait_until_ready()
    watches = db.list_watches()

    guild_id = settings.guild_id
    if guild_id is None:
        for guild in bot.guilds:
            guild_id = guild.id
            break

    config = server_config(guild_id)
    watch_poll_loop.change_interval(seconds=int(config["poll_interval_seconds"]))

    try:
        await provider.refresh()
        if guild_id is not None:
            catalog_refresh = "Never"
            catalog_error = "None"
            if catalog_provider is not None:
                catalog_refresh = catalog_provider.last_refresh
                catalog_error = catalog_provider.last_error

            rmp_refresh = "Never"
            rmp_error = "None"
            if rmp_provider is not None:
                rmp_refresh = rmp_provider.last_refresh
                rmp_error = rmp_provider.last_error

            grades_refresh = "Never"
            grades_error = "None"
            if grade_provider is not None:
                grades_refresh = grade_provider.last_refresh
                grades_error = grade_provider.last_error

            all_errors = [item for item in [catalog_error, rmp_error, grades_error, provider.last_error] if item and item != "None"]
            db.update_server_config(
                guild_id,
                last_catalog_refresh=catalog_refresh,
                last_rmp_refresh=rmp_refresh,
                last_grades_refresh=grades_refresh,
                last_error=" | ".join(all_errors) if all_errors else "None",
            )

        for watch in watches:
            crn = watch["crn"]
            open_seats = await provider.get_open_seats(crn, term=settings.vt_preferred_term)
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
                notified_value = 1 if dm_ok else notified_open
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, notified_value)
            elif open_seats == 0:
                db.update_watch_state(int(watch["user_id"]), crn, 0, 0)
            else:
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, notified_open)
    except Exception as exc:  # pragma: no cover - depends on live network
        LOGGER.exception("Watch poll failed: %s", exc)
        if guild_id is not None:
            db.update_server_config(guild_id, last_error=str(exc))


def main():
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and fill it in first.")
    bot.run(settings.discord_token)


if __name__ == "__main__":
    main()
