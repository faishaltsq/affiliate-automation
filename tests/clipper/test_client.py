"""tests/clipper/test_client.py — Unit tests for ClippoClient."""

from unittest.mock import MagicMock, patch
from src.clipper.client import ClippoClient


def test_clippo_client_parse_campaigns():
    mock_payload = {
        "status": 200,
        "data": {
            "data": [
                {
                    "id": "cmv56bule000jep26bgduxjok",
                    "title": "BRIGHTY X FUJI X ACID",
                    "ratePerKView": 4000,
                    "budgetPercentage": 100,
                    "hashtags": ["#BrightySabunJumbo"],
                    "assetVideoUrlsV2": [{"url": "https://drive.google.com/test"}],
                }
            ]
        },
    }
    with patch("src.clipper.client.sync_playwright") as mock_pw:
        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.evaluate.return_value = mock_payload
        mock_ctx.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_ctx
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        client = ClippoClient()
        campaigns = client.get_campaigns(limit=5)
        assert len(campaigns) == 1
        assert campaigns[0]["id"] == "cmv56bule000jep26bgduxjok"
        assert campaigns[0]["cpm"] == 4000
        assert campaigns[0]["budget_percentage"] == 100.0


def test_clippo_client_parse_detail():
    mock_payload = {
        "status": 200,
        "data": {
            "id": "cmv0h59il0kfbcv26ffgw79ut",
            "title": "KICK AVENUE FAIR 2026",
            "ratePerKView": 3000,
            "budgetPercentage": 99.95,
            "minDuration": 15,
            "maxDuration": 180,
            "hashtags": ["#kafair"],
            "campaignDos": ["Wajib highlight Kick Avenue"],
            "campaignDonts": ["Dilarang bot"],
            "campaignHook": {"ops": [{"insert": "Gilaa, iPhone 10rb doang!"}]},
            "ctaLabel": "Belanja di Kick Avenue",
            "assetVideoUrlsV2": [
                {"url": "https://youtu.be/123", "displayText": "Footage 1", "isRequired": True}
            ],
        },
    }
    with patch("src.clipper.client.sync_playwright") as mock_pw:
        mock_browser = MagicMock()
        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.evaluate.return_value = mock_payload
        mock_ctx.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_ctx
        mock_pw.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser

        client = ClippoClient()
        detail = client.get_campaign_detail("cmv0h59il0kfbcv26ffgw79ut")
        assert detail["title"] == "KICK AVENUE FAIR 2026"
        assert detail["cpm"] == 3000
        assert "Gilaa, iPhone 10rb doang!" in detail["hooks"]
        assert len(detail["dos"]) == 1
        assert detail["dos"][0] == "Wajib highlight Kick Avenue"
        assert detail["assets"][0]["url"] == "https://youtu.be/123"
        assert detail["assets"][0]["is_required"] is True
