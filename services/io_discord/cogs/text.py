import asyncio
import io
import json
import logging
import sys
import time
from collections import deque
from typing import Deque, Dict, Optional

import discord
from discord.ext import commands

from config import Config



class Text(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("Text")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        )
        self.logger.addHandler(handler)

        self.logger.info("Text cog initialized.")

    def cleanup(self) -> None:
        self.logger.info("Cleaning up Text cog resources.")
        self.logger.handlers.clear()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not self.chat_activated:
            return

        if not message.guild or message.guild.id != Config.GUILD_ID:
            return

        if message.author.bot or message.author == self.bot.user:
            return

        if not message.content and not message.stickers:
            return
        channel_id = message.channel.id

    @text.command(
        guild_ids=[Config.GUILD_ID],
        name="activate_chat",
        description="Activate the ability to chat with the assistant",
    )
    async def text_chat(
        self, ctx: discord.ApplicationContext, force_state: Optional[bool] = None
    ) -> None:
        """
        Toggle chat mode for Texte, optionally forcing the state.
        """
        self.logger.debug(
            "received the activate_chat command with force_state = %s", force_state
        )
        if force_state is None:
            self.chat_activated = not self.chat_activated
        else:
            self.chat_activated = force_state
        state = "on" if self.chat_activated else "off"
        await ctx.respond(f"chat mode set to: {state}")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Text(bot))

def teardown(bot: commands.Bot) -> None:
    cog = bot.get_cog("Text")
    if cog:
        cog.cleanup()
    try:
        bot.remove_cog("Text")
    except Exception as exc:
        print(f"Error removing cog Text: {exc}")
