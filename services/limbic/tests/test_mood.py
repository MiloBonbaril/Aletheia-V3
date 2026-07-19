import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mood import MoodState, apply_set, decay


def test_apply_set_clamps_and_stores():
    state = apply_set(MoodState(), "joyeuse", 1.5, "trop contente")
    assert state.emotion == "joyeuse"
    assert state.intensity == 1.0
    assert state.description == "trop contente"


def test_decay_reduces_intensity_then_floors_to_neutral():
    state = decay(MoodState(emotion="colere", intensity=0.12), 0.1)
    assert state.emotion == "colere"
    assert state.intensity == pytest.approx(0.02)

    state = decay(state, 0.1)
    assert state == MoodState()


def test_decay_is_noop_once_at_neutral_baseline():
    baseline = MoodState()
    assert decay(baseline, 0.1) is baseline
