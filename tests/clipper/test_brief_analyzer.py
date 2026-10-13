"""tests/clipper/test_brief_analyzer.py — Tests for brief analyzer."""

from unittest.mock import patch, MagicMock
from src.clipper.brief_analyzer import analyze_campaign_brief


def test_analyze_campaign_brief():
    detail = {
        "id": "cmv0h59il0kfbcv26ffgw79ut",
        "title": "KICK AVENUE FAIR 2026",
        "cpm": 3000,
        "budget_percentage": 99.95,
        "min_duration": 15,
        "max_duration": 180,
        "hashtags": ["#kafair", "#KickAvenue"],
        "dos": ["Wajib teks di akhir video minimal 5 detik"],
        "donts": ["Dilarang bot"],
        "hooks": "Gilaa iPhone 10rb doang!",
        "cta": "Belanja di Kick Avenue",
        "assets": [{"url": "https://youtu.be/123", "is_required": True}],
    }

    mock_llm_json = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"summary": "Event Kick Avenue Fair dengan raffle iPhone 10rb",'
                        ' "winning_hooks": ["iPhone seharga 10 ribu?! Gini caranya"],'
                        ' "key_requirements": ["Teks CTA di akhir video"],'
                        ' "forbidden_items": ["Dilarang reupload polosan"],'
                        ' "cta_text": "Belanja di Kick Avenue",'
                        ' "caption_recommendation": "Kapan lagi dapet iPhone 10rb? #kafair #KickAvenue"}'
                    )
                }
            }
        ]
    }

    with patch("src.clipper.brief_analyzer.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = mock_llm_json
        mock_post.return_value = mock_resp

        res = analyze_campaign_brief(detail)
        assert res["campaign_id"] == "cmv0h59il0kfbcv26ffgw79ut"
        assert res["cpm"] == 3000
        assert "iPhone seharga 10 ribu?!" in res["winning_hooks"][0]
        assert "Belanja di Kick Avenue" == res["cta_text"]
        assert len(res["assets"]) == 1
