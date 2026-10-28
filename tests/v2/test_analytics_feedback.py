"""tests/v2/test_analytics_feedback.py — Phase 6 analytics, feedback, experiments.

Acceptance criteria covered:
  #14 reject/revise feedback is preserved and represented in the next
      selection context
  #9  never use all-zero metrics to mean 'insights unavailable'
"""
from __future__ import annotations

import pytest

from src.core.storage import connect, migrate, transaction
from src.orchestration import analytics as ana
from src.orchestration import experiments as exp
from src.orchestration.publisher import PUBLISHED, enqueue, mark_published
from src.persona.editorial_memory import (
    CATEGORIES, category_counts, feedback_for_draft, has_feedback,
    prune, recent_feedback, record_feedback, revision_guidance,
)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


def _publish(db, threads_id: str, draft_id: int = 1) -> None:
    job_id, _, _ = enqueue(draft_id, f"konten {threads_id}", path=db)
    mark_published(job_id, threads_id, path=db)


# ── Snapshots: zero is not the same as unavailable ──────────────────────────
class TestSnapshots:
    def test_record_and_read(self, db):
        ana.record_snapshot("t1", views=100, likes=5, replies=2, path=db)
        s = ana.latest_snapshot("t1", path=db)
        assert s.views == 100
        assert s.likes == 5
        assert s.replies == 2
        assert s.source_status == ana.OK
        assert s.has_data is True

    def test_measured_zero_is_data(self, db):
        """A real zero must be distinguishable from 'never fetched'."""
        ana.record_snapshot("t1", views=0, likes=0, replies=0,
                            source_status=ana.OK, path=db)
        s = ana.latest_snapshot("t1", path=db)
        assert s.views == 0
        assert s.source_status == ana.OK
        assert s.has_data is True

    def test_unavailable_status_is_not_data(self, db):
        ana.record_snapshot("t1", views=None, likes=None, replies=None,
                            source_status=ana.UNAVAILABLE, path=db)
        s = ana.latest_snapshot("t1", path=db)
        assert s.has_data is False
        assert s.views is None

    def test_rate_limited_is_not_data(self, db):
        ana.record_snapshot("t1", views=None, likes=None, replies=None,
                            source_status=ana.RATE_LIMITED, path=db)
        assert ana.latest_snapshot("t1", path=db).has_data is False

    def test_not_found_is_not_data(self, db):
        ana.record_snapshot("t1", views=None, likes=None, replies=None,
                            source_status=ana.NOT_FOUND, path=db)
        assert ana.latest_snapshot("t1", path=db).has_data is False

    def test_unknown_status_falls_back_to_error(self, db):
        ana.record_snapshot("t1", 1, 1, 1, source_status="bogus", path=db)
        assert ana.latest_snapshot("t1", path=db).source_status == ana.ERROR

    def test_latest_returns_most_recent(self, db):
        ana.record_snapshot("t1", views=10, likes=1, replies=0, path=db)
        ana.record_snapshot("t1", views=20, likes=2, replies=1, path=db)
        assert ana.latest_snapshot("t1", path=db).views == 20

    def test_snapshots_since(self, db):
        ana.record_snapshot("t1", 10, 1, 0, path=db)
        ana.record_snapshot("t2", 20, 2, 1, path=db)
        snaps = ana.snapshots_since(hours=24, path=db)
        assert len(snaps) == 2

    def test_latest_snapshot_missing(self, db):
        assert ana.latest_snapshot("nope", path=db) is None


# ── Coverage caveats ────────────────────────────────────────────────────────
class TestCoverage:
    def test_empty_coverage(self, db):
        cov = ana.coverage(path=db)
        assert cov.posts == 0
        assert cov.coverage_ratio == 0.0
        assert "no posts" in cov.caveat

    def test_full_coverage(self, db):
        _publish(db, "t1")
        ana.record_snapshot("t1", 100, 10, 2, path=db)
        cov = ana.coverage(path=db)
        assert cov.posts == 1
        assert cov.with_data == 1
        assert cov.coverage_ratio == 1.0

    def test_low_coverage_is_flagged(self, db):
        for i in range(4):
            _publish(db, f"t{i}", draft_id=i)
        ana.record_snapshot("t0", 100, 10, 2, path=db)
        # The rest have no snapshot at all.
        cov = ana.coverage(path=db)
        assert cov.with_data == 1
        assert cov.coverage_ratio == 0.25
        assert "indicative, not conclusive" in cov.caveat

    def test_failed_fetches_are_counted_by_status(self, db):
        _publish(db, "t1")
        ana.record_snapshot("t1", None, None, None,
                            source_status=ana.UNAVAILABLE, path=db)
        cov = ana.coverage(path=db)
        assert cov.by_status.get(ana.UNAVAILABLE) == 1
        assert cov.with_data == 0


