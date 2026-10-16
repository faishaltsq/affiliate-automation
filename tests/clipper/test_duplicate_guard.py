"""tests/clipper/test_duplicate_guard.py — Anti-duplikat upload clipper.

Bukti insiden: campaign yang sama dijalankan berulang (dan dua campaign berbeda
yang menunjuk footage yang sama) mengunggah klip identik ke akun yang sama
berkali-kali. Guard = ledger fingerprint (akun + sumber footage + stem output).
"""

import os
import tempfile
from unittest.mock import patch, MagicMock

from src.clipper.pipeline import run_clipper_pipeline, _upload_fingerprint
from src.tiktok.state import VideoStateDB


def _campaign():
    return {
        "id": "cmqurp93t04xr9i268pvshwj0",
        "title": "CLIP FOR CLIPPO",
        "cpm": 1000,
        "hashtags": ["#clippo"],
        "assets": [{"url": "https://youtu.be/4ORaAYqgsxo", "display_name": "Footage"}],
        "source": "clippo",
    }


def _run(db_path: str, force: bool = False):
    """Run the pipeline end-to-end with everything external mocked."""
    with patch("src.tiktok.config.load_config", return_value={"db_path": db_path}), \
         patch("src.clipper.pipeline.ClippoClient") as mock_client_cls, \
         patch("src.clipper.pipeline.analyze_campaign_brief", return_value={"title": "CLIP FOR CLIPPO", "winning_hooks": ["Hook"], "hashtags": ["#clippo"]}), \
         patch("src.clipper.pipeline.download_asset_videos", return_value=["data/clippo/downloads/4ORaAYqgsxo.mp4"]), \
         patch("src.clipper.pipeline.render_clipper_video", return_value="data/clippo/output/clip_4ORaAYqgsxo_0_60.mp4"), \
         patch("src.clipper.pipeline.list_accounts", return_value=[{
             "name": "pochita_1", "username": "@pochitalkshop", "style": "default",
             "cookies_path": "data/tiktok/accounts/pochita_1/cookies.txt", "has_cookies": True,
         }]), \
         patch("src.clipper.pipeline.TikTokUploader") as mock_uploader_cls, \
         patch("src.clipper.punchline.transcribe_footage_clips", return_value=[]), \
         patch("src.clipper.story_analyzer.build_story_narrative_beats", return_value=[]), \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("os.path.exists", return_value=True):

        mock_client_cls.return_value.get_campaign_detail.return_value = _campaign()
        mock_uploader = MagicMock()
        mock_uploader.upload_video.return_value = True
        mock_uploader_cls.return_value = mock_uploader

        res = run_clipper_pipeline(
            campaign_id="cmqurp93t04xr9i268pvshwj0",
            source_url="https://youtu.be/4ORaAYqgsxo",
            count=1,
            distribution="distribute",
            force=force,
        )
        return res, mock_uploader


def test_second_run_skips_already_uploaded_clip():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        res1, up1 = _run(db_path)
        assert up1.upload_video.call_count == 1
        assert res1["success_count"] == 1

        # Second run of the same campaign → identical clip, same account.
        res2, up2 = _run(db_path)
        assert up2.upload_video.call_count == 0, "clip identik tidak boleh diupload ulang"
        assert res2["upload_results"][0].get("skipped_duplicate") is True

        # Ledger recorded exactly one upload.
        db = VideoStateDB(db_path)
        assert len(db.recent_clipper_uploads()) == 1
        db.close()
    finally:
        os.unlink(db_path)


def test_force_reuploads():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        _run(db_path)
        res2, up2 = _run(db_path, force=True)
        assert up2.upload_video.call_count == 1, "--force harus mengunggah ulang"
        assert not res2["upload_results"][0].get("skipped_duplicate")
    finally:
        os.unlink(db_path)


def test_fingerprint_is_stable_and_content_keyed():
    a = _upload_fingerprint("@pochitalkshop", "data/clippo/output/clip_X_0_60.mp4",
                            ["data/clippo/downloads/X.mp4"])
    b = _upload_fingerprint("@pochitalkshop", "data/clippo/output/clip_X_0_60.mp4",
                            ["data/clippo/downloads/X.mp4"])
    assert a == b

    # Different account → different identity (fan-out ke banyak akun tetap boleh).
    assert _upload_fingerprint("@dummydonut267", "data/clippo/output/clip_X_0_60.mp4",
                               ["data/clippo/downloads/X.mp4"]) != a
    # Different footage → different identity.
    assert _upload_fingerprint("@pochitalkshop", "data/clippo/output/clip_X_0_60.mp4",
                               ["data/clippo/downloads/Y.mp4"]) != a


def test_cross_campaign_same_footage_same_account_is_skipped():
    """Dua campaign berbeda yang menunjuk footage sama = klip sama.

    Insiden nyata: campaign PROTABUMIN merender footage milik campaign CLIPPO
    dan mengunggah klip yang sama ke @pochitalkshop lagi.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        fp1 = _upload_fingerprint("@pochitalkshop", "data/clippo/output/clip_4ORaAYqgsxo_0_60.mp4",
                                  ["data/clippo/downloads/4ORaAYqgsxo.mp4"])
        fp2 = _upload_fingerprint("@pochitalkshop", "data/clippo/output/clip_4ORaAYqgsxo_0_60.mp4",
                                  ["data/clippo/downloads/4ORaAYqgsxo.mp4"])
        db = VideoStateDB(db_path)
        db.mark_clipper_uploaded(fp1, "cmqurp93t04xr9i268pvshwj0", "@pochitalkshop", "clip.mp4")
        assert db.is_clipper_uploaded(fp2), "footage sama ke akun sama harus terdeteksi duplikat"
        db.close()
    finally:
        os.unlink(db_path)
