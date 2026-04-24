from __future__ import annotations
 
import sys
import types
import unittest
import asyncio

# AI was used to identify which modules needed mocking before bot.py could be imported

# AI was used to design the sys.modules patching strategy for discord, dotenv, and src packages

# AI was used to implement FakeDatabase as an in-memory substitute for the real SQLite database

# AI was used to create _AsyncSendMock so async Discord response methods could be captured in tests

# AI was used to determine that IsolatedAsyncioTestCase was needed to run async test methods 

# AI was used to wire fake service singletons into bot_module after import to override the real ones


# MOCK: dotenv
dotenv_mock = types.ModuleType("dotenv")
dotenv_mock.load_dotenv = lambda *args, **kwargs: None
sys.modules["dotenv"] = dotenv_mock
 
# MOCK: discord
discord_mock = types.ModuleType("discord")
discord_mock.Forbidden = type("Forbidden", (Exception,), {})
intents_instance = types.SimpleNamespace(members=True)
discord_mock.Intents = types.SimpleNamespace(default=lambda: intents_instance)
discord_mock.Object = lambda id: types.SimpleNamespace(id=id)
 
 
class _Member:
    def __init__(self, uid, name):
        self.id = uid
        self.display_name = name
 
 
discord_mock.Member = _Member
 
app_commands_mock = types.ModuleType("discord.app_commands")
app_commands_mock.Transformer = object
app_commands_mock.AppCommandError = Exception
app_commands_mock.describe = lambda **kw: (lambda f: f)
app_commands_mock.Transform = lambda val, cls: val
app_commands_mock.Range = lambda *a, **kw: None
app_commands_mock.checks = types.SimpleNamespace(
    has_permissions=lambda **kw: (lambda f: f)
)
 
 
class _FakeTree:
    def command(self, **kw):
        return lambda f: f
 
    def error(self, f):
        return f
 
    async def sync(self, guild=None):
        return []
 
    def copy_global_to(self, guild=None):
        pass
 
 
app_commands_mock.Tree = _FakeTree
discord_mock.app_commands = app_commands_mock
 
commands_mock = types.ModuleType("discord.ext.commands")
 
 
class _AsyncSendMock:
    """Minimal async callable that records calls."""
    def __init__(self, return_value=None):
        self.return_value = return_value
        self.calls = []
 
    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value
 
 
class _FakeBot:
    def __init__(self, *args, **kwargs):
        self.tree = _FakeTree()
        self.guilds = []
        self.user = types.SimpleNamespace(display_name="KITBot")
 
    def event(self, f):
        return f
 
    async def fetch_user(self, uid):
        m = _Member(uid, f"user_{uid}")
        m.send = _AsyncSendMock()
        return m
 
    async def wait_until_ready(self):
        pass
 
 
commands_mock.Bot = _FakeBot
 
tasks_mock = types.ModuleType("discord.ext.tasks")
tasks_mock.loop = lambda *args, **kwargs: (lambda f: f)
 
ext_mock = types.ModuleType("discord.ext")
ext_mock.commands = commands_mock
ext_mock.tasks = tasks_mock
discord_mock.ext = ext_mock
 
sys.modules["discord"] = discord_mock
sys.modules["discord.app_commands"] = app_commands_mock
sys.modules["discord.ext"] = ext_mock
sys.modules["discord.ext.commands"] = commands_mock
sys.modules["discord.ext.tasks"] = tasks_mock
 

# MOCK: src package + sub-modules
def _make_module(name):
    m = types.ModuleType(name)
    sys.modules[name] = m
    return m
 
 
src_mock = _make_module("src")
src_mock.__path__ = []
 
# ---- src.config ----
import os
from dataclasses import dataclass
from pathlib import Path
 
 
@dataclass
class Settings:
    discord_token: str = "fake-token"
    guild_id: int | None = None
    database_path: Path = Path("data/test.db")
    poll_interval_seconds: int = 60
 
 
