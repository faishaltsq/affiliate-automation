"""tests/v2/test_core_infra.py — Phase 1 core infrastructure tests.

Covers: typed config defaults (all live side effects OFF), secret redaction,
UTC/WIB clock, Telegram operator ACL, SQLite storage pragmas, additive
idempotent migrations, audit trail, and the post-sync tombstone contract.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone, timedelta

import pytest

from src.core import config as cfg
from src.core.clock import (
    utc_now, wib_now, to_wib, to_utc, sqlite_utc, parse_sqlite_utc,
    wib_display, wib_date_key, is_quiet_hour, seconds_until_quiet_end,
)
from src.core.security import (
    Actor, actor_from_message, actor_from_callback, is_operator,
    require_operator, AuthorizationDenied, is_known_chat,
    command_requires_operator, MUTATION_COMMANDS,
)
from src.core.storage import (
    connect, transaction, table_exists, column_names, user_version,
    migrate, migration_status, record_audit, MIGRATIONS,
)
from src.core import post_sync
from src.core.events import emit, recent, for_object


# ── Fixtures ────────────────────────────────────────────────────────────────
@pytest.fixture()
def db(tmp_path):
    """A fresh V2 database file with migrations applied."""
    path = tmp_path / "test.db"
    applied = migrate(path)
    assert applied, "migrations must apply to an empty database"
    return path


@pytest.fixture()
def settings(monkeypatch):
    """Settings with a deterministic, fully-off flag set."""
    cfg.reset_settings_cache()
    for key in (
        "AUTOPUBLISH_ENABLED", "AFFILIATE_AUTO_ENABLED", "DRY_RUN",
        "SHADOW_MODE", "V2_SCHEDULER_ENABLED", "V2_TELEGRAM_COMMANDS_ENABLED",
        "TELEGRAM_OPERATOR_IDS",
    ):
        monkeypatch.delenv(key, raising=False)
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


# ── Feature flags: everything live is OFF by default ────────────────────────
class TestFeatureFlags:
    def test_all_side_effects_default_off(self, settings):
        f = settings.flags
        assert f.autopublish_enabled is False
        assert f.affiliate_auto_enabled is False
        assert f.dry_run is True
        assert f.shadow_mode is True
        assert f.v2_scheduler_enabled is False
        assert f.v2_telegram_commands_enabled is False

    def test_live_publishing_requires_both_flags(self, settings):
        assert settings.flags.live_publishing_allowed is False
        assert settings.flags.live_affiliate_allowed is False

    def test_enabling_autopublish_alone_still_blocked_by_dry_run(self, monkeypatch):
        monkeypatch.setenv("AUTOPUBLISH_ENABLED", "true")
        f = cfg.load_feature_flags()
        assert f.autopublish_enabled is True
        assert f.dry_run is True
        assert f.live_publishing_allowed is False

    def test_both_flags_on_allows_live(self, monkeypatch):
        monkeypatch.setenv("AUTOPUBLISH_ENABLED", "1")
        monkeypatch.setenv("DRY_RUN", "false")
        f = cfg.load_feature_flags()
        assert f.live_publishing_allowed is True

    def test_describe_is_secret_free(self, settings):
        text = settings.flags.describe()
        assert "autopublish=" in text
        assert "token" not in text.lower()

    def test_safe_summary_hides_secrets(self, settings):
        summary = settings.safe_summary()
        assert summary["flags"]
        assert "api_key" not in str(summary).lower()
        assert summary["persona_version"] == "teman_tongkrongan_v1"


# ── Secret redaction ────────────────────────────────────────────────────────
class TestRedaction:
    def test_strips_token_query_params(self):
        url = "https://graph.threads.net/v1.0/123?access_token=SECRETVALUE&fields=id"
        out = cfg.redact_secrets(url)
        assert "SECRETVALUE" not in out
        assert "<redacted>" in out
        assert "fields=id" in out

    def test_strips_bearer_header(self):
        assert "abc123" not in cfg.redact_secrets("Authorization: Bearer abc123")

    def test_strips_known_env_secret_values(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999888777666:AAExtraSecretValue")
        out = cfg.redact_secrets("posting to bot 999888777666:AAExtraSecretValue now")
        assert "AAExtraSecretValue" not in out

    def test_empty_string_safe(self):
        assert cfg.redact_secrets("") == ""


# ── Clock ───────────────────────────────────────────────────────────────────
class TestClock:
    def test_utc_now_is_aware(self):
        assert utc_now().tzinfo is not None

    def test_wib_is_seven_hours_ahead(self):
        u = datetime(2026, 10, 28, 11, 0, 0, tzinfo=timezone.utc)
        assert to_wib(u).hour == 18
        assert to_wib(u).day == 28

    def test_naive_input_treated_as_utc(self):
        naive = datetime(2026, 10, 28, 11, 0, 0)
        assert to_utc(naive).hour == 11
        assert to_wib(naive).hour == 18

    def test_sqlite_format_has_no_timezone_suffix(self):
        out = sqlite_utc(datetime(2026, 10, 28, 11, 37, 18, tzinfo=timezone.utc))
        assert out == "2026-10-28 11:37:18"

    def test_parse_sqlite_roundtrip(self):
        dt = parse_sqlite_utc("2026-10-28 11:37:18")
        assert dt is not None
        assert dt.tzinfo is not None
        assert dt.hour == 11

    def test_parse_handles_iso_and_z(self):
        assert parse_sqlite_utc("2026-10-28T11:37:18+00:00") is not None
        assert parse_sqlite_utc("2026-10-28T11:37:18Z") is not None
        assert parse_sqlite_utc("2026-10-28") is not None
        assert parse_sqlite_utc("") is None
        assert parse_sqlite_utc(None) is None
        assert parse_sqlite_utc("garbage") is None

    def test_wib_display_is_human_readable(self):
        text = wib_display(datetime(2026, 10, 28, 11, 0, 0, tzinfo=timezone.utc))
        assert "18:00" in text
        assert "WIB" in text

    def test_date_key_uses_wib_calendar_day(self):
        # 22:00 UTC is already the next day in WIB.
        assert wib_date_key(datetime(2026, 10, 28, 22, 0, 0, tzinfo=timezone.utc)) == "2026-10-29"

    def test_quiet_hours_wrap_midnight(self):
        assert is_quiet_hour(23, 23, 6) is True
        assert is_quiet_hour(2, 23, 6) is True
        assert is_quiet_hour(5, 23, 6) is True
        assert is_quiet_hour(6, 23, 6) is False
        assert is_quiet_hour(12, 23, 6) is False

    def test_quiet_hours_same_day_window(self):
        assert is_quiet_hour(2, 1, 5) is True
        assert is_quiet_hour(6, 1, 5) is False

    def test_quiet_hours_disabled_when_start_equals_end(self):
        assert is_quiet_hour(3, 0, 0) is False

    def test_seconds_until_quiet_end_is_positive(self):
        assert seconds_until_quiet_end(6) > 0


# ── Telegram operator ACL ───────────────────────────────────────────────────
def _actor(uid=None, username="rando", chat="-1003757350269", private=False):
    return Actor(user_id=uid, username=username, chat_id=chat,
                 is_private=private, display=f"{username}({uid})")


class TestOperatorACL:
    def test_empty_allowlist_denies_everyone(self, settings):
        assert settings.telegram.operator_ids == ()
        assert is_operator(_actor(uid=12345)) is False

    def test_allowlisted_id_is_authorized(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111, 222")
        cfg.reset_settings_cache()
        assert is_operator(_actor(uid=222)) is True
        assert is_operator(_actor(uid=333)) is False

    def test_missing_user_id_denied(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
        cfg.reset_settings_cache()
        assert is_operator(_actor(uid=None)) is False

    def test_require_operator_raises_with_actor_and_action(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
        cfg.reset_settings_cache()
        with pytest.raises(AuthorizationDenied) as ei:
            require_operator(_actor(uid=999, username="intruder"), "/approve")
        assert "intruder" in str(ei.value)
        assert "/approve" in str(ei.value)

    def test_require_operator_passes_for_operator(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
        cfg.reset_settings_cache()
        require_operator(_actor(uid=111), "/approve")  # must not raise

    def test_actor_from_message_extracts_id(self):
        a = actor_from_message({
            "from": {"id": 555, "username": "op"},
            "chat": {"id": "-100", "type": "supergroup"},
        })
        assert a.user_id == 555
        assert a.username == "op"
        assert a.is_private is False

    def test_actor_from_callback_extracts_id(self):
        a = actor_from_callback({
            "from": {"id": 777},
            "message": {"chat": {"id": "-100", "type": "supergroup"}},
        })
        assert a.user_id == 777

    def test_known_chat_accepts_configured_group_and_dm(self, settings):
        assert is_known_chat(_actor(chat="-1003757350269")) is True
        assert is_known_chat(_actor(chat="999", private=True)) is True
        assert is_known_chat(_actor(chat="evil-group")) is False

    def test_mutation_commands_require_operator(self):
        for cmd in ("/approve 12", "/reject 12 alasan", "/pause", "/autopost on",
                    "/rollback", "/clearqueue", "/publish 5"):
            assert command_requires_operator(cmd) is True, cmd

    def test_readonly_commands_do_not_require_operator(self):
        for cmd in ("/status", "/plan", "/ideas", "/analytics 7d", "/products",
                    "/persona", "/schedule", "/help"):
            assert command_requires_operator(cmd) is False, cmd

    def test_inline_callbacks_are_mutations(self):
        for data in ("pub:abc123", "cancel:abc123", "pub_aff:xyz",
                     "cancel_aff:xyz", "reroll_aff:xyz", "set_persona:julid",
                     "approve:1", "reject:1", "revise:1"):
            assert command_requires_operator(data) is True, data

    def test_unknown_command_fails_closed(self):
        assert command_requires_operator("/totally_unknown_thing") is True

    def test_empty_text_is_not_a_mutation(self):
        assert command_requires_operator("") is False
        assert command_requires_operator("   ") is False

    def test_mutation_set_is_nonempty(self):
        assert len(MUTATION_COMMANDS) >= 10


# ── Storage & migrations ────────────────────────────────────────────────────
class TestMigrations:
    def test_migrate_creates_v2_tables(self, db):
        with connect(db) as conn:
            for table in ("persona_versions", "audience_signals", "content_ideas",
                          "content_drafts", "content_evaluations",
                          "publication_jobs", "performance_snapshots",
                          "feedback_events", "products", "promotions",
                          "experiment_assignments", "audit_events",
                          "schema_migrations", "telegram_callback_tokens",
                          "operator_sessions", "calendar_slots"):
                assert table_exists(conn, table), table

    def test_organic_posts_gains_tombstone_columns(self, db):
        with connect(db) as conn:
            cols = column_names(conn, "organic_posts")
            for c in ("status", "deleted_at", "delete_reason",
                      "last_sync_status", "last_sync_at", "persona_version",
                      "pillar_v2"):
                assert c in cols, c

    def test_migration_is_idempotent(self, tmp_path):
        path = tmp_path / "test.db"
        first = migrate(path)
        second = migrate(path)
        assert first
        assert second == []
        with connect(path) as conn:
            assert user_version(conn) == MIGRATIONS[-1].version

    def test_migration_applied_twice_does_not_corrupt(self, db):
        migrate(db)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO organic_posts (topic, content, threads_id) "
                    "VALUES ('t', 'c', '123')"
                )
        migrate(db)
        with connect(db) as conn:
            row = conn.execute("SELECT topic, content FROM organic_posts").fetchone()
            assert row["topic"] == "t"
            assert row["content"] == "c"

    def test_existing_rows_get_default_status(self, tmp_path):
        """A pre-V2 database must migrate without losing its 131 posts."""
        path = tmp_path / "legacy.db"
        legacy = sqlite3.connect(path)
        legacy.execute("""
            CREATE TABLE organic_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT, pillar TEXT, topic TEXT, content TEXT,
                image_url TEXT, threads_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        legacy.executemany(
            "INSERT INTO organic_posts (topic, threads_id) VALUES (?, ?)",
            [(f"topic {i}", f"tid{i}") for i in range(5)],
        )
        legacy.commit()
        legacy.close()

        applied = migrate(path)
        assert len(applied) >= 1
        with connect(path) as conn:
            rows = conn.execute(
                "SELECT id, status FROM organic_posts ORDER BY id"
            ).fetchall()
            assert len(rows) == 5
            assert all(r["status"] == "published" for r in rows)

    def test_migration_status_reports_pending(self, tmp_path):
        path = tmp_path / "fresh.db"
        st = migration_status(path)
        assert st["current_version"] == 0
        assert st["pending"]
        migrate(path)
        st2 = migration_status(path)
        assert st2["current_version"] == st2["latest_version"]
        assert st2["pending"] == []

    def test_wal_and_busy_timeout_enabled(self, db):
        with connect(db) as conn:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"

    def test_foreign_keys_on(self, db):
        with connect(db) as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_transaction_rolls_back(self, db):
        with connect(db) as conn:
            with pytest.raises(RuntimeError):
                with transaction(conn):
                    conn.execute(
                        "INSERT INTO audience_signals (category, problem) "
                        "VALUES ('x','y')"
                    )
                    raise RuntimeError("boom")
            n = conn.execute("SELECT COUNT(*) FROM audience_signals").fetchone()[0]
            assert n == 0


