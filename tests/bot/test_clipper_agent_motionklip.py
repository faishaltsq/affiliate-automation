"""tests/bot/test_clipper_agent_motionklip.py — Unit tests for Motionklip Telegram bot commands."""

import pytest
from unittest.mock import patch, MagicMock
from src.bot.clipper_agent import (
    _cmd_campaigns,
    _cmd_brief,
    _cmd_help,
    handle_clipper_message,
)
from src.clipper.motionklip_client import MotionklipClient
from src.clipper.client import ClippoClient


SAMPLE_MK_CAMPAIGN = {
    "id": "mk-12345",
    "title": "WINDAH BASUDARA X RUCAS",
    "cpm": 15000,
    "budget_percentage": 85.0,
    "min_duration": 30,
    "max_duration": 60,
    "hashtags": ["#WindahBasudara", "#Rucas", "#MotionKlip"],
    "dos": ["Pakai hook yang heboh di 3 detik awal"],
    "donts": ["Dilarang reupload mentah"],
    "assets": [{"url": "https://drive.google.com/test", "display_name": "Google Drive"}],
    "source": "motionklip",
}

SAMPLE_CLIPPO_CAMPAIGN = {
    "id": "cmv0h59il0kfbcv26ffgw79ut",
    "title": "Campaign Snack Video",
    "cpm": 12000,
    "budget_percentage": 60.0,
    "min_duration": 15,
    "max_duration": 60,
    "hashtags": ["#SnackVideo"],
    "dos": ["Gunakan hashtag resmi"],
    "donts": ["Dilarang spam"],
    "assets": [{"url": "https://clippo.id/asset1", "display_name": "Footage 1"}],
    "source": "clippo",
}


@patch("src.bot.clipper_agent._send_reply")
@patch.object(MotionklipClient, "get_campaigns")
def test_cmd_campaigns_source_mk_queries_motionklip_client(mock_get_mk, mock_send):
    mock_get_mk.return_value = [SAMPLE_MK_CAMPAIGN]

    _cmd_campaigns(msg_id=1, thread_id=246, source="mk")

    mock_get_mk.assert_called_once_with(limit=10)
    mock_send.assert_called()
    last_call_text = mock_send.call_args[0][0]
    assert "WINDAH BASUDARA X RUCAS" in last_call_text
    assert "mk-12345" in last_call_text
    assert "[MotionKlip]" in last_call_text


@patch("src.bot.clipper_agent._send_reply")
@patch.object(ClippoClient, "get_campaigns")
def test_cmd_campaigns_source_clippo_queries_clippo_client(mock_get_clippo, mock_send):
    mock_get_clippo.return_value = [SAMPLE_CLIPPO_CAMPAIGN]

    _cmd_campaigns(msg_id=2, thread_id=246, source="clippo")

    mock_get_clippo.assert_called_once_with(limit=10)
    mock_send.assert_called()
    last_call_text = mock_send.call_args[0][0]
    assert "Campaign Snack Video" in last_call_text
    assert "cmv0h59il0kfbcv26ffgw79ut" in last_call_text
    assert "[Clippo]" in last_call_text


@patch("src.bot.clipper_agent._send_reply")
@patch.object(MotionklipClient, "get_campaigns")
@patch.object(ClippoClient, "get_campaigns")
def test_cmd_campaigns_source_all_queries_both(mock_get_clippo, mock_get_mk, mock_send):
    mock_get_clippo.return_value = [SAMPLE_CLIPPO_CAMPAIGN]
    mock_get_mk.return_value = [SAMPLE_MK_CAMPAIGN]

    _cmd_campaigns(msg_id=3, thread_id=246, source="all")

    mock_get_clippo.assert_called_once_with(limit=10)
    mock_get_mk.assert_called_once_with(limit=10)
    last_call_text = mock_send.call_args[0][0]
    assert "[Clippo]" in last_call_text
    assert "[MotionKlip]" in last_call_text


@patch("src.bot.clipper_agent._send_reply")
@patch.object(MotionklipClient, "get_campaigns", side_effect=Exception("Discord timeout"))
@patch.object(ClippoClient, "get_campaigns")
def test_cmd_campaigns_motionklip_failure_graceful(mock_get_clippo, mock_get_mk, mock_send):
    mock_get_clippo.return_value = [SAMPLE_CLIPPO_CAMPAIGN]

    # Should not raise exception
    _cmd_campaigns(msg_id=4, thread_id=246, source="all")

    mock_get_clippo.assert_called_once_with(limit=10)
    last_call_text = mock_send.call_args[0][0]
    assert "Campaign Snack Video" in last_call_text


@patch("src.bot.clipper_agent._send_reply")
@patch("src.bot.clipper_agent.get_campaign_provider")
@patch("src.bot.clipper_agent.analyze_campaign_brief")
def test_cmd_brief_mk_queries_motionklip_provider(mock_analyze, mock_get_provider, mock_send):
    mock_provider = MagicMock()
    mock_provider.get_campaign_detail.return_value = SAMPLE_MK_CAMPAIGN
    mock_get_provider.return_value = mock_provider
    mock_analyze.return_value = {
        "winning_hooks": ["Hook Mantap"],
        "key_requirements": ["Tag @windahbasudara"],
        "forbidden_items": ["Dilarang sara"],
    }

    _cmd_brief("mk-12345", msg_id=5, thread_id=246)

    mock_get_provider.assert_called_once_with("mk-12345")
    mock_provider.get_campaign_detail.assert_called_once_with("mk-12345")
    last_call_text = mock_send.call_args[0][0]
    assert "[MotionKlip]" in last_call_text
    assert "WINDAH BASUDARA X RUCAS" in last_call_text


@patch("src.bot.clipper_agent._cmd_campaigns")
def test_handle_clipper_message_parses_campaigns_mk(mock_cmd_campaigns):
    handle_clipper_message({}, "/campaigns mk", 10, 246)
    mock_cmd_campaigns.assert_called_once_with(10, 246, source="mk")


@patch("src.bot.clipper_agent._cmd_campaigns")
def test_handle_clipper_message_parses_campaigns_motionklip(mock_cmd_campaigns):
    handle_clipper_message({}, "/campaigns motionklip", 11, 246)
    mock_cmd_campaigns.assert_called_once_with(11, 246, source="motionklip")


@patch("src.bot.clipper_agent._cmd_brief")
def test_handle_clipper_message_parses_brief_mk(mock_cmd_brief):
    handle_clipper_message({}, "/brief mk-999", 20, 246)
    mock_cmd_brief.assert_called_once_with("mk-999", 20, 246)


@patch("src.bot.clipper_agent._send_reply")
def test_cmd_help_documents_motionklip(mock_send):
    _cmd_help(msg_id=30, thread_id=246)
    mock_send.assert_called_once()
    help_text = mock_send.call_args[0][0]
    assert "/campaigns [mk|clippo]" in help_text
    assert any(x in help_text for x in ["/brief mk-<id>", "/brief mk-&lt;id&gt;"])
    assert any(x in help_text for x in ["/clip mk-<id> [count]", "/clip mk-&lt;id&gt; [count]"])
