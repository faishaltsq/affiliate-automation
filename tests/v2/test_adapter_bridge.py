"""tests/v2/test_adapter_bridge.py — the publisher/adapter seam.

`src/orchestration/publisher.py` defines `ThreadsPublisher.submit()` while
`src/adapters/threads.py` defines `ThreadsAdapter.publish_text()`. They are two
different abstractions and, until `AdapterThreadsPublisher` existed, nothing
connected them — so injecting the real dry-run adapter into `publish_job()`
raised NotImplementedError, which was caught as a generic FAILED. Every unit
test passed because each one used its own fake.

These tests pin the bridge so the seam cannot silently break again.
"""
from __future__ import annotations

import pytest

from src.adapters.threads import (
    DryRunThreadsAdapter, LiveThreadsAdapter, ThreadsAdapter,
    get_threads_adapter,
)
from src.core.storage import migrate
from src.orchestration import publisher as pub
from src.orchestration.publisher import (
    AdapterThreadsPublisher, FakeThreadsPublisher,
)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


class TestAdapterBridge:
    def test_bridge_routes_single_post_to_publish_text(self):
        adapter = DryRunThreadsAdapter()
        bridge = AdapterThreadsPublisher(adapter)
        result = bridge.submit("halo dunia", [])
        assert result["dry_run"] is True
        assert result["post_id"].startswith("dryrun_text_")
        assert adapter.calls[0]["op"] == "publish_text"

    def test_bridge_routes_parts_to_publish_chain(self):
        adapter = DryRunThreadsAdapter()
        bridge = AdapterThreadsPublisher(adapter)
        result = bridge.submit("", ["bagian satu", "bagian dua"])
        assert result["post_id"].startswith("dryrun_chain_")
        assert adapter.calls[0]["op"] == "publish_chain"
        assert len(adapter.calls[0]["parts"]) == 2

    def test_bridge_forwards_image_url(self):
        adapter = DryRunThreadsAdapter()
        bridge = AdapterThreadsPublisher(adapter)
        bridge.submit("teks", [], image_url="https://example.invalid/a.jpg")
        assert adapter.calls[0]["image_url"] == "https://example.invalid/a.jpg"

    def test_bridge_delegates_lookup(self):
        adapter = DryRunThreadsAdapter()
        bridge = AdapterThreadsPublisher(adapter)
        info = bridge.lookup("some_id")
        assert info["found"] is False
        assert info["definitely_absent"] is False
        assert adapter.calls[0]["op"] == "lookup_post"

    def test_bridge_satisfies_the_publisher_interface(self):
        assert issubclass(AdapterThreadsPublisher, pub.ThreadsPublisher)

    def test_bridge_works_with_any_adapter(self):
        """A custom adapter needs no changes to flow through the bridge."""

        class CustomAdapter(ThreadsAdapter):
            def __init__(self):
                self.seen = []

            def publish_text(self, text, image_url=""):
                self.seen.append(("text", text))
                return {"post_id": "custom_1"}

            def publish_chain(self, parts, image_url=""):
                self.seen.append(("chain", parts))
                return {"post_id": "custom_2"}

            def lookup_post(self, post_id):
                return {"found": True, "post_id": post_id}

            def get_insights(self, post_id):
                return {}

        adapter = CustomAdapter()
        bridge = AdapterThreadsPublisher(adapter)
        assert bridge.submit("x", [])["post_id"] == "custom_1"
        assert bridge.submit("", ["a"])["post_id"] == "custom_2"
        assert bridge.lookup("z")["found"] is True
        assert [op for op, _ in adapter.seen] == ["text", "chain"]


class TestDryRunAdapterPublishContract:
    """The publisher requires a non-empty post_id; the adapter must supply one."""

    def test_publish_text_returns_a_synthetic_id(self):
        adapter = DryRunThreadsAdapter()
        result = adapter.publish_text("halo")
        assert result["post_id"]
        assert result["post_id"].startswith("dryrun_")

    def test_publish_chain_returns_a_synthetic_id(self):
        adapter = DryRunThreadsAdapter()
        result = adapter.publish_chain(["a", "b"])
        assert result["post_id"]
        assert result["post_id"].startswith("dryrun_")

    def test_ids_are_unique_per_call(self):
        adapter = DryRunThreadsAdapter()
        ids = {adapter.publish_text(f"teks {i}")["post_id"] for i in range(5)}
        assert len(ids) == 5

    def test_synthetic_id_is_namespaced(self):
        """A dry-run id must never be mistakable for a real Threads id."""
        adapter = DryRunThreadsAdapter()
        assert adapter.publish_text("x")["post_id"].startswith("dryrun_")

    def test_result_is_flagged_as_dry_run(self):
        adapter = DryRunThreadsAdapter()
        assert adapter.publish_text("x")["dry_run"] is True
        assert adapter.publish_chain(["a"])["dry_run"] is True

    def test_calls_are_recorded_for_assertion(self):
        adapter = DryRunThreadsAdapter()
        adapter.publish_text("satu")
        adapter.publish_chain(["a", "b"])
        adapter.lookup_post("id")
        adapter.get_insights("id")
        assert [c["op"] for c in adapter.calls] == [
            "publish_text", "publish_chain", "lookup_post", "get_insights",
        ]


