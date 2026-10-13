import pytest
from unittest.mock import patch, MagicMock
from src.clipper.pipeline import run_clipper_pipeline


@patch("src.clipper.pipeline.ClippoClient")
def test_run_clipper_pipeline_dry_run_multi_video(mock_client_cls):
    mock_client = MagicMock()
    mock_client.get_campaign_detail.return_value = {
        "title": "Campaign Multi Emas",
        "cpm": 20000,
        "assets": [{"url": "https://example.com/v1.mp4"}],
    }
    mock_client_cls.return_value = mock_client

    res = run_clipper_pipeline("test_camp_123", dry_run=True, count=3)
    assert res["success"] is True
    assert res["dry_run"] is True
    assert res["count"] == 3
    assert res["title"] == "Campaign Multi Emas"
