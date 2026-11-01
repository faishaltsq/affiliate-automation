"""tests/v2/test_feedback_wiring.py — acceptance criterion #14.

"Reject/revise feedback is preserved and represented in the next selection
context."

The feedback memory module was built and tested in Phase 6, but nothing read
it — a rejection was a Telegram message that scrolled away and the next draft
was generated from an identical prompt. These tests pin the wiring so it cannot
silently detach again.
"""
from __future__ import annotations

import json

import pytest

from src.content import writer as writer_mod
from src.content.writer import write_draft
from src.core.llm import FakeLLMClient
from src.core.storage import migrate
from src.persona.editorial_memory import (
    record_feedback, revision_guidance,
)
from src.persona.voice import VoiceVariation


@pytest.fixture(autouse=True)
def _clean_variation_history():
    writer_mod.reset_variation_history()
    yield
    writer_mod.reset_variation_history()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


@pytest.fixture()
def pinned_single(monkeypatch):
    """Pin a single-post variation so the draft is never a thread."""
    monkeypatch.setattr(
        writer_mod, "varied_variation",
        lambda history, rng=None: VoiceVariation(
            "single_snappy", "observasi", "pendek", "ringan", "edukasi"
        ),
    )


def _write(db, content="Kabel charger selalu kusut di tas."):
    fake = FakeLLMClient(default=json.dumps({"content": content}))
    out = write_draft(pillar="relatable", client=fake, path=db, persist=False)
    assert out is not None
    return fake.calls[0]["messages"][0]["content"]


class TestFeedbackReachesWriter:
    def test_no_feedback_means_no_memory_block(self, db, pinned_single):
        prompt = _write(db)
        assert "UMPAN BALIK OPERATOR" not in prompt

    def test_feedback_appears_in_the_next_prompt(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="kepanjangan banget", path=db)
        prompt = _write(db)
        assert "UMPAN BALIK OPERATOR" in prompt
        assert "kepanjangan banget" in prompt

    def test_category_guidance_is_included(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        path=db)
        prompt = _write(db)
        assert "lebih pendek" in prompt

    def test_unsourced_claim_feedback_is_included(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111",
                        category="unsourced_claim", path=db)
        prompt = _write(db)
        assert "angka" in prompt

    def test_multiple_categories_all_appear(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        path=db)
        record_feedback(draft_id=2, actor_id="111", category="voice_off",
                        path=db)
        record_feedback(draft_id=3, actor_id="111", category="clickbait",
                        path=db)
        prompt = _write(db)
        assert "lebih pendek" in prompt
        assert "Teman Tongkrongan" in prompt
        assert "hook misteri" in prompt

    def test_feedback_is_bounded_in_the_prompt(self, db, pinned_single):
        """A long history must not bloat the prompt indefinitely."""
        for i in range(60):
            record_feedback(draft_id=i, actor_id="111", category="too_long",
                            reason=f"alasan nomor {i}", path=db)
        prompt = _write(db)
        assert prompt.count("alasan nomor ") <= 5

    def test_revision_feedback_and_memory_coexist(self, db, pinned_single):
        """This draft's note and the rolling memory are separate blocks."""
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="riwayat kepanjangan", path=db)
        fake = FakeLLMClient(default=json.dumps({
            "content": "Kabel charger selalu kusut di tas."
        }))
        out = write_draft(
            pillar="relatable", revision_feedback="buat lebih hangat",
            client=fake, path=db, persist=False,
        )
        assert out is not None
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "REVISI DIMINTA OPERATOR" in prompt
        assert "buat lebih hangat" in prompt
        assert "UMPAN BALIK OPERATOR" in prompt
        assert "riwayat kepanjangan" in prompt

    def test_pruned_history_still_feeds_the_writer(self, db, pinned_single):
        from src.persona.editorial_memory import prune

        for i in range(40):
            record_feedback(draft_id=i, actor_id="111", category="voice_off",
                            path=db)
        prune(keep=10, path=db)
        prompt = _write(db)
        assert "UMPAN BALIK OPERATOR" in prompt

    def test_memory_uses_the_caller_database(self, db, pinned_single):
        """Feedback recorded in one DB must not leak from another."""
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="hanya di db ini", path=db)
        assert "hanya di db ini" in revision_guidance(path=db)
        # A different database has no such feedback.
        assert "hanya di db ini" not in revision_guidance(
            path=db.parent / "other.db"
        )

    def test_writer_still_produces_a_draft_with_feedback(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111", category="no_substance",
                        reason="kurang isi", path=db)
        out = write_draft(
            pillar="relatable",
            client=FakeLLMClient(default=json.dumps({
                "content": "Kabel charger selalu kusut di tas."
            })),
            path=db, persist=False,
        )
        assert out is not None
        assert out.content

    def test_feedback_does_not_block_generation_on_db_error(self, db, monkeypatch, pinned_single):
        """A broken memory store must never stop a draft from being written."""
        def boom(*a, **k):
            raise RuntimeError("db gone")

        monkeypatch.setattr(
            "src.persona.editorial_memory.revision_guidance", boom
        )
        out = write_draft(
            pillar="relatable",
            client=FakeLLMClient(default=json.dumps({
                "content": "Kabel charger selalu kusut di tas."
            })),
            path=db, persist=False,
        )
        assert out is not None


