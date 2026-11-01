"""tests/v2/test_sol_persona.py — Tests for Sol-Inspired Conversational Persona V2.

Verifies:
1. Persona versioning and Teman Tongkrongan account identity preservation.
2. Canonical positive and negative reference library examples.
3. Length-follows-substance guidelines and voice delivery flexibility.
4. Writer prompt integration with reference library and creative process.
5. Critic and quality gate enforcement on negative fixtures and positive drafts.
6. Scheduler integration with sol_inspired_conversational_v1 persona.
"""
from __future__ import annotations

import json
import pytest

from src.content.critic import CriticVerdict, Finding, critique
from src.content.quality_gate import (
    APPROVED_AUTO,
    NEEDS_REVIEW,
    REJECTED,
    REVISE_ONCE,
    V_AI_MOTIVATIONAL_CONCLUSION,
    V_ARTIFICIAL_THREAD_PADDING,
    V_FORCED_METAPHOR,
    V_SPELLING_TYPO,
    evaluate_draft,
)
from src.content.writer import _writer_prompt, write_draft
from src.core.llm import FakeLLMClient
from src.core.storage import connect, migrate
from src.orchestration.scheduler import gate_draft
from src.persona.profile import (
    PERSONA_VERSION,
    SOL_CONVERSATIONAL_SEED,
    SOL_CONVERSATIONAL_VERSION,
    activate_persona_version,
    get_active_persona,
    get_persona_version,
    get_sol_persona,
    persona_prompt_block,
)
from src.persona.reference_library import (
    NEGATIVE_EXAMPLES,
    POSITIVE_EXAMPLES,
    REFERENCE_LIBRARY_VERSION,
    ReferenceExample,
    get_editorial_reference_prompt,
    get_reference_examples,
)
from src.persona.voice import (
    VoiceVariation,
    length_guide,
    should_allow_thread,
)
from src.strategy.ideation import IdeaCandidate, generate_ideas


# ── 1. Persona Profile & Versioning Tests ────────────────────────────────────
def test_sol_persona_identity_and_version(tmp_path):
    db = str(tmp_path / "persona.db")
    sol = get_sol_persona()
    assert sol.version == SOL_CONVERSATIONAL_VERSION
    assert sol.version == "sol_inspired_conversational_v1"
    assert sol.name == "Teman Tongkrongan"
    assert sol.handle == "pochita9887"
    assert "Sol-Inspired Conversational" in sol.archetype
    assert len(sol.voice_rules) >= 10

    # Test database activation
    ok = activate_persona_version(SOL_CONVERSATIONAL_VERSION, path=db)
    assert ok is True
    active = get_active_persona(path=db)
    assert active.version == SOL_CONVERSATIONAL_VERSION
    assert active.name == "Teman Tongkrongan"

    # Switch back to original
    ok_orig = activate_persona_version(PERSONA_VERSION, path=db)
    assert ok_orig is True
    active_orig = get_active_persona(path=db)
    assert active_orig.version == PERSONA_VERSION

    # Retrieve specific versions
    v_sol = get_persona_version(SOL_CONVERSATIONAL_VERSION, path=db)
    assert v_sol is not None
    assert v_sol.version == SOL_CONVERSATIONAL_VERSION

    v_orig = get_persona_version(PERSONA_VERSION, path=db)
    assert v_orig is not None
    assert v_orig.version == PERSONA_VERSION


def test_sol_persona_prompt_block():
    sol = get_sol_persona()
    block = persona_prompt_block(sol)
    assert "Teman Tongkrongan (pochita9887)" in block
    assert "sol-inspired conversational" in block.lower()
    assert "Panjang tulisan mengikuti substansi" in block
    assert "DILARANG KERAS:" in block


