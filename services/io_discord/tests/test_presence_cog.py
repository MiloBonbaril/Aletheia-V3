import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("DISCORD_USER_ID", "1")
os.environ.setdefault("TEXT_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_GUILD_ID", "42")

from config import Config
from cogs.presence import Presence


@dataclass
class FakeMember:
    bot: bool
    guild: object = None


@dataclass
class FakeChannel:
    members: list


@dataclass
class FakeGuild:
    id: int
    voice_channels: list = field(default_factory=list)


class FakeNats:
    def __init__(self):
        self.published = []

    async def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload.decode())))


def make_cog():
    cog = Presence.__new__(Presence)
    cog.logger = logging.getLogger("test.presence")
    cog.nc = FakeNats()
    cog.last_occupied = None
    return cog


def test_publishes_occupied_true_when_non_bot_joins():
    cog = make_cog()
    guild = FakeGuild(id=Config.GUILD_ID)
    guild.voice_channels = [FakeChannel(members=[FakeMember(bot=False, guild=guild)])]
    member = guild.voice_channels[0].members[0]

    asyncio.run(cog.on_voice_state_update(member, None, None))

    assert cog.nc.published == [("io.presence.discord_voice", {"occupied": True})]
    assert cog.last_occupied is True


def test_does_not_republish_when_occupancy_unchanged():
    cog = make_cog()
    cog.last_occupied = True
    guild = FakeGuild(id=Config.GUILD_ID)
    guild.voice_channels = [FakeChannel(members=[FakeMember(bot=False, guild=guild)])]
    member = guild.voice_channels[0].members[0]

    asyncio.run(cog.on_voice_state_update(member, None, None))

    assert cog.nc.published == []


def test_ignores_events_from_other_guilds():
    cog = make_cog()
    guild = FakeGuild(id=Config.GUILD_ID + 1)
    guild.voice_channels = [FakeChannel(members=[FakeMember(bot=False, guild=guild)])]
    member = guild.voice_channels[0].members[0]

    asyncio.run(cog.on_voice_state_update(member, None, None))

    assert cog.nc.published == []
    assert cog.last_occupied is None