config_mock = _make_module("src.config")
config_mock.Settings = Settings
config_mock.load_settings = lambda: Settings()
 
# ---- src.models ----
VALID_PRIVACY = {"public", "friends", "private"}
WEEKDAY_ORDER = ["M", "T", "W", "R", "F", "S", "U"]
 
 
@dataclass
class Profile:
    user_id: int
    major: str
    school: str
    term: str
    privacy: str
    requirements_text: str = ""
 
 
@dataclass
class ClassEntry:
    user_id: int
    crn: str
    course_code: str
    course_title: str
    instructor: str
    days: str
    start_time: str
    end_time: str
    location: str
    source: str
 
 
@dataclass
class Recommendation:
    course_code: str
    title: str
    instructor: str
    crn: str
    days: str
    start_time: str
    end_time: str
    score: float
    label: str
    explanation: str
 
 
models_mock = _make_module("src.models")
models_mock.ClassEntry = ClassEntry
models_mock.Profile = Profile
models_mock.Recommendation = Recommendation
models_mock.VALID_PRIVACY = VALID_PRIVACY
models_mock.WEEKDAY_ORDER = WEEKDAY_ORDER
 
# ---- src.db ----
class FakeDatabase:
    def __init__(self, *a, **kw):
        self._profiles: dict = {}
        self._classes: dict = {}
        self._friends: dict = {}
        self._watches: list = []
        self._server_config: dict = {}
 
    def upsert_profile(self, user_id, major, school, term):
        existing = self._profiles.get(user_id)
        privacy = existing.privacy if existing else "friends"
        self._profiles[user_id] = Profile(user_id, major, school, term, privacy)
 
    def get_profile(self, user_id):
        return self._profiles.get(user_id)
 
    def set_privacy(self, user_id, setting):
        p = self._profiles.get(user_id)
        if p:
            self._profiles[user_id] = Profile(
                p.user_id, p.major, p.school, p.term, setting
            )
 
    def add_friend(self, user_id, friend_id):
        self._friends.setdefault(user_id, set()).add(friend_id)
 
    def remove_friend(self, user_id, friend_id):
        self._friends.get(user_id, set()).discard(friend_id)
 
    def get_friends(self, user_id):
        return list(self._friends.get(user_id, set()))
 
    def list_classes(self, user_id):
        return [v for (uid, _), v in self._classes.items() if uid == user_id]
 
    def get_class(self, user_id, crn):
        return self._classes.get((user_id, crn))
 
    def save_class(self, entry: ClassEntry):
        self._classes[(entry.user_id, entry.crn)] = entry
 
    def remove_class(self, user_id, crn):
        return self._classes.pop((user_id, crn), None) is not None
 
    def list_watches(self):
        return self._watches
 
    def get_server_config(self, guild_id):
        return self._server_config.get(
            guild_id,
            {
                "enable_catalog": 1,
                "poll_interval_seconds": 60,
                "last_catalog_refresh": "Never",
                "last_error": "None",
            },
        )
 
    def update_server_config(self, guild_id, **kwargs):
        cfg = dict(self.get_server_config(guild_id))
        cfg.update(kwargs)
        self._server_config[guild_id] = cfg
 
    def update_watch_state(self, user_id, crn, open_seats, notified_open):
        for w in self._watches:
            if w["user_id"] == user_id and w["crn"] == crn:
                w["last_known_open_seats"] = open_seats
                w["notified_open"] = notified_open
 
    def add_watch(self, user_id, crn):
        self._watches.append(
            {"user_id": user_id, "crn": crn,
             "last_known_open_seats": 0, "notified_open": 0}
        )
 
    def remove_watch(self, user_id, crn):
        self._watches = [
            w for w in self._watches
            if not (w["user_id"] == user_id and w["crn"] == crn)
        ]
 
 
db_mock = _make_module("src.db")
db_mock.Database = FakeDatabase
 
