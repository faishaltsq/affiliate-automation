"""tests/v2/test_no_live_db_writes.py — guard against tests polluting production.

An earlier iteration of the V2 command handlers accepted a `path` parameter but
never forwarded it to the audit and feedback stores. Every test run therefore
wrote its fixtures into the LIVE `data/posts.db` — 18 feedback events and 162
audit events of test data sitting beside real operator history. Cleaning that up
required a manual backup-and-delete against a database the legacy daemon was
actively writing to.

Two rules are pinned here:

1. **When a caller supplies `path`, every store honours it.** Nothing leaks to
   the live database. This is the regression guard.
2. **`path=None` means the live database** — that is correct production
   behaviour, so these tests never dispatch with `path=None`.

`organic_posts` is additionally checked for immutability, because content
history is the one thing that must never move.
"""
from __future__ import annotations

import pytest

from src.bot.v2_commands import handle_v2_command
from src.core import config as cfg
from src.core.llm import FakeLLMClient
from src.core.storage import connect, migrate


@pytest.fixture()
def v2_on(monkeypatch):
    cfg.reset_settings_cache()
    monkeypatch.setenv("V2_TELEGRAM_COMMANDS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "isolated.db"
    migrate(path)
    return path


OPERATOR = {"id": 111, "username": "op"}
INTRUDER = {"id": 999, "username": "bad"}
CHAT = {"id": "-1003757350269", "type": "supergroup"}


def _msg(frm, text):
    return {
        "message_id": 1, "from": frm, "chat": CHAT,
        "message_thread_id": 9, "text": text,
    }


def _dispatch(msg, text, path, client=None):
    return handle_v2_command(
        msg, text, 1, 9, sender=lambda t, m: None, path=path,
        client=client or FakeLLMClient(),
    )


def _live_count(table: str) -> int:
    """Row count in the REAL configured database."""
    with connect() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _isolated_count(table: str, path) -> int:
    with connect(path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


class TestNothingLeaksToLiveDatabase:
    def test_audit_lands_in_the_supplied_database(self, v2_on, db):
        before_live = _live_count("audit_events")
        _dispatch(_msg(OPERATOR, "/status"), "/status", db)
        assert _isolated_count("audit_events", db) == 1
        assert _live_count("audit_events") == before_live

    def test_denied_command_audit_lands_in_the_supplied_database(self, v2_on, db):
        before_live = _live_count("audit_events")
        _dispatch(_msg(INTRUDER, "/pause"), "/pause", db)
        assert _isolated_count("audit_events", db) == 1
        assert _live_count("audit_events") == before_live

    def test_feedback_lands_in_the_supplied_database(self, v2_on, db):
        before_live = _live_count("feedback_events")
        _dispatch(_msg(OPERATOR, "/reject 1 terlalu panjang"),
                  "/reject 1 terlalu panjang", db)
        assert _isolated_count("feedback_events", db) == 1
        assert _live_count("feedback_events") == before_live

    def test_revise_feedback_lands_in_the_supplied_database(self, v2_on, db):
        before_live = _live_count("feedback_events")
        _dispatch(_msg(OPERATOR, "/revise 1 tambah detail"),
                  "/revise 1 tambah detail", db)
        assert _isolated_count("feedback_events", db) == 1
        assert _live_count("feedback_events") == before_live

    def test_pause_state_lands_in_the_supplied_database(self, v2_on, db):
        from src.orchestration.scheduler import is_paused

        before_live = is_paused(path=None)
        _dispatch(_msg(OPERATOR, "/pause"), "/pause", db)
        assert is_paused(path=db) is True
        assert is_paused(path=None) == before_live

    def test_approve_updates_the_supplied_database(self, v2_on, db):
        import json

        from src.core.storage import transaction

        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_drafts (content, status) "
                    "VALUES ('uji', 'EVALUATED')"
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO content_evaluations "
                    "(draft_id, scores_json, vetoes_json, verdict, total_score) "
                    "VALUES (?, '{}', '[]', 'NEEDS_REVIEW', 78)",
                    (did,),
                )
        before_live = _live_count("content_drafts")
        _dispatch(_msg(OPERATOR, f"/approve {did}"), f"/approve {did}", db)
        with connect(db) as conn:
            status = conn.execute(
                "SELECT status FROM content_drafts WHERE id = ?", (did,)
            ).fetchone()["status"]
        assert status == "APPROVED"
        assert _live_count("content_drafts") == before_live

    def test_every_command_leaves_the_live_db_alone(self, v2_on, db):
        """The full command surface, dispatched against an isolated database."""
        counts_before = {
            t: _live_count(t) for t in
            ("audit_events", "feedback_events", "content_drafts",
             "content_evaluations", "publication_jobs", "organic_posts")
        }
        commands = [
            "/persona", "/plan", "/ideas", "/draft", "/schedule",
            "/analytics", "/products", "/status", "/help",
            "/approve 1", "/revise 1 alasan", "/reject 1 alasan",
            "/pause", "/resume", "/autopost on", "/autopost off", "/rollback",
        ]
        for cmd in commands:
            _dispatch(_msg(OPERATOR, cmd), cmd, db)
            _dispatch(_msg(INTRUDER, cmd), cmd, db)
        for table, before in counts_before.items():
            assert _live_count(table) == before, (
                f"{table} changed in the live database during a test dispatch"
            )


class TestContentHistoryIsImmutable:
    def test_organic_posts_never_change(self, v2_on, db):
        before = _live_count("organic_posts")
        for cmd in ("/approve 1", "/reject 1 x", "/pause", "/rollback",
                    "/autopost on", "/revise 1 y", "/draft"):
            _dispatch(_msg(OPERATOR, cmd), cmd, db)
        assert _live_count("organic_posts") == before

    def test_live_database_stays_healthy(self, v2_on, db):
        _dispatch(_msg(OPERATOR, "/status"), "/status", db)
        with connect() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
