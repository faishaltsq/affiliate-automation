"""tests/clipper/test_pipeline_motionklip.py — Unit tests for Motionklip routing in clipper pipeline."""

from unittest.mock import patch, MagicMock
from src.clipper.pipeline import run_clipper_pipeline, get_campaign_provider
from src.clipper.motionklip_client import MotionklipClient
from src.clipper.client import ClippoClient


SAMPLE_MOTIONKLIP_CAMPAIGN = {
    "id": "mk-12345",
    "title": "WINDAH BASUDARA X RUCAS",
    "cpm": 15000,
    "budget_percentage": 85.0,
    "min_duration": 30,
    "max_duration": 60,
    "hashtags": ["#WindahBasudara", "#Rucas", "#MotionKlip"],
    "dos": [
        "Pakai hook yang heboh di 3 detik awal",
        "Wajib tag @windahbasudara dan @rucas",
    ],
    "donts": [
        "Dilarang reupload mentah tanpa edit",
        "Dilarang pakai footage yang berbau SARA",
    ],
    "assets": [
        {"url": "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz", "display_name": "Google Drive Folder"},
        {"url": "https://youtu.be/sample_stream_vod", "display_name": "YouTube Stream"},
    ],
    "source": "motionklip",
    "materials_count": 2,
}


def test_get_campaign_provider_routing():
    """Verify get_campaign_provider routes mk-* to MotionklipClient and others to ClippoClient."""
    mk_provider = get_campaign_provider("mk-1234567890")
    assert isinstance(mk_provider, MotionklipClient)

    clippo_provider = get_campaign_provider("cmv0h59il0kfbcv26ffgw79ut")
    assert isinstance(clippo_provider, ClippoClient)


def test_run_clipper_pipeline_motionklip_dry_run():
    """Verify run_clipper_pipeline routes mk-* campaign to Motionklip and succeeds in dry-run."""
    with patch.object(MotionklipClient, "get_campaign_detail", return_value=SAMPLE_MOTIONKLIP_CAMPAIGN):
        res = run_clipper_pipeline("mk-12345", dry_run=True)

        assert res["success"] is True
        assert res["dry_run"] is True
        assert res["campaign_id"] == "mk-12345"
        assert res["platform"] == "Motionklip"
        assert res["title"] == "WINDAH BASUDARA X RUCAS"
        assert res["cpm"] == 15000
        assert "brief" in res
        assert res["source_url"] == "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_run_clipper_pipeline_motionklip_not_found():
    """Verify run_clipper_pipeline handles missing Motionklip campaign gracefully."""
    with patch.object(MotionklipClient, "get_campaign_detail", return_value={}):
        res = run_clipper_pipeline("mk-99999", dry_run=True)

        assert res["success"] is False
        assert "tidak ditemukan di Motionklip" in res["error"]
