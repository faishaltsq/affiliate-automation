"""tests/v2/test_seams_and_fail_closed.py — cryptographic content hash and publish validation seams.

Verifies requirement 9:
The scheduler, manual approval flow (/approve), and publisher cannot bypass
the final evaluated content version.
"""
import pytest
from src.bot.v2_commands import Actor, _cmd_approve
from src.content.quality_gate import (
    APPROVED_AUTO,
    NEEDS_REVIEW,
    REJECTED,
    REVISE_ONCE,
    compute_content_hash,
    evaluate_draft,
    validate_draft_for_publishing,
)
from src.content.writer import DraftResult, save_draft
from src.core.llm import FakeLLMClient
from src.core.storage import connect, migrate, transaction
from src.orchestration import publisher as pub
from src.orchestration import scheduler as sched


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


def _create_sample_draft(db, content="Konten pengujian kualitas suara dan bahasa.", status="DRAFT"):
    draft = DraftResult(
        content=content,
        parts=[],
        structure="single",
        pillar="practical_tips",
        angle="sudut pandang uji",
        audience_problem="masalah uji",
        persona_version="teman_tongkrongan_v1",
        variation={"format": "short_paragraph"},
    )
    draft_id = save_draft(draft, path=db)
    with connect(db) as conn:
        with transaction(conn):
            conn.execute("UPDATE content_drafts SET status = ? WHERE id = ?", (status, draft_id))
    return draft_id, content


