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
        self._conn.commit()

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
