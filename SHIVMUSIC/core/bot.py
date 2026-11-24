from pyrogram import errors
from pyrogram.enums import ChatMemberStatus, ParseMode

import config

from ..logging import LOGGER
from ..error_logger import ErrorLoggingClient
from .session_cache import drop_cached_session, get_cached_session, remember_session


class ANJALI(ErrorLoggingClient):
    def __init__(self):
        LOGGER(__name__).info(f"Starting Bot...")
        # Reuse the saved Telegram session when we have one, so a restart does
        # not need a new auth.ImportBotAuthorization (the call that times out /
        # gets rate-limited when the bot restarts often).
        cached = get_cached_session(config.BOT_TOKEN)
        self._cached_session_used = bool(cached)
        if cached:
            LOGGER(__name__).info("Using saved Telegram session for the main bot.")
        super().__init__(
            name="SHIVMUSIC",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            session_string=cached or None,
            in_memory=True,
            max_concurrent_transmissions=7,
        )

    def on_cached_session_rejected(self):
        """Called by preflight.start_with_retry when Telegram refuses the saved
        session. Drop it so the next start does a normal token login."""
        drop_cached_session(config.BOT_TOKEN)

    async def start(self):
        await super().start()
        if not self._cached_session_used:
            await remember_session(self, config.BOT_TOKEN)
        self.id = self.me.id
        self.name = self.me.first_name + " " + (self.me.last_name or "")
        self.username = self.me.username
        self.mention = self.me.mention

        try:
            await self.send_message(
                chat_id=config.LOGGER_ID,
                text=f"<u><b>» {self.mention} ʙᴏᴛ sᴛᴀʀᴛᴇᴅ :</b><u>\n\nɪᴅ : <code>{self.id}</code>\nɴᴀᴍᴇ : {self.name}\nᴜsᴇʀɴᴀᴍᴇ : @{self.username}",
            )
        except (errors.ChannelInvalid, errors.PeerIdInvalid):
            LOGGER(__name__).error(
                "Bot has failed to access the log group/channel. Make sure that you have added your bot to your log group/channel."
            )
            exit()
        except Exception as ex:
            LOGGER(__name__).error(
                f"Bot has failed to access the log group/channel.\n  Reason : {type(ex).__name__}."
            )
            exit()

        a = await self.get_chat_member(config.LOGGER_ID, self.id)
        if a.status != ChatMemberStatus.ADMINISTRATOR:
            LOGGER(__name__).error(
                "Please Promote your bot as an admin in your log group/channel."
            )
            exit()
        LOGGER(__name__).info(f"Music Bot Started as {self.name}")

    async def stop(self):
        await super().stop()