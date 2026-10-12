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


class FakeNC:
    def __init__(self):
        self.published = []

    async def publish(self, subject, data):
        self.published.append((subject, json.loads(data.decode())))


def make_cog(vc):
    cog = voice_cog.Voice.__new__(voice_cog.Voice)
    cog.logger = logging.getLogger("test.voice")
    cog.bot = FakeBot(FakeGuild(vc))
    cog.speak_queue = asyncio.Queue()
    cog.nc = FakeNC()
    return cog


def test_on_speak_audio_queues_decoded_audio():
    async def scenario():
        cog = make_cog(FakeVoiceClient(connected=True))
        await cog._on_speak_audio(FakeMsg({"audio": "AQI=", "sequence": 1}))
        assert cog.speak_queue.get_nowait() == (b"\x01\x02", {"audio": "AQI=", "sequence": 1})

    asyncio.run(scenario())


def test_on_speak_audio_ignores_silence_fragment():
    async def scenario():
        cog = make_cog(FakeVoiceClient(connected=True))
        await cog._on_speak_audio(FakeMsg({"sequence": 1, "is_last": False}))
        assert cog.speak_queue.empty()

    asyncio.run(scenario())


def test_on_speak_audio_keeps_an_empty_last_fragment():
    """La fin d'une réponse arrive souvent sans audio : elle doit quand même passer, sinon
    `io.voice.speak.end` ne part jamais et le lobe_frontal attend son plafond."""
    async def scenario():
        cog = make_cog(FakeVoiceClient(connected=True))
        await cog._on_speak_audio(FakeMsg({"sequence": 3, "audio": "", "is_last": True}))
        assert cog.speak_queue.get_nowait() == (None, {"sequence": 3, "audio": "", "is_last": True})

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
        cog.speak_queue.put_nowait((b"wav-bytes", {"sequence": 2, "text": "Salut.", "correlation_id": "c1"}))
        await _drain_one(cog)
        assert vc.played == [b"wav-bytes"]
        assert cog.nc.published == [
            ("io.voice.speak.start", {"sequence": 2, "is_last": False, "correlation_id": "c1", "text": "Salut."}),
            ("io.voice.speak.end", {"sequence": 2, "is_last": False, "correlation_id": "c1"}),
        ]

    asyncio.run(scenario())


def test_playback_loop_drops_audio_when_not_connected(monkeypatch):
    monkeypatch.setattr(
        voice_cog.discord, "FFmpegPCMAudio", lambda source, pipe: source.read()
    )

    async def scenario():
        vc = FakeVoiceClient(connected=False)
        cog = make_cog(vc)
        cog.speak_queue.put_nowait((b"wav-bytes", {"sequence": 0, "is_last": True}))
        await _drain_one(cog)
        assert vc.played == []
        # Rien n'est joué, mais la fin part quand même : le lobe_frontal l'attend.
        assert [subject for subject, _ in cog.nc.published] == ["io.voice.speak.start", "io.voice.speak.end"]
        assert cog.nc.published[-1][1]["is_last"] is True

    asyncio.run(scenario())
