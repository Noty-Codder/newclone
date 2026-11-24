"""Central blockquote styling for every message the bot sends.

Telegram renders ``<blockquote>`` as the coloured vertical-bar card seen in
the reference UI.  Instead of hand editing hundreds of strings, the bot client
(see ``ErrorLoggingClient`` in ``error_logger.py``) runs every outgoing text /
caption through :func:`wrap_quote`.

Rules:
* Text that already contains a ``<blockquote`` is left untouched, so screens
  with a hand-made multi-quote layout (like the start screen) keep it and we
  never nest quotes (Telegram does not support nested blockquotes).
* Empty text is never wrapped.
* Markdown-only / entity based messages are skipped by the client hook.
* Pass ``quote_wrap=False`` to any send/edit call (or use :func:`no_quote`)
  to send raw text, e.g. for broadcasts of user supplied content.
"""

import contextvars
import re
from contextlib import contextmanager
from typing import Any, Optional

_HAS_QUOTE = re.compile(r"<\s*blockquote", re.IGNORECASE)

# When True, nothing is wrapped for the current task/context.
_skip_quote: contextvars.ContextVar = contextvars.ContextVar(
    "shivmusic_skip_blockquote", default=False
)


@contextmanager
def no_quote():
    """Temporarily disable automatic blockquote wrapping."""
    token = _skip_quote.set(True)
    try:
        yield
    finally:
        _skip_quote.reset(token)


def quoting_disabled() -> bool:
    return _skip_quote.get()


def has_quote(text: Any) -> bool:
    return isinstance(text, str) and bool(_HAS_QUOTE.search(text))


def wrap_quote(text: Any, expandable: bool = False) -> Any:
    """Return ``text`` wrapped in ``<blockquote>`` (HTML) when appropriate."""
    if not isinstance(text, str):
        return text

    body = text.strip()
    if not body or has_quote(body):
        return text

    tag = "blockquote expandable" if expandable else "blockquote"
    return f"<{tag}>{body}</{tag}>"


def parse_mode_allows_quote(parse_mode: Optional[Any]) -> bool:
    """HTML (and the library default, which understands HTML) can carry quotes.

    Explicit Markdown or "no parsing" modes cannot, so those are skipped.
    """
    if parse_mode is None:
        return True
    name = getattr(parse_mode, "name", str(parse_mode)).upper()
    return name in ("HTML", "DEFAULT")
