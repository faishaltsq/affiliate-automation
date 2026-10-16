"""tests/tiktok/test_outdoorboys.py"""

from src.tiktok.outdoorboys import select_action_chapters, clean_chapter_title


def test_select_action_chapters_prioritizes_action():
    chapters = [
        {"title": "Introduction to Alaska trip", "start_time": 0, "end_time": 120},
        {"title": "Hiking and arrival at site", "start_time": 120, "end_time": 300},
        {"title": "Building subzero snow shelter", "start_time": 300, "end_time": 600},
        {"title": "Catch and cook giant salmon over fire", "start_time": 600, "end_time": 950},
        {"title": "Night blizzard storm hits camp", "start_time": 950, "end_time": 1300},
        {"title": "Packing up and conclusion", "start_time": 1300, "end_time": 1500},
    ]

    selected = select_action_chapters(chapters, total_duration=1500, max_clips=3, clip_duration=75.0)

    assert len(selected) == 3
    # Check that intro & conclusion are skipped
    titles = [s["title"].lower() for s in selected]
    assert not any("introduction" in t for t in titles)
    assert not any("conclusion" in t for t in titles)

    # Check that chosen chapters are in chronological order for story progression
    assert selected[0]["start"] < selected[1]["start"] < selected[2]["start"]
    assert "shelter" in selected[0]["title"].lower()
    assert "cook" in selected[1]["title"].lower()
    assert "blizzard" in selected[2]["title"].lower()


def test_select_action_chapters_fallback_no_chapters():
    selected = select_action_chapters([], total_duration=1200, max_clips=3, clip_duration=60.0)
    assert len(selected) == 3
    assert selected[0]["start"] < selected[1]["start"] < selected[2]["start"]


def test_clean_chapter_title():
    raw = "Building a Secret Dugout Between Two Oak Trees!! [4K]"
    clean = clean_chapter_title(raw, max_chars=30)
    assert "Building" in clean
    assert "Dugout" in clean
    assert "!" not in clean
    assert "[" not in clean


def test_replenish_outdoorboys_queue_all_processed(monkeypatch):
    """All channel videos processed → replenish must return 0, never re-clip one.

    The old fallback ('choosing oldest candidate') re-sliced an already-processed
    video, whose clip rows flipped back to pending → duplicate uploads.
    """
    import tempfile
    import os
    from unittest.mock import patch
    from src.tiktok.state import VideoStateDB
    from src.tiktok.outdoorboys import replenish_outdoorboys_queue

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    db = None
    try:
        db = VideoStateDB(db_path)
        # v1 is already sliced AND already uploaded → queue empty, video known.
        db.enqueue_clip("v1_part1", "v1", 1, 1, "T1", "Ch1", "/tmp/p.mp4")
        db.mark_clip_uploaded("v1_part1")
        assert db.get_queued_count() == 0

        notices = []
        with patch(
            "src.tiktok.outdoorboys.fetch_channel_videos",
            return_value=[{"id": "v1", "title": "Already Done", "url": "https://youtu.be/v1"}],
        ), patch("src.tiktok.outdoorboys.generate_outdoorboys_clips") as mock_gen:
            cnt = replenish_outdoorboys_queue(db, config={}, notify_fn=notices.append)

        assert cnt == 0
        assert not mock_gen.called, "video yang sudah diproses tidak boleh dipotong ulang"
        assert db.get_queued_count() == 0, "klip lama tidak boleh kembali ke antrean"
        assert any("sudah pernah dipotong" in n for n in notices)
    finally:
        if db is not None:
            db.close()
        os.unlink(db_path)


def test_replenish_outdoorboys_queue_existing_queue(monkeypatch):
    import tempfile
    import os
    from src.tiktok.state import VideoStateDB
    from src.tiktok.outdoorboys import replenish_outdoorboys_queue

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        db = VideoStateDB(db_path)
        # Pre-seed one clip
        db.enqueue_clip("c1", "v1", 1, 1, "T1", "Ch1", "/tmp/p.mp4")

        # Replenish should return 1 without calling YouTube
        cnt = replenish_outdoorboys_queue(db, config={})
        assert cnt == 1
        db.close()
    finally:
        os.unlink(db_path)

