import logging
import os

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        logging.FileHandler("log.txt"),
        logging.StreamHandler(),
    ],
)

logging.getLogger("httpx").setLevel(logging.ERROR)
# WARNING (not ERROR) so Telegram connection problems such as transport
# errors and "Retrying ..." reasons are visible in the logs. Set the env var
# PYROGRAM_LOG_LEVEL=INFO to see every retry while debugging.
logging.getLogger("pyrogram").setLevel(
    getattr(logging, os.getenv("PYROGRAM_LOG_LEVEL", "WARNING").upper(), logging.WARNING)
)
logging.getLogger("pytgcalls").setLevel(logging.ERROR)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Forward every ERROR/CRITICAL record to the Telegram error group once a
# client has started. The handler is safe before startup and never raises back
# into the application's normal logging path.
from .error_logger import install_logging_handler

install_logging_handler()
