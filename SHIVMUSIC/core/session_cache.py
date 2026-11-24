"""Reuse Telegram sessions of bot-token clients across restarts.

Problem
-------
Every client is created with ``in_memory=True``. After each restart, crash or
deploy every bot (the main bot and *every clone*) therefore creates a brand
new auth key and calls ``auth.ImportBotAuthorization`` again. Telegram
rate-limits exactly this (FLOOD_WAIT / silent timeouts), which shows up as

    TimeoutError: Failed to invoke "auth.ImportBotAuthorization" after 10 retries

and a crash loop only makes it worse.

Fix
---
After a successful login the session string is saved in MongoDB. The next
start passes it as ``session_string=`` so Pyrogram skips the bot-token login
completely. If Telegram rejects the cached session it is dropped and a normal
token login is done instead.

The cache entry is bound to a hash of ``API_ID:token`` (the token itself is
never stored here), so a regenerated token or changed API_ID never reuses an
old session.
"""

import asyncio
import hashlib
import logging
from typing import Optional

log = logging.getLogger("SHIVMUSIC.session_cache")

COLLECTION = "bot_session_cache"

_AUTH_KEY_ERROR_NAMES = (
    "AuthKeyUnregistered",
    "AuthKeyInvalid",
    "AuthKeyDuplicated",
    "AuthKeyPermEmpty",
    "SessionRevoked",
    "SessionExpired",
)
_AUTH_KEY_TEXT = (
    "AUTH_KEY_UNREGISTERED",
    "AUTH_KEY_INVALID",
    "AUTH_KEY_DUPLICATED",
    "AUTH_KEY_PERM_EMPTY",
    "SESSION_REVOKED",
    "SESSION_EXPIRED",
)


def is_auth_key_error(exc) -> bool:
    """True when Telegram rejected the *session* (not the bot token)."""
    if exc is None:
        return False
    try:
        from pyrogram import errors

        classes = tuple(
            getattr(errors, n) for n in _AUTH_KEY_ERROR_NAMES if hasattr(errors, n)
        )
        if classes and isinstance(exc, classes):
            return True
    except Exception:
        pass
    text = f"{type(exc).__name__} {exc}".upper().replace(" ", "_")
    return any(t in text for t in _AUTH_KEY_TEXT)


def _collection():
    from SHIVMUSIC.core.mongo import pymongodb

    return pymongodb[COLLECTION]


def _identity(token: str):
    import config

    bot_id = str(token).split(":", 1)[0]
    digest = hashlib.sha256(f"{config.API_ID}:{token}".encode()).hexdigest()
    return bot_id, digest


def get_cached_session(token: Optional[str]) -> Optional[str]:
    """Blocking lookup. Never raises; returns None when nothing usable."""
    if not token:
        return None
    try:
        bot_id, digest = _identity(token)
        doc = _collection().find_one({"_id": bot_id})
        if doc and doc.get("digest") == digest and doc.get("session"):
            return doc["session"]
    except Exception as exc:
        log.warning("Session cache read failed (%s); using a fresh login.", type(exc).__name__)
    return None


def save_cached_session(token: Optional[str], session_string: Optional[str]) -> bool:
    if not token or not session_string:
        return False
    try:
        bot_id, digest = _identity(token)
        _collection().update_one(
            {"_id": bot_id},
            {"$set": {"digest": digest, "session": session_string}},
            upsert=True,
        )
        return True
    except Exception as exc:
        log.warning("Session cache write failed (%s).", type(exc).__name__)
        return False


def drop_cached_session(token: Optional[str]) -> None:
    if not token:
        return
    try:
        bot_id, _ = _identity(token)
        _collection().delete_one({"_id": bot_id})
    except Exception as exc:
        log.warning("Session cache delete failed (%s).", type(exc).__name__)


# Non-blocking wrappers for use inside the event loop.
async def aget_cached_session(token):
    return await asyncio.to_thread(get_cached_session, token)


async def adrop_cached_session(token):
    await asyncio.to_thread(drop_cached_session, token)


async def remember_session(client, token) -> bool:
    """Export the logged-in client's session and store it. Never raises."""
    try:
        session_string = await client.export_session_string()
    except Exception as exc:
        log.warning("Could not export session (%s).", type(exc).__name__)
        return False
    return await asyncio.to_thread(save_cached_session, token, session_string)
