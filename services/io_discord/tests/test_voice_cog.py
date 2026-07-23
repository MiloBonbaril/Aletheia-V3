import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("DISCORD_USER_ID", "1")
os.environ.setdefault("TEXT_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_GUILD_ID", "42")

import cogs.voice as voice_cog


class FakeMsg:
    def __init__(self, payload: dict):
        self.data = json.dumps(payload).encode()


class FakeVoiceClient:
    def __init__(self, connected: bool):
        self._connected = connected
        self.played = []

    def is_connected(self):
        return self._connected

    def play(self, source, after=None):
        self.played.append(source)
        if after:
            after(None)


class FakeGuild:
    def __init__(self, voice_client):
        self.voice_client = voice_client


class FakeBot:
    def __init__(self, guild):
        self.loop = asyncio.get_running_loop()
        self._guild = guild

    def get_guild(self, guild_id):
        return self._guild


def make_cog(vc):
    cog = voice_cog.Voice.__new__(voice_cog.Voice)
    cog.logger = logging.getLogger("test.voice")
    cog.bot = FakeBot(FakeGuild(vc))
    cog.speak_queue = asyncio.Queue()
    return cog


def test_on_speak_audio_queues_decoded_audio():
    async def scenario():
        cog = make_cog(FakeVoiceClient(connected=True))
        await cog._on_speak_audio(FakeMsg({"audio": "AQI=", "sequence": 1}))
        assert cog.speak_queue.get_nowait() == b"\x01\x02"

    asyncio.run(scenario())


def test_on_speak_audio_ignores_silence_fragment():
    async def scenario():
        cog = make_cog(FakeVoiceClient(connected=True))
        await cog._on_speak_audio(FakeMsg({"sequence": 1, "is_last": True}))
        assert cog.speak_queue.empty()

    asyncio.run(scenario())


async def _drain_one(cog):
    task = asyncio.ensure_future(cog._playback_loop())
    await asyncio.wait_for(cog.speak_queue.join(), timeout=1)
    task.cancel()


def test_playback_loop_plays_into_connected_voice_client(monkeypatch):
    monkeypatch.setattr(
        voice_cog.discord, "FFmpegPCMAudio", lambda source, pipe: source.read()
    )

    async def scenario():
        vc = FakeVoiceClient(connected=True)
        cog = make_cog(vc)
        cog.speak_queue.put_nowait(b"wav-bytes")
        await _drain_one(cog)
        assert vc.played == [b"wav-bytes"]

    asyncio.run(scenario())


def test_playback_loop_drops_audio_when_not_connected(monkeypatch):
    monkeypatch.setattr(
        voice_cog.discord, "FFmpegPCMAudio", lambda source, pipe: source.read()
    )

    async def scenario():
        vc = FakeVoiceClient(connected=False)
        cog = make_cog(vc)
        cog.speak_queue.put_nowait(b"wav-bytes")
        await _drain_one(cog)
        assert vc.played == []

    asyncio.run(scenario())
