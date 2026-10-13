import os
import pytest
from unittest.mock import patch, MagicMock
from src.clipper.subtitles import generate_clipper_subtitles, burn_subtitles_to_video

def test_generate_clipper_subtitles(tmp_path):
    dummy_mp4 = tmp_path / "final.mp4"
    dummy_mp4.write_text("fake_video")

    mock_seg = MagicMock()
    mock_seg.start = 1.0
    mock_seg.end = 3.5
    mock_seg.text = " Ini hasil tes kadarnya Mas "

    with patch("src.clipper.subtitles._get_whisper_model") as mock_m, \
         patch("src.clipper.subtitles.subprocess.run") as mock_run, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=50000):
        mock_run.return_value = MagicMock(returncode=0)
        mock_m.return_value.transcribe.return_value = ([mock_seg], None)

        out_ass = str(tmp_path / "final.ass")
        res = generate_clipper_subtitles(str(dummy_mp4), out_ass=out_ass, margin_v=480)
        assert res == out_ass
        content = (tmp_path / "final.ass").read_text(encoding="utf-8")
        assert "PlayResX: 1080" in content
        assert "Ini hasil tes kadarnya Mas" in content
        assert ",480,1" in content  # Verifies margin_v parameter is applied

def test_burn_subtitles_to_video(tmp_path):
    in_vid = str(tmp_path / "in.mp4")
    ass_p = str(tmp_path / "in.ass")
    out_vid = str(tmp_path / "out.mp4")
    (tmp_path / "in.mp4").write_text("dummy")
    (tmp_path / "in.ass").write_text("dummy")

    with patch("src.clipper.subtitles.subprocess.run") as mock_run, \
         patch("os.path.exists") as mock_exists, \
         patch("os.path.getsize", return_value=50000):
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        ok = burn_subtitles_to_video(in_vid, ass_p, out_vid)
        assert ok is True
        call_args = mock_run.call_args[0][0]
        assert "-vf" in call_args
        vf_idx = call_args.index("-vf")
        assert "subtitles=" in call_args[vf_idx + 1]
