import pytest
from unittest.mock import patch, MagicMock
from src.bot.clipper_agent import handle_clipper_message


@patch("src.bot.clipper_agent._cmd_clip_async")
def test_handle_clipper_message_parses_count(mock_clip_async):
    # Test positional count: /clip camp123 2
    handle_clipper_message({}, "/clip camp123 2", 100, 246)
    mock_clip_async.assert_called_once()
    args, kwargs = mock_clip_async.call_args
    # signature: (campaign_id, source_url, target_accounts, msg_id, thread_id, count=..., distribution=...)
    assert args[0] == "camp123"
    assert kwargs.get("count") == 2 or (len(args) > 5 and args[5] == 2)


@patch("src.bot.clipper_agent._cmd_clip_async")
def test_handle_clipper_message_parses_flag_count(mock_clip_async):
    # Test flag count: /clip camp456 --count 3
    handle_clipper_message({}, "/clip camp456 --count 3", 101, 246)
    mock_clip_async.assert_called_once()
    args, kwargs = mock_clip_async.call_args
    assert args[0] == "camp456"
    assert kwargs.get("count") == 3 or (len(args) > 5 and args[5] == 3)