# ── 2. Reference Library Tests ───────────────────────────────────────────────
def test_reference_library_positive_examples():
    pos = get_reference_examples(is_positive=True)
    assert len(pos) >= 6
    categories = {ex.category for ex in pos}
    assert "relatable" in categories
    assert "practical_tips" in categories
    assert "opinions" in categories
    assert "thoughtful_observations" in categories
    assert "discoveries" in categories
    assert "affiliate" in categories

    for ex in pos:
        assert ex.is_positive is True
        assert len(ex.content) > 20
        assert len(ex.why) > 10
        assert ex.version == REFERENCE_LIBRARY_VERSION


def test_reference_library_negative_examples():
    neg = get_reference_examples(is_positive=False)
    assert len(neg) >= 7
    ids = {ex.id for ex in neg}
    assert "neg_three_part_charger_thread" in ids
    assert "neg_typo_and_repetitive_powerbank" in ids
    assert "neg_forced_personification" in ids
    assert "neg_overexplaining_obvious" in ids
    assert "neg_ai_motivational_conclusion" in ids
    assert "neg_repetitive_joke_structure" in ids
    assert "neg_artificially_casual_slang" in ids

    # Check charger thread specific parts
    charger_thread = next(ex for ex in neg if ex.id == "neg_three_part_charger_thread")
    assert charger_thread.parts is not None
    assert len(charger_thread.parts) == 3
    assert "kabelnya nakal" in charger_thread.parts[1]


def test_reference_prompt_formatting():
    prompt = get_editorial_reference_prompt()
    assert "STANDAR EDITORIAL CONVERSATIONAL" in prompt
    assert "--- ACUAN GAYA POSITIF ---" in prompt
    assert "--- POLA BURUK YANG WAJIB DIHINDARI ---" in prompt
    assert "neg_three_part_charger_thread" not in prompt  # formatted nicely without raw id
    assert "Contoh Positif" in prompt
    assert "Contoh Buruk/Dilarang" in prompt


# ── 3. Voice Delivery & Substance Model Tests ────────────────────────────────
def test_length_guide_follows_substance():
    guide_short = length_guide("sangat_pendek")
    assert "Panjang mengikuti substansi" in guide_short
    assert "15-20 kata" in guide_short

    guide_medium = length_guide("sedang")
    assert "Panjang mengikuti substansi" in guide_medium
    assert "60-75 kata" in guide_medium


def test_thread_justification_rules():
    simple_idea = IdeaCandidate(
        pillar="relatable",
        angle="observasi santai",
        audience_problem="Kabel di tas suka kusut",
    )
    # Simple observation must NEVER be allowed as a thread
    assert should_allow_thread(simple_idea, evidence="", pillar="relatable") is False

    # Multi-point idea with verified evidence can be considered
    dense_idea = IdeaCandidate(
        pillar="practical_tips",
        angle="perbandingan teknis kabel GaN",
        audience_problem="Perbedaan protokol pengisian daya cepat USB-PD dan PPS",
    )
    evidence = (
        "- Protokol USB-PD 3.0 mendukung daya hingga 100W dengan voltase tetap.\n"
        "- Protokol PPS menyesuaikan voltase per 20mV untuk menjaga suhu baterai tetap dingin."
    )
    # With verified dense evidence and tips pillar
    allowed = should_allow_thread(dense_idea, evidence=evidence, pillar="practical_tips")
    assert isinstance(allowed, bool)


# ── 4. Writer Integration Tests ──────────────────────────────────────────────
def test_writer_prompt_contains_reference_library_and_creative_flow(tmp_path):
    db = str(tmp_path / "writer.db")
    sol = get_sol_persona()
    idea = IdeaCandidate(
        pillar="relatable",
        angle="observasi kebiasaan yang jarang disadari",
        audience_problem="Beli tablet buat kerja malah buat nonton",
    )
    variation = VoiceVariation(format="single_snappy", length="pendek")
    prompt = _writer_prompt(
        idea, None, variation, evidence="", path=db, persona=sol
    )
    assert "PROSES KREATIF EDITORIAL (Sol-Inspired Conversational):" in prompt
    assert "STANDAR EDITORIAL CONVERSATIONAL" in prompt
    assert "Panjang Mengikuti Substansi" in prompt
    assert "Uji Baca Bersuara" in prompt