# ---- src.providers.mock_data ----
class FakeMockDataProvider:
    def __init__(self, *a, **kw):
        self._courses: dict = {}
        self._seats: dict = {}
 
    async def get_course_by_crn(self, crn, term="", school=""):
        return self._courses.get(crn)
 
    async def list_courses_for_profile(self, major, school, term):
        return list(self._courses.values())
 
    async def get_open_seats(self, crn, term=""):
        return self._seats.get(crn)
 
    async def set_open_seats(self, crn, seats):
        if crn not in self._courses:
            return False
        self._seats[crn] = seats
        return True
 
    async def refresh(self):
        pass
 
    def _seed_course(self, course):
        self._courses[course.crn] = course
 
 
providers_pkg = _make_module("src.providers")
providers_pkg.__path__ = []
mock_data_mod = _make_module("src.providers.mock_data")
mock_data_mod.MockDataProvider = FakeMockDataProvider
 
# ---- src.services.* ----
services_pkg = _make_module("src.services")
services_pkg.__path__ = []
for _svc in ("schedule_service", "privacy_service", "free_time_service", "watch_service"):
    _make_module(f"src.services.{_svc}")
 
 
class FakeScheduleService:
    def __init__(self, db):
        self.db = db
 
    def add_or_replace_class(self, entry):
        self.db.save_class(entry)
 
    def edit_class(self, user_id, crn, days, start_time, end_time, location=""):
        entry = self.db.get_class(user_id, crn)
        if entry is None:
            return None
        updated = ClassEntry(
            user_id=entry.user_id, crn=entry.crn,
            course_code=entry.course_code, course_title=entry.course_title,
            instructor=entry.instructor, days=days,
            start_time=start_time, end_time=end_time,
            location=location, source=entry.source,
        )
        self.db.save_class(updated)
        return updated
 
 
class FakePrivacyService:
    def __init__(self, db):
        self.db = db
 
    def can_view_schedule(self, target_id, viewer_id):
        if target_id == viewer_id:
            return True
        p = self.db.get_profile(target_id)
        if p is None:
            return True
        if p.privacy == "public":
            return True
        if p.privacy == "friends":
            return viewer_id in self.db.get_friends(target_id)
        return False
 
 
class FakeFreeTimeService:
    def __init__(self, db):
        self.db = db
 
    def compute(self, user_ids, start_time, end_time, weekdays_only):
        return []
 
 
class FakeWatchService:
    def __init__(self, db, provider):
        self.db = db
        self.provider = provider
 
    async def add_watch(self, user_id, crn):
        self.db.add_watch(user_id, crn)
        return True, f"Now watching CRN {crn}"
 
    def remove_watch(self, user_id, crn):
        self.db.remove_watch(user_id, crn)
        return True, f"Stopped watching CRN {crn}"
 
 
sys.modules["src.services.schedule_service"].ScheduleService = FakeScheduleService
sys.modules["src.services.privacy_service"].PrivacyService = FakePrivacyService
sys.modules["src.services.free_time_service"].FreeTimeService = FakeFreeTimeService
sys.modules["src.services.watch_service"].WatchService = FakeWatchService
 
# ---- src.utils.* ----
utils_pkg = _make_module("src.utils")
utils_pkg.__path__ = []
 
formatters_mock = _make_module("src.utils.formatters")
formatters_mock.format_schedule = lambda name, classes, privacy: f"schedule:{name}"
formatters_mock.format_free_time = lambda title, windows, excluded: f"free:{title}"
formatters_mock.format_status = lambda lines: "\n".join(lines)
formatters_mock.text_block = lambda title, lines: f"[{title}] " + " | ".join(lines)
 
time_utils_mock = _make_module("src.utils.time_utils")
time_utils_mock.normalize_days = lambda d: d.upper()
 

# Import bot after all mocks are in place
import bot as bot_module  # noqa: E402
 
# Wire fake singletons into the imported module
_fake_db = FakeDatabase()
_fake_provider = FakeMockDataProvider()
_fake_schedule_svc = FakeScheduleService(_fake_db)
_fake_privacy_svc = FakePrivacyService(_fake_db)
_fake_free_svc = FakeFreeTimeService(_fake_db)
_fake_watch_svc = FakeWatchService(_fake_db, _fake_provider)
 