class TestPublishThroughTheRealAdapter:
    """End-to-end through the state machine using the real dry-run adapter."""

    def test_dry_run_publish_reaches_published(self, db):
        from src.adapters.threads import DryRunThreadsAdapter as DRA

        job_id, _, _ = pub.enqueue(1, "konten uji", path=db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id, AdapterThreadsPublisher(DRA()),
            content="konten uji", path=db,
        )
        assert out.succeeded is True
        assert out.published_id.startswith("dryrun_")
        assert pub.get_job(job_id, path=db).state == pub.PUBLISHED

    def test_dry_run_republish_is_a_noop(self, db):
        from src.adapters.threads import DryRunThreadsAdapter as DRA

        job_id, _, _ = pub.enqueue(1, "konten uji", path=db)
        pub.claim_next_job(path=db)
        bridge = AdapterThreadsPublisher(DRA())
        first = pub.publish_job(job_id, bridge, content="konten uji", path=db)
        second = pub.publish_job(job_id, bridge, content="konten uji", path=db)
        assert first.published_id == second.published_id
        assert bridge._adapter.calls.count(
            {"op": "publish_text", "text": "konten uji",
             "image_url": "", "at": bridge._adapter.calls[0]["at"]}
        ) == 1

    def test_dry_run_chain_publish_reaches_published(self, db):
        from src.adapters.threads import DryRunThreadsAdapter as DRA

        job_id, _, _ = pub.enqueue(2, "bagian", path=db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id, AdapterThreadsPublisher(DRA()),
            content="bagian", parts=["satu", "dua"], path=db,
        )
        assert out.succeeded is True
        assert out.published_id.startswith("dryrun_chain_")

    def test_timeout_still_yields_unknown_outcome(self, db):
        """The bridge must not swallow the ambiguous case."""
        from src.adapters.threads import DryRunThreadsAdapter as DRA

        class TimeoutAdapter(DRA):
            def publish_text(self, text, image_url=""):
                raise TimeoutError("read timed out")

        job_id, _, _ = pub.enqueue(3, "konten", path=db)
        pub.claim_next_job(path=db)
        out = pub.publish_job(
            job_id, AdapterThreadsPublisher(TimeoutAdapter()),
            content="konten", path=db,
        )
        assert out.state == pub.UNKNOWN_OUTCOME

    def test_reconciliation_through_the_bridge(self, db):
        from src.adapters.threads import DryRunThreadsAdapter as DRA

        class FoundAdapter(DRA):
            def lookup_post(self, post_id):
                return {"found": True, "post_id": "real_123"}

        job_id, _, _ = pub.enqueue(4, "konten", path=db)
        pub.claim_next_job(path=db)

        class TimeoutAdapter(DRA):
            def publish_text(self, text, image_url=""):
                raise TimeoutError("t")

        pub.publish_job(job_id, AdapterThreadsPublisher(TimeoutAdapter()),
                        content="konten", path=db)
        out = pub.reconcile_unknown(
            job_id, AdapterThreadsPublisher(FoundAdapter()), path=db
        )
        assert out.state == pub.PUBLISHED
        assert out.published_id == "real_123"


class TestDefaultAdapterSelection:
    def test_default_is_dry_run(self, monkeypatch):
        from src.core import config as cfg

        cfg.reset_settings_cache()
        monkeypatch.delenv("AUTOPUBLISH_ENABLED", raising=False)
        monkeypatch.delenv("DRY_RUN", raising=False)
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            assert isinstance(get_threads_adapter(), DryRunThreadsAdapter)
        finally:
            cfg.reset_settings_cache()

    def test_live_adapter_is_not_the_default(self, monkeypatch):
        from src.core import config as cfg

        cfg.reset_settings_cache()
        monkeypatch.delenv("AUTOPUBLISH_ENABLED", raising=False)
        monkeypatch.delenv("DRY_RUN", raising=False)
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            assert not isinstance(get_threads_adapter(), LiveThreadsAdapter)
        finally:
            cfg.reset_settings_cache()
