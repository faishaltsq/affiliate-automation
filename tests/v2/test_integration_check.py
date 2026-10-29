"""tests/v2/test_integration_check.py — the offline end-to-end rehearsal.

Unit tests prove each component works. This proves they work *together*:
calendar → scheduler → ideation → writer → gate → queue → publish → recovery.

It is the check that found the publisher/adapter interface mismatch, so it runs
as part of the normal suite rather than only on demand.
"""
from __future__ import annotations

import pytest

from src.core.storage import migrate
from src.integration_check import run


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "integration.db"
    migrate(path)
    return path


class TestIntegrationRehearsal:
    def test_every_check_passes(self, db):
        report = run(path=db)
        failed = [c for c in report.checks if not c.passed]
        assert not failed, "; ".join(f"{c.name}: {c.detail}" for c in failed)

    def test_produces_a_full_check_suite(self, db):
        report = run(path=db)
        assert len(report.checks) >= 15

    def test_no_external_calls(self, db):
        report = run(path=db)
        assert report.external_calls == 0

    def test_report_is_serialisable(self, db):
        import json

        report = run(path=db)
        assert json.dumps(report.to_dict())

    def test_is_deterministic(self, tmp_path):
        """Two independent runs on fresh databases must agree.

        Running twice against the SAME database is not comparable — the first
        run's queued jobs legitimately change what the second one sees.
        """
        a = run(path=tmp_path / "a.db")
        b = run(path=tmp_path / "b.db")
        assert [(c.name, c.passed) for c in a.checks] == \
               [(c.name, c.passed) for c in b.checks]


class TestRehearsalInvariants:
    """The specific guarantees the rehearsal exists to prove."""

    def test_pipeline_reaches_published(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "publish completes through the adapter")
        assert check.passed is True
        assert "PUBLISHED" in check.detail

    def test_gate_rejects_before_the_queue(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name.startswith("gate rejects fabricated"))
        assert check.passed is True
        assert "fabricated_firsthand_usage" in check.detail

    def test_timeout_never_becomes_a_retry(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "publish timeout marks UNKNOWN_OUTCOME")
        assert check.passed is True

    def test_reconciliation_never_republishes(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "reconciliation never republishes")
        assert check.passed is True
        assert "submit_calls=0" in check.detail

    def test_restart_preserves_queued_work(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "restart sweep preserves queued work")
        assert check.passed is True

    def test_pause_survives_a_restart(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "pause persists across a restart")
        assert check.passed is True

    def test_quiet_hours_block_posting(self, db):
        report = run(path=db)
        check = next(c for c in report.checks
                     if c.name == "quiet hours block posting")
        assert check.passed is True


class TestCutoverPreflight:
    """The preflight must report blockers without changing anything."""

    def test_preflight_returns_a_check_list(self):
        from src.integration_check import preflight

        checks = preflight()
        assert checks
        for name, ok, detail in checks:
            assert isinstance(name, str) and name
            assert isinstance(ok, bool)
            assert isinstance(detail, str)

    def test_preflight_reports_live_publishing_off(self, monkeypatch):
        from src.core import config as cfg
        from src.integration_check import preflight

        cfg.reset_settings_cache()
        monkeypatch.delenv("AUTOPUBLISH_ENABLED", raising=False)
        monkeypatch.delenv("DRY_RUN", raising=False)
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            checks = dict((n, (ok, d)) for n, ok, d in preflight())
            assert checks["live publishing is OFF"][0] is True
            assert checks["dry run is ON"][0] is True
        finally:
            cfg.reset_settings_cache()

    def test_preflight_flags_an_empty_operator_allowlist(self, monkeypatch):
        from src.core import config as cfg
        from src.integration_check import preflight

        cfg.reset_settings_cache()
        monkeypatch.delenv("TELEGRAM_OPERATOR_IDS", raising=False)
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            checks = {n: ok for n, ok, _ in preflight()}
            assert checks["Telegram operator allowlist is populated"] is False
        finally:
            cfg.reset_settings_cache()

    def test_preflight_passes_with_operators_configured(self, monkeypatch):
        from src.core import config as cfg
        from src.integration_check import preflight

        cfg.reset_settings_cache()
        monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111,222")
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            checks = {n: ok for n, ok, _ in preflight()}
            assert checks["Telegram operator allowlist is populated"] is True
        finally:
            cfg.reset_settings_cache()

    def test_preflight_flags_live_publishing_when_enabled(self, monkeypatch):
        from src.core import config as cfg
        from src.integration_check import preflight

        cfg.reset_settings_cache()
        monkeypatch.setenv("AUTOPUBLISH_ENABLED", "true")
        monkeypatch.setenv("DRY_RUN", "false")
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            checks = {n: ok for n, ok, _ in preflight()}
            assert checks["live publishing is OFF"] is False
        finally:
            cfg.reset_settings_cache()

    def test_preflight_changes_nothing(self, db):
        """A read-only check must leave the database untouched."""
        from src.integration_check import preflight
        from src.core.storage import connect

        with connect(db) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM organic_posts"
            ).fetchone()[0]
        preflight()
        with connect(db) as conn:
            after = conn.execute(
                "SELECT COUNT(*) FROM organic_posts"
            ).fetchone()[0]
        assert before == after
