import sys
import os
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main

@pytest.mark.asyncio
async def test_io_text_commands():
    mock_nc = AsyncMock()
    
    # Message lines sent to stdin
    lines = ["Hello Aletheia\n", ":w\n", ":quit\n"]
    line_iter = iter(lines)
    
    def mock_readline():
        try:
            return next(line_iter)
        except StopIteration:
            return ""

    with patch('nats.connect', return_value=mock_nc), \
         patch('sys.stdin.readline', side_effect=mock_readline), \
         patch('builtins.print'):
         
         await main.main()
         
         # NATS subscriber configuration
         mock_nc.subscribe.assert_called_once()
         
         # NATS publish message text checks
         mock_nc.publish.assert_called_once()
         topic, payload = mock_nc.publish.call_args[0]
         assert topic == "io.user.msg.text"
         
         payload_dict = json.loads(payload.decode())
         assert payload_dict["text"] == "Milo: Hello Aletheia"
         
         # Verify connection is closed on exit
         mock_nc.close.assert_called_once()

@pytest.mark.asyncio
async def test_io_text_clear_command():
    mock_nc = AsyncMock()
    
    # Message lines: input something, then :c (clear), then input something else, send, quit
    lines = ["Trash line\n", ":clear\n", "Good line\n", ":send\n", ":q\n"]
    line_iter = iter(lines)
    
    def mock_readline():
        try:
            return next(line_iter)
        except StopIteration:
            return ""

    with patch('nats.connect', return_value=mock_nc), \
         patch('sys.stdin.readline', side_effect=mock_readline), \
         patch('builtins.print'):
         
         await main.main()
         
         # NATS publish should only contain the good line
         mock_nc.publish.assert_called_once()
         topic, payload = mock_nc.publish.call_args[0]
         payload_dict = json.loads(payload.decode())
         
         assert "Trash line" not in payload_dict["text"]
         assert payload_dict["text"] == "Milo: Good line"
