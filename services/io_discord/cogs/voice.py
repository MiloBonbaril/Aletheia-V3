import asyncio
import logging
import sys

import discord
from discord.ext import commands
from discord.sinks.errors import RecordingException

from config import Config
from voice import build_recording_attachments, format_recording_summary

# Discord's voice mode negotiation (discord/gateway.py's initial_connection) picks the
# first of its own offered modes that's also in VoiceClient.supported_modes — and the
# default list includes the newer AEAD mode "aead_xchacha20_poly1305_rtpsize", which is
# unreliable on this py-cord 2.7.0rc1 build (voice handshake never completes / garbled
# audio). Dropping it forces negotiation onto the older, stable non-AEAD modes.
discord.VoiceClient.supported_modes = (
    "xsalsa20_poly1305_lite",
    "xsalsa20_poly1305_suffix",
    "xsalsa20_poly1305",
)


class Voice(commands.Cog):
    voice = discord.SlashCommandGroup(
        "voice",
        "Commands for the voice cog",
        guild_ids=[Config.GUILD_ID],
    )

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger("Voice")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s:%(levelname)s:%(name)s: %(message)s")
        )
        self.logger.addHandler(handler)

        self.logger.info("Voice cog initialized.")

    def cleanup(self) -> None:
        self.logger.info("Cleaning up Voice cog resources.")
        self.logger.handlers.clear()

    @voice.command(
        guild_ids=[Config.GUILD_ID],
        name="join",
        description="Join the voice channel you're currently in",
    )
    async def voice_join(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer()
        if not ctx.author.voice:
            await ctx.respond("You need to be in a voice channel first.")
            return

        channel = ctx.author.voice.channel
        try:
            if ctx.voice_client:
                await ctx.voice_client.move_to(channel)
            else:
                await channel.connect()
        except (asyncio.TimeoutError, discord.ClientException) as e:
            self.logger.error(f"Failed to join {channel.name}: {e}")
            await ctx.respond(f"Couldn't join {channel.name} — voice connection failed ({e}).")
            return

        vc = ctx.voice_client
        # py-cord's connect() can exhaust its internal retries on the voice
        # websocket step without raising, leaving a client that looks present
        # but never finished the handshake — don't trust the lack of exception.
        if not vc or not vc.is_connected():
            self.logger.error(f"Voice handshake with {channel.name} never completed.")
            if vc:
                await vc.disconnect(force=True)
            await ctx.respond(
                f"Joined {channel.name} but the voice link never came up — "
                "likely a network/route issue to Discord's voice servers. Try again?"
            )
            return

        await ctx.respond(f"Joined {channel.name}.")

    @voice.command(
        guild_ids=[Config.GUILD_ID],
        name="leave",
        description="Leave the current voice channel",
    )
    async def voice_leave(self, ctx: discord.ApplicationContext) -> None:
        await ctx.defer()
        vc = ctx.voice_client
        if not vc:
            await ctx.respond("Not currently in a voice channel.")
            return
        if vc.recording:
            vc.stop_recording()
        await vc.disconnect()
        await ctx.respond("Left the voice channel.")

    @voice.command(
        guild_ids=[Config.GUILD_ID],
        name="record",
        description="Record a sample of the voice channel and upload it as mp3",
    )
    async def voice_record(
        self, ctx: discord.ApplicationContext, duration: int = 10
    ) -> None:
        vc = ctx.voice_client
        if not vc or not vc.is_connected():
            await ctx.respond("Not in a voice channel — use /voice join first.")
            return
        if vc.recording:
            await ctx.respond("Already recording.")
            return

        await ctx.respond(f"Recording for {duration}s...")
        vc.start_recording(discord.sinks.MP3Sink(), self._on_recording_finished, ctx.channel)
        await asyncio.sleep(duration)
        try:
            if vc.recording:
                vc.stop_recording()
        except RecordingException:
            pass  # already stopped by /voice leave or a disconnect while we were sleeping

    async def _on_recording_finished(self, sink, channel: discord.TextChannel) -> None:
        attachments = build_recording_attachments(sink.audio_data, sink.encoding)
        summary = format_recording_summary(sink.audio_data.keys())
        files = [discord.File(file_obj, filename) for filename, file_obj in attachments]
        try:
            await channel.send(summary, files=files)
        except Exception as e:
            self.logger.error(f"Failed to send recording: {e}")


def setup(bot: commands.Bot) -> None:
    bot.add_cog(Voice(bot))


def teardown(bot: commands.Bot) -> None:
    cog = bot.get_cog("Voice")
    if cog:
        cog.cleanup()
    try:
        bot.remove_cog("Voice")
    except Exception as exc:
        print(f"Error removing cog Voice: {exc}")