# ── Audit trail ─────────────────────────────────────────────────────────────
class TestAuditTrail:
    def test_record_audit_writes_row(self, db):
        with connect(db) as conn:
            with transaction(conn):
                rid = record_audit(
                    conn, actor_type="operator", actor_id="111",
                    action="approve", object_type="draft", object_id="7",
                    before={"status": "EVALUATED"},
                    after={"status": "APPROVED"},
                    reason="suara pas",
                )
            row = conn.execute(
                "SELECT * FROM audit_events WHERE id = ?", (rid,)
            ).fetchone()
        assert row["action"] == "approve"
        assert row["actor_id"] == "111"
        assert "EVALUATED" in row["before_json"]
        assert "APPROVED" in row["after_json"]

    def test_emit_opens_own_transaction(self, db):
        rid = emit("pause", object_type="scheduler", actor_type="operator",
                   actor_id="111", path=db)
        assert rid > 0
        events = recent(limit=5, path=db)
        assert events[0]["action"] == "pause"

    def test_for_object_returns_history_in_order(self, db):
        emit("create", object_type="draft", object_id="9", path=db)
        emit("evaluate", object_type="draft", object_id="9", path=db)
        emit("approve", object_type="draft", object_id="9", path=db)
        hist = for_object("draft", "9", path=db)
        assert [h["action"] for h in hist] == ["create", "evaluate", "approve"]

    def test_audit_redacts_secrets_in_reason(self, db, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "12345678:SECRETTOKENVALUE")
        emit("note", reason="token 12345678:SECRETTOKENVALUE leaked", path=db)
        events = recent(limit=1, path=db)
        assert "SECRETTOKENVALUE" not in events[0]["reason"]


