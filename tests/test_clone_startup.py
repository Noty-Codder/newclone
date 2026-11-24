"""Offline tests for session reuse + retry logic of clone startup.

Run:  python -m pytest tests -q      (or call the test_* functions directly)
No Telegram, MongoDB or real pyrogram is needed: small fakes are used.
"""
import asyncio, importlib.util, os, sys, types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _mod(name, **attrs):
    m = types.ModuleType(name)
    m.__path__ = []
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Any:
    """Stand-in for pyrogram filters: supports & | and calls."""
    def __call__(self, *a, **k): return self
    def __and__(self, o): return self
    def __or__(self, o): return self
    def __invert__(self): return self
    def __getattr__(self, n): return self


# ---------------- fake pyrogram ----------------
def _err(name, base=Exception):
    return type(name, (base,), {})

_names = ["AccessTokenExpired", "AccessTokenInvalid", "UserDeactivated", "AuthKeyUnregistered",
          "PeerIdInvalid", "InputUserDeactivated", "UserIsBlocked", "SessionRevoked",
          "UserAlreadyParticipant", "AuthKeyInvalid", "AuthKeyDuplicated", "SessionExpired"]
E = {n: _err(n) for n in _names}


class FloodWait(Exception):
    def __init__(self, value=0): self.value = value

errs = _mod("pyrogram.errors", FloodWait=FloodWait, **E)
pyro = _mod("pyrogram", errors=errs, filters=_Any(), idle=lambda: None)
_mod("pyrogram.types", InlineKeyboardMarkup=object, InlineKeyboardButton=object)
_mod("requests")

# ---------------- fake config / project modules ----------------
_mod("config", API_ID=1, API_HASH="h", OWNER_ID=1, OWNER_USERNAME="o", LOGGER_ID=0,
     CLONE_LOGGER=0, SUPPORT_CHAT="s", SUPPORT_CHANNEL="c", START_IMG_URL=["x"],
     BOT_LINK="l", CLONE_LOGGER_2=0)


class FakeClient:
    script = []          # exceptions to raise on successive start() calls
    made = []

    def __init__(self, *args, **kwargs):
        self.kwargs, self.is_connected = kwargs, False
        self.exported = False
        FakeClient.made.append(self)

    async def start(self):
        if FakeClient.script:
            exc = FakeClient.script.pop(0)
            if exc is not None:
                raise exc
        self.is_connected = True

    async def stop(self): self.is_connected = False
    async def get_me(self): return SimpleNamespace(id=555, username="clonebot")
    async def export_session_string(self): self.exported = True; return "NEW_SESSION"


class FakeApp:
    def on_message(self, *a, **k): return lambda f: f
    async def send_message(self, *a, **k): pass

class FakeColl:
    def __init__(self): self.docs = {}
    def find_one(self, f): return self.docs.get(f["_id"])
    def update_one(self, f, upd, upsert=False):
        self.docs.setdefault(f["_id"], {"_id": f["_id"]}).update(upd["$set"])
    def delete_one(self, f): self.docs.pop(f["_id"], None)

CACHE = FakeColl()
_mod("SHIVMUSIC")
_mod("SHIVMUSIC.core")
_mod("SHIVMUSIC.core.mongo", pymongodb={"bot_session_cache": CACHE})
sc = _load("SHIVMUSIC.core.session_cache", "SHIVMUSIC/core/session_cache.py")

APP = FakeApp()
sys.modules["SHIVMUSIC"].app = APP
_mod("SHIVMUSIC.error_logger", ErrorLoggingClient=FakeClient)
_mod("SHIVMUSIC.utils")
_mod("SHIVMUSIC.utils.database", get_assistant=None, clonebotdb=SimpleNamespace(),
     is_autoplay_on=None, autoplay_on=None, autoplay_off=None)
_mod("SHIVMUSIC.utils.database.clonedb", has_user_cloned_any_bot=None, get_owner_id_from_db=None)
_mod("SHIVMUSIC.utils.decorators")
_mod("SHIVMUSIC.utils.decorators.language", language=lambda f: f)
_mod("SHIVMUSIC.misc", SUDOERS=_Any())