class TestFeedbackPreservation:
    def test_history_is_never_erased_by_a_new_draft(self, db, pinned_single):
        record_feedback(draft_id=1, actor_id="111", category="too_long",
                        reason="pertama", path=db)
        _write(db)
        _write(db)
        assert "pertama" in revision_guidance(path=db)

    def test_reject_reason_survives_publication(self, db, pinned_single):
        """A reason recorded against a draft stays after that draft ships."""
        record_feedback(draft_id=7, actor_id="111", category="off_brand",
                        reason="terlalu promosi", path=db)
        from src.persona.editorial_memory import feedback_for_draft

        _write(db)
        events = feedback_for_draft(7, path=db)
        assert len(events) == 1
        assert events[0]["reason"] == "terlalu promosi"

class TestEditorialMetadataAndDiversity:
    def test_save_draft_populates_editorial_metadata(self, db):
        from src.content.writer import save_draft, DraftResult
        from src.persona.editorial_memory import get_recent_editorial_metadata

        draft = DraftResult(
            content="Konten tes editorial",
            parts=[],
            structure="single",
            pillar="practical_tips",
            angle="langkah konkret",
            audience_problem="baterai boros",
            persona_version="2.0",
            variation={"opening": "observasi", "format": "single_snappy", "goal": "edukasi"},
            idea_id=1,
        )
        did = save_draft(draft, path=db)
        assert did > 0

        meta = get_recent_editorial_metadata(limit=1, path=db)
        assert len(meta) == 1
        assert meta[0]["opening"] == "observasi"
        assert meta[0]["pillar"] == "practical_tips"
        assert meta[0]["format"] == "single_snappy"

    def test_can_use_opening_blocks_consecutive_duplicates(self, db):
        from src.persona.editorial_memory import can_use_opening, record_draft_editorial_metadata
        from src.content.writer import save_draft, DraftResult

        for i in range(2):
            d = DraftResult(
                content=f"Draft {i}", parts=[], structure="single",
                pillar="relatable", angle="a", audience_problem="p",
                persona_version="2.0", variation={"opening": "observasi", "pillar": "relatable"},
                idea_id=i+1,
            )
            save_draft(d, path=db)

        # 2 consecutive 'observasi' openings should block a 3rd 'observasi'
        assert can_use_opening("observasi", limit=2, path=db) is False
        # Different opening is permitted
        assert can_use_opening("kontras", limit=2, path=db) is True

    def test_can_use_category_in_window_enforces_rotation(self, db):
        from src.persona.editorial_memory import can_use_category_in_window
        from src.content.writer import save_draft, DraftResult

        for i in range(2):
            d = DraftResult(
                content=f"Draft {i}", parts=[], structure="single",
                pillar="practical_tips", angle="a", audience_problem="p",
                persona_version="2.0", variation={"opening": f"op_{i}", "pillar": "practical_tips"},
                idea_id=i+1,
            )
            save_draft(d, path=db)

        # practical_tips appeared 2 times in 2 posts; max_in_window=2 should block a 3rd
        assert can_use_category_in_window("practical_tips", max_in_window=2, path=db) is False
        assert can_use_category_in_window("relatable", max_in_window=2, path=db) is True

    def test_new_critic_categories_in_feedback_guidance(self, db):
        record_feedback(draft_id=1, actor_id="111", category="forced_metaphor",
                        reason="personifikasi kabel nakal", path=db)
        record_feedback(draft_id=2, actor_id="111", category="generic_ai_voice",
                        reason="klise niatnya kenyataannya", path=db)
        guidance = revision_guidance(path=db)
        assert "personifikasi benda mati" in guidance
        assert "klise AI" in guidance