# ── The R1 fix: no deletion on transient failure ────────────────────────────
class TestPostSyncNeverDeletes:
    def _seed(self, db, n=3):
        with connect(db) as conn:
            with transaction(conn):
                for i in range(n):
                    conn.execute(
                        "INSERT INTO organic_posts (topic, content, threads_id) "
                        "VALUES (?, ?, ?)",
                        (f"topic {i}", f"konten {i}", f"tid{i}"),
                    )

    def test_200_marks_published(self, db):
        self._seed(db, 1)
        with connect(db) as conn:
            out = post_sync.reconcile_post(
                conn, 1, "tid0", lambda tid: (200, "ok")
            )
        assert out.result == post_sync.OK
        assert out.tombstoned is False
        with connect(db) as conn:
            row = conn.execute("SELECT status FROM organic_posts WHERE id=1").fetchone()
        assert row["status"] == "published"

    def test_404_tombstones_but_keeps_row(self, db):
        self._seed(db, 1)
        with connect(db) as conn:
            out = post_sync.reconcile_post(
                conn, 1, "tid0", lambda tid: (404, "not found")
            )
        assert out.result == post_sync.NOT_FOUND
        assert out.tombstoned is True
        with connect(db) as conn:
            row = conn.execute(
                "SELECT status, deleted_at, delete_reason, content "
                "FROM organic_posts WHERE id=1"
            ).fetchone()
        assert row["status"] == "deleted"
        assert row["deleted_at"] is not None
        assert "404" in row["delete_reason"]
        assert row["content"] == "konten 0"  # history preserved

    @pytest.mark.parametrize("code,expected", [
        (429, post_sync.RATE_LIMITED),
        (500, post_sync.SERVER_ERROR),
        (502, post_sync.SERVER_ERROR),
        (503, post_sync.SERVER_ERROR),
        (401, post_sync.AUTH_ERROR),
        (403, post_sync.AUTH_ERROR),
        (400, post_sync.PERMISSION_ERROR),
    ])
    def test_transient_codes_never_delete(self, db, code, expected):
        self._seed(db, 1)
        with connect(db) as conn:
            out = post_sync.reconcile_post(
                conn, 1, "tid0", lambda tid, c=code: (c, "err")
            )
        assert out.result == expected
        assert out.tombstoned is False
        with connect(db) as conn:
            rows = conn.execute("SELECT id, status, content FROM organic_posts").fetchall()
        assert len(rows) == 1
        assert rows[0]["status"] == "published"
        assert rows[0]["content"] == "konten 0"

    def test_timeout_never_deletes(self, db):
        self._seed(db, 1)

        def boom(tid):
            raise TimeoutError("read timed out")

        with connect(db) as conn:
            out = post_sync.reconcile_post(conn, 1, "tid0", boom)
        assert out.result == post_sync.TRANSPORT_ERROR
        with connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM organic_posts").fetchone()[0] == 1

    def test_connection_error_never_deletes(self, db):
        self._seed(db, 1)

        def boom(tid):
            raise ConnectionError("connection reset")

        with connect(db) as conn:
            out = post_sync.reconcile_post(conn, 1, "tid0", boom)
        assert out.result == post_sync.TRANSPORT_ERROR
        with connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM organic_posts").fetchone()[0] == 1

    def test_row_count_unchanged_after_full_transient_sweep(self, db):
        self._seed(db, 5)
        before = post_sync.row_count(db)
        summary = post_sync.reconcile_all(
            lambda tid: (503, "service unavailable"), path=db
        )
        after = post_sync.row_count(db)
        assert before == after == 5
        assert summary["server_error"] == 5
        assert summary["tombstoned"] == 0

    def test_reconcile_all_counts_outcomes(self, db):
        self._seed(db, 4)
        codes = {"tid0": (200, "ok"), "tid1": (404, "gone"),
                 "tid2": (429, "slow down"), "tid3": (500, "oops")}
        summary = post_sync.reconcile_all(
            lambda tid: codes[tid], path=db
        )
        assert summary["checked"] == 4
        assert summary["ok"] == 1
        assert summary["tombstoned"] == 1
        assert summary["rate_limited"] == 1
        assert summary["server_error"] == 1
        assert post_sync.row_count(db) == 4  # nothing deleted

    def test_tombstoned_rows_are_skipped_next_sweep(self, db):
        self._seed(db, 1)
        with connect(db) as conn:
            post_sync.reconcile_post(conn, 1, "tid0", lambda tid: (404, "gone"))
        summary = post_sync.reconcile_all(lambda tid: (200, "ok"), path=db)
        assert summary["checked"] == 0

    def test_status_counts(self, db):
        self._seed(db, 3)
        with connect(db) as conn:
            post_sync.reconcile_post(conn, 1, "tid0", lambda tid: (404, "gone"))
        counts = post_sync.status_counts(db)
        assert counts["deleted"] == 1
        assert counts["published"] == 2

    def test_classify_status_edge_cases(self):
        assert post_sync.classify_status(200) == post_sync.OK
        assert post_sync.classify_status(404) == post_sync.NOT_FOUND
        assert post_sync.classify_status(429) == post_sync.RATE_LIMITED
        assert post_sync.classify_status(None, "Read timed out") == post_sync.TRANSPORT_ERROR
        assert post_sync.classify_status(None, "Connection reset by peer") == post_sync.TRANSPORT_ERROR
        assert post_sync.classify_status(None, "weird") == post_sync.UNKNOWN
        # Any 4xx is treated as a permission/validation problem, never as
        # proof the post is gone — only 404 means that.
        assert post_sync.classify_status(418) == post_sync.PERMISSION_ERROR
        assert post_sync.classify_status(451) == post_sync.PERMISSION_ERROR
        assert post_sync.classify_status(301) == post_sync.UNKNOWN

    def test_default_prober_without_credentials(self, settings, monkeypatch):
        """The prober must refuse to touch the network when unconfigured."""
        monkeypatch.setattr(
            "src.core.config.get_settings",
            lambda: type("S", (), {"threads": type("T", (), {
                "configured": False, "base_url": "x", "access_token": ""})()})(),
        )
        code, detail = post_sync.default_threads_prober("tid")
        assert code is None
        assert "not configured" in detail


