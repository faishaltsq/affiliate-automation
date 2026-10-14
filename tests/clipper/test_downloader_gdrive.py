"""tests/clipper/test_downloader_gdrive.py — Unit tests for Google Drive asset download."""

from unittest.mock import patch, MagicMock
from pathlib import Path
from src.clipper.downloader import download_asset_videos, download_asset_video, _download_cloud_or_archive


def test_download_gdrive_folder_triggers_ytdlp(tmp_path):
    """Google Drive folder URLs trigger yt-dlp without --no-playlist and output title format."""
    folder_url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"

    with patch("subprocess.run") as mock_run:
        def fake_run(cmd, *args, **kwargs):
            # Assert yt-dlp was invoked with right args for folder
            assert any("yt_dlp" in str(arg) for arg in cmd)
            assert "--no-playlist" not in cmd
            assert any("%(title)s.%(ext)s" in str(arg) for arg in cmd)
            assert folder_url in cmd

            # Simulate yt-dlp downloading 2 video files
            (tmp_path / "clip_01.mp4").write_text("dummy video 1")
            (tmp_path / "clip_02.mp4").write_text("dummy video 2")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        vids = download_asset_videos(folder_url, output_dir=str(tmp_path))

        assert len(vids) == 2
        assert "clip_01.mp4" in vids[0]
        assert "clip_02.mp4" in vids[1]
        assert mock_run.called


def test_download_gdrive_file_triggers_ytdlp(tmp_path):
    """Google Drive file URLs trigger yt-dlp with title format and return file without early exit."""
    file_url = "https://drive.google.com/file/d/1XyZ9876543210/view?usp=sharing"

    with patch("subprocess.run") as mock_run:
        def fake_run(cmd, *args, **kwargs):
            assert any("yt_dlp" in str(arg) for arg in cmd)
            assert any("%(title)s.%(ext)s" in str(arg) for arg in cmd)
            assert file_url in cmd

            (tmp_path / "single_asset.mp4").write_text("single video")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        vids = download_asset_videos(file_url, output_dir=str(tmp_path))

        assert len(vids) == 1
        assert "single_asset.mp4" in vids[0]
        assert mock_run.called


def test_download_asset_video_single_gdrive(tmp_path):
    """download_asset_video returns first file from Google Drive."""
    folder_url = "https://drive.google.com/drive/folders/test123"

    with patch("subprocess.run") as mock_run:
        def fake_run(cmd, *args, **kwargs):
            (tmp_path / "part1.mp4").write_text("v1")
            (tmp_path / "part2.mp4").write_text("v2")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run

        vid = download_asset_video(folder_url, output_dir=str(tmp_path))

        assert vid is not None
        assert "part1.mp4" in vid


def test_download_cloud_gdrive_file_converts_to_uc_export():
    """_download_cloud_or_archive converts drive file link to direct uc?export=download URL."""
    file_url = "https://drive.google.com/file/d/12345FILEID67890/view?usp=sharing"

    with patch("requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        res = _download_cloud_or_archive(file_url, output_dir="dummy_dir")
        assert res == []
        # Verify requests.get called with converted export URL
        call_url = mock_get.call_args[0][0]
        assert "drive.google.com/uc?export=download&id=12345FILEID67890" in call_url