# ── Pillar performance excludes unavailable, not zeros ──────────────────────
class TestPillarPerformance:
    def test_unavailable_posts_are_excluded_not_zeroed(self, db):
        """The V1 bug: all-zero rows dragged every average to 0."""
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_ideas (pillar, angle) VALUES "
                    "('relatable', 'a')"
                )
                idea_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO content_drafts (idea_id, content, status) "
                    "VALUES (?, 'x', 'DRAFT')",
                    (idea_id,),
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _publish(db, "good", draft_id=did)
        ana.record_snapshot("good", views=500, likes=20, replies=5, path=db)

        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_ideas (pillar, angle) VALUES "
                    "('relatable', 'b')"
                )
                idea2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO content_drafts (idea_id, content, status) "
                    "VALUES (?, 'y', 'DRAFT')",
                    (idea2,),
                )
                did2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _publish(db, "bad", draft_id=did2)
        ana.record_snapshot("bad", None, None, None,
                            source_status=ana.UNAVAILABLE, path=db)

        perf = ana.pillar_performance(path=db)
        assert perf["relatable"]["posts"] == 1
        assert perf["relatable"]["avg_views"] == 500.0

    def test_real_zeros_are_included(self, db):
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_ideas (pillar, angle) VALUES "
                    "('opinions', 'a')"
                )
                idea_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "INSERT INTO content_drafts (idea_id, content, status) "
                    "VALUES (?, 'x', 'DRAFT')",
                    (idea_id,),
                )
                did = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        _publish(db, "z", draft_id=did)
        ana.record_snapshot("z", views=0, likes=0, replies=0,
                            source_status=ana.OK, path=db)
        perf = ana.pillar_performance(path=db)
        assert perf["opinions"]["avg_views"] == 0.0

    def test_empty_performance(self, db):
        assert ana.pillar_performance(path=db) == {}


# ── Refresh queue ───────────────────────────────────────────────────────────
class TestRefreshQueue:
    def test_never_fetched_post_is_listed(self, db):
        _publish(db, "t1")
        need = ana.posts_needing_refresh(path=db)
        assert any(p["threads_id"] == "t1" for p in need)
        assert need[0]["last_status"] == "never"

    def test_recently_fetched_post_is_skipped(self, db):
        _publish(db, "t1")
        ana.record_snapshot("t1", 10, 1, 0, path=db)
        need = ana.posts_needing_refresh(cooldown_hours=6, path=db)
        assert not any(p["threads_id"] == "t1" for p in need)

    def test_failed_fetch_is_retried(self, db):
        _publish(db, "t1")
        ana.record_snapshot("t1", None, None, None,
                            source_status=ana.UNAVAILABLE, path=db)
        need = ana.posts_needing_refresh(cooldown_hours=0, path=db)
        assert any(p["threads_id"] == "t1" for p in need)
        assert need[0]["last_status"] == ana.UNAVAILABLE

    def test_unpublished_jobs_are_not_listed(self, db):
        enqueue(1, "x", path=db)
        assert ana.posts_needing_refresh(path=db) == []


# ── Health ──────────────────────────────────────────────────────────────────
class TestHealth:
    def test_health_reports_everything(self, db):
        h = ana.health(path=db)
        assert "scheduler_paused" in h
        assert "queue" in h
        assert "metric_coverage" in h
        assert "caveat" in h["metric_coverage"]

    def test_health_shows_queue_depth(self, db):
        enqueue(1, "a", path=db)
        enqueue(2, "b", path=db)
        h = ana.health(path=db)
        assert h["queue"].get("QUEUED") == 2


