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