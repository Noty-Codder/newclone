"""Tests for the automatic blockquote UI (run: python tests/test_blockquote.py)."""
import asyncio
import enum
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    # --- minimal stubs so the module can be imported without pyrogram ----
    class ParseMode(enum.Enum):
        DEFAULT = "d"
        HTML = "h"
        MARKDOWN = "m"
        DISABLED = "x"

    class Client:
        def __init__(self, *a, **k):
            self.bot_token = k.get("bot_token")
            self.calls = []

        def add_handler(self, h, group=0):
            return h

        async def start(self, *a, **k):
            return None

        async def send_message(self, chat_id, text, parse_mode=None, entities=None, **k):
            self.calls.append(("send_message", text))

        async def edit_message_text(self, chat_id, message_id, text, parse_mode=None, entities=None, **k):
            self.calls.append(("edit_message_text", text))

        async def edit_message_caption(self, chat_id, message_id, caption, parse_mode=None, caption_entities=None, **k):
            self.calls.append(("edit_message_caption", caption))

        async def send_photo(self, chat_id, photo, caption="", parse_mode=None, caption_entities=None, **k):
            self.calls.append(("send_photo", caption))

        send_video = send_animation = send_audio = send_document = send_photo

        async def edit_message_media(self, chat_id, message_id, media, **k):
            self.calls.append(("edit_message_media", media.caption))

    pg = types.ModuleType("pyrogram"); pg.Client = Client
    en = types.ModuleType("pyrogram.enums"); en.ParseMode = ParseMode
    cfg = types.ModuleType("config")
    sys.modules.update({"pyrogram": pg, "pyrogram.enums": en, "config": cfg})

    pkg = types.ModuleType("SHIVMUSIC"); pkg.__path__ = [os.path.join(ROOT, "SHIVMUSIC")]
    sys.modules["SHIVMUSIC"] = pkg
    for name in ("blockquote", "error_logger"):
        spec = importlib.util.spec_from_file_location(
            f"SHIVMUSIC.{name}", os.path.join(ROOT, "SHIVMUSIC", f"{name}.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"SHIVMUSIC.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["SHIVMUSIC.blockquote"], sys.modules["SHIVMUSIC.error_logger"], ParseMode


bq, el, ParseMode = _load()


def test_helper():
    assert bq.wrap_quote("hi") == "<blockquote>hi</blockquote>"
    assert bq.wrap_quote("  hi \n") == "<blockquote>hi</blockquote>"
    assert bq.wrap_quote("") == ""
    assert bq.wrap_quote("<blockquote>a</blockquote>") == "<blockquote>a</blockquote>"
    assert bq.wrap_quote("x <BLOCKQUOTE>a</BLOCKQUOTE>").count("<blockquote") == 0  # untouched
    assert bq.parse_mode_allows_quote(None)
    assert bq.parse_mode_allows_quote(ParseMode.HTML)
    assert not bq.parse_mode_allows_quote(ParseMode.MARKDOWN)
    assert not bq.parse_mode_allows_quote(ParseMode.DISABLED)


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_bot_client_wraps_everything():
    c = el.ErrorLoggingClient(bot_token="123:abc")

    async def go():
        await c.send_message(1, "plain")
        await c.send_message(chat_id=1, text="kw")
        await c.edit_message_text(1, 2, "edit")
        await c.edit_message_caption(1, 2, "cap")
        await c.send_photo(1, "p", caption="photo cap")
        await c.send_video(1, "v", "positional cap")
        await c.edit_message_media(1, 2, types.SimpleNamespace(caption="media cap", caption_entities=None, parse_mode=None))
    run(go())
    assert all(t.startswith("<blockquote>") and t.endswith("</blockquote>") for _, t in c.calls), c.calls
    assert len(c.calls) == 7


def test_skips():
    c = el.ErrorLoggingClient(bot_token="123:abc")

    async def go():
        await c.send_message(1, "x", quote_wrap=False)               # opt-out
        await c.send_message(1, "x", parse_mode=ParseMode.MARKDOWN)  # markdown
        await c.send_message(1, "x", entities=[object()])            # entities
        await c.send_message(1, "<blockquote>q</blockquote>")        # already quoted
        with bq.no_quote():
            await c.send_message(1, "x")                             # context opt-out
    run(go())
    assert [t for _, t in c.calls] == ["x", "x", "x", "<blockquote>q</blockquote>", "x"], c.calls


def test_assistant_client_untouched():
    c = el.ErrorLoggingClient()  # no bot_token -> userbot / assistant
    run(c.send_message(1, "hello"))
    assert c.calls == [("send_message", "hello")]


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("ok", n)