# ── Commerce metrics: never inferred from engagement ────────────────────────
class TestCommerceMetrics:
    def test_records_platform_reported_figures(self, db):
        ana.record_commerce_metric("post1", product_id=1, clicks=10,
                                   attributed_orders=2, commission=15000.0,
                                   path=db)
        s = ana.commerce_summary(path=db)
        assert s["clicks"] == 10
        assert s["attributed_orders"] == 2
        assert s["commission"] == 15000.0
        assert s["reports"] == 1

    def test_empty_summary(self, db):
        s = ana.commerce_summary(path=db)
        assert s["clicks"] == 0
        assert s["commission"] == 0.0
        assert "inferred from Threads engagement" in s["note"]

    def test_partial_metrics_are_allowed(self, db):
        """Clicks without orders is a legitimate platform report."""
        ana.record_commerce_metric("p", product_id=1, clicks=5, path=db)
        s = ana.commerce_summary(path=db)
        assert s["clicks"] == 5
        assert s["attributed_orders"] == 0

    def test_commerce_is_separate_from_engagement(self, db):
        """Recording Threads metrics must never create commerce data."""
        _publish(db, "t1")
        ana.record_snapshot("t1", views=99999, likes=500, replies=80, path=db)
        s = ana.commerce_summary(path=db)
        assert s["reports"] == 0
        assert s["clicks"] == 0
        assert s["commission"] == 0.0


# ── Feedback memory ─────────────────────────────────────────────────────────
class TestFeedbackMemory:
    def test_record_and_retrieve(self, db):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="kepanjangan", path=db)
        events = recent_feedback(path=db)
        assert len(events) == 1
        assert events[0]["category"] == "too_long"
        assert events[0]["reason"] == "kepanjangan"

    def test_history_is_never_erased(self, db):
        for i in range(5):
            record_feedback(draft_id=i, actor_id="111", category="voice_off",
                            path=db)
        assert len(recent_feedback(limit=50, path=db)) == 5

    def test_feedback_for_draft(self, db):
        record_feedback(draft_id=7, actor_id="111", category="no_substance",
                        path=db)
        record_feedback(draft_id=8, actor_id="111", category="too_long",
                        path=db)
        mine = feedback_for_draft(7, path=db)
        assert len(mine) == 1
        assert mine[0]["category"] == "no_substance"

    def test_unknown_category_becomes_other(self, db):
        record_feedback(draft_id=1, actor_id="111", category="wat",
                        path=db)
        assert recent_feedback(path=db)[0]["category"] == "other"

    def test_category_counts(self, db):
        for _ in range(3):
            record_feedback(draft_id=1, actor_id="111", category="too_long",
                            path=db)
        record_feedback(draft_id=2, actor_id="111", category="voice_off",
                        path=db)
        counts = category_counts(path=db)
        assert counts["too_long"] == 3
        assert counts["voice_off"] == 1

    def test_revision_guidance_empty_without_feedback(self, db):
        assert revision_guidance(path=db) == ""

    def test_revision_guidance_mentions_categories_and_reasons(self, db):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="kepanjangan banget", path=db)
        record_feedback(draft_id=2, actor_id="111", category="voice_off",
                        path=db)
        text = revision_guidance(path=db)
        assert "UMPAN BALIK OPERATOR" in text
        assert "kepanjangan banget" in text
        assert "pendek" in text  # guidance for too_long

    def test_guidance_is_bounded(self, db):
        for i in range(50):
            record_feedback(draft_id=i, actor_id="111", category="too_long",
                            reason=f"alasan {i}", path=db)
        text = revision_guidance(limit=12, path=db)
        # At most 5 specific reasons are echoed, not 50.
        assert text.count("alasan ") <= 5

    def test_prune_keeps_recent(self, db):
        for i in range(30):
            record_feedback(draft_id=i, actor_id="111", category="other",
                            path=db)
        removed = prune(keep=10, path=db)
        assert removed == 20
        assert len(recent_feedback(limit=50, path=db)) == 10

    def test_prune_noop_when_under_limit(self, db):
        record_feedback(draft_id=1, actor_id="111", category="other", path=db)
        assert prune(keep=10, path=db) == 0

    def test_has_feedback(self, db):
        assert has_feedback(path=db) is False
        record_feedback(draft_id=1, actor_id="111", category="other", path=db)
        assert has_feedback(path=db) is True

    def test_all_categories_are_known(self):
        assert "voice_off" in CATEGORIES
        assert "unsourced_claim" in CATEGORIES