def test_writer_prompt_bans_rhetorical_question_openers(tmp_path):
    """A rhetorical opener delays the point and reads like an ad (observed live)."""
    db = str(tmp_path / "writer_rhetorical.db")
    idea = IdeaCandidate(
        pillar="discoveries",
        angle="tethering bawaan sudah cukup",
        audience_problem="Pasang aplikasi tambahan cuma buat berbagi koneksi",
    )
    variation = VoiceVariation(format="single_snappy", length="pendek")
    prompt = _writer_prompt(
        idea, None, variation, evidence="", path=db, persona=get_sol_persona()
    )
    assert "pertanyaan retoris" in prompt
    assert "Kenapa masih" in prompt


def test_writer_prompt_requires_affiliate_disclosure(tmp_path):
    """An affiliate draft must ship its own disclosure text, not rely on the gate."""
    from src.commerce.disclosure import DISCLOSURE_SHORT

    db = str(tmp_path / "writer_affiliate.db")
    affiliate = IdeaCandidate(
        pillar="affiliate",
        angle="satu adaptor untuk dua perangkat",
        audience_problem="Bawa charger laptop dan HP terpisah",
    )
    organic = IdeaCandidate(
        pillar="relatable",
        angle="kabel kusut di tas",
        audience_problem="Kabel charger selalu kusut",
    )
    variation = VoiceVariation(format="single_paragraph", length="sedang")

    aff_prompt = _writer_prompt(
        affiliate, None, variation,
        evidence="Produk: Anker Nano II 65W. Harga terverifikasi: Rp449.000.",
        path=db, persona=get_sol_persona(),
    )
    org_prompt = _writer_prompt(
        organic, None, variation, evidence="", path=db, persona=get_sol_persona()
    )

    assert DISCLOSURE_SHORT in aff_prompt
    assert "KEWAJIBAN AFILIASI" in aff_prompt
    assert "KEWAJIBAN AFILIASI" not in org_prompt



def test_write_draft_with_sol_persona(tmp_path):
    db = str(tmp_path / "writer_draft.db")
    migrate(db)
    sol = get_sol_persona()
    fake_payload = json.dumps({
        "content": (
            "Niat beli tablet buat produktivitas pas kerja di kafe, ujung-ujungnya "
            "cuma jadi layar kedua buat nonton sambil scroll HP. "
            "Ada kenyamanan aneh waktu sadar kita nggak sendirian ngalamin ini."
        ),
        "disclosure": "",
    })
    client = FakeLLMClient(default=fake_payload)
    draft = write_draft(
        pillar="relatable",
        client=client,
        path=db,
        persona=sol,
    )
    assert draft is not None
    assert "Niat beli tablet" in draft.content
    assert draft.pillar == "relatable"


# ── 5. Critic & Quality Gate Enforcement Tests ───────────────────────────────
def test_quality_gate_rejects_charger_thread(tmp_path):
    db = str(tmp_path / "gate.db")
    charger_thread = next(ex for ex in NEGATIVE_EXAMPLES if ex.id == "neg_three_part_charger_thread")
    # Mechanical hard veto for thread padding and dangerous phrasing
    decision = evaluate_draft(
        charger_thread.content,
        parts=charger_thread.parts,
        pillar="practical_tips",
        require_critic=False,
        path=db,
    )
    assert decision.verdict == REJECTED
    assert any(
        v in (V_ARTIFICIAL_THREAD_PADDING, V_FORCED_METAPHOR, "generic_filler")
        for v in decision.vetoes + decision.reason_codes
    )


