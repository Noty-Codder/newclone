"""Offline tests for SHIVMUSIC.preflight (no Telegram / pyrogram needed)."""
import asyncio, importlib.util, os, sys, types
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_preflight():
    errs = types.ModuleType("pyrogram.errors")

    class FloodWait(Exception):
        def __init__(self, value=0): self.value = value

    class AccessTokenInvalid(Exception): pass

    class AuthKeyUnregistered(Exception): pass

    errs.FloodWait, errs.AccessTokenInvalid = FloodWait, AccessTokenInvalid
    errs.AuthKeyUnregistered = AuthKeyUnregistered
    pkg = types.ModuleType("pyrogram"); pkg.errors = errs
    sys.modules["pyrogram"], sys.modules["pyrogram.errors"] = pkg, errs
    spec = importlib.util.spec_from_file_location(
        "preflight_under_test", os.path.join(ROOT, "SHIVMUSIC", "preflight.py"))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod, errs


pf, errs = _load_preflight()
GOOD = SimpleNamespace(API_ID=1, API_HASH="a" * 32, BOT_TOKEN="123456:" + "A" * 35,
                       MONGO_DB_URI="mongodb://x", LOGGER_ID=-100, STRING1="s")


def test_valid_config():
    assert pf.validate_config(GOOD) == ([], [])


def test_missing_values_are_fatal():
    bad = SimpleNamespace(API_ID=0, API_HASH=None, BOT_TOKEN="", MONGO_DB_URI=None,
                          LOGGER_ID=0, STRING1="")
    fatal, _ = pf.validate_config(bad)
    assert len(fatal) == 6


def test_odd_format_only_warns():
    odd = SimpleNamespace(**{**GOOD.__dict__, "API_HASH": "short", "BOT_TOKEN": "nope"})
    fatal, warn = pf.validate_config(odd)
    assert fatal == [] and len(warn) == 2


class FakeClient:
    def __init__(self, failures): self.failures, self.calls, self.is_connected = failures, 0, False
    async def start(self):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return "started"


def _run(coro): return asyncio.new_event_loop().run_until_complete(coro)


def test_retry_then_success():
    c = FakeClient([TimeoutError("x"), TimeoutError("x")])
    assert _run(pf.start_with_retry(c, attempts=5, base_delay=0)) == "started"
    assert c.calls == 3


def test_fatal_error_exits_without_retry():
    c = FakeClient([errs.AccessTokenInvalid()])
    try:
        _run(pf.start_with_retry(c, attempts=5, base_delay=0)); assert False
    except SystemExit:
        pass
    assert c.calls == 1


def test_gives_up_after_attempts():
    c = FakeClient([TimeoutError("x")] * 3)
    try:
        _run(pf.start_with_retry(c, attempts=3, base_delay=0)); assert False
    except TimeoutError:
        pass
    assert c.calls == 3


def test_rejected_saved_session_is_dropped_then_process_restarts():
    c = FakeClient([errs.AuthKeyUnregistered()])
    c._cached_session_used, c.dropped = True, False
    c.on_cached_session_rejected = lambda: setattr(c, "dropped", True)
    try:
        _run(pf.start_with_retry(c, attempts=5, base_delay=0)); assert False
    except SystemExit:
        pass
    assert c.dropped and c.calls == 1


def test_auth_key_error_without_saved_session_is_raised_normally():
    c = FakeClient([errs.AuthKeyUnregistered()])
    try:
        _run(pf.start_with_retry(c, attempts=5, base_delay=0)); assert False
    except errs.AuthKeyUnregistered:
        pass
