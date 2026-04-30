from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.core.models import ClassEntry, SchedulePreferences, VALID_PRIVACY
from src.providers.udc_grade_client import UDCGradeClient
from src.core.runtime import create_runtime
from src.utils.formatters import (
    format_dars_import_result,
    format_free_time,
    format_preferences,
    format_recommendations,
    format_schedule,
    format_schedule_plans,
    format_udc_course_matches,
    text_block,
)
from src.utils.term_utils import academic_terms_for, choose_next_term
from src.utils.time_utils import format_time_range, normalize_days
from src.ui.views import UDCGradePageView


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("kit_bot")

runtime = create_runtime()
settings = runtime.settings
db = runtime.db
mock_provider = runtime.mock_provider
catalog_provider = runtime.catalog_provider
rmp_provider = runtime.rmp_provider
grade_provider = runtime.grade_provider
provider = runtime.provider
privacy_service = runtime.privacy_service
schedule_service = runtime.schedule_service
free_time_service = runtime.free_time_service
watch_service = runtime.watch_service
recommendation_service = runtime.recommendation_service
preference_service = runtime.preference_service
dars_service = runtime.dars_service

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def build_entry_from_course(user_id: int, course) -> ClassEntry:
    return ClassEntry(
        user_id=user_id,
        schedule_key=db.get_active_schedule(user_id),
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


def normalize_course_code(value: str) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())


