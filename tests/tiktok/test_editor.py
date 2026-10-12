"""Tests for editor.py — video trim, crop, overlay."""

from __future__ import annotations

import json
import os
import subprocess
import pytest

from src.tiktok.editor import get_video_duration, process_video

DUMMY_DURATION = 4  # seconds


@pytest.fixture()
def dummy_video(tmp_path):
    """Generate a short 1280x720 blue dummy video with silent audio."""
    path = str(tmp_path / "test_dummy.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=1280x720:d={DUMMY_DURATION}",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", str(DUMMY_DURATION),
        "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-shortest",
        path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    yield path


class TestGetVideoDuration:
    def test_returns_approx_duration(self, dummy_video):
        dur = get_video_duration(dummy_video)
        assert abs(dur - DUMMY_DURATION) < 0.5, f"Expected ~{DUMMY_DURATION}s, got {dur}"

    def test_nonexistent_file_raises(self):
        with pytest.raises(RuntimeError):
            get_video_duration("nonexistent_video.mp4")


class TestProcessVideo:
    def test_output_dimensions_9_16(self, dummy_video, tmp_path):
        out = str(tmp_path / "out.mp4")
        result = process_video(dummy_video, out)
        assert result == out
        assert os.path.isfile(out)

        # Verify dimensions via ffprobe
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json",
                out,
            ],
            capture_output=True, text=True, check=True,
        )
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        assert stream["width"] == 1080
        assert stream["height"] == 1920

    def test_trim_respects_max_duration(self, dummy_video, tmp_path):
        out = str(tmp_path / "short.mp4")
        process_video(dummy_video, out, max_duration=2)
        dur = get_video_duration(out)
        assert dur <= 2.5, f"Trimmed video too long: {dur}s"

    def test_overlay_text_does_not_crash(self, dummy_video, tmp_path):
        out = str(tmp_path / "overlay.mp4")
        result = process_video(dummy_video, out, overlay_text="Hello TikTok")
        assert result == out
        assert os.path.isfile(out)

    def test_bad_input_raises(self, tmp_path):
        out = str(tmp_path / "fail.mp4")
        with pytest.raises(RuntimeError):
            process_video("nonexistent.mp4", out)
