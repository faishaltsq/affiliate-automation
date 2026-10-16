"""tests/tiktok/test_outdoorboys_queue.py"""

import os
import tempfile
from src.tiktok.state import VideoStateDB


def test_enqueue_and_get_next_queued_clip():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        db = VideoStateDB(db_path)
        assert db.get_queued_count() == 0
        assert db.get_next_queued_clip() is None

        # Enqueue Part 1 and Part 2
        db.enqueue_clip(
            clip_id="vid1_part1",
            video_id="vid1",
            part_num=1,
            total_parts=2,
            title="Outdoor Boys Survival Part 1",
            chapter_title="Building snow shelter",
            file_path="/tmp/vid1_part1.mp4",
        )
        db.enqueue_clip(
            clip_id="vid1_part2",
            video_id="vid1",
            part_num=2,
            total_parts=2,
            title="Outdoor Boys Survival Part 2",
            chapter_title="Cooking over fire",
            file_path="/tmp/vid1_part2.mp4",
        )

        assert db.get_queued_count() == 2
        assert db.is_video_processed("vid1")
        assert not db.is_video_processed("vid2")

        # First pop must be Part 1
        clip1 = db.get_next_queued_clip()
        assert clip1 is not None
        assert clip1["clip_id"] == "vid1_part1"
        assert clip1["part_num"] == 1

        # Mark uploaded
        db.mark_clip_uploaded("vid1_part1")
        assert db.get_queued_count() == 1

        # Second pop must be Part 2
        clip2 = db.get_next_queued_clip()
        assert clip2 is not None
        assert clip2["clip_id"] == "vid1_part2"
        assert clip2["part_num"] == 2

        db.mark_clip_uploaded("vid1_part2")
        assert db.get_queued_count() == 0
        assert db.get_next_queued_clip() is None

        # Re-enqueueing a clip that already went out must NOT resurrect it.
        # (Old INSERT OR REPLACE reset status to 'pending' → duplicate upload.)
        db.enqueue_clip(
            clip_id="vid1_part1",
            video_id="vid1",
            part_num=1,
            total_parts=2,
            title="Outdoor Boys Survival Part 1",
            chapter_title="Building snow shelter",
            file_path="/tmp/vid1_part1.mp4",
        )
        assert db.get_queued_count() == 0, "klip yang sudah diupload tidak boleh kembali ke antrean"
        assert db.get_next_queued_clip() is None

        db.close()
    finally:
        os.unlink(db_path)
