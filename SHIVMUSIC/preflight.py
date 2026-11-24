"""Startup diagnostics and a resilient start() for the main bot client.

Why this exists
---------------
When Telegram cannot be reached (or the credentials are wrong) Pyrogram only
raises a generic

    TimeoutError: Failed to invoke "auth.ImportBotAuthorization" after 10 retries

and the process crashes, so Heroku restarts it again and again. This module

1. validates the config values and prints *what exactly* is wrong,
2. checks that the dyno can reach Telegram (TCP + Bot API) and that the bot
   token is valid, without ever printing the token,
3. starts the client with a few spaced-out retries instead of crash-looping
   (crash loops can also trigger Telegram FLOOD_WAIT on login).
"""

import asyncio
import re
import socket
import time
from email.utils import parsedate_to_datetime
from logging import getLogger

from pyrogram import errors

log = getLogger("SHIVMUSIC.preflight")

# Production Telegram data centers (port 443 is what Pyrogram uses).
TELEGRAM_DCS = (
    ("DC2", "149.154.167.51", 443),
    ("DC4", "149.154.167.91", 443),
    ("DC5", "91.108.56.130", 443),
)

_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{20,}$")
_API_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")

# Errors that retrying can never fix.
_FATAL_NAMES = (
    "AccessTokenInvalid",
    "AccessTokenExpired",
    "ApiIdInvalid",
    "ApiIdPublishedFlood",
)
FATAL_ERRORS = tuple(
    getattr(errors, name) for name in _FATAL_NAMES if hasattr(errors, name)
)

# Errors that mean "Telegram refused the saved *session*" (not the bot token).
_SESSION_NAMES = (
    "AuthKeyUnregistered",
    "AuthKeyInvalid",
    "AuthKeyDuplicated",
    "AuthKeyPermEmpty",
    "SessionRevoked",
    "SessionExpired",
)
SESSION_ERRORS = tuple(
    getattr(errors, name) for name in _SESSION_NAMES if hasattr(errors, name)
)


def validate_config(cfg) -> tuple[list[str], list[str]]:
    """Return (fatal_problems, warnings) for the given config module/object."""
    fatal: list[str] = []
    warn: list[str] = []

    if not getattr(cfg, "API_ID", 0):
        fatal.append("API_ID is missing or 0 (get it from https://my.telegram.org).")

    api_hash = getattr(cfg, "API_HASH", None)
    if not api_hash:
        fatal.append("API_HASH is missing (get it from https://my.telegram.org).")
    elif not _API_HASH_RE.match(api_hash):
        warn.append("API_HASH does not look like a 32-character hex string.")

    token = getattr(cfg, "BOT_TOKEN", None)
    if not token:
        fatal.append("BOT_TOKEN is missing (create one with @BotFather).")
    elif not _TOKEN_RE.match(token):
        warn.append("BOT_TOKEN does not look like '<digits>:<letters/digits>'.")

    if not getattr(cfg, "MONGO_DB_URI", None):
        fatal.append("MONGO_DB_URI is missing.")

    if not getattr(cfg, "LOGGER_ID", 0):
        fatal.append(
            "LOGGER_ID is missing or 0 (the bot sends its start message there)."
        )

    if not getattr(cfg, "STRING1", None):
        fatal.append("STRING_SESSION (assistant session string) is missing.")

    return fatal, warn


async def _check_tcp(name: str, host: str, port: int, timeout: float = 6.0):
    started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return name, True, f"{(time.monotonic() - started) * 1000:.0f} ms"
    except (OSError, asyncio.TimeoutError, socket.gaierror) as exc:
        return name, False, type(exc).__name__


async def check_telegram_reachable() -> bool:
    """TCP-connect to Telegram DCs. True when at least one is reachable."""
    results = await asyncio.gather(*(_check_tcp(*dc) for dc in TELEGRAM_DCS))
    for name, ok, info in results:
        (log.info if ok else log.warning)(
            "Telegram %s port 443: %s (%s)", name, "reachable" if ok else "BLOCKED", info
        )
    reachable = any(ok for _, ok, _ in results)
    if not reachable:
        log.error(
            "No Telegram data center is reachable from this server. "
            "The host/network is blocking Telegram, so login can never succeed. "
            "Try restarting the dyno, another Heroku region, or another host."
        )
    return reachable


