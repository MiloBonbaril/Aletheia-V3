import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.prompt_builder import PromptBuilder


def test_mood_appears_only_after_set_mood_across_two_turns():
    builder = PromptBuilder()

    turn_1 = builder.build_system_prompt()
    assert "<mood>" not in turn_1

    # Ce que ferait mood_update_handler (main.py) en recevant un limbic.mood.update.
    builder.mood = {"emotion": "taquine", "intensity": 0.7, "description": "un peu moqueuse"}

    turn_2 = builder.build_system_prompt()
    assert "<mood>" in turn_2
    assert "un peu moqueuse" in turn_2


def test_mood_section_falls_back_to_emotion_and_intensity_without_description():
    builder = PromptBuilder()
    builder.mood = {"emotion": "joyeuse", "intensity": 0.8, "description": None}
    prompt = builder.build_system_prompt()
    assert "<mood>" in prompt
    assert "joyeuse" in prompt
    assert "0.8" in prompt
