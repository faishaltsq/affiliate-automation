"""tests/v2/test_negative_editorial_fixtures.py — Permanent negative regression fixtures.

Captures real and synthetic failure cases that violate editorial quality,
natural voice, format density, and safety rules.

Covers the reproduction case:
- Draft 7 published to Threads (@ranaraaa911/post/DfHhaebn3kU):
  A 3-part thread about charger cable wear artificially stretched from a single
  trivial observation with forced metaphors ("kabelnya nakal") and unsafe
  practical advice ("benang tembaga butuh sedikit perhatian").
"""
from __future__ import annotations

import pytest

from src.content.critic import CriticVerdict, critique
from src.content.quality_gate import (
    APPROVED_AUTO,
    NEEDS_REVIEW,
    REJECTED,
    REVISE_ONCE,
    V_ARTIFICIAL_THREAD_PADDING,
    V_FORCED_METAPHOR,
    V_GENERIC_AI_VOICE,
    V_REDUNDANT_PARTS,
    V_UNNATURAL_INDONESIAN,
    V_UNSAFE_PRACTICAL_ADVICE,
    evaluate_draft,
    hard_vetoes,
)
from src.core.llm import FakeLLMClient
from src.core.storage import migrate


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


# The exact reproduction text published live to Threads (post ID 18104878811294956)
CHARGER_REPRO_PARTS = [
    "Kabel charger di tas tuh niatnya biar praktis. Kenyataannya, dia lebih sering balik rumah dalam keadaan kusut dan ujungnya udah mulai rusak.",
    "Putusnya bukan karena kabelnya nakal. Biasanya karena sehari-hari nyelip sama barang lain yang lebih keras.",
    "Pas ngecek isi tas, sekalian liat ujung kabel. Kalau udah mulai keliatan ada benang tembaga, itu tandanya dia butuh sedikit perhatian.",
]

CHARGER_REPRO_TEXT = "\n\n---\n\n".join(CHARGER_REPRO_PARTS)


class TestReproductionCaseNegativeFixture:
    """The charger cable 3-part thread that was published live must NEVER pass."""

    def test_charger_repro_hard_vetoes_fire(self):
        """Mechanical checks must flag forced metaphor, unsafe advice, and thread padding."""
        vetoes = hard_vetoes(
            CHARGER_REPRO_TEXT,
            parts=CHARGER_REPRO_PARTS,
            pillar="relatable",
        )
        assert V_FORCED_METAPHOR in vetoes, f"Expected forced_metaphor in {vetoes}"
        assert V_UNSAFE_PRACTICAL_ADVICE in vetoes, f"Expected unsafe_practical_advice in {vetoes}"
        assert (
            V_ARTIFICIAL_THREAD_PADDING in vetoes or V_REDUNDANT_PARTS in vetoes
        ), f"Expected thread padding/redundancy in {vetoes}"

    def test_charger_repro_gate_evaluation_rejects(self, db):
        """Gate must reject the reproduction draft even with a lenient or fake critic."""
        decision = evaluate_draft(
            CHARGER_REPRO_TEXT,
            parts=CHARGER_REPRO_PARTS,
            pillar="relatable",
            client=FakeLLMClient(
                default='{"persona_voice": 5, "audience_relevance": 10, "natural_writing": 8, '
                '"specific_useful_content": 5, "evidential_correctness": 10, "novelty": 2, '
                '"notes": "Padding artifisial, metafora aneh kabel nakal, saran bahaya kawat terbuka", '
                '"concerns": ["kabel nakal metafora aneh", "benang tembaga bahaya arus pendek"], '
                '"reason_codes": ["forced_metaphor", "artificial_thread_padding", "unsafe_practical_advice", "unnatural_indonesian"]}'
            ),
            path=db,
            persist=False,
        )
        assert decision.verdict == REJECTED
        assert not decision.passed
        assert V_FORCED_METAPHOR in decision.vetoes or V_FORCED_METAPHOR in decision.reason_codes
        assert (
            V_UNSAFE_PRACTICAL_ADVICE in decision.vetoes
            or V_UNSAFE_PRACTICAL_ADVICE in decision.reason_codes
        )

    def test_charger_repro_cannot_be_approved_auto(self, db):
        """Even if critic returned 95, hard vetoes must prevent auto-approval."""
        decision = evaluate_draft(
            CHARGER_REPRO_TEXT,
            parts=CHARGER_REPRO_PARTS,
            pillar="relatable",
            client=FakeLLMClient(
                default='{"persona_voice": 20, "audience_relevance": 20, "natural_writing": 20, '
                '"specific_useful_content": 20, "evidential_correctness": 15, "novelty": 5, '
                '"notes": "Terlalu memuji tanpa sadar bahaya", "concerns": []}'
            ),
            path=db,
            persist=False,
        )
        assert decision.verdict == REJECTED
        assert decision.passed is False