async def check_bot_token(token: str) -> str:
    """Ask the Bot API who the token belongs to.

    Returns one of: "ok", "invalid", "unreachable".
    The token itself is never logged.
    """
    try:
        import aiohttp
    except ImportError:  # pragma: no cover - aiohttp is in requirements.txt
        return "unreachable"

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json(content_type=None)
                _warn_on_clock_skew(resp.headers.get("Date"))
                if resp.status == 200 and data.get("ok"):
                    log.info(
                        "Bot token OK: @%s", (data.get("result") or {}).get("username")
                    )
                    return "ok"
                if resp.status in (401, 404):
                    log.error(
                        "Telegram says this BOT_TOKEN is INVALID/REVOKED. "
                        "Get a fresh one from @BotFather and update the config var."
                    )
                    return "invalid"
                log.warning("Bot API answered HTTP %s: %s", resp.status, data)
                return "unreachable"
    except Exception as exc:
        log.warning("Could not reach api.telegram.org: %s", type(exc).__name__)
        return "unreachable"


def _warn_on_clock_skew(date_header):
    if not date_header:
        return
    try:
        skew = abs(time.time() - parsedate_to_datetime(date_header).timestamp())
    except Exception:
        return
    if skew > 30:
        log.warning(
            "System clock differs from Telegram's by ~%.0fs; "
            "large skew can break MTProto login.",
            skew,
        )


async def run_preflight(cfg) -> None:
    """Run all checks. Exits the process only for certain, unfixable problems."""
    fatal, warn = validate_config(cfg)
    for message in warn:
        log.warning(message)
    if fatal:
        for message in fatal:
            log.error("CONFIG ERROR: %s", message)
        raise SystemExit(1)

    reachable = await check_telegram_reachable()
    if reachable and await check_bot_token(cfg.BOT_TOKEN) == "invalid":
        raise SystemExit(1)


async def start_with_retry(client, attempts: int = 5, base_delay: float = 5.0):
    """client.start() with spaced retries and readable error messages."""
    for attempt in range(1, attempts + 1):
        try:
            return await client.start()
        except SESSION_ERRORS as exc:
            if not getattr(client, "_cached_session_used", False):
                raise
            # The saved session is no longer valid: forget it. The process
            # exits and the next start logs in again with the bot token.
            log.warning(
                "Saved Telegram session was rejected (%s) and has been deleted. "
                "Restarting to log in with the bot token.",
                type(exc).__name__,
            )
            try:
                client.on_cached_session_rejected()
            except Exception:
                log.exception("Could not delete the saved session.")
            raise SystemExit(1)
        except FATAL_ERRORS as exc:
            log.error(
                "Login failed permanently: %s. Fix BOT_TOKEN / API_ID / API_HASH "
                "in your config vars.",
                type(exc).__name__,
            )
            raise SystemExit(1)
        except errors.FloodWait as exc:
            wait = int(getattr(exc, "value", 0) or 0) + 2
            log.warning(
                "Telegram FLOOD_WAIT on login: sleeping %ss (attempt %s/%s).",
                wait,
                attempt,
                attempts,
            )
            if attempt == attempts:
                raise
            await asyncio.sleep(wait)
        except (TimeoutError, ConnectionError, OSError) as exc:
            # A half-started client (connected but failed later) is not a
            # network problem; do not try to start it twice.
            if getattr(client, "is_connected", False) or attempt == attempts:
                raise
            delay = min(base_delay * 2 ** (attempt - 1), 60)
            log.warning(
                "Could not log in to Telegram (%s: %s). Retry %s/%s in %.0fs...",
                type(exc).__name__,
                exc,
                attempt,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