bot_module.db = _fake_db
bot_module.provider = _fake_provider
bot_module.schedule_service = _fake_schedule_svc
bot_module.privacy_service = _fake_privacy_svc
bot_module.free_time_service = _fake_free_svc
bot_module.watch_service = _fake_watch_svc
 

# Shared fixtures
class DummyCourse:
    crn = "12345"
    course_code = "CS3704"
    title = "Intermediate Software Design and Engineering"
    instructor = "Minhyuk Ko"
    days = "TR"
    start_time = "17:00"
    end_time = "18:15"
    location = "Room 104A"
 
 
def _make_interaction(user_id=1, guild_id=100, display_name="Alice"):
    member = _Member(user_id, display_name)
    resp = types.SimpleNamespace(
        is_done=lambda: False,
        send_message=_AsyncSendMock(),
    )
    return types.SimpleNamespace(
        user=member,
        guild_id=guild_id,
        response=resp,
        followup=types.SimpleNamespace(send=_AsyncSendMock()),
    )
 
 

# Unit Tests
class TestBuildEntryFromCourse(unittest.TestCase):
    """build_entry_from_course is synchronous."""
 
    def test_fields_copied_correctly(self):
        entry = bot_module.build_entry_from_course(42, DummyCourse())
        self.assertEqual(entry.user_id, 42)
        self.assertEqual(entry.crn, "12345")
        self.assertEqual(entry.course_code, "CS3704")
        self.assertEqual(entry.course_title,
                         "Intermediate Software Design and Engineering")
        self.assertEqual(entry.instructor, "Minhyuk Ko")
        self.assertEqual(entry.start_time, "17:00")
        self.assertEqual(entry.end_time, "18:15")
        self.assertEqual(entry.location, "Room 104A")
        self.assertEqual(entry.source, "catalog")
 
    def test_days_are_normalised(self):
        course = DummyCourse()
        course.days = "tr"
        entry = bot_module.build_entry_from_course(1, course)
        self.assertEqual(entry.days, "TR")
 
 
class TestServerConfig(unittest.TestCase):
    """server_config() is synchronous."""
 
    def test_returns_default_when_guild_is_none(self):
        cfg = bot_module.server_config(None)
        self.assertIn("enable_catalog", cfg)
        self.assertIn("poll_interval_seconds", cfg)
 
    def test_returns_db_config_for_guild(self):
        _fake_db.update_server_config(999, enable_catalog=0)
        cfg = bot_module.server_config(999)
        self.assertEqual(cfg["enable_catalog"], 0)
 
 
class TestAddClass(unittest.IsolatedAsyncioTestCase):
 
    def setUp(self):
        _fake_db._classes.clear()
        _fake_provider._courses.clear()
 
    async def test_adds_valid_course(self):
        _fake_provider._seed_course(DummyCourse())
        interaction = _make_interaction(user_id=10)
        await bot_module.addclass(interaction, crn="12345")
 
        saved = _fake_db.get_class(10, "12345")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.course_code, "CS3704")
 
    async def test_rejects_duplicate_crn(self):
        _fake_provider._seed_course(DummyCourse())
        interaction = _make_interaction(user_id=10)
        await bot_module.addclass(interaction, crn="12345")
        await bot_module.addclass(interaction, crn="12345")
 
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("already", msg)
 
    async def test_rejects_unknown_crn(self):
        interaction = _make_interaction(user_id=10)
        await bot_module.addclass(interaction, crn="99999")
 
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("invalid", msg)
 
    async def test_rejects_when_catalog_disabled(self):
        _fake_db.update_server_config(100, enable_catalog=0)
        interaction = _make_interaction(user_id=10, guild_id=100)
        await bot_module.addclass(interaction, crn="12345")
 
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("disabled", msg)
 
        _fake_db.update_server_config(100, enable_catalog=1)

 