# ── Experiments ─────────────────────────────────────────────────────────────
class TestExperiments:
    def test_assign_variant(self, db):
        e = exp.assign_variant(draft_id=1, hypothesis="hook_type",
                               variant="observasi", path=db)
        assert e.variant == "observasi"
        assert exp.variant_for_draft(1, "hook_type", path=db) == "observasi"

    def test_random_assignment_stays_in_the_arm_set(self, db):
        import random

        rng = random.Random(3)
        for i in range(20):
            e = exp.assign_variant(draft_id=i, hypothesis="length", rng=rng,
                                   path=db)
            assert e.variant in exp.HYPOTHESES["length"]

    def test_unknown_hypothesis_raises(self, db):
        with pytest.raises(ValueError):
            exp.assign_variant(draft_id=1, hypothesis="nope", path=db)

    def test_no_data_report(self, db):
        rep = exp.evaluate_experiment("hook_type", path=db)
        assert rep.confidence == "no_data"
        assert rep.winner is None

    def test_small_sample_refuses_a_winner(self, db):
        """Acceptance: never declare a winner on a handful of posts."""
        import random

        rng = random.Random(5)
        for i in range(3):
            exp.assign_variant(draft_id=i, hypothesis="hook_type",
                               variant="observasi", rng=rng, path=db)
            _publish(db, f"t{i}", draft_id=i)
            ana.record_snapshot(f"t{i}", views=100 + i, likes=5, replies=1,
                                path=db)
        rep = exp.evaluate_experiment("hook_type", path=db)
        assert rep.winner is None
        assert rep.confidence == "insufficient_data"
        assert "need at least" in rep.caveat

    def test_thin_variant_refuses_a_winner(self, db):
        """One variant with plenty of data and one with almost none."""
        import random

        rng = random.Random(7)
        # 12 posts on 'observasi' — enough to clear the total-sample floor.
        for i in range(12):
            exp.assign_variant(draft_id=i, hypothesis="hook_type",
                               variant="observasi", rng=rng, path=db)
            _publish(db, f"a{i}", draft_id=i)
            ana.record_snapshot(f"a{i}", views=100, likes=5, replies=1, path=db)
        # 1 post on 'kontras' — a huge number on a single post must not win.
        for i in range(12, 13):
            exp.assign_variant(draft_id=i, hypothesis="hook_type",
                               variant="kontras", rng=rng, path=db)
            _publish(db, f"a{i}", draft_id=i)
            ana.record_snapshot(f"a{i}", views=9999, likes=500, replies=90,
                                path=db)
        rep = exp.evaluate_experiment("hook_type", path=db)
        assert rep.total_samples == 13
        assert rep.confidence == "insufficient_data"
        assert "kontras" in rep.caveat

    def test_sufficient_samples_names_a_directional_winner(self, db):
        import random

        rng = random.Random(11)
        for i in range(6):
            exp.assign_variant(draft_id=i, hypothesis="length",
                               variant="pendek", rng=rng, path=db)
            _publish(db, f"s{i}", draft_id=i)
            ana.record_snapshot(f"s{i}", views=100, likes=5, replies=1, path=db)
        for i in range(6, 12):
            exp.assign_variant(draft_id=i, hypothesis="length",
                               variant="sedang", rng=rng, path=db)
            _publish(db, f"s{i}", draft_id=i)
            ana.record_snapshot(f"s{i}", views=400, likes=20, replies=4, path=db)
        rep = exp.evaluate_experiment("length", path=db)
        assert rep.winner == "sedang"
        assert rep.confidence == "directional"
        assert "noisy" in rep.caveat

    def test_assignments_listing(self, db):
        exp.assign_variant(draft_id=1, hypothesis="length", variant="pendek",
                           path=db)
        exp.assign_variant(draft_id=2, hypothesis="length", variant="sedang",
                           path=db)
        rows = exp.assignments("length", path=db)
        assert len(rows) == 2

    def test_hypotheses_cover_the_planned_variables(self):
        for key in ("hook_type", "length", "humor_intensity", "content_format",
                    "problem_category"):
            assert key in exp.HYPOTHESES
            assert len(exp.HYPOTHESES[key]) >= 2


# ── Reports ─────────────────────────────────────────────────────────────────
class TestReports:
    def test_daily_report(self, db):
        r = exp.daily_report(path=db)
        assert "posts_today" in r
        assert "metric_coverage" in r
        assert "caveat" in r["metric_coverage"]

    def test_weekly_report(self, db):
        r = exp.weekly_report(path=db)
        assert r["window"] == "7 days"
        assert "uncertainty_note" in r
        assert "statistically established" in r["uncertainty_note"]

    def test_weekly_report_includes_feedback_counts(self, db):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        path=db)
        r = exp.weekly_report(path=db)
        assert r["operator_feedback"]["count"] == 1
        assert r["operator_feedback"]["by_category"]["too_long"] == 1

    def test_weekly_report_omits_empty_experiments(self, db):
        r = exp.weekly_report(path=db)
        assert r["experiments"] == {}
