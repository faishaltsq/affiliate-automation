"""tests/v2/test_legacy_sync_fix.py — the R1 data-loss regression guard.

`src/organic/db.py::sync_with_threads_status` used to DELETE organic_posts rows
for every non-200 response. These tests pin the replacement behaviour through
the legacy entry point so a future refactor cannot silently reintroduce the bug.
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from src.core.storage import connect, migrate, transaction
from src.organic import db as legacy_db


@pytest.fixture()
def legacy_schema(tmp_path, monkeypatch):
    """A database shaped exactly like the pre-V2 production one."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE organic_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT, pillar TEXT, topic TEXT, content TEXT,
            image_url TEXT, threads_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            post_structure TEXT DEFAULT 'single',
            source_summary TEXT DEFAULT '',
            views INTEGER DEFAULT 0, likes INTEGER DEFAULT 0,
            replies INTEGER DEFAULT 0, metrics_updated_at TIMESTAMP
        )
    """)
    con.executemany(
        "INSERT INTO organic_posts (topic, content, threads_id, pillar) "
        "VALUES (?, ?, ?, ?)",
        [
            ("topik a", "konten a", "tid_a", "tech_gadget_ai"),
            ("topik b", "konten b", "tid_b", "market_finance_stocks"),
            ("topik c", "konten c", "tid_c", "breaking_disaster"),
        ],
    )
    con.commit()
    con.close()
    monkeypatch.setattr(legacy_db, "DB_PATH", path)
    return path


def _rows(path):
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, topic, content, threads_id, status, deleted_at, "
            "last_sync_status FROM organic_posts ORDER BY id"
        ).fetchall()]


class TestLegacySyncNoLongerDeletes:
    def test_all_rate_limited_keeps_every_row(self, legacy_schema):
        with patch(
            "src.core.post_sync.default_threads_prober",
            return_value=(429, "slow down"),
        ):
            result = legacy_db.sync_with_threads_status()
        rows = _rows(legacy_schema)
        assert len(rows) == 3
        assert result["rows_removed"] == 0
        assert result["deleted"] == 0
        assert all(r["status"] == "published" for r in rows)
        assert all(r["last_sync_status"] == "rate_limited" for r in rows)

    def test_all_server_errors_keep_every_row(self, legacy_schema):
        with patch(
            "src.core.post_sync.default_threads_prober",
            return_value=(500, "boom"),
        ):
            legacy_db.sync_with_threads_status()
        rows = _rows(legacy_schema)
        assert len(rows) == 3
        assert all(r["status"] == "published" for r in rows)
        assert all(r["content"].startswith("konten") for r in rows)

    def test_auth_failure_keeps_every_row(self, legacy_schema):
        with patch(
            "src.core.post_sync.default_threads_prober",
            return_value=(401, "token expired"),
        ):
            legacy_db.sync_with_threads_status()
        assert len(_rows(legacy_schema)) == 3

    def test_timeout_keeps_every_row(self, legacy_schema):
        def boom(tid):
            raise TimeoutError("read timed out")

        with patch("src.core.post_sync.default_threads_prober", side_effect=boom):
            legacy_db.sync_with_threads_status()
        rows = _rows(legacy_schema)
        assert len(rows) == 3
        assert all(r["last_sync_status"] == "transport_error" for r in rows)

    def test_404_tombstones_but_keeps_history(self, legacy_schema):
        with patch(
            "src.core.post_sync.default_threads_prober",
            return_value=(404, "not found"),
        ):
            result = legacy_db.sync_with_threads_status()
        rows = _rows(legacy_schema)
        assert len(rows) == 3, "a confirmed deletion must not erase the row"
        assert result["deleted"] == 3
        assert result["rows_removed"] == 0
        for r in rows:
            assert r["status"] == "deleted"
            assert r["deleted_at"] is not None
            assert "404" in r["last_sync_status"] or r["last_sync_status"] == "not_found"
            assert r["content"], "content history preserved"

    def test_mixed_outcomes_are_classified_independently(self, legacy_schema):
        codes = {
            "tid_a": (200, "ok"),
            "tid_b": (404, "gone"),
            "tid_c": (503, "unavailable"),
        }
        with patch(
            "src.core.post_sync.default_threads_prober",
            side_effect=lambda tid: codes[tid],
        ):
            result = legacy_db.sync_with_threads_status()
        rows = {r["threads_id"]: r for r in _rows(legacy_schema)}
        assert len(rows) == 3
        assert rows["tid_a"]["status"] == "published"
        assert rows["tid_b"]["status"] == "deleted"
        assert rows["tid_c"]["status"] == "published"
        # `alive` counts only the posts the API explicitly confirmed this
        # sweep; the 503 row keeps its published status but is not confirmed.
        assert result["alive"] == 1
        assert result["deleted"] == 1
        assert result["rows_removed"] == 0
        assert result["detail"]["server_error"] == 1

    def test_repeated_syncs_are_stable(self, legacy_schema):
        with patch(
            "src.core.post_sync.default_threads_prober",
            return_value=(200, "ok"),
        ):
            legacy_db.sync_with_threads_status()
            legacy_db.sync_with_threads_status()
        assert len(_rows(legacy_schema)) == 3

    def test_init_organic_db_applies_v2_columns(self, legacy_schema):
        legacy_db.init_organic_db()
        with connect(legacy_schema) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(organic_posts)")}
        for c in ("status", "deleted_at", "delete_reason", "last_sync_status"):
            assert c in cols, c

    def test_init_organic_db_preserves_rows(self, legacy_schema):
        legacy_db.init_organic_db()
        assert len(_rows(legacy_schema)) == 3
