"""tests/v2/test_scheduler_loop.py — the long-running scheduler loop.

`run_loop()` is the piece that turns scheduling decisions into actual drafts.
It had never been exercised: every scheduler test called `should_post_now()`
directly with synthetic fixtures.

Running it for the first time exposed a design bug — `run_once(dry_run=True)`
skipped generation entirely, so shadow mode left the queue permanently empty
and was useless for its stated purpose of letting an operator review what
*would* have gone out.
"""
from __future__ import annotations

import json
import random

import pytest

from src.content import writer as writer_mod
from src.core.llm import FakeLLMClient
from src.core.storage import migrate
from src.orchestration import scheduler as sched
from src.orchestration.publisher import (
    QUEUED, jobs_in_state, queue_depth,
)
from src.persona.voice import VoiceVariation
from src.strategy.calendar import build_calendar


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    writer_mod.reset_variation_history()
    sched._running = True
    yield
    writer_mod.reset_variation_history()
    sched._running = True


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "sched.db"
    migrate(path)
    build_calendar(days=7, rng=random.Random(7), path=path)
    return path


def _fake_llm(content="Kabel charger yang selalu kusut di tas dan putus di ujung."):
    return FakeLLMClient(default=json.dumps({
        "content": content, "disclosure": "",
    }))


@pytest.fixture()
def pinned(monkeypatch):
    monkeypatch.setattr(
        writer_mod, "varied_variation",
        lambda history, rng=None: VoiceVariation(
            "single_snappy", "observasi", "pendek", "ringan", "edukasi"
        ),
    )


class TestShadowMode:
    def test_shadow_generates_and_queues(self, db, pinned):
        """The whole point of shadow mode: drafts exist for review.

        The day cap is 1 main post/day, so three passes produce exactly one
        draft — the second and third are correctly refused.
        """
        summary = sched.run_loop(
            path=db, dry_run=True, client=_fake_llm(),
            sleeper=lambda s: None, max_passes=3,
        )
        assert summary["enqueued"] == 1
        assert summary["published"] == 0
        assert len(jobs_in_state(QUEUED, path=db)) == 1

    def test_shadow_never_publishes(self, db, pinned):
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=3)
        assert len(jobs_in_state("PUBLISHED", path=db)) == 0
        assert queue_depth(path=db) == {"QUEUED": 1}

    def test_shadow_reports_dry_run_in_each_pass(self, db, pinned):
        passes = []
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1,
                       on_pass=lambda r: passes.append(r))
        assert passes[0]["dry_run"] is True
        assert "shadow" in passes[0]["reason"]

    def test_shadow_records_the_post_against_the_day_cap(self, db, pinned):
        """Enqueued drafts must count toward the daily cap."""
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1)
        assert sched.posts_today(path=db) == 1

    def test_day_cap_stops_a_second_main_post(self, db, pinned):
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1)
        passes = []
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1,
                       on_pass=lambda r: passes.append(r))
        assert passes[0]["ran"] is False
        assert "already published" in passes[0]["reason"]

    def test_paused_scheduler_does_nothing(self, db, pinned):
        sched.set_paused(True, path=db)
        summary = sched.run_loop(
            path=db, dry_run=True, client=_fake_llm(),
            sleeper=lambda s: None, max_passes=2,
        )
        assert summary["enqueued"] == 0
        assert queue_depth(path=db) == {}

    def test_loop_survives_a_writer_failure(self, db, pinned):
        """A failing pass must not kill the loop.

        write_draft catches the LLM error and returns None, so the pass is
        counted as skipped rather than as an error. Either way the loop keeps
        running and nothing is enqueued.
        """
        class Boom:
            def complete(self, *a, **k):
                raise RuntimeError("llm down")

        summary = sched.run_loop(
            path=db, dry_run=True, client=Boom(),
            sleeper=lambda s: None, max_passes=2,
        )
        assert summary["passes"] == 2
        assert summary["enqueued"] == 0
        assert queue_depth(path=db) == {}

    def test_max_passes_bounds_the_loop(self, db, pinned):
        summary = sched.run_loop(
            path=db, dry_run=True, client=_fake_llm(),
            sleeper=lambda s: None, max_passes=1,
        )
        assert summary["passes"] == 1


