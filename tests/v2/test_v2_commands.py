"""tests/v2/test_v2_commands.py — Phase B: the V2 Telegram Command Center.

Acceptance criterion #13: "Telegram unauthorized sender/callback cannot
publish, approve, reject, change persona, or enable automation."

Every test supplies a recording `sender`, so no Telegram call is ever made.
"""
from __future__ import annotations

import pytest

from src.bot.v2_commands import (
    ALL_COMMANDS, CommandResult, handle_v2_command, v2_enabled,
)
from src.core import config as cfg
from src.core.llm import FakeLLMClient
from src.core.security import Actor
from src.core.storage import connect, migrate, transaction


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


@pytest.fixture()
def flags_off(monkeypatch):
    """V2 commands disabled — the default."""
    cfg.reset_settings_cache()
    for key in ("V2_TELEGRAM_COMMANDS_ENABLED", "TELEGRAM_OPERATOR_IDS"):
        monkeypatch.delenv(key, raising=False)
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


@pytest.fixture()
def flags_on(monkeypatch):
    """V2 commands enabled, with OPERATOR allowlisted and INTRUDER not."""
    cfg.reset_settings_cache()
    monkeypatch.setenv("V2_TELEGRAM_COMMANDS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()


OPERATOR = {"id": 111, "username": "op", "first_name": "Op"}
INTRUDER = {"id": 999, "username": "bad", "first_name": "Bad"}
CHAT = {"id": "-1003757350269", "type": "supergroup"}


def _msg(frm, text, chat=None, thread=9, mid=1):
    return {
        "message_id": mid,
        "from": frm,
        "chat": chat or CHAT,
        "message_thread_id": thread,
        "text": text,
    }


def _run(msg, text, sender=None, path=None, client=None):
    """Dispatch one command with a recording sender and a fake LLM.

    `client` defaults to a FakeLLMClient so /draft never reaches the network.
    """
    sent: list[str] = []

    def record(text_, mid):
        sent.append(text_)

    result = handle_v2_command(
        msg, text, msg.get("message_id"), msg.get("message_thread_id"),
        sender=sender or record, path=path, client=client or _fake_llm(),
    )
    return result, sent


def _fake_llm():
    import json

    from src.core.llm import FakeLLMClient

    return FakeLLMClient(default=json.dumps({
        "content": "Kabel charger yang selalu kusut di tas itu biasanya bukan "
                   "soal kabelnya, tapi soal cara bungkusnya.",
        "disclosure": "",
    }))


# ── Feature gate ────────────────────────────────────────────────────────────
class TestFeatureGate:
    def test_disabled_by_default(self, flags_off):
        assert v2_enabled() is False

    def test_disabled_means_not_handled(self, flags_off, db):
        """While V2 is dark, the legacy bot keeps working untouched."""
        result, sent = _run(_msg(OPERATOR, "/status"), "/status", path=db)
        assert result.handled is False
        assert sent == []

    def test_enabled_handles_known_commands(self, flags_on, db):
        result, sent = _run(_msg(OPERATOR, "/status"), "/status", path=db)
        assert result.handled is True
        assert sent

    def test_unknown_command_is_not_handled(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/nonsense"), "/nonsense", path=db)
        assert result.handled is False

    def test_non_command_is_not_handled(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "halo bot"), "halo bot", path=db)
        assert result.handled is False

    def test_all_documented_commands_exist(self):
        for cmd in ("/persona", "/plan", "/ideas", "/draft", "/schedule",
                    "/analytics", "/products", "/status", "/approve",
                    "/revise", "/reject", "/pause", "/resume", "/autopost",
                    "/rollback", "/help"):
            assert cmd in ALL_COMMANDS, cmd


# ── Authorization: the core guarantee ───────────────────────────────────────
class TestMutationAuthorization:
    @pytest.mark.parametrize("command", [
        "/approve 1", "/revise 1 alasan", "/reject 1 alasan", "/pause",
        "/resume", "/autopost on", "/autopost off", "/rollback",
    ])
    def test_intruder_cannot_run_mutations(self, command, flags_on, db):
        result, sent = _run(_msg(INTRUDER, command), command, path=db)
        assert result.handled is True
        assert result.authorized is False
        assert result.denied_reason == "not_an_operator"
        assert "Tidak diizinkan" in result.text

    @pytest.mark.parametrize("command", [
        "/approve 1", "/revise 1 alasan", "/reject 1 alasan", "/pause",
        "/resume", "/autopost on", "/rollback",
    ])
    def test_operator_can_run_mutations(self, command, flags_on, db):
        result, sent = _run(_msg(OPERATOR, command), command, path=db)
        assert result.handled is True
        assert result.authorized is True
        assert sent

    def test_empty_allowlist_denies_everyone(self, monkeypatch, db):
        cfg.reset_settings_cache()
        monkeypatch.setenv("V2_TELEGRAM_COMMANDS_ENABLED", "true")
        monkeypatch.delenv("TELEGRAM_OPERATOR_IDS", raising=False)
        s = cfg.Settings()
        monkeypatch.setattr(cfg, "_settings", s, raising=False)
        try:
            result, _ = _run(_msg(OPERATOR, "/pause"), "/pause", path=db)
            assert result.authorized is False
        finally:
            cfg.reset_settings_cache()

    def test_missing_user_id_is_denied(self, flags_on, db):
        anon = {"username": "ghost", "first_name": "Ghost"}
        result, _ = _run(_msg(anon, "/pause"), "/pause", path=db)
        assert result.authorized is False

    def test_denied_command_changes_no_state(self, flags_on, db):
        from src.orchestration.scheduler import is_paused

        assert is_paused(path=db) is False
        _run(_msg(INTRUDER, "/pause"), "/pause", path=db)
        assert is_paused(path=db) is False

    def test_denied_command_is_audited(self, flags_on, db):
        from src.core.events import recent

        _run(_msg(INTRUDER, "/pause"), "/pause", path=db)
        events = recent(limit=10, path=db)
        assert any(e["actor_type"] == "denied" for e in events)

    def test_readonly_commands_are_open_to_members(self, flags_on, db):
        """A group member may read status without being an operator."""
        result, sent = _run(_msg(INTRUDER, "/status"), "/status", path=db)
        assert result.handled is True
        assert result.authorized is True
        assert sent

    def test_command_with_bot_suffix_is_normalised(self, flags_on, db):
        result, _ = _run(
            _msg(OPERATOR, "/status@my_bot"), "/status@my_bot", path=db
        )
        assert result.handled is True
        assert result.command == "/status"


# ── Read-only command output ────────────────────────────────────────────────
class TestReadOnlyCommands:
    def test_persona_shows_the_bible(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/persona"), "/persona", path=db)
        assert "Teman Tongkrongan" in result.text
        assert "teman_tongkrongan_v1" in result.text

    def test_plan_lists_slots(self, flags_on, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=7, rng=random.Random(1), path=db)
        result, _ = _run(_msg(OPERATOR, "/plan"), "/plan", path=db)
        assert "Rencana konten" in result.text

    def test_ideas_returns_candidates(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/ideas"), "/ideas", path=db)
        assert "ide kandidat" in result.text

    def test_ideas_filters_by_pillar(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/ideas practical_tips"),
                         "/ideas practical_tips", path=db)
        assert "practical_tips" in result.text

    def test_draft_preview_never_publishes(self, flags_on, db, monkeypatch):
        import json

        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        # Pin a single-post variation so the fake reply matches the schema.
        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: VoiceVariation(
                "single_snappy", "observasi", "pendek", "ringan", "edukasi"
            ),
        )
        fake = FakeLLMClient(default=json.dumps({
            "content": "Kabel charger yang selalu kusut di tas itu biasanya "
                       "bukan soal kabelnya, tapi soal cara bungkusnya.",
            "disclosure": "",
        }))
        result, _ = _run(_msg(OPERATOR, "/draft"), "/draft", path=db,
                         client=fake)
        assert "Pratinjau draf" in result.text
        assert "belum dipublikasikan" in result.text.lower()
        from src.orchestration.publisher import queue_depth

        assert queue_depth(path=db) == {}

    def test_schedule_renders_wib(self, flags_on, db):
        from src.strategy.calendar import build_calendar
        import random

        build_calendar(days=7, rng=random.Random(2), path=db)
        result, _ = _run(_msg(OPERATOR, "/schedule"), "/schedule", path=db)
        assert "Jadwal" in result.text

    def test_analytics_defaults_to_7_days(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/analytics"), "/analytics", path=db)
        assert "Analitik 7 hari" in result.text

    def test_analytics_accepts_30d(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/analytics 30d"),
                         "/analytics 30d", path=db)
        assert "Analitik 30 hari" in result.text

    def test_analytics_reports_coverage_caveat(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/analytics"), "/analytics", path=db)
        assert "Cakupan metrik" in result.text

    def test_products_shows_catalog_state(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/products"), "/products", path=db)
        assert "Katalog produk" in result.text
        assert "Siap komisi" in result.text

    def test_status_reports_scheduler_and_queue(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/status"), "/status", path=db)
        assert "Status sistem" in result.text
        assert "Antrean publikasi" in result.text

    def test_help_lists_every_command(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/help"), "/help", path=db)
        for cmd in ("/persona", "/approve", "/pause", "/rollback"):
            assert cmd in result.text, cmd

    def test_start_also_shows_help(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/start"), "/start", path=db)
        assert result.handled is True
        assert "perintah" in result.text


# ── Mutation behaviour ──────────────────────────────────────────────────────
class TestMutationBehaviour:
    def _seed_draft(self, db, status="EVALUATED"):
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_drafts (content, status) "
                    "VALUES ('uji', ?)", (status,)
                )
                return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    def test_pause_persists(self, flags_on, db):
        from src.orchestration.scheduler import is_paused

        _run(_msg(OPERATOR, "/pause"), "/pause", path=db)
        assert is_paused(path=db) is True

    def test_resume_clears_pause(self, flags_on, db):
        from src.orchestration.scheduler import is_paused, set_paused

        set_paused(True, path=db)
        _run(_msg(OPERATOR, "/resume"), "/resume", path=db)
        assert is_paused(path=db) is False

    def test_approve_requires_a_draft_id(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/approve"), "/approve", path=db)
        assert "Format" in result.text

    def test_approve_rejects_unknown_draft(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/approve 9999"),
                         "/approve 9999", path=db)
        assert "belum dievaluasi" in result.text

    def test_approve_cannot_override_a_hard_veto(self, flags_on, db):
        """A vetoed draft stays rejected no matter who approves it."""
        import json

        did = self._seed_draft(db)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_evaluations "
                    "(draft_id, scores_json, vetoes_json, verdict, total_score) "
                    "VALUES (?, '{}', ?, 'REJECTED', 0)",
                    (did, json.dumps(["fabricated_firsthand_usage"])),
                )
        result, _ = _run(_msg(OPERATOR, f"/approve {did}"),
                         f"/approve {did}", path=db)
        assert "Veto keras" in result.text
        with connect(db) as conn:
            status = conn.execute(
                "SELECT status FROM content_drafts WHERE id = ?", (did,)
            ).fetchone()["status"]
        assert status != "APPROVED"

    def test_approve_accepts_a_reviewable_draft(self, flags_on, db):
        import json

        did = self._seed_draft(db)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_evaluations "
                    "(draft_id, scores_json, vetoes_json, verdict, total_score) "
                    "VALUES (?, '{}', '[]', 'NEEDS_REVIEW', 78)",
                    (did,),
                )
        result, _ = _run(_msg(OPERATOR, f"/approve {did}"),
                         f"/approve {did}", path=db)
        assert "disetujui" in result.text
        with connect(db) as conn:
            status = conn.execute(
                "SELECT status FROM content_drafts WHERE id = ?", (did,)
            ).fetchone()["status"]
        assert status == "APPROVED"

    def test_approve_does_not_publish(self, flags_on, db):
        """Approval queues; it never publishes."""
        import json

        did = self._seed_draft(db)
        with connect(db) as conn:
            with transaction(conn):
                conn.execute(
                    "INSERT INTO content_evaluations "
                    "(draft_id, scores_json, vetoes_json, verdict, total_score) "
                    "VALUES (?, '{}', '[]', 'NEEDS_REVIEW', 78)",
                    (did,),
                )
        _run(_msg(OPERATOR, f"/approve {did}"), f"/approve {did}", path=db)
        from src.orchestration.publisher import queue_depth

        assert queue_depth(path=db) == {}

    def test_reject_requires_a_reason(self, flags_on, db):
        did = self._seed_draft(db)
        result, _ = _run(_msg(OPERATOR, f"/reject {did}"),
                         f"/reject {did}", path=db)
        assert "wajib diisi" in result.text

    def test_reject_records_the_reason(self, flags_on, db):
        did = self._seed_draft(db)
        _run(_msg(OPERATOR, f"/reject {did} terlalu panjang"),
             f"/reject {did} terlalu panjang", path=db)
        from src.persona.editorial_memory import feedback_for_draft

        events = feedback_for_draft(did, path=db)
        assert events
        assert events[0]["reason"] == "terlalu panjang"

    def test_reject_preserves_history(self, flags_on, db):
        did = self._seed_draft(db)
        _run(_msg(OPERATOR, f"/reject {did} nada salah"),
             f"/reject {did} nada salah", path=db)
        with connect(db) as conn:
            row = conn.execute(
                "SELECT status, content FROM content_drafts WHERE id = ?", (did,)
            ).fetchone()
        assert row["status"] == "REJECTED"
        assert row["content"] == "uji"  # the draft body is not erased

    def test_revise_records_feedback_without_deleting(self, flags_on, db):
        did = self._seed_draft(db)
        _run(_msg(OPERATOR, f"/revise {did} tambah detail"),
             f"/revise {did} tambah detail", path=db)
        from src.persona.editorial_memory import feedback_for_draft

        events = feedback_for_draft(did, path=db)
        assert events[0]["reason"] == "tambah detail"
        with connect(db) as conn:
            row = conn.execute(
                "SELECT content FROM content_drafts WHERE id = ?", (did,)
            ).fetchone()
        assert row["content"] == "uji"

    def test_autopost_on_explains_the_env_flag(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/autopost on"),
                         "/autopost on", path=db)
        assert "AUTOPUBLISH_ENABLED" in result.text
        assert "tidak bisa mengubahnya" in result.text

    def test_autopost_requires_an_argument(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/autopost"), "/autopost", path=db)
        assert "Format" in result.text

    def test_autopost_does_not_flip_the_flag(self, flags_on, db):
        """A Telegram command must never enable live publishing."""
        _run(_msg(OPERATOR, "/autopost on"), "/autopost on", path=db)
        assert flags_on.flags.autopublish_enabled is False

    def test_rollback_reports_safety(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/rollback"), "/rollback", path=db)
        assert "rollback" in result.text.lower()
        assert "06-cutover-rollback" in result.text

    def test_mutations_are_audited(self, flags_on, db):
        from src.core.events import recent

        _run(_msg(OPERATOR, "/pause"), "/pause", path=db)
        events = recent(limit=10, path=db)
        assert any(e["action"] == "pause" for e in events)


# ── Chat scoping ────────────────────────────────────────────────────────────
class TestChatScoping:
    def test_foreign_group_is_ignored(self, flags_on, db):
        foreign = {"id": "-1009999999999", "type": "supergroup"}
        result, sent = _run(_msg(OPERATOR, "/status", chat=foreign),
                            "/status", path=db)
        assert result.handled is False
        assert sent == []

    def test_configured_group_is_accepted(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/status"), "/status", path=db)
        assert result.handled is True

    def test_private_dm_is_accepted(self, flags_on, db):
        dm = {"id": 111, "type": "private"}
        result, _ = _run(_msg(OPERATOR, "/status", chat=dm),
                         "/status", path=db)
        assert result.handled is True


# ── Robustness ──────────────────────────────────────────────────────────────
class TestRobustness:
    def test_sender_failure_does_not_break_the_command(self, flags_on, db):
        def boom(text_, mid):
            raise RuntimeError("telegram down")

        result = handle_v2_command(
            _msg(OPERATOR, "/status"), "/status", 1, 9, sender=boom,
            path=db, client=_fake_llm(),
        )
        assert result.handled is True

    def test_no_sender_still_returns_a_result(self, flags_on, db):
        result = handle_v2_command(
            _msg(OPERATOR, "/status"), "/status", 1, 9, sender=None,
            path=db, client=_fake_llm(),
        )
        assert result.handled is True
        assert result.text

    def test_result_is_serialisable(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, "/status"), "/status", path=db)
        assert isinstance(result.to_dict(), dict)

    def test_empty_text_is_not_handled(self, flags_on, db):
        result, _ = _run(_msg(OPERATOR, ""), "", path=db)
        assert result.handled is False
