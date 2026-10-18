"""tests/test_threads_bot_personas.py — Test Telegram bot persona commands and routing."""

from unittest.mock import patch, MagicMock
from src.bot.threads_agent import handle_threads_message, _cmd_persona
from src.organic.personas import (
    get_active_manual_persona,
    set_active_manual_persona,
    PERSONAS,
)


def test_cmd_persona_without_args():
    with patch("src.bot.threads_agent._send_reply") as mock_send:
        _cmd_persona("", msg_id=101, thread_id=9)
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        text = args[0]
        markup = kwargs.get("reply_markup")

        assert "Panel Pengaturan Persona Threads Manual" in text
        assert "inline_keyboard" in markup
        buttons = [btn["callback_data"] for row in markup["inline_keyboard"] for btn in row]
        assert "th:set_persona:julid" in buttons
        assert "th:set_persona:calm" in buttons
        assert "th:set_persona:nerd" in buttons
        assert "th:set_persona:energic" in buttons
        assert "th:set_persona:angry" in buttons
        assert "th:set_persona:default" in buttons


def test_cmd_persona_with_valid_arg(tmp_path, monkeypatch):
    test_state_file = tmp_path / "persona_state.json"
    monkeypatch.setattr("src.organic.personas.STATE_FILE", test_state_file)

    with patch("src.bot.threads_agent._send_reply") as mock_send:
        _cmd_persona("julid", msg_id=102, thread_id=9)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][0]
        assert "Persona Manual Diubah" in text
        assert "Netizen Julid" in text
        assert get_active_manual_persona() == "julid"


def test_callback_set_persona(tmp_path, monkeypatch):
    test_state_file = tmp_path / "persona_state.json"
    monkeypatch.setattr("src.organic.personas.STATE_FILE", test_state_file)

    with patch("src.bot.threads_agent._send_reply") as mock_send:
        handle_threads_message({}, "set_persona:nerd", msg_id=103, thread_id=9)
        mock_send.assert_called_once()
        text = mock_send.call_args[0][0]
        assert "Netizen Nerd" in text
        assert get_active_manual_persona() == "nerd"


def test_shortcut_persona_command():
    with patch("src.bot.threads_agent._create_and_preview_draft") as mock_draft:
        handle_threads_message({}, "/julid kebakaran lahan gambut", msg_id=104, thread_id=9)
        mock_draft.assert_called_once()
        args, kwargs = mock_draft.call_args
        topic, pillar, post_format, msg_id, thread_id = args
        persona = kwargs.get("persona")

        assert topic == "kebakaran lahan gambut"
        assert post_format == "with_image"
        assert persona == "julid"


def test_tagged_persona_command():
    with patch("src.bot.threads_agent._create_and_preview_draft") as mock_draft:
        handle_threads_message({}, "/post_photo [angry] kabut asap kalimantan", msg_id=105, thread_id=9)
        mock_draft.assert_called_once()
        args, kwargs = mock_draft.call_args
        topic, pillar, post_format, msg_id, thread_id = args
        persona = kwargs.get("persona")

        assert topic == "kabut asap kalimantan"
        assert post_format == "with_image"
        assert persona == "angry"
