import sys
import os
import tempfile
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.prompt_builder import PromptBuilder
import main

@pytest.fixture
def temp_config_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock md files
        with open(os.path.join(tmpdir, "PERSONA.md"), "w", encoding="utf-8") as f:
            f.write("I am Aletheia.")
        with open(os.path.join(tmpdir, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("I remember coding.")
        with open(os.path.join(tmpdir, "USER.md"), "w", encoding="utf-8") as f:
            f.write("User is Milo.")
        yield tmpdir

def test_prompt_builder_init(temp_config_dir):
    builder = PromptBuilder(temp_config_dir)
    assert builder._persona == "I am Aletheia."
    assert builder._core_memory == "I remember coding."
    assert builder._users == "User is Milo."

def test_prompt_builder_is_discord_url_expired():
    # Valid non-expired (far future hex timestamp)
    current_time = time.time()
    future_hex = hex(int(current_time + 1000))[2:]
    expired_hex = hex(int(current_time - 100))[2:]

    url_valid = f"https://cdn.discordapp.com/attachments/123/456/image.png?ex={future_hex}"
    url_expired = f"https://cdn.discordapp.com/attachments/123/456/image.png?ex={expired_hex}"
    url_other = "https://example.com/image.png"

    # Standard private static method check
    assert not PromptBuilder._is_discord_url_expired(url_valid, current_time)
    assert PromptBuilder._is_discord_url_expired(url_expired, current_time)
    assert not PromptBuilder._is_discord_url_expired(url_other, current_time)

def test_prompt_builder_build(temp_config_dir):
    builder = PromptBuilder(temp_config_dir)
    
    # 1. Simple text prompt
    messages = builder.build("hello", history=[])
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "I am Aletheia." in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "hello" in messages[1]["content"]

    # 2. Sequential user role compaction check
    messages_compaction = builder.build("world", history=[
        {"role": "user", "content": "first msg"},
        {"role": "user", "content": "second msg"}
    ])
    # The system prompt + historical combined + current combined = 2 final messages: system & user
    assert len(messages_compaction) == 2
    assert messages_compaction[1]["role"] == "user"
    assert isinstance(messages_compaction[1]["content"], list)

    # 3. Tool roles repair check
    messages_tool_repair = builder.build("tell me about the database", history=[
        {"role": "user", "content": "get context"},
        {"role": "tool", "content": "semantic memory info"}
    ])
    # Tool role should be repaired/injected into the preceding message content
    assert len(messages_tool_repair) == 2
    user_content = messages_tool_repair[1]["content"]
    assert "semantic memory info" in str(user_content)

@pytest.mark.asyncio
async def test_execute_tool_calls():
    # Setup mock tool functions
    mock_save = AsyncMock(return_value="Saved successfully")
    mock_get = AsyncMock(return_value="Found memories")
    
    main.available_functions = {
        "save_to_memory": mock_save,
        "get_from_memory": mock_get
    }

    tool_calls = [
        {
            "id": "call_1",
            "function": {
                "name": "save_to_memory",
                "arguments": '{"text": "Milo loves Rust"}'
            }
        },
        {
            "id": "call_2",
            "function": {
                "name": "get_from_memory",
                "arguments": '{"prompt": "database"}'
            }
        }
    ]

    results = await main.execute_tool_calls(tool_calls, sequence=1)
    
    # Verify both tool calls were executed in parallel
    assert len(results) == 2
    assert results[0]["tool_call_id"] == "call_1"
    assert results[0]["content"] == "Saved successfully"
    assert results[1]["tool_call_id"] == "call_2"
    assert results[1]["content"] == "Found memories"
    
    mock_save.assert_called_once_with(text="Milo loves Rust")
    mock_get.assert_called_once_with(prompt="database")