def test_quality_gate_catches_typo_powerbank(tmp_path):
    db = str(tmp_path / "gate_typo.db")
    typo_draft = next(ex for ex in NEGATIVE_EXAMPLES if ex.id == "neg_typo_and_repetitive_powerbank")

    # Mock critic returning score 88 but finding MAJOR typo "Sebagnya"
    critic_resp = json.dumps({
        "persona_voice": 18,
        "audience_relevance": 18,
        "natural_writing": 16,
        "specific_useful_content": 18,
        "evidential_correctness": 13,
        "novelty": 5,
        "findings": [
            {
                "code": "spelling_typo",
                "severity": "MAJOR",
                "evidence": "Sebagnya hilang jadi panas",
                "suggested_fix": "Ganti 'Sebagnya' menjadi 'Sebagian'",
            }
        ],
        "concerns": ["Ada kata salah ketik 'Sebagnya'."],
        "strengths": ["Menjelaskan efisiensi daya."],
        "notes": "Typo nyata pada kata 'Sebagnya'.",
    })
    client = FakeLLMClient(default=critic_resp)
    decision = evaluate_draft(
        typo_draft.content,
        pillar="practical_tips",
        client=client,
        path=db,
    )
    # Must NOT be APPROVED_AUTO despite 88 raw score!
    assert decision.verdict == REVISE_ONCE
    assert decision.total_score <= 68.0
    assert any(f.get("code") == V_SPELLING_TYPO for f in decision.findings)


def test_quality_gate_rejects_ai_motivational_platitude(tmp_path):
    db = str(tmp_path / "gate_platitude.db")
    platitude = (
        "Setiap perangkat punya kelebihan dan kekurangan masing-masing. "
        "Gunakan teknologi dengan bijak agar harimu semakin produktif. "
        "Semangat menjalani aktivitas hari ini kawan-kawan!"
    )
    decision = evaluate_draft(
        platitude,
        pillar="opinions",
        require_critic=False,
        path=db,
    )
    # Caught by deterministic filler/platitude pattern
    assert decision.verdict == REJECTED


def test_clean_sol_draft_passes_quality_gate(tmp_path):
    db = str(tmp_path / "gate_clean.db")
    clean_draft = (
        "Port Type-C kerasa longgar bukan selalu berarti konektornya rusak. "
        "Seringnya cuma serat kain dari saku celana yang memadat di dalem. "
        "Coba bersihin pelan-pelan pakai tusuk gigi kayu kering pas HP mati. "
        "Biasanya langsung seret lagi tanpa perlu ganti port."
    )
    critic_resp = json.dumps({
        "persona_voice": 19,
        "audience_relevance": 19,
        "natural_writing": 19,
        "specific_useful_content": 19,
        "evidential_correctness": 14,
        "novelty": 4,
        "findings": [],
        "concerns": [],
        "strengths": ["Sangat praktis dan mengalir alami."],
        "notes": "Tulisan bersih dan sangat membumi.",
    })
    client = FakeLLMClient(default=critic_resp)
    decision = evaluate_draft(
        clean_draft,
        pillar="practical_tips",
        client=client,
        path=db,
    )
    assert decision.verdict == APPROVED_AUTO
    assert decision.total_score >= 85.0
    assert len(decision.vetoes) == 0


# ── 6. Scheduler Integration Tests ───────────────────────────────────────────
def test_scheduler_gate_draft_supports_sol_persona(tmp_path):
    db = str(tmp_path / "sched.db")
    migrate(db)
    sol = get_sol_persona()

    class MockDraft:
        content = (
            "Adaptor GaN 65W dua port itu investasi paling masuk akal kalau sering bawa "
            "laptop dan HP barengan. Bobotnya sepertiga charger lama dan hemat colokan."
        )
        parts = None
        evidence = ""
        pillar = "practical_tips"
        draft_id = 1
        variation = {"format": "single_snappy"}

    critic_resp = json.dumps({
        "persona_voice": 18,
        "audience_relevance": 18,
        "natural_writing": 18,
        "specific_useful_content": 18,
        "evidential_correctness": 14,
        "novelty": 4,
        "findings": [],
        "concerns": [],
        "strengths": ["Ringkas dan substantif."],
        "notes": "Baik.",
    })
    client = FakeLLMClient(default=critic_resp)
    decision = gate_draft(MockDraft(), path=db, client=client, persona=sol)
    assert decision.verdict == APPROVED_AUTO
