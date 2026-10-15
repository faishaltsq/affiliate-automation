"""state.py — Track which YouTube videos have been uploaded to avoid duplicates."""

import sqlite3
import datetime


class VideoStateDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_videos (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                channel TEXT NOT NULL,
                uploaded_at TEXT NOT NULL DEFAULT (datetime('now')),
                status TEXT NOT NULL DEFAULT 'uploaded'
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS run_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL DEFAULT (datetime('now')),
                videos_found INTEGER NOT NULL DEFAULT 0,
                videos_uploaded INTEGER NOT NULL DEFAULT 0,
                videos_skipped INTEGER NOT NULL DEFAULT 0,
                errors TEXT
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS clip_queue (
                clip_id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                part_num INTEGER NOT NULL,
                total_parts INTEGER NOT NULL,
                title TEXT NOT NULL,
                chapter_title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                uploaded_at TEXT
            )
        """)
        self._conn.commit()

    def enqueue_clip(
        self,
        clip_id: str,
        video_id: str,
        part_num: int,
        total_parts: int,
        title: str,
        chapter_title: str,
        file_path: str,
    ):
        """Add a generated multi-part clip to the scheduled upload queue."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO clip_queue
            (clip_id, video_id, part_num, total_parts, title, chapter_title, file_path, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
            """,
            (clip_id, video_id, part_num, total_parts, title, chapter_title, file_path),
        )
        self._conn.commit()

    def get_next_queued_clip(self) -> dict | None:
        """Fetch the next pending clip in order (first by video batch created_at, then part_num)."""
        row = self._conn.execute(
            """
            SELECT clip_id, video_id, part_num, total_parts, title, chapter_title, file_path, status, created_at
            FROM clip_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC, part_num ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def mark_clip_uploaded(self, clip_id: str):
        """Mark a queued clip as uploaded."""
        self._conn.execute(
            "UPDATE clip_queue SET status = 'uploaded', uploaded_at = datetime('now') WHERE clip_id = ?",
            (clip_id,),
        )
        self._conn.commit()

    def get_queued_count(self, video_id: str | None = None) -> int:
        """Return count of pending clips in queue."""
        if video_id:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM clip_queue WHERE status = 'pending' AND video_id = ?",
                (video_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM clip_queue WHERE status = 'pending'"
            ).fetchone()
        return row[0] if row else 0

    def is_video_processed(self, video_id: str) -> bool:
        """Check if any clips have already been generated for this video."""
        row = self._conn.execute(
            "SELECT 1 FROM clip_queue WHERE video_id = ? LIMIT 1", (video_id,)
        ).fetchone()
        return row is not None

    def is_uploaded(self, video_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM uploaded_videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        return row is not None

    def mark_uploaded(self, video_id: str, title: str, channel: str):
        self._conn.execute(
            "INSERT OR IGNORE INTO uploaded_videos (video_id, title, channel) VALUES (?, ?, ?)",
            (video_id, title, channel),
        )
        self._conn.commit()

    def log_run(self, found: int, uploaded: int, skipped: int, errors: str = ""):
        self._conn.execute(
            "INSERT INTO run_log (videos_found, videos_uploaded, videos_skipped, errors) VALUES (?, ?, ?, ?)",
            (found, uploaded, skipped, errors),
        )
        self._conn.commit()

    def get_stats(self, days: int = 7) -> dict:
        total = self._conn.execute("SELECT COUNT(*) FROM uploaded_videos").fetchone()[0]
        total_runs = self._conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0]
        last_run = self._conn.execute(
            "SELECT run_at, videos_uploaded FROM run_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_run_dict = dict(last_run) if last_run else None
        return {
            "total_uploaded": total,
            "total_runs": total_runs,
            "last_run": last_run_dict,
        }

    def get_recent_uploads(self, limit: int = 5) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT video_id, title, channel, uploaded_at, status FROM uploaded_videos ORDER BY uploaded_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self._conn.close()
