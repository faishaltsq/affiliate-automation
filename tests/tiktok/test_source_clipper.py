"""tests/tiktok/test_source_clipper.py"""

import os
import tempfile
from unittest.mock import patch

from src.tiktok.source_clipper import run_source_pipeline
from src.tiktok.state import VideoStateDB


def _db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    return f.name, VideoStateDB(f.name)


SEGMENTS = [
    {"start": 5.0, "end": 9.0, "text": "we found the trail"},
    {"start": 60.0, "end": 64.0, "text": "here comes the storm"},
    {"start": 300.0, "end": 305.0, "text": "that is a walrus tusk"},
]


def test_pipeline_uploads_every_clip_and_uses_shared_transcript(tmp_path):
    db_path, db = _db()
    source = tmp_path / "src.mp4"
    source.write_bytes(b"0" * 5000)

    hooks = [
        {"start": 0.0, "end": 75.0, "duration": 75.0, "hook_title": "HOOK ONE", "reason": ""},
        {"start": 295.0, "end": 370.0, "duration": 75.0, "hook_title": "HOOK TWO", "reason": ""},
    ]
    rendered, tt, yt = [], [], []

    def fake_render(**kw):
        rendered.append(kw)
        with open(kw["output_path"], "wb") as fh:
            fh.write(b"0" * 3000)
        return kw["output_path"]

    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata",
                   return_value={"id": "abc123", "title": "Long Source", "duration": 3600}), \
             patch("src.tiktok.source_clipper.transcribe_source",
                   return_value={"segments": SEGMENTS, "language": "en", "duration": 3600}), \
             patch("src.tiktok.source_clipper.download_video_720p", return_value=str(source)), \
             patch("src.tiktok.source_clipper.find_hook_windows", return_value=hooks), \
             patch("src.tiktok.source_clipper.process_video", side_effect=fake_render):
            res = run_source_pipeline(
                url="https://youtu.be/abc123", count=2, db=db,
                config={"queue_dir": str(tmp_path / "q"), "download_dir": str(tmp_path / "d")},
                upload_tiktok_fn=lambda p, c: (tt.append(p) or True),
                upload_youtube_fn=lambda p, t, d, g: (yt.append(p) or {"success": True, "url": "u"}),
            )

        assert res["success"] is True
        assert res["clips_enqueued"] == 2
        assert res["clips_uploaded"] == 2
        assert len(tt) == 2 and len(yt) == 2

        # THE ONE RULE: subtitles are injected, and rebased to clip-relative time
        assert rendered[0]["subtitle_segments"] == [
            {"start": 5.0, "end": 9.0, "text": "we found the trail"},
            {"start": 60.0, "end": 64.0, "text": "here comes the storm"},
        ]
        assert rendered[1]["subtitle_segments"] == [
            {"start": 5.0, "end": 10.0, "text": "that is a walrus tusk"},
        ]
        assert rendered[0]["subtitle_language"] == "en"
        assert "HOOK ONE" in rendered[0]["overlay_text"]
        assert rendered[0]["start_time"] == 0.0
        # Both clips went out, so neither is still advertised as pending in the queue.
        assert db.get_queued_count() == 0
    finally:
        db.close()
        os.unlink(db_path)


