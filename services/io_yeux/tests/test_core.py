import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import after_observation, build_state, decide, difference, thumbnail


def frame(value: float) -> np.ndarray:
    return np.full((90, 160), value, dtype=np.float32)


def test_thumbnail_is_160x90_grayscale_on_0_1():
    thumb = thumbnail(Image.new("RGB", (3840, 2160), (255, 255, 255)))
    assert thumb.shape == (90, 160)
    assert thumb.max() == pytest.approx(1.0)


def test_difference_is_mean_absolute_difference():
    assert difference(frame(0.2), frame(0.5)) == pytest.approx(0.3)
    assert difference(frame(0.4), frame(0.4)) == 0.0


def test_first_frame_is_observed():
    assert decide(None, frame(0.0), threshold=0.02) == "change"


def test_below_threshold_is_not_observed():
    assert decide(frame(0.0), frame(0.01), threshold=0.02) is None


def test_above_threshold_is_observed():
    assert decide(frame(0.0), frame(0.5), threshold=0.02) == "change"


def test_slow_drift_ends_up_observed():
    # La référence est la dernière observation réussie, pas la frame précédente :
    # chaque pas reste sous le seuil, mais leur somme le franchit.
    reference = frame(0.0)
    triggers = [decide(reference, frame(step * 0.01), threshold=0.02) for step in range(1, 4)]
    assert triggers == [None, None, "change"]


def test_reference_moves_only_after_a_successful_observation():
    old, new = frame(0.0), frame(0.5)
    assert after_observation(old, new, ok=True) is new
    assert after_observation(old, new, ok=False) is old
    # Après un échec, la capture suivante est encore « différente » : nouvel essai.
    assert decide(after_observation(old, new, ok=False), new, threshold=0.02) == "change"


def test_build_state_copies_the_vlm_answer():
    state = build_state(
        {"application": "Blender", "activity": "Ajuste un rig.", "visible_text": ["Pose Mode"]},
        observed_at=1000, trigger="change",
    )
    assert state == {
        "observed_at": 1000,
        "checked_at": 1000,
        "trigger": "change",
        "application": "Blender",
        "activity": "Ajuste un rig.",
        "visible_text": ["Pose Mode"],
    }


def test_build_state_applies_the_bounds_again():
    state = build_state(
        {"application": "A" * 300, "activity": "x" * 500, "visible_text": ["y" * 200] * 9},
        observed_at=1, trigger="change",
    )
    assert len(state["application"]) == 80
    assert len(state["activity"]) == 200
    assert len(state["visible_text"]) == 5
    assert all(len(text) == 80 for text in state["visible_text"])


def test_build_state_rejects_a_bad_shape():
    with pytest.raises((KeyError, TypeError)):
        build_state({"activity": "x"}, observed_at=1, trigger="change")
    with pytest.raises(TypeError):
        build_state({"application": "A", "activity": "x", "visible_text": "pas une liste"},
                    observed_at=1, trigger="change")
