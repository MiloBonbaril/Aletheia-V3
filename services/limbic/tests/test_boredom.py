import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from boredom import BoredomState, tick, reset, should_trigger


def test_tick_increments_boredom():
    state = tick(BoredomState(), 0.1)
    assert state.boredom == pytest.approx(0.1)
    state = tick(state, 0.1)
    assert state.boredom == pytest.approx(0.2)


def test_reset_returns_to_zero():
    state = BoredomState(boredom=5.0)
    assert reset(state) == BoredomState()


def test_should_trigger_false_below_threshold():
    assert should_trigger(boredom=0.5, threshold=1.0, presence=True, hour=12, gate_start_hour=9, gate_end_hour=23) is False


def test_should_trigger_false_without_presence():
    assert should_trigger(boredom=2.0, threshold=1.0, presence=False, hour=12, gate_start_hour=9, gate_end_hour=23) is False


def test_should_trigger_false_outside_time_window():
    assert should_trigger(boredom=2.0, threshold=1.0, presence=True, hour=3, gate_start_hour=9, gate_end_hour=23) is False


def test_should_trigger_true_when_all_conditions_met():
    assert should_trigger(boredom=1.0, threshold=1.0, presence=True, hour=12, gate_start_hour=9, gate_end_hour=23) is True


def test_should_trigger_true_even_when_boredom_far_exceeds_threshold():
    # Couvre l'AC "le trigger reste en attente et se déclenche dès que les gates s'ouvrent" :
    # comme le boredom ne redescend jamais tout seul, il reste >= threshold bien après l'avoir
    # franchi -- devrait donc rester "should_trigger" tant qu'aucun reset n'a eu lieu.
    assert should_trigger(boredom=50.0, threshold=1.0, presence=True, hour=12, gate_start_hour=9, gate_end_hour=23) is True
