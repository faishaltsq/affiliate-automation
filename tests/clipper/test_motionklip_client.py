"""tests/clipper/test_motionklip_client.py — Unit tests for Motionklip Discord REST Client."""

from unittest.mock import MagicMock, patch
import requests
import pytest
from src.clipper.motionklip_client import MotionklipClient
from src.config import DISCORD_TOKEN, MOTIONKLIP_GUILD_ID, MOTIONKLIP_CHANNELS


SAMPLE_DISCORD_CAMPAIGN_MESSAGE = {
    "id": "111222333444555666",
    "channel_id": "999888777",
    "content": (
        "📢 **NEW CAMPAIGN: WINDAH BASUDARA X RUCAS**\n\n"
        "💰 **Reward:** Rp 15.000 / 1.000 views\n"
        "🎯 **Budget Pool:** Rp 20.000.000 (Sisa 85%)\n"
        "⏱ **Durasi Clip:** 30 - 60 detik\n"
        "🏷 **Hashtags:** #WindahBasudara #Rucas #MotionKlip\n"
        "📌 **Do's:**\n"
        "- Pakai hook yang heboh di 3 detik awal\n"
        "- Wajib tag @windahbasudara dan @rucas\n"
        "🚫 **Don'ts:**\n"
        "- Dilarang reupload mentah tanpa edit\n"
        "- Dilarang pakai footage yang berbau SARA\n\n"
        "🎬 **Bahan Footage:**\n"
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz\n"
        "https://youtu.be/sample_stream_vod"
    ),
    "embeds": [],
    "attachments": [],
}


def test_config_discord_motionklip_variables():
    """Verify src.config exports DISCORD_TOKEN, MOTIONKLIP_GUILD_ID, MOTIONKLIP_CHANNELS."""
    assert isinstance(DISCORD_TOKEN, str)
    assert isinstance(MOTIONKLIP_GUILD_ID, str)
    assert isinstance(MOTIONKLIP_CHANNELS, list)


def test_get_campaigns_returns_parsed_campaigns_with_mk_prefix():
    """Test get_campaigns returns parsed campaign dicts with 'mk-' id prefix."""
    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [SAMPLE_DISCORD_CAMPAIGN_MESSAGE]
        mock_get.return_value = mock_response

        client = MotionklipClient(token="mock_token", channels=["999888777"])
        campaigns = client.get_campaigns(limit=10)

        assert len(campaigns) == 1
        campaign = campaigns[0]
        assert campaign["id"] == "mk-111222333444555666"
        assert "WINDAH BASUDARA X RUCAS" in campaign["title"]
        assert campaign["cpm"] == 15000
        assert campaign["budget_percentage"] == 85.0
        assert campaign["min_duration"] == 30
        assert campaign["max_duration"] == 60
        assert "#WindahBasudara" in campaign["hashtags"]
        assert len(campaign["dos"]) >= 2
        assert len(campaign["donts"]) >= 2
        assert len(campaign["assets"]) == 2
        assert campaign["source"] == "motionklip"
        assert campaign["materials_count"] == 2


def test_get_campaign_detail_with_mocked_discord_response():
    """Test get_campaign_detail('mk-123') fetches message from Discord API and parses it."""
    detail_msg = {
        "id": "123",
        "channel_id": "999888777",
        "content": (
            "📢 **NEW CAMPAIGN: DETAIL SPECIFIC TEST**\n\n"
            "💰 **Reward:** Rp 12.000 / 1.000 views\n"
            "🎬 **Bahan:** https://youtu.be/detail_vod"
        ),
        "embeds": [],
        "attachments": [],
    }

    with patch("requests.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = detail_msg
        mock_get.return_value = mock_response

        client = MotionklipClient(token="mock_token", channels=["999888777"])
        detail = client.get_campaign_detail("mk-123")

        assert detail["id"] == "mk-123"
        assert "DETAIL SPECIFIC TEST" in detail["title"]
        assert detail["cpm"] == 12000
        assert len(detail["assets"]) == 1
        assert detail["source"] == "motionklip"


def test_graceful_error_and_fallback_missing_token():
    """Test client returns empty fallback when DISCORD_TOKEN is missing."""
    client = MotionklipClient(token="")
    campaigns = client.get_campaigns()
    assert campaigns == []
    detail = client.get_campaign_detail("mk-123")
    assert detail == {}


def test_graceful_error_and_fallback_http_errors():
    """Test client handles 429 rate limit, 403 forbidden, and 500 server error without crashing."""
    client = MotionklipClient(token="mock_token", channels=["999888777"])

    # 429 Rate limited
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.json.return_value = {"message": "You are being rate limited.", "retry_after": 2.5}
        mock_get.return_value = mock_resp

        assert client.get_campaigns() == []
        assert client.get_campaign_detail("mk-123") == {}

    # 403 Forbidden
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {"message": "Missing Access"}
        mock_get.return_value = mock_resp

        assert client.get_campaigns() == []
        assert client.get_campaign_detail("mk-123") == {}

    # 500 Internal Server Error
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"message": "Internal Server Error"}
        mock_get.return_value = mock_resp

        assert client.get_campaigns() == []
        assert client.get_campaign_detail("mk-123") == {}

    # Network Connection Error
    with patch("requests.get", side_effect=requests.exceptions.ConnectionError("Discord unreachable")):
        assert client.get_campaigns() == []
        assert client.get_campaign_detail("mk-123") == {}


def test_caching_within_ttl_does_not_hit_network():
    """Test that second call within TTL reuses cached data and does not hit network."""
    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [SAMPLE_DISCORD_CAMPAIGN_MESSAGE]
        mock_get.return_value = mock_resp

        client = MotionklipClient(token="mock_token", channels=["999888777"], cache_ttl=60)

        # 1. get_campaigns caching
        first_result = client.get_campaigns()
        assert mock_get.call_count == 1

        second_result = client.get_campaigns()
        # Network must not be called again
        assert mock_get.call_count == 1
        assert first_result == second_result

        # 2. get_campaign_detail for campaign fetched during get_campaigns
        cached_detail = client.get_campaign_detail("mk-111222333444555666")
        assert mock_get.call_count == 1
        assert cached_detail["id"] == "mk-111222333444555666"

        # 3. get_campaign_detail for un-cached campaign
        detail_msg = {
            "id": "777888",
            "channel_id": "999888777",
            "content": "📢 **NEW CAMPAIGN: CACHED DETAIL**\n💰 **Reward:** Rp 10.000 / 1k views",
            "embeds": [],
            "attachments": [],
        }
        mock_detail_resp = MagicMock()
        mock_detail_resp.status_code = 200
        mock_detail_resp.json.return_value = detail_msg
        mock_get.return_value = mock_detail_resp

        det1 = client.get_campaign_detail("mk-777888")
        assert mock_get.call_count == 2
        det2 = client.get_campaign_detail("mk-777888")
        # Cached, so call_count is still 2
        assert mock_get.call_count == 2
        assert det1 == det2
