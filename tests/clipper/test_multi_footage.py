"""tests/clipper/test_multi_footage.py — Unit tests for multi-footage clipping and cloud downloads."""

import os
from unittest.mock import patch, MagicMock
from src.clipper.downloader import download_asset_videos
from src.clipper.editor import render_clipper_video


def test_download_asset_videos_cached(tmp_path):
    sub = tmp_path / "sub_extracted"
    sub.mkdir()
    (sub / "part2.mp4").write_text("v2")
    (sub / "part1.mp4").write_text("v1")
    (sub / "part10.mp4").write_text("v10")

    res = download_asset_videos("https://example.com/sub", output_dir=str(tmp_path))
    assert len(res) == 3
    # Check natural sorting: part1, part2, part10
    assert "part1.mp4" in res[0]
    assert "part2.mp4" in res[1]
    assert "part10.mp4" in res[2]


def test_render_clipper_video_multi_footage_mock():
    with patch("src.clipper.editor.subprocess.run") as mock_run, \
         patch("src.clipper.editor.get_video_duration", return_value=30.0), \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("os.path.exists", return_value=True):
        mock_run.return_value = MagicMock(returncode=0)

        out = render_clipper_video(
            input_path=["foot1.mp4", "foot2.mp4", "foot3.mp4"],
            duration_sec=60.0,
            hook_text="EDUKASI EMAS",
        )
        assert out is not None
        assert "clip_multi_3_60s.mp4" in out


def test_render_clipper_video_filters_invalid_media():
    def fake_is_valid(path):
        return "invalid" not in path

    with patch("src.clipper.editor.subprocess.run") as mock_run, \
         patch("src.clipper.editor.get_video_duration", return_value=30.0), \
         patch("src.clipper.editor.is_valid_media_file", side_effect=fake_is_valid), \
         patch("os.path.exists", return_value=True):
        mock_run.return_value = MagicMock(returncode=0)

        out = render_clipper_video(
            input_path=["foot1.mp4", "invalid_page.mp4", "foot2.mp4"],
            duration_sec=60.0,
            hook_text="EDUKASI EMAS",
        )
        assert out is not None
        # Must have filtered out invalid_page and kept only 2 valid clips
        assert "clip_multi_2_60s.mp4" in out
        # Verify ffmpeg called with concat=n=2 (only valid files)
        render_calls = [args[0][0] for args in mock_run.call_args_list if "-filter_complex" in args[0][0]]
        assert len(render_calls) > 0
        call_args = render_calls[0]
        assert "-filter_complex" in call_args
        fc = call_args[call_args.index("-filter_complex") + 1]
        assert "concat=n=2:v=1:a=1" in fc
        assert "EDUKASI EMAS" in fc


def test_render_clipper_multi_with_climax_teaser_and_all_footages():
    climax_info = {
        "target_file": "foot2.mp4",
        "climax_start": 12.0,
        "climax_duration": 3.5,
        "punchline_hook": "DETIK-DETIK NANGKEP PENJUAL!",
        "reason": "Dramatic confrontation",
    }
    with patch("src.clipper.editor.subprocess.run") as mock_run, \
         patch("src.clipper.editor.get_video_duration", return_value=30.0), \
         patch("src.clipper.editor.is_valid_media_file", return_value=True), \
         patch("src.clipper.editor.generate_clipper_subtitles", return_value="dummy.ass"), \
         patch("src.clipper.editor.burn_subtitles_to_video", return_value=True), \
         patch("os.path.exists", return_value=True):
        mock_run.return_value = MagicMock(returncode=0)

        out = render_clipper_video(
            input_path=["foot1.mp4", "foot2.mp4", "foot3.mp4"],
            duration_sec=60.0,
            hook_text="EDUKASI EMAS",
            climax_info=climax_info,
            auto_subtitles=True,
        )
        assert out is not None
        render_calls = [args[0][0] for args in mock_run.call_args_list if "-filter_complex" in args[0][0]]
        assert len(render_calls) > 0
        call_args = render_calls[0]
        assert "-filter_complex" in call_args
        fc = call_args[call_args.index("-filter_complex") + 1]
        # Must have concat=n=4 (teaser + all 3 footages)
        assert "concat=n=4:v=1:a=1" in fc
        assert "DETIK-DETIK NANGKEP PENJUAL!" in fc