class TestCryptographicPublishSeams:
    """Verifies that every path to publication requires exact sha256 evaluation match."""

    def test_tampered_content_fails_publish_validation(self, db):
        """If draft text is edited after evaluation, hash mismatch blocks publishing."""
        draft_id, content = _create_sample_draft(db, "Teks awal yang dievaluasi.")
        eval_dec = evaluate_draft(
            content,
            pillar="practical_tips",
            client=FakeLLMClient(default='{"persona_voice": 18, "audience_relevance": 18, "natural_writing": 18, "specific_useful_content": 18, "evidential_correctness": 14, "novelty": 4, "notes": "ok"}'),
            draft_id=draft_id,
            path=db,
            persist=True,
        )
        assert eval_dec.verdict == APPROVED_AUTO

        # Valid before tampering
        val1 = validate_draft_for_publishing(draft_id, review_required=False, path=db)
        assert val1.eligible is True

        # Tamper draft in database
        with connect(db) as conn:
            with transaction(conn):
                conn.execute("UPDATE content_drafts SET content = 'Teks diubah tanpa evaluasi!' WHERE id = ?", (draft_id,))

        val2 = validate_draft_for_publishing(draft_id, review_required=False, path=db)
        assert val2.eligible is False
        assert val2.reason == "content_hash_mismatch"

    def test_scheduler_refuses_publishing_tampered_draft(self, db, monkeypatch):
        """Scheduler _drain_queue drops and marks failed any job with tampered content."""
        import src.orchestration.scheduler as sched_mod
        import src.adapters.threads as threads_mod
        from src.adapters.threads import DryRunThreadsAdapter

        live_flags = type("F", (), {
            "live_publishing_allowed": True,
            "autopublish_enabled": True, "dry_run": False,
            "affiliate_auto_enabled": False, "shadow_mode": False,
            "v2_scheduler_enabled": True,
            "v2_telegram_commands_enabled": False,
        })()
        settings = type("S", (), {"flags": live_flags, "scheduler": None})()
        monkeypatch.setattr(sched_mod, "get_settings", lambda: settings)
        adapter = DryRunThreadsAdapter()
        monkeypatch.setattr(threads_mod, "get_threads_adapter", lambda: adapter)

        draft_id, content = _create_sample_draft(db, "Konten yang disetujui auto.", status="APPROVED")
        evaluate_draft(
            content,
            pillar="practical_tips",
            client=FakeLLMClient(default='{"persona_voice": 18, "audience_relevance": 18, "natural_writing": 18, "specific_useful_content": 18, "evidential_correctness": 14, "novelty": 4, "notes": "ok"}'),
            draft_id=draft_id,
            path=db,
            persist=True,
        )

        job_id, op_id, _ = pub.enqueue(draft_id, content, path=db)

        # Tamper draft
        with connect(db) as conn:
            with transaction(conn):
                conn.execute("UPDATE content_drafts SET content = 'Konten dibajak!' WHERE id = ?", (draft_id,))

        drained = sched._drain_queue(path=db, review_required=False)

        # Must not publish
        assert drained == 0
        assert len(adapter.calls) == 0
        job = pub.get_job(job_id, path=db)
        assert job.state == pub.FAILED
        assert "gate_veto: content_hash_mismatch" in job.last_error

    def test_manual_approve_command_refuses_tampered_draft(self, db):
        """Operator /approve command refuses draft when content changed after evaluation."""
        operator = Actor(user_id=12345, username="operator", chat_id="12345", is_private=True, display="Operator")
        draft_id, content = _create_sample_draft(db, "Konten butuh review.", status="DRAFT")

        evaluate_draft(
            content,
            pillar="opinions",
            client=FakeLLMClient(default='{"persona_voice": 15, "audience_relevance": 15, "natural_writing": 15, "specific_useful_content": 15, "evidential_correctness": 10, "novelty": 4, "notes": "ok"}'),
            draft_id=draft_id,
            path=db,
            persist=True,
        )

        # Tamper content
        with connect(db) as conn:
            with transaction(conn):
                conn.execute("UPDATE content_drafts SET content = 'Konten diganti diam-diam!' WHERE id = ?", (draft_id,))

        res = _cmd_approve(operator, str(draft_id), msg_id=1, path=db)
        assert "berubah sejak evaluasi terakhir" in res.text

        # Verify draft was NOT approved
        with connect(db) as conn:
            row = conn.execute("SELECT status FROM content_drafts WHERE id = ?", (draft_id,)).fetchone()
            assert row["status"] != "APPROVED"

    def test_manual_approve_command_refuses_rejected_and_revise_once(self, db):
        """Operator /approve command fails closed on REJECTED or REVISE_ONCE."""
        operator = Actor(user_id=12345, username="operator", chat_id="12345", is_private=True, display="Operator")

        # Rejected draft
        d_rej, c_rej = _create_sample_draft(db, "Klaim palsu 100% pasti cuan.")
        evaluate_draft(
            c_rej,
            pillar="opinions",
            client=FakeLLMClient(default='{"persona_voice": 5, "notes": "bad", "reason_codes": ["generic_ai_voice"]}'),
            draft_id=d_rej,
            path=db,
            persist=True,
        )
        res_rej = _cmd_approve(operator, str(d_rej), msg_id=1, path=db)
        assert "ditolak oleh gate" in res_rej.text or "veto" in res_rej.text.lower()

        # Revise once draft
        d_rev, c_rev = _create_sample_draft(db, "Draft butuh perbaikan alur.")
        evaluate_draft(
            c_rev,
            pillar="opinions",
            client=FakeLLMClient(default='{"persona_voice": 10, "audience_relevance": 10, "natural_writing": 10, "specific_useful_content": 10, "evidential_correctness": 10, "novelty": 2, "notes": "perlu revisi"}'),
            draft_id=d_rev,
            path=db,
            persist=True,
        )
        res_rev = _cmd_approve(operator, str(d_rev), msg_id=1, path=db)
        assert "REVISE_ONCE" in res_rev.text

    def test_publisher_publish_job_refuses_mismatched_content_hash(self, db):
        """Direct publish_job fails closed if job.content_hash != evaluated content_hash."""
        draft_id, content = _create_sample_draft(db, "Konten asli.", status="APPROVED")
        evaluate_draft(
            content,
            pillar="practical_tips",
            client=FakeLLMClient(default='{"persona_voice": 18, "audience_relevance": 18, "natural_writing": 18, "specific_useful_content": 18, "evidential_correctness": 14, "novelty": 4, "notes": "ok"}'),
            draft_id=draft_id,
            path=db,
            persist=True,
        )

        job_id, _, _ = pub.enqueue(draft_id, content, path=db)
        pub.claim_next_job(path=db)

        # Tamper draft in DB
        with connect(db) as conn:
            with transaction(conn):
                conn.execute("UPDATE content_drafts SET content = 'Teks di DB diubah!' WHERE id = ?", (draft_id,))

        fake_pub = pub.FakeThreadsPublisher([{"post_id": "threads_xyz"}])
        outcome = pub.publish_job(job_id, fake_pub, content=content, path=db)

        assert outcome.succeeded is False
        assert outcome.state == pub.FAILED
        assert "content_hash_mismatch" in outcome.error
        assert len(fake_pub.submits) == 0
