"""tests/tiktok/test_transcript.py"""

from unittest.mock import MagicMock, patch

from src.tiktok.transcript import transcribe_source, slice_segments


def _fake_model(segs):
    model = MagicMock()
    model.transcribe.return_value = (segs, MagicMock(language="en", language_probability=1.0))
    return model


def _seg(start, end, text):
    s = MagicMock()
    s.start, s.end, s.text = start, end, text
    return s


def test_transcribe_source_autodetects_language(tmp_path):
    """Language must be auto-detected, never forced to Indonesian."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 2000)

    def fake_run(cmd, **kwargs):
        with open(cmd[-1], "wb") as fh:
            fh.write(b"0" * 5000)

        class R:
            returncode = 0
            stderr = ""

        return R()

    with patch("src.tiktok.transcript._get_whisper_model", return_value=_fake_model([_seg(1.0, 2.0, "hello")])), \
         patch("src.tiktok.transcript.subprocess.run", side_effect=fake_run):
        res = transcribe_source(str(video), language=None, model_size="base")

    assert res["language"] == "en"
    assert len(res["segments"]) == 1
    assert res["segments"][0]["text"] == "hello"


def test_transcribe_source_has_no_duration_cap(tmp_path):
    """A long source must be transcribed end to end."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 2000)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        with open(cmd[-1], "wb") as fh:
            fh.write(b"0" * 5000)

        class R:
            returncode = 0
            stderr = ""

        return R()

    with patch("src.tiktok.transcript._get_whisper_model", return_value=_fake_model([])), \
         patch("src.tiktok.transcript.subprocess.run", side_effect=fake_run):
        transcribe_source(str(video))

    assert "-t" not in seen["cmd"], "no sample cap for a long source"


def test_transcribe_source_missing_file(tmp_path):
    res = transcribe_source(str(tmp_path / "nope.mp4"))
    assert res["segments"] == []
    assert res["language"] is None


def test_slice_segments_rebases_to_clip_time():
    """Subtitles must be clip-relative, not source-relative."""
    segs = [
        {"start": 100.0, "end": 102.0, "text": "before"},
        {"start": 205.0, "end": 208.0, "text": "inside one"},
        {"start": 208.0, "end": 212.0, "text": "inside two"},
        {"start": 400.0, "end": 402.0, "text": "after"},
    ]
    out = slice_segments(segs, window_start=200.0, window_end=215.0)

    assert [s["text"] for s in out] == ["inside one", "inside two"]
    assert out[0]["start"] == 5.0    # 205 - 200
    assert out[0]["end"] == 8.0
    assert out[1]["start"] == 8.0
    assert out[1]["end"] == 12.0


def test_slice_segments_clamps_overhang():
    """A segment spilling past the window must be clamped to the window edge."""
    segs = [{"start": 198.0, "end": 300.0, "text": "long one"}]
    out = slice_segments(segs, window_start=200.0, window_end=215.0)
    assert out[0]["start"] == 0.0
    assert out[0]["end"] == 15.0


def test_slice_segments_empty():
    assert slice_segments([], 0.0, 10.0) == []
