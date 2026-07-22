import asyncio
import logging
import sys

import discord
from discord import app_commands
from discord.ext import commands, voice_recv

from config import Config
from voice import build_recording_attachments, format_recording_summary


class _RecordSink(voice_recv.AudioSink):
    """Accumulates decoded PCM (48kHz s16le stereo) per speaking user."""

    def __init__(self) -> None:
        super().__init__()
        self.pcm_by_user: dict[int, bytearray] = {}

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data) -> None:
        if user is not None:
            self.pcm_by_user.setdefault(user.id, bytearray()).extend(data.pcm)

    def cleanup(self) -> None:
        pass


class Voice(commands.Cog):
    voice = app_commands.Group(
        name="voice",
        description="Commands for the voice cog",
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

    def cog_unload(self) -> None:
        self.logger.info("Cleaning up Voice cog resources.")
        self.logger.handlers.clear()

    async def _join_vc(self, interaction: discord.Interaction) -> dict:
        """Join or move to the author's current voice channel."""
        member = interaction.user
        if not isinstance(member, discord.Member) or not member.voice or not member.voice.channel:
            return {"status": "error", "message": "You need to be in a voice channel first."}

        dest = member.voice.channel
        vc = interaction.guild.voice_client
        try:
            if vc and vc.is_connected():
                if vc.channel.id != dest.id:
                    await vc.move_to(dest)
            else:
                # VoiceRecvClient is required for audio receive; explicit timeout
                # (shorter than the 60s default) so a bad connection fails faster.
                vc = await dest.connect(
                    reconnect=True, timeout=30, cls=voice_recv.VoiceRecvClient
                )
        except (asyncio.TimeoutError, discord.ClientException) as e:
            self.logger.error(f"Failed to join {dest.name}: {e}")
            return {"status": "error", "message": f"Couldn't join {dest.name} — voice connection failed ({e})."}

        # Don't trust the lack of exception: verify the handshake completed.
        if not vc.is_connected():
            self.logger.error(f"Voice handshake with {dest.name} never completed.")
            await vc.disconnect(force=True)
            return {
                "status": "error",
                "message": (
                    f"Joined {dest.name} but the voice link never came up — "
                    "likely a network/route issue to Discord's voice servers."
                ),
            }

        return {"status": "success", "voice_client": vc, "channel": dest}

    @voice.command(name="join", description="Join the voice channel you're currently in")
    async def voice_join(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        result = await self._join_vc(interaction)
        if result["status"] != "success":
            await interaction.followup.send(result["message"])
            return
        await interaction.followup.send(f"Joined {result['channel'].name}.")

    @voice.command(name="leave", description="Leave the current voice channel")
    async def voice_leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        vc = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("Not currently in a voice channel.")
            return
        if vc.is_listening():
            vc.stop_listening()
        await vc.disconnect()
        await interaction.followup.send("Left the voice channel.")

    @voice.command(
        name="record",
        description="Record a sample of the voice channel and upload it as mp3",
    )
    async def voice_record(
        self, interaction: discord.Interaction, duration: int = 10
    ) -> None:
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            await interaction.response.send_message("Not in a voice channel — use /voice join first.")
            return
        if vc.is_listening():
            await interaction.response.send_message("Already recording.")
            return

        await interaction.response.send_message(f"Recording for {duration}s...")
        sink = _RecordSink()
        vc.listen(sink)
        await asyncio.sleep(duration)
        if vc.is_listening():
            vc.stop_listening()

        # ffmpeg encoding is blocking — keep it off the event loop
        attachments = await asyncio.to_thread(build_recording_attachments, sink.pcm_by_user)
        summary = format_recording_summary(sink.pcm_by_user.keys())
        files = [discord.File(file_obj, filename) for filename, file_obj in attachments]
        try:
            await interaction.channel.send(summary, files=files)
        except Exception as e:
            self.logger.error(f"Failed to send recording: {e}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))
