"""tests/tiktok/test_subtitles.py — Unit tests for subtitles module."""

import os
from unittest.mock import patch, MagicMock
from src.tiktok.subtitles import format_ass_timestamp, generate_subtitles_ass


def test_format_ass_timestamp():
    assert format_ass_timestamp(0.0) == "0:00:00.00"
    assert format_ass_timestamp(5.25) == "0:00:05.25"
    assert format_ass_timestamp(65.5) == "0:01:05.50"
    assert format_ass_timestamp(3661.12) == "1:01:01.12"


def test_generate_subtitles_ass_no_file():
    res = generate_subtitles_ass("nonexistent_file_path.mp4")
    assert res is None


def test_generate_subtitles_ass_mock(tmp_path):
    video_dummy = tmp_path / "vid.mp4"
    video_dummy.write_text("dummy")

    mock_seg = MagicMock()
    mock_seg.start = 1.0
    mock_seg.end = 4.0
    mock_seg.text = " Halo semuanya kita sedang camping "

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_seg], MagicMock())

    with patch("src.tiktok.subtitles._get_whisper_model", return_value=mock_model), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        out_ass = str(tmp_path / "subs.ass")
        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=5000):
            res = generate_subtitles_ass(
                str(video_dummy),
                start_time=0.0,
                duration=10.0,
                output_ass_path=out_ass,
                margin_v=600,
            )
            assert res == out_ass
            assert os.path.exists(out_ass)
            content = (tmp_path / "subs.ass").read_text(encoding="utf-8")
            assert "PlayResX: 1080" in content
            assert "PlayResY: 1920" in content
            assert "Halo semuanya kita sedang camping" in content
            assert "Dialogue: 0,0:00:01.00,0:00:04.00" in content


def test_get_whisper_model_caches_per_size():
    """Two different sizes must not clobber each other."""
    from unittest.mock import MagicMock, patch as _patch
    import src.tiktok.subtitles as sub

    sub._WHISPER_MODELS.clear()
    fake = MagicMock()
    with _patch.dict("sys.modules", {"faster_whisper": MagicMock(WhisperModel=MagicMock(return_value=fake))}):
        sub._get_whisper_model("tiny")
        sub._get_whisper_model("base")
    assert set(sub._WHISPER_MODELS.keys()) == {"tiny", "base"}


def test_generate_subtitles_does_not_force_language(tmp_path):
    """Source may be English; subtitles must not hardcode Indonesian."""
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    video_dummy = tmp_path / "vid.mp4"
    video_dummy.write_text("dummy")

    mock_seg = MagicMock()
    mock_seg.start, mock_seg.end, mock_seg.text = 1.0, 4.0, " hello world "

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_seg], MagicMock())

    with _patch("src.tiktok.subtitles._get_whisper_model", return_value=mock_model), \
         _patch("subprocess.run") as mock_run, \
         _patch("os.path.exists", return_value=True), \
         _patch("os.path.getsize", return_value=5000):
        mock_run.return_value = MagicMock(returncode=0)
        out_ass = str(tmp_path / "subs.ass")
        generate_subtitles_ass(str(video_dummy), duration=10.0, output_ass_path=out_ass)

    kwargs = mock_model.transcribe.call_args.kwargs
    assert kwargs.get("language") is None, f"language must default to None, got {kwargs.get('language')!r}"


def test_generate_subtitles_uses_injected_segments_without_transcribing(tmp_path):
    """THE ONE RULE: when segments are supplied, Whisper must NOT be called."""
    from unittest.mock import MagicMock
    from unittest.mock import patch as _patch

    video_dummy = tmp_path / "vid.mp4"
    video_dummy.write_text("dummy")
    out_ass = str(tmp_path / "subs.ass")

    mock_model = MagicMock()

    with _patch("src.tiktok.subtitles._get_whisper_model", return_value=mock_model), \
         _patch("subprocess.run") as mock_run, \
         _patch("os.path.exists", return_value=True):
        mock_run.return_value = MagicMock(returncode=0)
        res = generate_subtitles_ass(
            str(video_dummy),
            duration=10.0,
            output_ass_path=out_ass,
            segments=[
                {"start": 0.5, "end": 2.0, "text": "we found the trail"},
                {"start": 2.0, "end": 4.5, "text": "and followed it north"},
            ],
        )

    assert res == out_ass
    mock_model.transcribe.assert_not_called()
    content = (tmp_path / "subs.ass").read_text(encoding="utf-8")
    assert "we found the trail" in content
    assert "and followed it north" in content
    assert "0:00:00.50,0:00:02.00" in content