# ── LLM abstraction ─────────────────────────────────────────────────────────
class TestLLM:
    def test_fake_client_returns_scripted_reply(self):
        from src.core.llm import FakeLLMClient, complete_json

        fake = FakeLLMClient(script={"tulis": '{"content": "halo"}'})
        data = complete_json(fake, [{"role": "user", "content": "tulis sesuatu"}])
        assert data["content"] == "halo"

    def test_complete_json_rejects_non_json(self):
        from src.core.llm import FakeLLMClient, complete_json, LLMError

        fake = FakeLLMClient(default="this is not json at all")
        with pytest.raises(LLMError):
            complete_json(fake, [{"role": "user", "content": "x"}])

    def test_complete_json_rejects_json_array(self):
        from src.core.llm import FakeLLMClient, complete_json, LLMError

        fake = FakeLLMClient(default='["a", "b"]')
        with pytest.raises(LLMError):
            complete_json(fake, [{"role": "user", "content": "x"}])

    def test_complete_json_strips_markdown_fence(self):
        from src.core.llm import FakeLLMClient, complete_json

        fake = FakeLLMClient(default='```json\n{"content": "ok"}\n```')
        data = complete_json(fake, [{"role": "user", "content": "x"}])
        assert data["content"] == "ok"

    def test_fake_client_can_fail_specific_calls(self):
        from src.core.llm import FakeLLMClient, LLMError

        fake = FakeLLMClient(replies=['{"a":1}', '{"b":2}'], raise_on={0})
        with pytest.raises(LLMError):
            fake.complete([{"role": "user", "content": "x"}])
        out = fake.complete([{"role": "user", "content": "x"}])
        assert out.text == '{"a":1}'

    def test_fake_client_records_calls(self):
        from src.core.llm import FakeLLMClient

        fake = FakeLLMClient()
        fake.complete([{"role": "user", "content": "hello"}], temperature=0.9)
        assert len(fake.calls) == 1
        assert fake.calls[0]["temperature"] == 0.9

    def test_http_client_raises_when_all_models_fail(self, monkeypatch):
        from src.core.llm import HttpLLMClient, LLMError

        class Resp:
            status_code = 500
            text = "boom"

            def raise_for_status(self):
                raise RuntimeError("500")

        import requests

        monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
        client = HttpLLMClient(cfg.LLMConfig(
            model="m1", fallback_models=("m2",), api_key="k",
        ), sleeper=lambda s: None)
        with pytest.raises(LLMError) as ei:
            client.complete([{"role": "user", "content": "x"}])
        assert "all LLM providers failed" in str(ei.value)

    def test_http_client_redacts_key_in_error(self, monkeypatch):
        from src.core.llm import HttpLLMClient, LLMError

        class Resp:
            status_code = 500
            text = "boom"

            def raise_for_status(self):
                raise RuntimeError("500")

        import requests

        monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
        client = HttpLLMClient(cfg.LLMConfig(
            model="m1", fallback_models=(), api_key="SUPERSECRETKEY",
        ), sleeper=lambda s: None)
        with pytest.raises(LLMError) as ei:
            client.complete([{"role": "user", "content": "x"}])
        assert "SUPERSECRETKEY" not in str(ei.value)
