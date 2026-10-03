import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import (SUSPENSION_TIMEOUT, Suspension, after_observation, decide, fragment_end,
                  interaction_started, is_suspended, voice_event)

T0 = 1000.0
KNOBS = {"threshold": 0.02, "since_check": 0.0, "heartbeat": 30.0, "identical": 0.00005}


def frame(value: float) -> np.ndarray:
    return np.full((90, 160), value, dtype=np.float32)


def test_interaction_suspends_the_vision():
    state = interaction_started(Suspension(), T0)
    assert is_suspended(state, T0 + 1)
    assert not is_suspended(Suspension(), T0)


def test_nothing_happens_during_a_suspension():
    # Ni changement, ni heartbeat : la conversation garde llama-server pour elle.
    assert decide(frame(0.0), frame(0.5), **KNOBS, suspended=True) is None
    assert decide(frame(0.0), frame(0.01), **{**KNOBS, "since_check": 60.0}, suspended=True) is None
    assert decide(None, frame(0.0), **KNOBS, suspended=True) is None


def test_one_observation_at_the_end_when_the_screen_changed():
    reference = frame(0.0)
    during = [decide(reference, frame(v), **KNOBS, suspended=True) for v in (0.3, 0.6, 0.9)]
    assert during == [None, None, None]
    latest = frame(0.9)
    assert decide(reference, latest, **KNOBS, suspended=False) == "change"
    reference = after_observation(reference, latest, ok=True)
    assert decide(reference, latest, **KNOBS, suspended=False) is None


def test_no_observation_at_the_end_when_the_screen_did_not_change():
    assert decide(frame(0.0), frame(0.0), **KNOBS, suspended=False) is None


def test_with_voice_the_last_fragment_does_not_lift_the_suspension():
    state = voice_event(Suspension(), T0 - 60)  # io_voix vu il y a une minute
    state = interaction_started(state, T0)
    state = fragment_end(state, T0 + 2)
    assert is_suspended(state, T0 + 3)


def test_with_voice_the_end_of_speech_lifts_the_suspension():
    state = interaction_started(voice_event(Suspension(), T0 - 60), T0)
    state = voice_event(state, T0 + 5, is_last_end=True)
    assert not is_suspended(state, T0 + 5)


def test_a_speech_end_that_is_not_the_last_does_not_lift_the_suspension():
    state = interaction_started(Suspension(), T0)
    state = voice_event(state, T0 + 2, is_last_end=False)
    assert is_suspended(state, T0 + 3)


def test_without_voice_the_last_fragment_lifts_the_suspension():
    state = interaction_started(Suspension(), T0)
    assert not is_suspended(fragment_end(state, T0 + 2), T0 + 2)


def test_voice_seen_more_than_10_minutes_ago_counts_as_absent():
    state = interaction_started(voice_event(Suspension(), T0 - 601), T0)
    assert not is_suspended(fragment_end(state, T0 + 2), T0 + 2)


def test_the_suspension_ends_after_30_seconds_without_a_signal():
    state = interaction_started(Suspension(), T0)
    assert is_suspended(state, T0 + SUSPENSION_TIMEOUT - 0.1)
    assert not is_suspended(state, T0 + SUSPENSION_TIMEOUT)