def test_pipeline_reports_partial_upload_failure(tmp_path):
    db_path, db = _db()
    source = tmp_path / "src.mp4"
    source.write_bytes(b"0" * 5000)
    hooks = [{"start": 0.0, "end": 75.0, "duration": 75.0, "hook_title": "H", "reason": ""}]

    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata",
                   return_value={"id": "x1", "title": "T", "duration": 3600}), \
             patch("src.tiktok.source_clipper.transcribe_source",
                   return_value={"segments": SEGMENTS, "language": "en", "duration": 3600}), \
             patch("src.tiktok.source_clipper.download_video_720p", return_value=str(source)), \
             patch("src.tiktok.source_clipper.find_hook_windows", return_value=hooks), \
             patch("src.tiktok.source_clipper.process_video",
                   side_effect=lambda **kw: (open(kw["output_path"], "wb").write(b"0" * 3000), kw["output_path"])[1]):
            res = run_source_pipeline(
                url="https://youtu.be/x1", count=1, db=db,
                config={"queue_dir": str(tmp_path / "q"), "download_dir": str(tmp_path / "d")},
                upload_tiktok_fn=lambda p, c: False,
                upload_youtube_fn=lambda p, t, d, g: {"success": False, "error": "quota"},
            )

        assert res["clips_uploaded"] == 0
        assert res["clips_enqueued"] == 1
        assert res["errors"]
        # Both uploads failed, so the clip stays pending for a later /upload retry.
        assert db.get_queued_count() == 1
    finally:
        db.close()
        os.unlink(db_path)


def test_pipeline_refuses_already_processed_source(tmp_path):
    db_path, db = _db()
    db.enqueue_clip("abc123_part1", "abc123", 1, 1, "T", "H", "/tmp/x.mp4")
    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata",
                   return_value={"id": "abc123", "title": "T", "duration": 3600}):
            res = run_source_pipeline(url="https://youtu.be/abc123", db=db, config={})
        assert res["success"] is False
        assert "sudah" in res["error"].lower()
    finally:
        db.close()
        os.unlink(db_path)


def test_pipeline_fails_without_metadata(tmp_path):
    db_path, db = _db()
    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata", return_value={}):
            res = run_source_pipeline(url="https://youtu.be/bad", db=db, config={})
        assert res["success"] is False and res["error"]
    finally:
        db.close()
        os.unlink(db_path)


def test_pipeline_fails_when_transcription_empty(tmp_path):
    db_path, db = _db()
    source = tmp_path / "src.mp4"
    source.write_bytes(b"0" * 5000)
    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata",
                   return_value={"id": "n1", "title": "T", "duration": 3600}), \
             patch("src.tiktok.source_clipper.transcribe_source",
                   return_value={"segments": [], "language": None, "duration": 0}), \
             patch("src.tiktok.source_clipper.download_video_720p", return_value=str(source)):
            res = run_source_pipeline(url="https://youtu.be/n1", db=db,
                                      config={"queue_dir": str(tmp_path / "q"),
                                              "download_dir": str(tmp_path / "d")})
        assert res["success"] is False
        assert "transkrip" in res["error"].lower()
    finally:
        db.close()
        os.unlink(db_path)


def test_pipeline_reports_language_from_transcript(tmp_path):
    db_path, db = _db()
    source = tmp_path / "src.mp4"
    source.write_bytes(b"0" * 5000)
    hooks = [{"start": 0.0, "end": 75.0, "duration": 75.0, "hook_title": "H", "reason": ""}]
    try:
        with patch("src.tiktok.source_clipper.fetch_source_metadata",
                   return_value={"id": "z9", "title": "T", "duration": 3600}), \
             patch("src.tiktok.source_clipper.transcribe_source",
                   return_value={"segments": SEGMENTS, "language": "ja", "duration": 3600}), \
             patch("src.tiktok.source_clipper.download_video_720p", return_value=str(source)), \
             patch("src.tiktok.source_clipper.find_hook_windows", return_value=hooks), \
             patch("src.tiktok.source_clipper.process_video",
                   side_effect=lambda **kw: (open(kw["output_path"], "wb").write(b"0" * 3000), kw["output_path"])[1]):
            res = run_source_pipeline(
                url="https://youtu.be/z9", count=1, db=db,
                config={"queue_dir": str(tmp_path / "q"), "download_dir": str(tmp_path / "d")},
                upload_tiktok_fn=lambda p, c: True,
                upload_youtube_fn=lambda p, t, d, g: {"success": True},
            )
        assert res["language"] == "ja"
    finally:
        db.close()
        os.unlink(db_path)