class TestAdditionalEditorialNegativeFixtures:
    """Other unacceptable output patterns that must fail before publication."""

    def test_forced_anthropomorphic_metaphor(self):
        text = "Power bank gue kayaknya lagi ngambek dan punya perasaan, seharian nggak mau ngisi daya."
        vetoes = hard_vetoes(text, pillar="relatable")
        assert V_FORCED_METAPHOR in vetoes

    def test_unsafe_exposed_wire_advice(self):
        text = "Kalau kawat tembaga kabel charger udah keliatan terkelupas, cukup lakban aja biar hemat."
        vetoes = hard_vetoes(text, pillar="practical_tips")
        assert V_UNSAFE_PRACTICAL_ADVICE in vetoes

    def test_generic_ai_voice_opening(self):
        text = "Di era digital saat ini, tidak dapat dipungkiri bahwa smartphone sangat penting bagi kita semua."
        vetoes = hard_vetoes(text, pillar="opinions")
        assert V_GENERIC_AI_VOICE in vetoes

    def test_thread_padding_on_trivial_observation(self):
        parts = [
            "Mousepad di meja rasanya sempit banget kalau kerja seharian.",
            "Bukan karena mousenya nakal, tapi karena barang di meja makin numpuk.",
            "Coba rapikan sedikit biar tangan lebih leluasa.",
        ]
        text = "\n\n---\n\n".join(parts)
        vetoes = hard_vetoes(text, parts=parts, pillar="relatable")
        assert V_ARTIFICIAL_THREAD_PADDING in vetoes or V_FORCED_METAPHOR in vetoes

POWER_BANK_DEFECT_DRAFT = (
    "Kapasitas power bank di kotak tuh bukan jaminan berapa banyak daya yang benar-benar masuk ke HP. "
    "Sebagnya hilang jadi panas dan proses konversi. Kesalahan umum: langsung tergiur yang paling gede tanpa mikir pemakaian nyata sehari-hari. "
    "Padahal kalau nggak sering keluar rumah, bawa yang terlalu besar cuma bikin tas makin berat. Mending pilih yang ukurannya wajar dan pas di saku. "
    "Kapasitas besar nggak ada gunanya kalau power bank-nya sendiri sering ditinggal di rumah."
)


class TestPowerBankDefectRegressionFixture:
    """Exact regression fixture: power bank draft with typo 'Sebagnya', format mismatch, etc.

    It previously received APPROVED_AUTO with score 88 despite defects.
    It must NEVER receive APPROVED_AUTO while reported defects remain.
    """

    def test_power_bank_defect_draft_blocked_from_auto_approval_with_critic_score_88(self, db):
        """Even if raw critic score is 88, major defects (typo, format mismatch) must force REVISE_ONCE."""
        # Critic reports score 88 with findings/concerns highlighting the typo and format mismatch
        critic_json = (
            '{"persona_voice": 18, "audience_relevance": 18, "natural_writing": 16, '
            '"specific_useful_content": 18, "evidential_correctness": 14, "novelty": 4, '
            '"notes": "Ada typo Sebagnya dan pembuka agak berbelit-belit. Format deklarasi multi_paragraph tapi hanya 1 paragraf.", '
            '"concerns": ["Typo Sebagnya", "Format declared multi_paragraph but only 1 paragraph", "Convoluted opening"], '
            '"findings": ['
            '  {"code": "spelling_typo", "severity": "MAJOR", "evidence": "Sebagnya", "suggested_fix": "Ganti jadi Sebagiannya"}, '
            '  {"code": "format_mismatch", "severity": "MAJOR", "evidence": "Single paragraph text", "suggested_fix": "Bagi jadi 2 paragraf"} '
            '], '
            '"reason_codes": ["spelling_typo", "format_mismatch"]}'
        )

        decision = evaluate_draft(
            POWER_BANK_DEFECT_DRAFT,
            pillar="opinions",
            format_name="multi_paragraph",
            client=FakeLLMClient(default=critic_json),
            path=db,
            persist=False,
        )

        # Inviolable requirement: MUST NOT be APPROVED_AUTO
        assert decision.verdict != APPROVED_AUTO, f"Defective draft received {decision.verdict}!"
        assert decision.verdict == REVISE_ONCE
        assert decision.total_score <= 70.0  # Capped due to major defect
        assert decision.revision_allowed is True
        codes = [f["code"] for f in decision.findings]
        assert "spelling_typo" in codes
        assert "format_mismatch" in codes

    def test_power_bank_mechanical_backstop_blocks_auto_even_with_blind_critic(self, db):
        """Mechanical backstops must detect 'Sebagnya' and format mismatch even if model didn't."""
        blind_critic_json = (
            '{"persona_voice": 18, "audience_relevance": 18, "natural_writing": 18, '
            '"specific_useful_content": 17, "evidential_correctness": 13, "novelty": 4, '
            '"notes": "Bagus banget", "concerns": [], "findings": [], "reason_codes": []}'
        )
        decision = evaluate_draft(
            POWER_BANK_DEFECT_DRAFT,
            pillar="opinions",
            format_name="multi_paragraph",
            client=FakeLLMClient(default=blind_critic_json),
            path=db,
            persist=False,
        )
        assert decision.verdict != APPROVED_AUTO
        assert decision.verdict == REVISE_ONCE
        codes = [f["code"] for f in decision.findings]
        assert "spelling_typo" in codes

    def test_editorial_pass_cleans_power_bank_draft(self):
        """The pre-evaluation editor must correct 'Sebagnya', compress redundant conclusion, and normalize format."""
        from src.content.editor import edit_and_clean_draft

        cleaned, parts, eff_format, edits = edit_and_clean_draft(
            POWER_BANK_DEFECT_DRAFT,
            intended_format="multi_paragraph",
        )
        assert "Sebagnya" not in cleaned
        assert "sebagiannya" in cleaned.lower()
        # Generic conclusion removed or compressed
        assert "sering ditinggal di rumah" not in cleaned or eff_format in ("short_paragraph", "multi_paragraph")
        assert any(e.get("reason") == "spelling_typo" or e.get("code") == "format_normalized" for e in edits)
