import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.prompt_builder import PromptBuilder


def test_build_topic_prompt_has_persona_system_and_user_instruction():
    builder = PromptBuilder()
    messages = builder.build_topic_prompt()

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "<persona>" in messages[0]["content"]
    assert "<core_memory>" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "sujet" in messages[1]["content"].lower()


def test_build_topic_prompt_has_no_history_or_rag_sections():
    builder = PromptBuilder()
    messages = builder.build_topic_prompt()

    assert "\n  <recall>" not in messages[0]["content"]
    assert "\n  <context" not in messages[0]["content"]
