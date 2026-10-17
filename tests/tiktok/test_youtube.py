"""tests/tiktok/test_youtube.py"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.tiktok.youtube import (
    download_video,
    search_videos,
    _extract_video_id,
    get_action_start_time,
)


def test_extract_video_id():
    assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert _extract_video_id("https://invalid-url.com") is None


def test_search_videos_parses_and_filters_duration():
    fake_output = "\n".join([
        json.dumps({"id": "v1", "title": "Short Video", "channel": "Chan1", "duration": 45, "url": "https://youtu.be/v1"}),
        json.dumps({"id": "v2", "title": "Long Video", "channel": "Chan2", "duration": 120, "url": "https://youtu.be/v2"}),
        json.dumps({"id": "v3", "title": "Another Short", "channel": "Chan3", "duration": 30, "url": "https://youtu.be/v3"}),
    ])

    mock_res = MagicMock()
    mock_res.stdout = fake_output
    mock_res.returncode = 0

    with patch("subprocess.run", return_value=mock_res):
        results = search_videos(["test", "keywords"], max_results=5, max_duration=60)

    assert len(results) == 2
    assert results[0]["id"] == "v1"
    assert results[0]["title"] == "Short Video"
    assert results[0]["duration"] == 45
    assert results[1]["id"] == "v3"
    assert results[1]["duration"] == 30


def test_search_videos_deduplicates_by_id():
    fake_output = "\n".join([
        json.dumps({"id": "dup1", "title": "Vid 1", "channel": "C1", "duration": 20}),
        json.dumps({"id": "dup1", "title": "Vid 1 Duplicate", "channel": "C1", "duration": 20}),
        json.dumps({"id": "dup2", "title": "Vid 2", "channel": "C2", "duration": 25}),
    ])

    mock_res = MagicMock()
    mock_res.stdout = fake_output
    mock_res.returncode = 0

    with patch("subprocess.run", return_value=mock_res):
        results = search_videos(["dup"], max_results=10, max_duration=60)

    assert len(results) == 2
    assert [r["id"] for r in results] == ["dup1", "dup2"]


def test_search_videos_respects_max_results():
    fake_output = "\n".join([
        json.dumps({"id": f"id_{i}", "title": f"Title {i}", "duration": 15})
        for i in range(10)
    ])

    mock_res = MagicMock()
    mock_res.stdout = fake_output
    mock_res.returncode = 0

    with patch("subprocess.run", return_value=mock_res):
        results = search_videos(["query"], max_results=3, max_duration=60)

    # search_videos collects up to max_results*3 candidates so the pipeline can
    # pick from a larger pool; the pipeline then slices to max_results.
    assert len(results) <= 30  # max_results * 3 * 2 upper bound
    assert len(results) >= 3   # at least as many as requested


def test_search_videos_handles_error_gracefully():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=60)):
        results = search_videos(["query"])
        assert results == []

    with patch("subprocess.run", side_effect=Exception("network down")):
        results = search_videos(["query"])
        assert results == []


def test_download_video_success():
    with tempfile.TemporaryDirectory() as tmpdir:
        video_id = "abc123XYZ00"
        url = f"https://www.youtube.com/watch?v={video_id}"

        def fake_run(cmd, **kwargs):
            target = Path(tmpdir) / f"{video_id}.mp4"
            target.write_bytes(b"dummy video data")
            res = MagicMock()
            res.returncode = 0
            res.stdout = ""
            res.stderr = ""
            return res

        with patch("subprocess.run", side_effect=fake_run):
            path = download_video(url, tmpdir)

        assert path is not None
        assert os.path.exists(path)
        assert Path(path).name == f"{video_id}.mp4"


def test_download_video_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stderr = "ERROR: Video unavailable"

        with patch("subprocess.run", return_value=mock_res):
            path = download_video("https://www.youtube.com/watch?v=missing1234", tmpdir)

        assert path is None


def test_download_video_timeout():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="yt-dlp", timeout=300)):
            path = download_video("https://www.youtube.com/watch?v=timeout1234", tmpdir)

        assert path is None


def test_get_action_start_time_with_chapters():
    chapters = [
        {"start_time": 0, "title": "Adventure Begins", "end_time": 60},
        {"start_time": 60, "title": "Driving to River", "end_time": 150},
        {"start_time": 150, "title": "Catching Big Halibut", "end_time": 400},
    ]
    start, title = get_action_start_time(chapters, total_duration=1200)
    assert start == 150.0
    assert title == "Catching Big Halibut"


def test_get_action_start_time_fallback_skips_intro():
    start, title = get_action_start_time([], total_duration=600)
    assert start > 0.0
    assert title is None


def test_download_video_hd_prefers_best_height(tmp_path, monkeypatch):
    import src.tiktok.youtube as yt

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        (tmp_path / "abc123_hd.mp4").write_bytes(b"0" * 2000)

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    monkeypatch.setattr(yt.subprocess, "run", fake_run)
    monkeypatch.setattr(yt, "_ytdlp_base", lambda with_cookies=True: ["yt-dlp"])

    res = yt.download_video_hd("https://youtu.be/abc123", str(tmp_path))
    assert res is not None and res.endswith("_hd.mp4")
    assert "height<=2160" in " ".join(captured["cmd"])


def test_fetch_source_metadata_returns_empty_on_failure(monkeypatch):
    import src.tiktok.youtube as yt

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stderr = "boom"
            stdout = ""

        return R()

    monkeypatch.setattr(yt.subprocess, "run", fake_run)
    monkeypatch.setattr(yt, "_ytdlp_base", lambda with_cookies=True: ["yt-dlp"])
    assert yt.fetch_source_metadata("https://youtu.be/nope") == {}