def save_schedule_preferences(preferences: SchedulePreferences) -> None:
    db.save_preferences(
        preferences.user_id,
        raw_text=preferences.raw_text,
        preferred_start=preferences.preferred_start,
        preferred_end=preferences.preferred_end,
        avoid_early=int(preferences.avoid_early),
        avoid_late=int(preferences.avoid_late),
        avoid_friday=int(preferences.avoid_friday),
        avoid_days=preferences.avoid_days,
        preferred_days=preferences.preferred_days,
        compact_days=int(preferences.compact_days),
        max_days=preferences.max_days,
        breaks_preference=preferences.breaks_preference,
        min_avg_gpa=preferences.min_avg_gpa,
        min_rmp_rating=preferences.min_rmp_rating,
        hard_time_window=int(preferences.hard_time_window),
        target_courses=preferences.target_courses,
        preferred_mode=preferences.preferred_mode,
        notes=preferences.notes,
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


def active_schedule_label(user_id: int) -> str:
    return db.get_active_schedule(user_id)


def active_term(profile_data) -> str:
    if profile_data is None:
        return settings.vt_preferred_term
    if db.get_active_schedule(profile_data.user_id) == "next":
        return choose_next_term(next_semester_kind_for_profile(profile_data)) or settings.vt_preferred_term
    return academic_terms_for().current or settings.vt_preferred_term


def term_for_schedule(profile_data, schedule_key: str) -> str:
    if profile_data is None:
        return settings.vt_preferred_term
    if schedule_key == "next":
        return choose_next_term(next_semester_kind_for_profile(profile_data)) or settings.vt_preferred_term
    return academic_terms_for().current or settings.vt_preferred_term


def next_semester_kind_for_profile(profile_data) -> str:
    if profile_data is not None and str(profile_data.next_term).strip().lower().startswith("summer"):
        return "off"
    return "main"


def refresh_profile_terms(user_id: int, next_semester: str = "main"):
    profile_data = db.get_profile(user_id)
    if profile_data is None:
        return None
    terms = academic_terms_for()
    selected_next_semester = next_semester or next_semester_kind_for_profile(profile_data)
    next_term = choose_next_term(selected_next_semester)
    db.upsert_profile(user_id, profile_data.major, profile_data.school, terms.current, next_term)
    return db.get_profile(user_id)


def profile_semester_choices() -> list[app_commands.Choice[str]]:
    terms = academic_terms_for()
    return [
        app_commands.Choice(name=f"current semester ({terms.current})", value="current"),
        app_commands.Choice(name=f"next main semester ({terms.next_main})", value="main"),
        app_commands.Choice(name=f"next off semester ({terms.next_off})", value="off"),
    ]


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


def recommendation_empty_reason(profile_data, config: dict) -> list[str]:
    if profile_data is None:
        return ["No profile is saved yet. Use /profile or /uploaddars first."]
    if not profile_data.major or not active_term(profile_data):
        return ["Your profile needs a major before recommendations can run."]
    if not config["enable_catalog"]:
        return ["Catalog source is disabled for this server, so there are no courses to recommend."]

    lines = [
        f"No catalog courses were found for Virginia Tech / {active_term(profile_data)}.",
        "Your DARS import saved your current classes, but recommendations still need catalog data for the term you want to plan.",
    ]

    if settings.vt_preferred_term:
        lines.append(f"Configured fallback/planning term: {settings.vt_preferred_term}")
    if catalog_provider is None:
        lines.append("Live VT catalog provider is unavailable; the bot is using the local sample catalog only.")
    elif getattr(catalog_provider, "last_error", "None") != "None":
        lines.append(f"Live VT catalog error: {catalog_provider.last_error}")

    sample_terms = sorted({course.term for course in mock_provider.courses.values() if course.term})
    if sample_terms:
        lines.append(f"Local sample catalog terms: {', '.join(sample_terms)}")
    lines.append("Use /profile to save your major, then /switchschedule to choose current or next.")
    return lines


async def send_udc_grade_lookup(
    interaction: discord.Interaction,
    subject: str = "",
    course_number: str = "",
    instructor: str = "",
    limit: int = 20,
):
    subject = subject.strip().upper()
    course_number = course_number.strip()
    instructor = instructor.strip()
    limit = max(1, min(int(limit or 20), 30))

    client = UDCGradeClient()
    try:
        if not subject or not course_number:
            courses = await asyncio.to_thread(client.list_courses, subject, course_number)
            if course_number and len(courses) == 1:
                subject = str(courses[0][0]).strip().upper()
                course_number = str(courses[0][1]).strip()
            else:
                message = format_udc_course_matches(subject, courses)
                if instructor and not course_number:
                    message += "\n" + text_block(
                        "UDC note",
                        ["Instructor filtering is applied after a specific course is selected."],
                    )
                await interaction.followup.send(message, ephemeral=True)
                return

        rows = await asyncio.to_thread(client.fetch_course_rows, subject, course_number)
    except Exception as exc:
        LOGGER.exception("UDC grade lookup failed")
        await interaction.followup.send(text_block("UDC grades", [f"Lookup failed: {exc}"]), ephemeral=True)
        return

    if instructor:
        needle = instructor.casefold()
        rows = [row for row in rows if needle in str(row.get("instructor") or "").casefold()]

    view = UDCGradePageView(interaction.user.id, subject, course_number, rows, limit, instructor)
    if view.page_count > 1:
        await interaction.followup.send(view.message(), view=view, ephemeral=True)
    else:
        await interaction.followup.send(view.message(), ephemeral=True)


def parse_course_code(value: str) -> tuple[str, str]:
    normalized = " ".join(str(value or "").upper().replace("-", " ").split())
    match = re.fullmatch(r"([A-Z]{2,5})\s*(\d{3,4}[A-Z]?)", normalized)
    if match is None:
        return "", ""
    return match.group(1), match.group(2)


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
@app_commands.describe(
    major="Example: CS",
    semester="Choose which semester you want to work on.",
)
@app_commands.choices(
    semester=profile_semester_choices()
)
async def profile(
    interaction: discord.Interaction,
    major: str,
    semester: app_commands.Choice[str],
):
    school = "Virginia Tech"
    terms = academic_terms_for()
    selected = semester.value
    if selected == "current":
        profile_data = db.get_profile(interaction.user.id)
        next_kind = next_semester_kind_for_profile(profile_data)
        active_schedule = "current"
        selected_term = terms.current
    else:
        next_kind = selected
        active_schedule = "next"
        selected_term = choose_next_term(next_kind)
    next_term = choose_next_term(next_kind)
    db.upsert_profile(interaction.user.id, major.strip(), school, terms.current, next_term)
    db.set_active_schedule(interaction.user.id, active_schedule)
    await interaction.response.send_message(
        text_block(
            "Profile saved",
            [
                f"Major: {major.strip()}",
                "School: Virginia Tech",
                f"Current term: {terms.current}",
                f"Next term: {next_term}",
                f"Selected term: {selected_term}",
                f"Active schedule: {active_schedule}",
            ],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="myprofile", description="View your saved academic profile.")
async def myprofile(interaction: discord.Interaction):
    profile_data = refresh_profile_terms(interaction.user.id, "")
    if profile_data is None:
        await interaction.response.send_message(
            text_block("My profile", ["No profile saved yet.", "Use /profile or /uploaddars to create one."]),
            ephemeral=True,
        )
        return

    classes = db.list_classes(interaction.user.id)
    await interaction.response.send_message(
        text_block(
            "My profile",
            [
                f"Major: {profile_data.major or 'Not set'}",
                "School: Virginia Tech",
                f"Current term: {profile_data.current_term or profile_data.term or 'Not set'}",
                f"Next term: {profile_data.next_term or 'Not set'}",
                f"Active schedule: {profile_data.active_schedule}",
                f"Privacy: {profile_data.privacy}",
                f"Saved classes in active schedule: {len(classes)}",
            ],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="uploaddars", description="Import your academic profile from a DARS PDF without saving the PDF.")
@app_commands.describe(
    file="DARS PDF printed or saved from the webpage",
    term="Optional term override, such as Fall 2026",
)
async def uploaddars(interaction: discord.Interaction, file: discord.Attachment, term: str = ""):
    suffix = Path(file.filename).suffix.lower()
    if suffix != ".pdf":
        await interaction.response.send_message(text_block("DARS import", ["Please upload a PDF file."]), ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        payload = await file.read()
        parsed = await asyncio.to_thread(dars_service.parse_pdf_bytes, payload, term.strip() or settings.vt_preferred_term)
    except Exception as exc:
        LOGGER.exception("DARS import failed")
        await interaction.followup.send(text_block("DARS import", [f"Could not read that DARS PDF: {exc}"]), ephemeral=True)
        return

    if parsed.major:
        db.upsert_profile(interaction.user.id, parsed.major, "Virginia Tech", parsed.term)
    if parsed.requirements_text:
        db.update_requirements(interaction.user.id, parsed.requirements_text)

    await interaction.followup.send(
        format_dars_import_result(
            parsed.major,
            parsed.school,
            parsed.term,
            parsed.requirements_text,
            parsed.missing_courses,
            parsed.completed_courses,
            parsed.credit_requirements,
            parsed.current_courses,
            parsed.planned_courses,
            parsed.warnings,
        ),
        ephemeral=True,
    )


@bot.tree.command(name="prefs", description="View the schedule preferences you have saved.")
async def myprefs(interaction: discord.Interaction):
    preferences = db.get_preferences(interaction.user.id)
    await interaction.response.send_message(format_preferences(preferences), ephemeral=True)


@bot.tree.command(name="switchschedule", description="Choose whether schedule commands use your current or next schedule.")
@app_commands.choices(
    schedule=[
        app_commands.Choice(name="current", value="current"),
        app_commands.Choice(name="next", value="next"),
    ]
)
async def switchschedule(interaction: discord.Interaction, schedule: app_commands.Choice[str]):
    db.set_active_schedule(interaction.user.id, schedule.value)
    profile_data = refresh_profile_terms(interaction.user.id, "")
    term = active_term(profile_data)
    await interaction.response.send_message(
        text_block(
            "Active schedule changed",
            [
                f"Now using: {schedule.value}",
                f"Term: {term or 'Not set'}",
                "Schedule commands now read and write this schedule.",
            ],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="planschedule", description="Describe the schedule you want and get schedule options in one step.")
@app_commands.describe(description="Optional request, such as: easy schedule with average GPA above 3.6 and classes from 9am to 5pm")
async def planschedule(interaction: discord.Interaction, description: str = ""):
    await interaction.response.defer(thinking=True, ephemeral=True)
    preferences = db.get_preferences(interaction.user.id)
    if description.strip():
        preferences = preference_service.parse_description(interaction.user.id, description)
        save_schedule_preferences(preferences)

    config = server_config(interaction.guild_id)
    effective_mode = recommendation_service.effective_mode("balanced", preferences)
    plans = await recommendation_service.recommend_schedules(
        interaction.user.id,
        effective_mode,
        enable_rmp=bool(config["enable_rmp"]),
        enable_grades=bool(config["enable_grades"]),
    )

    profile_data = db.get_profile(interaction.user.id)
    profile_label = "No profile"
    if profile_data is not None:
        profile_label = f"{profile_data.major} | Virginia Tech | {active_term(profile_data)} | {profile_data.active_schedule}"

    message = format_preferences(db.get_preferences(interaction.user.id))
    message += "\n" + format_schedule_plans(profile_label, effective_mode, plans)
    if not plans:
        message += "\n" + text_block(
            "Planner notes",
            [
                "No schedule satisfied every detected constraint.",
                "Try relaxing the GPA floor, widening the time window, or lowering the requested course count.",
            ],
        )
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(name="addclass", description="Add a class to your profile by CRN or course name/code.")
@app_commands.describe(course="CRN, course code, or name. Examples: 46802, CS 3704, Linear Algebra")
async def addclass(interaction: discord.Interaction, course: str):
    config = server_config(interaction.guild_id)
    if not config["enable_catalog"]:
        await interaction.response.send_message(text_block("Add class", ["Catalog source is disabled by admin settings."]), ephemeral=True)
        return

    profile_data = db.get_profile(interaction.user.id)
    school = "Virginia Tech"
    term = active_term(profile_data)
    schedule_key = db.get_active_schedule(interaction.user.id)

    course_query = course.strip()
    course_record = None
    if course_query.isdigit():
        if db.get_class(interaction.user.id, course_query):
            await interaction.response.send_message(text_block("Add class", [f"CRN {course_query} is already in your schedule."]), ephemeral=True)
            return
        course_record = await provider.get_course_by_crn(course_query, school=school, term=term)
    else:
        matches = await provider.search_courses(course_query, school=school, term=term)
        unsaved_matches = [item for item in matches if db.get_class(interaction.user.id, item.crn) is None]
        if len(unsaved_matches) == 1:
            course_record = unsaved_matches[0]
        elif unsaved_matches:
            lines = [f"Found {len(unsaved_matches)} matching section(s). Run /addclass with the CRN you want:", ""]
            for item in unsaved_matches[:8]:
                time_span = format_time_range(item.start_time, item.end_time)
                seats = item.open_seats if item.open_seats is not None else "?"
                lines.append(f"{item.crn}: {item.course_code} - {item.title} | {item.days or 'TBA'} {time_span} | {item.instructor} | seats {seats}")
            if len(unsaved_matches) > 8:
                lines.append("")
                lines.append("Showing first 8 matches. Try a CRN for the exact section.")
            await interaction.response.send_message(text_block("Choose a section", lines), ephemeral=True)
            return
        elif matches:
            await interaction.response.send_message(text_block("Add class", ["Every matching section is already in your schedule."]), ephemeral=True)
            return

    if course_record is None:
        await interaction.response.send_message(
            text_block("Add class", [f"No available class matched '{course_query}' for {school} / {term or 'the configured term'}."]),
            ephemeral=True,
        )
        return

    entry = build_entry_from_course(interaction.user.id, course_record)
    try:
        schedule_service.add_or_replace_class(entry)
    except ValueError as exc:
        await interaction.response.send_message(text_block("Add class", [str(exc)]), ephemeral=True)
        return
    await interaction.response.send_message(
        text_block(
            "Class added successfully",
            [
                f"CRN: {entry.crn}",
                f"Course: {entry.course_code} - {entry.course_title}",
                f"Instructor: {entry.instructor}",
                f"Days: {entry.days}",
                f"Time: {format_time_range(entry.start_time, entry.end_time)}",
                f"Location: {entry.location or '-'}",
                f"Source: {entry.source}",
                f"Schedule: {schedule_key}",
                "Use /schedule to view your saved timetable.",
            ],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="removeclass", description="Remove a class from your schedule by CRN or course code/name.")
@app_commands.describe(course="CRN, course code, or name. Examples: 46802, CS 3704, CS3704")
async def removeclass(interaction: discord.Interaction, course: str):
    query = course.strip()
    schedule_key = db.get_active_schedule(interaction.user.id)
    saved_classes = db.list_classes(interaction.user.id)
    if not saved_classes:
        await interaction.response.send_message(text_block("Remove class", [f"Your {schedule_key} schedule is already empty."]), ephemeral=True)
        return

    if query.isdigit():
        removed = db.remove_class(interaction.user.id, query)
        if removed:
            message = f"CRN {query} was removed from your {schedule_key} schedule."
        else:
            message = f"CRN {query} is not currently listed in your schedule."
        await interaction.response.send_message(text_block("Remove class", [message]), ephemeral=True)
        return

    normalized_query = "".join(ch for ch in query.upper() if ch.isalnum())
    lowered_query = " ".join(query.lower().split())
    matches = [
        entry
        for entry in saved_classes
        if normalized_query == "".join(ch for ch in entry.course_code.upper() if ch.isalnum())
        or lowered_query in " ".join(entry.course_title.lower().split())
    ]

    if len(matches) == 1:
        removed = db.remove_class(interaction.user.id, matches[0].crn)
        message = f"{matches[0].course_code} ({matches[0].crn}) was removed from your {schedule_key} schedule." if removed else "That class was not found."
        await interaction.response.send_message(text_block("Remove class", [message]), ephemeral=True)
        return

    if matches:
        lines = [f"Found {len(matches)} saved matching class(es). Run /removeclass with the CRN you want:", ""]
        for entry in matches[:8]:
            time_span = format_time_range(entry.start_time, entry.end_time)
            lines.append(f"{entry.crn}: {entry.course_code} - {entry.course_title} | {entry.days or 'TBA'} {time_span}")
        await interaction.response.send_message(text_block("Choose a class", lines), ephemeral=True)
        return

    await interaction.response.send_message(
        text_block("Remove class", [f"No saved class matched '{query}'. Try /schedule to see the exact CRN or course code."]),
        ephemeral=True,
    )


@bot.tree.command(name="clearschedule", description="Delete every class from your saved schedule.")
@app_commands.describe(confirm="Type DELETE to confirm you want to clear your whole schedule.")
async def clearschedule(interaction: discord.Interaction, confirm: str):
    schedule_key = db.get_active_schedule(interaction.user.id)
    if confirm.strip() != "DELETE":
        await interaction.response.send_message(
            text_block(
                "Clear schedule",
                [
                    "Nothing was deleted.",
                    "Run /clearschedule confirm:DELETE to remove every class from your saved schedule.",
                ],
            ),
            ephemeral=True,
        )
        return

    removed_count = db.clear_schedule(interaction.user.id)
    if removed_count == 0:
        message = f"Your {schedule_key} schedule was already empty."
    else:
        message = f"Deleted {removed_count} saved class(es) from your {schedule_key} schedule."
    await interaction.response.send_message(text_block("Schedule cleared", [message]), ephemeral=True)


@bot.tree.command(name="schedule", description="View your saved schedule.")
async def myschedule(interaction: discord.Interaction):
    profile_data = db.get_profile(interaction.user.id)
    privacy = "friends"
    if profile_data is not None:
        privacy = profile_data.privacy

    classes = db.list_classes(interaction.user.id)
    schedule_key = db.get_active_schedule(interaction.user.id)
    await interaction.response.send_message(format_schedule(f"{interaction.user.display_name} ({schedule_key})", classes, privacy), ephemeral=True)


@bot.tree.command(name="viewschedule", description="View another user's schedule if their privacy settings allow it.")
async def schedule(interaction: discord.Interaction, user: discord.Member):
    if not privacy_service.can_view_schedule(user.id, interaction.user.id):
        await interaction.response.send_message(text_block("Schedule", ["Not permitted."]), ephemeral=True)
        return

    profile_data = db.get_profile(user.id)
    privacy = "friends"
    if profile_data is not None:
        privacy = profile_data.privacy

    classes = db.list_classes(user.id)
    schedule_key = db.get_active_schedule(user.id)
    await interaction.response.send_message(format_schedule(f"{user.display_name} ({schedule_key})", classes, privacy), ephemeral=True)


@bot.tree.command(description="Set schedule privacy: public=anyone, friends=approved users, private=only you.")
@app_commands.describe(setting="public=anyone, friends=approved users, private=only you")
@app_commands.choices(
    setting=[
        app_commands.Choice(name="public", value="public"),
        app_commands.Choice(name="friends", value="friends"),
        app_commands.Choice(name="private", value="private"),
    ]
)
async def privacy(interaction: discord.Interaction, setting: app_commands.Choice[str]):
    setting_value = setting.value
    db.set_privacy(interaction.user.id, setting_value)
    await interaction.response.send_message(text_block("Privacy updated", [f"New setting: {setting_value}"]), ephemeral=True)


@bot.tree.command(name="addfriend", description="Add a user to your schedule friends list.")
async def addfriend(interaction: discord.Interaction, user: discord.Member):
    if user.id == interaction.user.id:
        await interaction.response.send_message(text_block("Friends", ["You do not need to add yourself."]), ephemeral=True)
        return

    db.add_friend(interaction.user.id, user.id)
    await interaction.response.send_message(
        text_block(
            "Friends",
            [f"{user.display_name} can now view your schedule when privacy is set to friends."],
        ),
        ephemeral=True,
    )


@bot.tree.command(name="removefriend", description="Remove a user from your schedule friends list.")
async def removefriend(interaction: discord.Interaction, user: discord.Member):
    db.remove_friend(interaction.user.id, user.id)
    await interaction.response.send_message(
        text_block(
            "Friends",
            [f"{user.display_name} was removed from your schedule friends list."],
        ),
        ephemeral=True,
    )


@bot.tree.command(description="Compute overlapping free time for up to three users.")
@app_commands.describe(
    user1="First user",
    user2="Second user",
    user3="Third user",
    start_time="Start time, like 9:00 AM or 9am",
    end_time="End time, like 6:00 PM or 6pm",
    weekdays_only="True to check Monday-Friday only",
    include_me="Include your own schedule in the overlap",
)
async def free(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: discord.Member | None = None,
    user3: discord.Member | None = None,
    start_time: str = "9:00 AM",
    end_time: str = "6:00 PM",
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
        await interaction.response.send_message(text_block("Shared free time", ["No accessible schedules were available."]), ephemeral=True)
        return

    try:
        windows = free_time_service.compute(accessible_ids, start_time, end_time, weekdays_only)
    except ValueError as exc:
        await interaction.response.send_message(text_block("Shared free time", [str(exc)]), ephemeral=True)
        return
    names = ", ".join(member.display_name for member in requested_users if member.id in accessible_ids)
    await interaction.response.send_message(format_free_time(f"Shared free time for {names}", windows, excluded), ephemeral=True)


@bot.tree.command(description="Recommend courses using your profile and optional schedule request.")
@app_commands.describe(
    description="Optional request, such as: easy classes from 9am to 5pm, avoid Friday",
)
async def recommend(
    interaction: discord.Interaction,
    description: str = "",
):
    await interaction.response.defer(thinking=True, ephemeral=True)
    config = server_config(interaction.guild_id)
    preferences = db.get_preferences(interaction.user.id)
    if description.strip():
        preferences = preference_service.parse_description(interaction.user.id, description)
        save_schedule_preferences(preferences)
    effective_mode = recommendation_service.effective_mode("balanced", preferences)
    recommendations = await recommendation_service.recommend(
        interaction.user.id,
        effective_mode,
        enable_rmp=bool(config["enable_rmp"]),
        enable_grades=bool(config["enable_grades"]),
    )

    profile_data = db.get_profile(interaction.user.id)
    profile_label = "No profile"
    if profile_data is not None:
        profile_label = f"{profile_data.major} | Virginia Tech | {active_term(profile_data)} | {profile_data.active_schedule}"

    warning_lines: list[str] = []
    if not recommendations:
        warning_lines.extend(recommendation_empty_reason(profile_data, config))
    if not config["enable_rmp"]:
        warning_lines.append("Warning: professor-rating source is disabled, so neutral defaults were used.")
    if not config["enable_grades"]:
        warning_lines.append("Warning: grade-history source is disabled, so neutral defaults were used.")

    message = format_recommendations(profile_label, effective_mode, recommendations)
    if warning_lines:
        message += "\n" + text_block("Source warnings", warning_lines)
    await interaction.followup.send(message, ephemeral=True)


@bot.tree.command(description="Look up a professor's RateMyProfessors score.")
@app_commands.describe(
    name="Professor name, such as Jane Smith or Senger",
    course="Optional course context, such as CS 2506",
)
async def professor(interaction: discord.Interaction, name: str, course: str = ""):
    professor_name = name.strip()
    course_context = course.strip()
    if not professor_name:
        await interaction.response.send_message(text_block("Professor rating", ["Professor name is required."]), ephemeral=True)
        return

    if rmp_provider is None:
        await interaction.response.send_message(text_block("Professor rating", ["RateMyProfessors provider is disabled."]), ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    rating = await rmp_provider.get_rating(professor_name, course_context)
    if rating is None:
        context = f" for {course_context}" if course_context else ""
        lines = [
            f"No RateMyProfessors match found for '{professor_name}'{context} at {settings.rmp_school_name}.",
            f"Last RMP status: {rmp_provider.last_error}",
        ]
        await interaction.followup.send(text_block("Professor rating", lines), ephemeral=True)
        return

    raw_rating = {}
    if rating.raw_json:
        try:
            raw_rating = json.loads(rating.raw_json)
        except ValueError:
            raw_rating = {}

    lines = [
        f"Professor: {rating.professor_name}",
        f"School: {rating.school_name}",
        f"Course context: {course_context or 'None'}",
        f"Overall rating: {rating.avg_rating:.1f}/5",
        f"Difficulty: {rating.avg_difficulty:.1f}/5",
        f"Ratings: {rating.num_ratings}",
    ]
    department = str(raw_rating.get("department") or "").strip()
    if department:
        lines.insert(3, f"Department: {department}")
    if rating.would_take_again is not None and rating.would_take_again >= 0:
        lines.append(f"Would take again: {rating.would_take_again:.0f}%")
    await interaction.followup.send(text_block("Professor rating", lines), ephemeral=True)


@bot.tree.command(name="coursegrades", description="Search course grade history; add instructor from +1 more to filter.")
@app_commands.describe(
    course="Course code, such as CS3704. Optional: open +1 more to add instructor.",
    instructor="Optional: filter results by instructor last name or full name",
)
async def coursegrades(
    interaction: discord.Interaction,
    course: str,
    instructor: str = "",
):
    subject, course_number = parse_course_code(course)
    if not subject or not course_number:
        await interaction.response.send_message(
            text_block("Course grades", ["Enter a course like CS3704 or CS 3704."]),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    await send_udc_grade_lookup(interaction, subject, course_number, instructor)


@bot.tree.command(name="watchclass", description="Watch a class by CRN or course code and get a DM when a seat opens.")
@app_commands.describe(course="CRN or course code, such as 46802 or CS3704")
async def watchclass(interaction: discord.Interaction, course: str):
    profile_data = db.get_profile(interaction.user.id)
    schedule_key = db.get_active_schedule(interaction.user.id)
    success, message = await watch_service.add_watch(
        interaction.user.id,
        course,
        term=active_term(profile_data),
        schedule_key=schedule_key,
    )
    await interaction.response.send_message(text_block("Watch class", [f"Schedule: {schedule_key}", message]), ephemeral=True)
    if success:
        await safe_send_dm(interaction.user.id, text_block("Watch started", [message]))


@bot.tree.command(name="unwatchclass", description="Remove a class from your watchlist by CRN or course code.")
@app_commands.describe(course="CRN or course code, such as 46802 or CS3704")
async def unwatchclass(interaction: discord.Interaction, course: str):
    profile_data = db.get_profile(interaction.user.id)
    schedule_key = db.get_active_schedule(interaction.user.id)
    _, message = await watch_service.remove_watch(
        interaction.user.id,
        course,
        term=active_term(profile_data),
        schedule_key=schedule_key,
    )
    await interaction.response.send_message(text_block("Unwatch class", [f"Schedule: {schedule_key}", message]), ephemeral=True)


@bot.tree.command(name="help", description="Show the bot commands and what they do.")
async def helpbot(interaction: discord.Interaction):
    message = "\n".join(
        [
            "**HokieScheduler help**",
            "You have two schedules: `current` and `next`. Use `/switchschedule` to choose which one commands edit.",
            "",
            "**Start here**",
            "`/profile` - set your major and choose whether next schedule uses the main or off semester",
            "`/switchschedule` - switch active schedule: current or next",
            "`/myprofile` - view your profile and active schedule",
            "`/uploaddars` - import DARS context for recommendations",
            "",
            "**Schedule**",
            "`/addclass` - add a class to the active schedule",
            "`/removeclass` - remove a class from the active schedule",
            "`/clearschedule` - delete the active schedule",
            "`/schedule` - view your active schedule",
            "`/viewschedule` - view another user's active schedule if permitted",
            "",
            "**Planning**",
            "`/planschedule` - plan around the active schedule",
            "`/prefs` - view saved planning preferences",
            "`/recommend` - get suggestions for the active schedule's term",
            "`/free` - find shared free time",
            "",
            "**Course Data**",
            "`/professor` - look up a RateMyProfessors score",
            "`/coursegrades` - search course grade history",
            "`/watchclass` - watch a CRN or course in the active schedule",
            "`/unwatchclass` - stop watching a CRN or course",
            "",
            "**Privacy & Friends**",
            "`/privacy` - choose who can view your schedule",
            "`/addfriend` - allow someone to view friends-only schedules",
            "`/removefriend` - remove a schedule friend",
        ]
    )
    await interaction.response.send_message(message, ephemeral=True)


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
            schedule_key = watch["schedule_key"]
            watch_profile = db.get_profile(int(watch["user_id"]))
            open_seats = await provider.get_open_seats(crn, term=term_for_schedule(watch_profile, schedule_key))
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
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, notified_value, schedule_key=schedule_key)
            elif open_seats == 0:
                db.update_watch_state(int(watch["user_id"]), crn, 0, 0, schedule_key=schedule_key)
            else:
                db.update_watch_state(int(watch["user_id"]), crn, open_seats, notified_open, schedule_key=schedule_key)
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
