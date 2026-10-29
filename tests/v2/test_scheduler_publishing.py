"""tests/v2/test_scheduler_publishing.py — Phase 4 durable scheduler & publishing.

Acceptance criteria covered:
  #9  post history remains after confirmed deletion; 429/5xx/401/403/timeout
      never trigger deletion (also in test_legacy_sync_fix.py)
  #11 timeout after publication does not trigger duplicate retry
  #12 scheduler respects calendar slots, quiet hours, day cap, pause/resume,
      and cross-restart behaviour
  #13 unauthorized Telegram sender/callback cannot publish/approve/reject
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.adapters.telegram import (
    CALLBACK_TTL_SECONDS, CallbackToken, DryRunTelegramAdapter,
    LiveTelegramAdapter, consume_callback_token, format_schedule_for_operator,
    get_telegram_adapter, issue_callback_token, purge_expired_tokens,
)
from src.adapters.threads import (
    DryRunThreadsAdapter, LiveThreadsAdapter, get_threads_adapter,
)
from src.core.clock import to_utc, utc_now, wib_date_key, WIB
from src.core.security import Actor, AuthorizationDenied, is_operator
from src.core.storage import connect, migrate, transaction
from src.orchestration import publisher as pub
from src.orchestration import recovery as rec
from src.orchestration import scheduler as sched


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


@pytest.fixture()
def settings(monkeypatch):
    from src.core import config as cfg

    cfg.reset_settings_cache()
    for key in ("AUTOPUBLISH_ENABLED", "AFFILIATE_AUTO_ENABLED", "DRY_RUN",
                "SHADOW_MODE", "V2_SCHEDULER_ENABLED", "TELEGRAM_OPERATOR_IDS",
                "V2_QUIET_START_HOUR", "V2_QUIET_END_HOUR",
                "V2_MAIN_POSTS_PER_DAY", "V2_MAX_EXTRA_POSTS_PER_WEEK"):
        monkeypatch.delenv(key, raising=False)
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


def _wib(hour: int, minute: int = 0) -> datetime:
    """A WIB wall-clock time as an aware UTC datetime."""
    local = datetime.now(WIB).replace(hour=hour, minute=minute, second=0,
                                      microsecond=0)
    return to_utc(local)


def _enqueue(db, draft_id: int = 1, content: str = "konten uji") -> tuple:
    return pub.enqueue(draft_id, content, path=db)


# ── Publication state machine ───────────────────────────────────────────────
class TestPublicationStateMachine:
    def test_enqueue_creates_queued_job(self, db):
        job_id, op_id, created = _enqueue(db)
        assert created is True
        job = pub.get_job(job_id, path=db)
        assert job.state == pub.QUEUED
        assert job.attempts == 0
        assert job.operation_id == op_id

    def test_operation_id_is_stable(self, db):
        a = pub.make_operation_id(7, "teks yang sama")
        b = pub.make_operation_id(7, "teks yang sama")
        assert a == b
        assert pub.make_operation_id(8, "teks yang sama") != a
        assert pub.make_operation_id(7, "teks lain") != a

    def test_duplicate_enqueue_returns_same_job(self, db):
        id1, op1, created1 = _enqueue(db, draft_id=5, content="abc")
        id2, op2, created2 = _enqueue(db, draft_id=5, content="abc")
        assert created1 is True
        assert created2 is False
        assert id1 == id2
        assert op1 == op2

    def test_claim_is_atomic(self, db):
        _enqueue(db)
        job = pub.claim_next_job(path=db)
        assert job is not None
        assert job.state == pub.PUBLISHING
        assert job.attempts == 1
        # Second claim finds nothing left.
        assert pub.claim_next_job(path=db) is None

    def test_claim_sets_a_lease(self, db):
        _enqueue(db)
        job = pub.claim_next_job(path=db)
        assert job.lease_expires_at is not None
        assert job.claimed_at is not None

    def test_publish_success_marks_published(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id, pub.FakeThreadsPublisher([{"post_id": "real_123"}]),
            content="konten uji", path=db,
        )
        assert out.succeeded is True
        assert out.published_id == "real_123"
        assert pub.get_job(job_id, path=db).state == pub.PUBLISHED

    def test_publish_is_idempotent_per_operation(self, db):
        """The core guarantee: the same operation never publishes twice."""
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        fake = pub.FakeThreadsPublisher([{"post_id": "real_123"}])
        first = pub.publish_job(job_id, fake, content="konten uji", path=db)
        second = pub.publish_job(job_id, fake, content="konten uji", path=db)
        assert first.succeeded and second.succeeded
        assert first.published_id == second.published_id
        assert len(fake.submits) == 1, "publisher was called twice!"

    def test_clear_error_marks_failed(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id,
            pub.FakeThreadsPublisher([ConnectionError("refused")]),
            content="konten uji", path=db,
        )
        assert out.state == pub.FAILED
        job = pub.get_job(job_id, path=db)
        assert job.state == pub.FAILED
        assert "ConnectionError" in job.last_error

    def test_timeout_marks_unknown_outcome_not_failed(self, db):
        """Acceptance #11: a timeout must not become a retry."""
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id,
            pub.FakeThreadsPublisher([TimeoutError("read timed out")]),
            content="konten uji", path=db,
        )
        assert out.state == pub.UNKNOWN_OUTCOME
        assert pub.get_job(job_id, path=db).state == pub.UNKNOWN_OUTCOME

    def test_timeout_does_not_republish(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        fake = pub.FakeThreadsPublisher([TimeoutError("timed out")])
        pub.publish_job(job_id, fake, content="konten uji", path=db)
        # A second attempt on the same job must not call submit again.
        out2 = pub.publish_job(job_id, fake, content="konten uji", path=db)
        assert out2.state == pub.UNKNOWN_OUTCOME
        assert len(fake.submits) == 1

    def test_no_post_id_returned_is_failed(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id, pub.FakeThreadsPublisher([{"unexpected": "shape"}]),
            content="konten uji", path=db,
        )
        assert out.state == pub.FAILED
        assert "no post id" in out.error

    def test_publishing_unclaimed_job_is_refused(self, db):
        job_id, _, _ = _enqueue(db)
        out = pub.publish_job(
            job_id, pub.FakeThreadsPublisher(), content="konten uji", path=db,
        )
        assert out.state == pub.QUEUED
        assert "not claimable" in out.error

    def test_publish_missing_job_is_failed(self, db):
        out = pub.publish_job(9999, pub.FakeThreadsPublisher(),
                              content="x", path=db)
        assert out.state == pub.FAILED
        assert out.error == "job_not_found"

    def test_requeue_respects_retry_budget(self, db):
        job_id, _, _ = _enqueue(db)
        for _ in range(pub.MAX_ATTEMPTS):
            pub.claim_next_job(path=db)
            out = pub.publish_job(
                job_id, pub.FakeThreadsPublisher([ConnectionError("x")]),
                content="konten uji", path=db,
            )
            assert out.state == pub.FAILED
            # A failed job must be requeued before it can be attempted again —
            # that is what stops a broken draft from hammering the API.
            pub.requeue(job_id, path=db)
        assert pub.get_job(job_id, path=db).attempts == pub.MAX_ATTEMPTS
        assert pub.requeue(job_id, path=db) is False

    def test_requeue_works_before_budget_exhausted(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([ConnectionError("x")]),
                        content="konten uji", path=db)
        assert pub.requeue(job_id, path=db) is True
        assert pub.get_job(job_id, path=db).state == pub.QUEUED

    def test_requeue_refuses_non_failed_jobs(self, db):
        job_id, _, _ = _enqueue(db)
        assert pub.requeue(job_id, path=db) is False

    def test_queue_depth_and_oldest_age(self, db):
        _enqueue(db, draft_id=1, content="a")
        _enqueue(db, draft_id=2, content="b")
        depth = pub.queue_depth(path=db)
        assert depth.get(pub.QUEUED) == 2
        assert pub.oldest_pending_age_seconds(path=db) is not None

    def test_jobs_in_state(self, db):
        job_id, _, _ = _enqueue(db)
        assert len(pub.jobs_in_state(pub.QUEUED, path=db)) == 1
        pub.claim_next_job(path=db)
        assert len(pub.jobs_in_state(pub.PUBLISHING, path=db)) == 1
        assert len(pub.jobs_in_state(pub.QUEUED, path=db)) == 0

    def test_get_job_by_operation(self, db):
        _, op_id, _ = _enqueue(db)
        job = pub.get_job_by_operation(op_id, path=db)
        assert job is not None
        assert job.operation_id == op_id


# ── Recovery ────────────────────────────────────────────────────────────────
class TestRecovery:
    def test_expired_lease_is_reclaimed(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db, lease_seconds=0)
        # Force the lease into the past.
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "UPDATE publication_jobs SET lease_expires_at = ? WHERE id = ?",
                    ("2000-01-01 00:00:00", job_id),
                )
        n = rec.reclaim_expired_leases(path=db)
        assert n == 1
        assert pub.get_job(job_id, path=db).state == pub.QUEUED

    def test_live_lease_is_not_reclaimed(self, db):
        _enqueue(db)
        pub.claim_next_job(path=db, lease_seconds=3600)
        assert rec.reclaim_expired_leases(path=db) == 0

    def test_unknown_reconciled_as_published(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        fake = pub.FakeThreadsPublisher(
            lookups={"x": {"found": True, "post_id": "found_999"}}
        )
        out = pub.reconcile_unknown(job_id, fake, container_id="x", path=db)
        assert out.state == pub.PUBLISHED
        assert out.reconciled is True
        assert pub.get_job(job_id, path=db).published_id == "found_999"

    def test_unknown_reconciled_as_absent_requeues(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        fake = pub.FakeThreadsPublisher(
            lookups={"x": {"found": False, "definitely_absent": True}}
        )
        out = pub.reconcile_unknown(job_id, fake, container_id="x", path=db)
        assert out.state == pub.QUEUED
        assert pub.get_job(job_id, path=db).state == pub.QUEUED

    def test_unknown_stays_pending_when_lookup_inconclusive(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        fake = pub.FakeThreadsPublisher(lookups={"x": {"found": False}})
        out = pub.reconcile_unknown(job_id, fake, container_id="x", path=db)
        assert out.state == pub.UNKNOWN_OUTCOME
        assert "manual review" in out.error

    def test_reconcile_never_republishes(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        fake = pub.FakeThreadsPublisher(
            lookups={"x": {"found": True, "post_id": "p1"}}
        )
        pub.reconcile_unknown(job_id, fake, container_id="x", path=db)
        assert len(fake.submits) == 0, "reconciliation must never publish"

    def test_reconcile_ignores_non_unknown_jobs(self, db):
        job_id, _, _ = _enqueue(db)
        out = pub.reconcile_unknown(job_id, pub.FakeThreadsPublisher(), path=db)
        assert out.state == pub.QUEUED

    def test_startup_sweep_reclaims_and_requeues(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db, lease_seconds=0)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "UPDATE publication_jobs SET lease_expires_at = ? WHERE id = ?",
                    ("2000-01-01 00:00:00", job_id),
                )
        report = rec.startup_sweep(publisher=None, path=db)
        assert report.expired_leases_reclaimed == 1
        assert "reclaimed 1 expired lease(s)" in report.actions

    def test_startup_sweep_reports_human_review_needs(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        report = rec.startup_sweep(
            publisher=pub.FakeThreadsPublisher(lookups={"x": {"found": False}}),
            path=db,
        )
        assert report.unknown_still_pending == 1
        assert any("UNKNOWN_OUTCOME" in n for n in report.needs_human)

    def test_pending_human_review_lists_unknown_and_parked(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        items = rec.pending_human_review(path=db)
        assert any(i["state"] == pub.UNKNOWN_OUTCOME for i in items)

    def test_is_safe_to_restart_blocks_on_unknown(self, db):
        job_id, _, _ = _enqueue(db)
        pub.claim_next_job(path=db)
        pub.publish_job(job_id, pub.FakeThreadsPublisher([TimeoutError("t")]),
                        content="konten uji", path=db)
        safe, reason = rec.is_safe_to_restart(path=db)
        assert safe is False
        assert "UNKNOWN_OUTCOME" in reason

    def test_is_safe_to_restart_ok_when_clean(self, db):
        safe, reason = rec.is_safe_to_restart(path=db)
        assert safe is True
        assert reason == "no in-flight publications"

    def test_restart_preserves_pending_work(self, db):
        """Acceptance #12: a restart must not lose or duplicate queued work."""
        _enqueue(db, draft_id=1, content="a")
        _enqueue(db, draft_id=2, content="b")
        before = {j.operation_id for j in pub.jobs_in_state(pub.QUEUED, path=db)}
        # Simulate a crash + restart: fresh connections, sweep, then claim.
        rec.startup_sweep(publisher=None, path=db)
        after = {j.operation_id for j in pub.jobs_in_state(pub.QUEUED, path=db)}
        assert before == after
        assert len(after) == 2


# ── Scheduler ───────────────────────────────────────────────────────────────
class TestScheduler:
    def test_default_state_is_running(self, db):
        assert sched.is_paused(path=db) is False

    def test_pause_and_resume_persist(self, db):
        sched.set_paused(True, path=db)
        assert sched.is_paused(path=db) is True
        # A brand-new "process" reading the same DB sees the pause.
        assert sched._get_state(path=db) == sched.PAUSED
        sched.set_paused(False, path=db)
        assert sched.is_paused(path=db) is False

    def test_paused_scheduler_skips(self, db):
        sched.set_paused(True, path=db)
        d = sched.should_post_now(now=_wib(10), path=db)
        assert d.should_run is False
        assert "paused" in d.reason

    def test_quiet_hours_block_posting(self, db, settings):
        d = sched.should_post_now(now=_wib(2), path=db)
        assert d.should_run is False
        assert d.quiet is True
        assert d.wait_seconds > 0

    def test_quiet_hours_boundary_is_inclusive_at_start(self, db, settings):
        d = sched.should_post_now(now=_wib(23), path=db)
        assert d.should_run is False
        assert d.quiet is True

    def test_quiet_hours_end_allows_posting(self, db, settings):
        d = sched.should_post_now(now=_wib(6), path=db)
        assert d.quiet is False

    def test_day_cap_blocks_second_main_post(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(1), path=db)
        # Pretend a main post already went out today.
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_drafts (content, status) VALUES ('x','DRAFT')"
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO publication_jobs (draft_id, operation_id, state) "
                    "VALUES (?, ?, 'PUBLISHED')",
                    (did, f"op_seed_{did}"),
                )
        d = sched.should_post_now(now=_wib(10), path=db)
        assert d.should_run is False
        assert "already published" in d.reason

    def test_no_calendar_slot_skips(self, db):
        d = sched.should_post_now(now=_wib(10), path=db)
        assert d.should_run is False
        assert "no calendar slot" in d.reason

    def test_main_slot_runs_when_available(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(2), path=db)
        d = sched.should_post_now(now=_wib(10), path=db)
        assert d.should_run is True
        assert d.slot_kind == "main"

    def test_extras_respect_weekly_cap(self, db, settings):
        from src.strategy.calendar import build_calendar
        from src.core.clock import to_wib, utc_now
        import random

        build_calendar(days=14, rng=random.Random(3), path=db)
        # Exhaust the main slot so only extras can fire.
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_drafts (content, status) VALUES ('x','DRAFT')"
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO publication_jobs (draft_id, operation_id, state) "
                    "VALUES (?, ?, 'PUBLISHED')",
                    (did, f"op_seed_{did}"),
                )
        # Record extras against THIS week so the cap applies.
        local = to_wib(utc_now())
        monday = (local - timedelta(days=local.weekday())).strftime("%Y-%m-%d")
        sched._kv_set(
            sched._EXTRA_COUNT_KEY,
            f"{settings.scheduler.max_extra_posts_per_week}|{monday}",
            path=db,
        )
        d = sched.should_post_now(now=_wib(10), path=db, extra_due=True)
        assert d.should_run is False
        assert "weekly extra cap" in d.reason

    def test_extra_due_false_never_fires_extra(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=14, rng=random.Random(4), path=db)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_drafts (content, status) VALUES ('x','DRAFT')"
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO publication_jobs (draft_id, operation_id, state) "
                    "VALUES (?, ?, 'PUBLISHED')",
                    (did, f"op_seed_{did}"),
                )
        d = sched.should_post_now(now=_wib(10), path=db, extra_due=False)
        assert d.should_run is False

    def test_record_post_tracks_extras_per_week(self, db):
        sched.record_post("extra", path=db)
        sched.record_post("extra", path=db)
        assert sched.extras_this_week(path=db) == 2

    def test_record_main_does_not_touch_extras(self, db):
        sched.record_post("main", path=db)
        assert sched.extras_this_week(path=db) == 0

    def test_next_run_delay_zero_when_due(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(5), path=db)
        assert sched.next_run_delay(now=_wib(10), path=db) == 0.0

    def test_next_run_delay_positive_when_paused(self, db):
        sched.set_paused(True, path=db)
        assert sched.next_run_delay(now=_wib(10), path=db) > 0

    def test_next_run_delay_waits_for_quiet_hours_end(self, db):
        delay = sched.next_run_delay(now=_wib(2), path=db)
        assert delay > 0

    def test_status_reports_everything(self, db):
        st = sched.status(path=db)
        assert st["state"] in (sched.RUNNING, sched.PAUSED)
        assert "queue" in st
        assert "wib_now" in st
        assert st["main_posts_per_day"] == 1

    def test_run_once_dry_run_has_no_side_effects(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(6), path=db)

        def writer(pillar, kind):
            raise AssertionError("writer must not run in dry-run mode")

        result = sched.run_once(writer, lambda *a: None, path=db, dry_run=True)
        assert result["dry_run"] is True
        assert result["ran"] is True
        assert pub.queue_depth(path=db) == {}

    def test_run_once_skips_when_paused(self, db):
        sched.set_paused(True, path=db)
        result = sched.run_once(lambda *a: None, lambda *a: None, path=db,
                                dry_run=False)
        assert result["ran"] is False
        assert "paused" in result["reason"]

    def test_run_once_skips_slot_when_no_draft(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(7), path=db)
        result = sched.run_once(lambda p, k: None, lambda *a: None,
                                path=db, dry_run=False)
        assert result["ran"] is False
        assert "no acceptable draft" in result["reason"]

    def test_run_once_enqueues_a_draft(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=3, rng=random.Random(8), path=db)

        class FakeDraft:
            id = 42
            content = "konten draf"

        enqueued = []

        def enqueue_fn(draft_id, content):
            enqueued.append((draft_id, content))
            return (99, "op_x", True)

        result = sched.run_once(lambda p, k: FakeDraft(), enqueue_fn,
                                path=db, dry_run=False)
        assert result["ran"] is True
        assert enqueued == [(42, "konten draf")]
        assert result["job_id"] == 99


# ── Telegram callback tokens ────────────────────────────────────────────────
OPERATOR = Actor(user_id=111, username="op", chat_id="-100",
                 is_private=False, display="op(111)")
INTRUDER = Actor(user_id=999, username="bad", chat_id="-100",
                 is_private=False, display="bad(999)")


@pytest.fixture()
def operator_settings(monkeypatch):
    """Settings with OPERATOR on the allowlist and INTRUDER off it."""
    from src.core import config as cfg

    cfg.reset_settings_cache()
    monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


class TestCallbackTokens:
    def test_issue_and_consume(self, db, operator_settings):
        tok = issue_callback_token("draft-1", "approve", OPERATOR, path=db)
        assert tok
        ok, reason, record = consume_callback_token(tok, OPERATOR, path=db)
        assert ok is True
        assert reason == "ok"
        assert record.draft_id == "draft-1"
        assert record.action == "approve"

    def test_token_is_single_use(self, db, operator_settings):
        tok = issue_callback_token("draft-1", "approve", OPERATOR, path=db)
        assert consume_callback_token(tok, OPERATOR, path=db)[0] is True
        ok, reason, _ = consume_callback_token(tok, OPERATOR, path=db)
        assert ok is False
        assert reason == "already_consumed"

    def test_token_is_opaque(self, db, operator_settings):
        tok = issue_callback_token("draft-1", "approve", OPERATOR, path=db)
        assert "draft-1" not in tok
        assert len(tok) >= 16

    def test_unknown_token_is_rejected(self, db, operator_settings):
        ok, reason, _ = consume_callback_token("nonsense", OPERATOR, path=db)
        assert ok is False
        assert reason == "unknown_token"

    def test_expired_token_is_rejected(self, db, operator_settings):
        tok = issue_callback_token("draft-1", "approve", OPERATOR,
                                   ttl_seconds=0, path=db)
        ok, reason, _ = consume_callback_token(tok, OPERATOR, path=db)
        assert ok is False
        assert reason == "expired"

    def test_different_actor_cannot_consume(self, db, operator_settings):
        """Acceptance #13: a replay by someone else must fail."""
        tok = issue_callback_token("draft-1", "approve", OPERATOR, path=db)
        ok, reason, _ = consume_callback_token(tok, INTRUDER, path=db)
        assert ok is False
        assert reason in ("actor_mismatch", "not_an_operator")

    def test_non_operator_cannot_consume_own_token(self, db, operator_settings):
        tok = issue_callback_token("draft-1", "approve", INTRUDER, path=db)
        ok, reason, _ = consume_callback_token(tok, INTRUDER, path=db)
        assert ok is False
        assert reason == "not_an_operator"

    def test_actor_without_id_cannot_consume(self, db, operator_settings):
        anon = Actor(user_id=None, username="x", chat_id="-100",
                     is_private=False, display="x")
        tok = issue_callback_token("draft-1", "approve", OPERATOR, path=db)
        ok, reason, _ = consume_callback_token(tok, anon, path=db)
        assert ok is False

    def test_purge_removes_only_expired_unconsumed(self, db, operator_settings):
        good = issue_callback_token("d", "approve", OPERATOR, path=db)
        issue_callback_token("d", "reject", OPERATOR, ttl_seconds=0, path=db)
        n = purge_expired_tokens(path=db)
        assert n == 1
        ok, _, _ = consume_callback_token(good, OPERATOR, path=db)
        assert ok is True

    def test_ttl_is_short(self):
        assert CALLBACK_TTL_SECONDS <= 3600


# ── Adapters ────────────────────────────────────────────────────────────────
class TestAdapters:
    def test_dry_run_threads_adapter_suppresses_everything(self):
        a = DryRunThreadsAdapter()
        r = a.publish_text("halo")
        assert r["dry_run"] is True
        # A synthetic id, not a real one: the publisher requires a non-empty
        # post_id, and a dry run must still be able to reach PUBLISHED.
        assert r["post_id"].startswith("dryrun_")
        assert len(a.calls) == 1

    def test_dry_run_chain(self):
        a = DryRunThreadsAdapter()
        r = a.publish_chain(["a", "b"])
        assert r["dry_run"] is True

    def test_dry_run_lookup_makes_no_claim(self):
        a = DryRunThreadsAdapter()
        info = a.lookup_post("x")
        assert info["found"] is False
        assert info["definitely_absent"] is False

    def test_dry_run_insights_are_null(self):
        a = DryRunThreadsAdapter()
        info = a.get_insights("x")
        assert info["views"] is None

    def test_default_adapter_is_dry_run(self, settings):
        a = get_threads_adapter()
        assert isinstance(a, DryRunThreadsAdapter)

    def test_live_adapter_requires_credentials(self, settings, monkeypatch):
        """An unconfigured live adapter must refuse to exist."""
        import src.adapters.threads as threads_mod

        monkeypatch.setattr(
            threads_mod, "get_settings",
            lambda: type("S", (), {"threads": type("T", (), {
                "configured": False, "user_id": "", "access_token": "",
                "base_url": "x"})()})(),
        )
        with pytest.raises(RuntimeError):
            threads_mod.LiveThreadsAdapter()

    def test_live_adapter_constructs_when_configured(self, monkeypatch):
        import src.adapters.threads as threads_mod

        monkeypatch.setattr(
            threads_mod, "get_settings",
            lambda: type("S", (), {"threads": type("T", (), {
                "configured": True, "user_id": "123", "access_token": "tok",
                "base_url": "https://example.invalid"})()})(),
        )
        a = threads_mod.LiveThreadsAdapter()
        assert a._cfg.user_id == "123"

    def test_dry_run_telegram_adapter_suppresses(self):
        a = DryRunTelegramAdapter()
        r = a.send_text("-100", "halo")
        assert r["dry_run"] is True
        assert len(a.sent) == 1

    def test_dry_run_telegram_buttons(self):
        a = DryRunTelegramAdapter()
        r = a.send_with_buttons("-100", "pilih", [[{"text": "OK"}]])
        assert r["dry_run"] is True

    def test_default_telegram_adapter_is_dry_run(self, settings):
        assert isinstance(get_telegram_adapter(), DryRunTelegramAdapter)

    def test_live_telegram_requires_token(self, settings, monkeypatch):
        import src.adapters.telegram as tg_mod

        monkeypatch.setattr(
            tg_mod, "get_settings",
            lambda: type("S", (), {"telegram": type("T", (), {
                "configured": False, "bot_token": ""})()})(),
        )
        with pytest.raises(RuntimeError):
            tg_mod.LiveTelegramAdapter()

    def test_schedule_formatting(self, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=7, rng=random.Random(11), path=db)
        text = format_schedule_for_operator(path=db)
        assert "Jadwal" in text
        assert "WIB" in text

    def test_schedule_formatting_empty_calendar(self, db):
        text = format_schedule_for_operator(path=db)
        assert "Belum ada kalender" in text


# ── Authorization at the mutation boundary ──────────────────────────────────
class TestMutationAuthorization:
    def test_operator_can_mutate(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
        from src.core import config as cfg

        cfg.reset_settings_cache()
        try:
            assert is_operator(OPERATOR) is True
        finally:
            cfg.reset_settings_cache()

    def test_intruder_cannot_mutate(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
        from src.core import config as cfg

        cfg.reset_settings_cache()
        try:
            assert is_operator(INTRUDER) is False
            with pytest.raises(AuthorizationDenied):
                from src.core.security import require_operator
                require_operator(INTRUDER, "/approve")
        finally:
            cfg.reset_settings_cache()

    def test_empty_allowlist_denies_all_mutations(self, settings):
        assert is_operator(OPERATOR) is False