class TestRemoveClass(unittest.IsolatedAsyncioTestCase):
 
    def setUp(self):
        _fake_db._classes.clear()
 
    async def test_removes_existing_class(self):
        _fake_db.save_class(bot_module.build_entry_from_course(5, DummyCourse()))
        interaction = _make_interaction(user_id=5)
        await bot_module.removeclass(interaction, crn="12345")
 
        self.assertIsNone(_fake_db.get_class(5, "12345"))
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("removed", msg)
 
    async def test_reports_when_class_not_found(self):
        interaction = _make_interaction(user_id=5)
        await bot_module.removeclass(interaction, crn="00000")
 
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("not currently", msg)
 
 
class TestEditClass(unittest.IsolatedAsyncioTestCase):
 
    def setUp(self):
        _fake_db._classes.clear()
        _fake_db.save_class(bot_module.build_entry_from_course(7, DummyCourse()))
 
    async def test_updates_existing_class(self):
        interaction = _make_interaction(user_id=7)
        await bot_module.editclass(
            interaction, crn="12345", days="MWF",
            start_time="10:00", end_time="11:00", location="New Hall"
        )
        updated = _fake_db.get_class(7, "12345")
        self.assertEqual(updated.days, "MWF")
        self.assertEqual(updated.start_time, "10:00")
        self.assertEqual(updated.location, "New Hall")
 
    async def test_reports_when_class_missing(self):
        interaction = _make_interaction(user_id=7)
        await bot_module.editclass(
            interaction, crn="00000", days="M",
            start_time="09:00", end_time="10:00"
        )
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("not currently", msg)
 
 
class TestPrivacy(unittest.IsolatedAsyncioTestCase):
 
    def setUp(self):
        _fake_db._profiles.clear()
        _fake_db._friends.clear()
 
    async def test_sets_privacy(self):
        _fake_db.upsert_profile(20, "CS", "VT", "Fall 2026")
        interaction = _make_interaction(user_id=20)
        await bot_module.privacy(interaction, setting="private")
        self.assertEqual(_fake_db.get_profile(20).privacy, "private")
 
    def test_public_allows_anyone(self):
        _fake_db.upsert_profile(21, "CS", "VT", "Fall 2026")
        _fake_db.set_privacy(21, "public")
        self.assertTrue(_fake_privacy_svc.can_view_schedule(21, 99))
 
    def test_private_blocks_non_owner(self):
        _fake_db.upsert_profile(22, "CS", "VT", "Fall 2026")
        _fake_db.set_privacy(22, "private")
        self.assertFalse(_fake_privacy_svc.can_view_schedule(22, 99))
 
    def test_friends_only_allows_friend(self):
        _fake_db.upsert_profile(23, "CS", "VT", "Fall 2026")
        _fake_db.set_privacy(23, "friends")
        _fake_db.add_friend(23, 50)
        self.assertTrue(_fake_privacy_svc.can_view_schedule(23, 50))
        self.assertFalse(_fake_privacy_svc.can_view_schedule(23, 51))
 
 
class TestFriends(unittest.IsolatedAsyncioTestCase):
 
    def setUp(self):
        _fake_db._friends.clear()
 
    async def test_addfriend_adds_friend(self):
        interaction = _make_interaction(user_id=1)
        await bot_module.addfriend(interaction, user=_Member(2, "Bob"))
        self.assertIn(2, _fake_db.get_friends(1))
 
    async def test_addfriend_rejects_self(self):
        interaction = _make_interaction(user_id=1)
        await bot_module.addfriend(interaction, user=_Member(1, "Alice"))
        msg = interaction.response.send_message.calls[-1][0][0]
        self.assertIn("yourself", msg)
 
    async def test_removefriend_removes_friend(self):
        _fake_db.add_friend(1, 2)
        interaction = _make_interaction(user_id=1)
        await bot_module.removefriend(interaction, user=_Member(2, "Bob"))
        self.assertNotIn(2, _fake_db.get_friends(1))
