import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from presence import compute_occupancy


@dataclass
class FakeMember:
    bot: bool


def test_occupied_when_a_non_bot_member_is_present():
    assert compute_occupancy([FakeMember(bot=True), FakeMember(bot=False)]) is True


def test_not_occupied_when_only_bots_present():
    assert compute_occupancy([FakeMember(bot=True)]) is False


def test_not_occupied_when_no_members():
    assert compute_occupancy([]) is False
