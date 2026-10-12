"""tests/tiktok/test_uploader.py"""

import os
import pytest
from unittest.mock import patch, MagicMock

from src.tiktok.uploader import TikTokUploader


def test_init_raises_if_cookies_missing(tmp_path):
    missing = str(tmp_path / "nonexistent.txt")
    with pytest.raises(FileNotFoundError):
        TikTokUploader(missing)


def test_upload_video_file_not_found(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookies")
    uploader = TikTokUploader(str(cookies))
    assert not uploader.upload_video(str(tmp_path / "missing.mp4"), "Description")


def test_upload_video_subprocess_success(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookies")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")

    uploader = TikTokUploader(str(cookies))
    mock_proc = MagicMock(returncode=0)

    with patch("subprocess.run", return_value=mock_proc):
        assert uploader.upload_video(str(video), "Caption") is True


def test_upload_video_subprocess_failure(tmp_path):
    cookies = tmp_path / "cookies.txt"
    cookies.write_text("dummy cookies")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"data")

    uploader = TikTokUploader(str(cookies))
    mock_proc = MagicMock(returncode=1, stderr="Subprocess error")

    with patch("subprocess.run", return_value=mock_proc):
        assert uploader.upload_video(str(video), "Caption") is False
