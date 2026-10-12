"""tests/tiktok/test_state.py"""

import tempfile
import os
from src.tiktok.state import VideoStateDB


def test_is_uploaded_returns_false_for_new_video():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        db = VideoStateDB(db_path)
        assert not db.is_uploaded('test123')
        db.mark_uploaded('test123', 'Test Video', 'Test Channel')
        assert db.is_uploaded('test123')
        db.close()
    finally:
        os.unlink(db_path)


def test_mark_uploaded_ignores_duplicate():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        db = VideoStateDB(db_path)
        db.mark_uploaded('dup1', 'Dup Video', 'Chan')
        db.mark_uploaded('dup1', 'Dup Video', 'Chan')
        assert db.is_uploaded('dup1')
        db.close()
    finally:
        os.unlink(db_path)


def test_log_run_and_get_stats():
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name
    try:
        db = VideoStateDB(db_path)
        db.mark_uploaded('v1', 'V1', 'C1')
        db.mark_uploaded('v2', 'V2', 'C2')
        db.log_run(3, 2, 1)
        stats = db.get_stats()
        assert stats['total_uploaded'] == 2
        assert stats['last_run'] is not None
        assert stats['last_run']['videos_uploaded'] == 2
        db.close()
    finally:
        os.unlink(db_path)
