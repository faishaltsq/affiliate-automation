"""tests/clipper/test_watermark_overlay.py — Tests for Motionklip watermark overlay integration."""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image

from src.clipper.editor import render_clipper_video, _render_clipper_multi_video
from src.clipper.pipeline import run_clipper_pipeline
from src.clipper.motionklip_client import MotionklipClient


def test_motionklip_watermark_asset_exists_and_valid():
    """Verify assets/watermarks/motionklip.png exists, is 1080x1920 RGBA."""
    wm_path = Path("assets/watermarks/motionklip.png")
    assert wm_path.exists(), "assets/watermarks/motionklip.png must exist"

    with Image.open(wm_path) as img:
        assert img.size == (1080, 1920), f"Expected size 1080x1920, got {img.size}"
        assert img.mode == "RGBA", f"Expected mode RGBA, got {img.mode}"


def test_single_render_includes_watermark_in_ffmpeg():
    """Verify render_clipper_video includes watermark input and overlay in FFmpeg cmd."""
    with patch("subprocess.run") as mock_run, \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("src.clipper.editor.get_video_duration", return_value=60.0), \
         patch("os.path.exists", return_value=True):

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        out = render_clipper_video(
            input_path="mock_video.mp4",
            output_dir="data/clippo/output",
            watermark_path="assets/watermarks/motionklip.png",
            auto_subtitles=False,
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        # Verify watermark input is added
        assert "assets/watermarks/motionklip.png" in cmd
        # Verify overlay is in filter complex
        fc_idx = cmd.index("-filter_complex")
        fc_str = cmd[fc_idx + 1]
        assert "overlay=0:0" in fc_str


def test_multi_render_includes_watermark_in_ffmpeg():
    """Verify _render_clipper_multi_video includes watermark input and overlay in FFmpeg cmd."""
    with patch("subprocess.run") as mock_run, \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("src.clipper.editor.get_video_dimensions", return_value=(1920, 1080)), \
         patch("src.clipper.editor.get_video_duration", return_value=30.0), \
         patch("os.path.exists", return_value=True):

        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        out = _render_clipper_multi_video(
            video_paths=["clip1.mp4", "clip2.mp4"],
            output_dir="data/clippo/output",
            watermark_path="assets/watermarks/motionklip.png",
            auto_subtitles=False,
        )

        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert "assets/watermarks/motionklip.png" in cmd
        fc_idx = cmd.index("-filter_complex")
        fc_str = cmd[fc_idx + 1]
        assert "overlay=0:0" in fc_str


def test_pipeline_motionklip_passes_watermark():
    """Verify run_clipper_pipeline for mk-... campaign detects watermark and passes to render."""
    mock_campaign = {
        "id": "mk-1556638531588464740",
        "title": "Apiipp X Motion Klip",
        "brand": "Motion Klip",
        "cpm": 5000,
        "source": "motionklip",
        "assets": ["https://youtube.com/watch?v=mock1"],
        "brief": "Buat klip semenarik mungkin.",
        "dos": ["Wajib pakai watermark"],
        "donts": [],
        "min_duration": 30,
        "max_duration": 60,
    }

    with patch("src.clipper.pipeline.MotionklipClient") as mock_mk_cls, \
         patch("src.clipper.pipeline.download_asset_videos", return_value=["data/clippo/downloads/mock.mp4"]), \
         patch("src.clipper.pipeline.render_clipper_video", return_value="data/clippo/output/mock_out.mp4") as mock_render, \
         patch("src.clipper.pipeline.list_accounts", return_value=[{"name": "pochita_1", "username": "@pochitalkshop", "cookies_path": "mock.txt", "has_cookies": True}]), \
         patch("src.tiktok.uploader.TikTokUploader.upload_video", return_value=True), \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("os.path.exists", return_value=True):

        mock_mk_inst = MagicMock()
        mock_mk_inst.get_campaign_detail.return_value = mock_campaign
        mock_mk_cls.return_value = mock_mk_inst

        res = run_clipper_pipeline(
            campaign_id="mk-1556638531588464740",
            count=1,
            dry_run=False,
        )

        assert mock_render.called
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs.get("watermark_path") is not None
        assert "motionklip" in call_kwargs.get("watermark_path").lower()


