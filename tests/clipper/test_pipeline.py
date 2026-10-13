"""tests/clipper/test_pipeline.py — Unit tests for clipper pipeline."""

from unittest.mock import patch, MagicMock
from src.clipper.pipeline import run_clipper_pipeline


def test_run_clipper_pipeline_dry_run():
    with patch("src.clipper.pipeline.ClippoClient") as mock_client:
        mock_instance = MagicMock()
        mock_instance.get_campaign_detail.return_value = {
            "id": "cmv0h59il0kfbcv26ffgw79ut",
            "title": "KICK AVENUE FAIR 2026",
            "cpm": 3000,
            "hashtags": ["#kafair"],
            "assets": [{"url": "https://youtu.be/123", "is_required": True}],
        }
        mock_client.return_value = mock_instance

        res = run_clipper_pipeline(
            campaign_id="cmv0h59il0kfbcv26ffgw79ut",
            dry_run=True,
        )
        assert res["success"] is True
        assert res["dry_run"] is True
        assert res["campaign_id"] == "cmv0h59il0kfbcv26ffgw79ut"
        assert res["title"] == "KICK AVENUE FAIR 2026"
        assert res["cpm"] == 3000
