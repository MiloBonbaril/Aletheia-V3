import logging
import sys
import json

import nats
import discord
from discord.ext import commands

from config import Config
from presence import compute_occupancy


class Presence(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("Presence")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        )
        self.logger.addHandler(handler)

        self.logger.info("Presence cog initialized.")

        self.nc = None
        # None tant qu'aucun état n'a encore été publié, pour forcer la première publication.
        self.last_occupied = None

        self.bot.loop.create_task(self.setup_nats())

    async def setup_nats(self):
        try:
            self.nc = await nats.connect("nats://localhost:4222")
            self.logger.info("Connected to NATS.")
        except Exception as e:
            self.logger.error(f"Failed to connect to NATS: {e}")

    def cog_unload(self) -> None:
        self.logger.info("Cleaning up Presence cog resources.")
        if self.nc and not self.nc.is_closed:
            self.bot.loop.create_task(self.nc.close())
        self.logger.handlers.clear()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if not member.guild or member.guild.id != Config.GUILD_ID:
            return

        members_in_voice = [
            m for channel in member.guild.voice_channels for m in channel.members
        ]
        occupied = compute_occupancy(members_in_voice)

        if occupied == self.last_occupied or not self.nc:
            return

        try:
            await self.nc.publish(
                "io.presence.discord_voice",
                json.dumps({"occupied": occupied}).encode(),
            )
            self.last_occupied = occupied
            self.logger.debug(f"Published presence {occupied} to NATS.")
        except Exception as e:
            self.logger.error(f"Failed to publish presence to NATS: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Presence(bot))