CLEARED = []
async def _clear_dead_clone(**kw): CLEARED.append(kw)
_mod("SHIVMUSIC.utils.clone_guard",
     is_dead_bot_error=lambda e: type(e).__name__ in ("AccessTokenInvalid", "AccessTokenExpired"),
     clear_dead_clone=_clear_dead_clone, _stop_quietly=None)

os.environ["CLONE_START_RETRIES"] = "3"
clone = _load("SHIVMUSIC.plugins_tools_clone_under_test", "SHIVMUSIC/plugins/tools/clone.py")

TOKEN = "555:" + "A" * 35
SCHEDULED = []

async def _fake_delayed(token, session, wait, number, attempt=1):
    SCHEDULED.append((wait, attempt))
clone.delayed_start = _fake_delayed


def _reset(script=()):
    FakeClient.script, FakeClient.made = list(script), []
    SCHEDULED.clear(); CLEARED.clear(); CACHE.docs.clear()
    clone.ACTIVE_CLONES.clear(); clone.CLONES.clear()


def _run(coro):
    async def wrapper():
        result = await coro
        await asyncio.sleep(0)       # let create_task() callbacks run
        return result
    return asyncio.new_event_loop().run_until_complete(wrapper())


def test_fresh_login_saves_session():
    _reset()
    assert _run(clone._start_clone_runtime(TOKEN, None, 1)) is True
    c = FakeClient.made[0]
    assert c.kwargs["session_string"] is None and c.exported
    assert sc.get_cached_session(TOKEN) == "NEW_SESSION"
    assert 555 in clone.ACTIVE_CLONES


def test_cached_session_is_reused_without_new_login():
    _reset()
    sc.save_cached_session(TOKEN, "OLD_SESSION")
    assert _run(clone._start_clone_runtime(TOKEN, None, 1)) is True
    c = FakeClient.made[0]
    assert c.kwargs["session_string"] == "OLD_SESSION" and not c.exported


def test_cache_is_bound_to_the_token():
    _reset()
    sc.save_cached_session(TOKEN, "OLD_SESSION")
    assert sc.get_cached_session("555:" + "B" * 35) is None   # regenerated token


def test_timeout_is_retried_with_backoff_not_skipped():
    _reset([TimeoutError("x")])
    assert _run(clone._start_clone_runtime(TOKEN, None, 1)) is False
    assert SCHEDULED == [(30, 2)]
    assert not FakeClient.made[0].is_connected          # half-started client cleaned up


def test_gives_up_after_last_attempt():
    _reset([TimeoutError("x")])
    assert _run(clone._start_clone_runtime(TOKEN, None, 1, attempt=3)) is False
    assert SCHEDULED == []


def test_floodwait_is_retried_after_the_wait():
    _reset([FloodWait(100)])
    _run(clone._start_clone_runtime(TOKEN, None, 1))
    assert SCHEDULED == [(106, 2)]


def test_rejected_cached_session_falls_back_to_token_login():
    _reset([E["AuthKeyUnregistered"]()])
    sc.save_cached_session(TOKEN, "OLD_SESSION")
    _run(clone._start_clone_runtime(TOKEN, None, 1))
    assert sc.get_cached_session(TOKEN) is None          # dropped
    assert SCHEDULED == [(1, 1)] and CLEARED == []       # retried, NOT deleted from DB


def test_dead_token_is_cleared_and_not_retried():
    _reset([E["AccessTokenInvalid"]()])
    _run(clone._start_clone_runtime(TOKEN, None, 1))
    assert len(CLEARED) == 1 and SCHEDULED == []


def test_already_running_clone_is_not_started_twice():
    _reset()
    clone.ACTIVE_CLONES[555] = SimpleNamespace(is_connected=True)
    assert _run(clone._start_clone_runtime(TOKEN, None, 1)) is True
    assert FakeClient.made == []
