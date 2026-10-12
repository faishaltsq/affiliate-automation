"""tests/test_multi_topic_bot.py — Test topic routing and agent intent parsing."""

from unittest.mock import patch, MagicMock
from src.bot.telegram_listener import handle_message, THREADS_THREAD_ID, TIKTOK_THREAD_ID
from src.bot.tiktok_agent import _detect_intent


def test_tiktok_intent_detection():
    # Upload intent
    intent, kw = _detect_intent("upload 1 video mancing dong", "upload 1 video mancing dong")
    assert intent == "upload"
    assert kw == "mancing"

    # Dry run intent
    intent, kw = _detect_intent("coba tes render dry run video pertukangan", "coba tes render dry run video pertukangan")
    assert intent == "dryrun"

    # Status intent
    intent, _ = _detect_intent("cek status upload hari ini", "cek status upload hari ini")
    assert intent == "status"

    # Conversational non-action
    intent, _ = _detect_intent("menurutmu video apa yang cocok buat niche kuliner?", "menurutmu video apa yang cocok buat niche kuliner?")
    assert intent is None


def test_telegram_listener_routes_to_tiktok_topic():
    msg = {
        "message_id": 101,
        "message_thread_id": int(TIKTOK_THREAD_ID),
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "text": "/status",
    }
    with patch("src.bot.telegram_listener.handle_tiktok_message") as mock_tiktok:
        handle_message(msg)
        mock_tiktok.assert_called_once_with(msg, "/status", 101, int(TIKTOK_THREAD_ID))


def test_telegram_listener_routes_to_threads_topic():
    msg = {
        "message_id": 102,
        "message_thread_id": int(THREADS_THREAD_ID),
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "text": "/status",
    }
    with patch("src.bot.telegram_listener.handle_threads_message") as mock_threads:
        handle_message(msg)
        mock_threads.assert_called_once_with(msg, "/status", 102, int(THREADS_THREAD_ID))


def test_telegram_listener_routes_by_content_in_general_chat():
    msg = {
        "message_id": 103,
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "text": "upload tiktok video baru dong",
    }
    with patch("src.bot.telegram_listener.handle_tiktok_message") as mock_tiktok:
        handle_message(msg)
        assert mock_tiktok.called
