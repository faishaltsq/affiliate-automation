"""tests/tiktok/test_scheduler_reminder.py"""

from unittest.mock import patch

import src.tiktok.scheduler as sched


def test_reminder_mode_does_not_upload():
    with patch.object(sched, "_current_mode", "reminder"), \
         patch.object(sched, "_notify_telegram") as notify, \
         patch("src.tiktok.pipeline.run_pipeline") as run_pipeline, \
         patch.object(sched, "_get_pending_clip_count", return_value=4):
        sched._run_scheduled_pipeline(slot_name="12:00")

    assert notify.called
    assert not run_pipeline.called, "reminder mode must not upload"


def test_fyp_mode_still_uploads():
    with patch.object(sched, "_current_mode", "fyp"), \
         patch.object(sched, "_notify_telegram"), \
         patch("src.tiktok.pipeline.run_pipeline",
               return_value={"success": True, "videos": []}) as run_pipeline, \
         patch.object(sched, "_get_pending_clip_count", return_value=0):
        sched._run_scheduled_pipeline(slot_name="12:00")

    assert run_pipeline.called