class TestShutdown:
    def test_request_shutdown_stops_the_loop(self, db, pinned):
        def sleeper(seconds):
            sched.request_shutdown()

        summary = sched.run_loop(
            path=db, dry_run=True, client=_fake_llm(),
            sleeper=sleeper, max_passes=None,
        )
        assert summary["passes"] >= 1

    def test_is_running_defaults_true(self):
        assert sched.is_running() is True

    def test_request_shutdown_sets_the_flag(self):
        sched.request_shutdown()
        assert sched.is_running() is False
        sched._running = True


class TestDrainQueue:
    """_drain_queue publishes queued jobs, but only when live is allowed."""

    LIVE = type("F", (), {
        "live_publishing_allowed": True,
        "autopublish_enabled": True, "dry_run": False,
        "affiliate_auto_enabled": False, "shadow_mode": False,
        "v2_scheduler_enabled": True,
        "v2_telegram_commands_enabled": False,
    })()
    SCHED = type("C", (), {
        "main_posts_per_day": 1, "max_extra_posts_per_week": 3,
        "quiet_start_hour": 23, "quiet_end_hour": 6,
        "poll_interval_seconds": 300, "paused": False,
    })()

    def _patch_live(self, monkeypatch, adapter):
        """Patch the names scheduler.py actually resolves at call time.

        scheduler.py does `from src.core.config import get_settings` at module
        level, so the name lives in src.orchestration.scheduler's namespace.
        Patching src.core.config.get_settings has no effect on it.
        """
        import src.orchestration.scheduler as sched_mod
        import src.adapters.threads as threads_mod

        settings = type("S", (), {
            "flags": self.LIVE, "scheduler": self.SCHED,
        })()
        monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)
        monkeypatch.setattr(threads_mod, "get_threads_adapter", lambda: adapter)

    def test_drain_is_a_noop_when_live_publishing_is_off(self, db, pinned):
        """A misconfigured flag must not publish by accident."""
        sched.run_loop(path=db, dry_run=True, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1)
        published = sched._drain_queue(path=db)
        assert published == 0
        assert len(jobs_in_state(QUEUED, path=db)) == 1

    def test_drain_publishes_when_live_is_allowed(self, db, monkeypatch, pinned):
        from src.adapters.threads import DryRunThreadsAdapter

        self._patch_live(monkeypatch, DryRunThreadsAdapter())
        sched.run_loop(path=db, dry_run=False, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1)
        assert len(jobs_in_state("PUBLISHED", path=db)) == 1
        assert queue_depth(path=db).get("QUEUED", 0) == 0

    def test_drain_stops_at_an_unknown_outcome(self, db, monkeypatch, pinned):
        """Never continue past an ambiguous publish."""
        from src.adapters.threads import DryRunThreadsAdapter

        class TimeoutAdapter(DryRunThreadsAdapter):
            def publish_text(self, text, image_url=""):
                raise TimeoutError("read timed out")

        self._patch_live(monkeypatch, TimeoutAdapter())
        sched.run_loop(path=db, dry_run=False, client=_fake_llm(),
                       sleeper=lambda s: None, max_passes=1)
        assert len(jobs_in_state("UNKNOWN_OUTCOME", path=db)) == 1
        assert len(jobs_in_state("PUBLISHED", path=db)) == 0


class TestDefaults:
    def test_default_writer_returns_a_draft_or_none(self, db, pinned):
        out = sched.default_writer("relatable", "main", path=db,
                                   client=_fake_llm())
        assert out is not None
        assert out.content

    def test_default_writer_returns_none_on_bad_output(self, db, pinned):
        out = sched.default_writer(
            "relatable", "main", path=db,
            client=FakeLLMClient(default="not json at all"),
        )
        assert out is None

    def test_default_enqueue_creates_a_job(self, db):
        job_id, op_id, created = sched.default_enqueue(1, "konten", path=db)
        assert created is True
        assert job_id > 0
