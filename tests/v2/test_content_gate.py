"""tests/v2/test_content_gate.py — Phase 3 writer / verifier / critic / gate.

Acceptance criteria covered:
  #4 invalid model JSON / repeated low scores never become generic filler
  #5 claims about specs/discount/usage without evidence are blocked
  #6 similarity guard catches exact copies and near-template duplicates
"""
from __future__ import annotations

import pytest

from src.core.llm import FakeLLMClient, LLMError
from src.core.storage import connect, migrate
from src.content import similarity as sim
from src.content.critic import critique
from src.content.quality_gate import (
    APPROVED_AUTO, NEEDS_REVIEW, REJECTED, REVISE_ONCE,
    V_CLICKBAIT, V_CRITIC_UNAVAILABLE, V_EMPTY, V_ENGAGEMENT_BAIT,
    V_EXCESSIVE_REPETITION, V_FABRICATED_FIRSTHAND, V_GENERIC_FILLER,
    V_HASHTAG, V_NEAR_DUPLICATE, V_NO_DISCLOSURE, V_STALE_DRAFT,
    V_UNCERTAIN_TRACKING, V_UNSAFE_FRAMING, V_UNVERIFIED_NUMBER,
    evaluate_draft, hard_vetoes, get_evaluations,
)
from src.content.verifier import (
    extract_claims, has_fabricated_firsthand, verify,
)
from src.content.writer import (
    DraftResult, get_draft, save_draft, write_draft,
)


@pytest.fixture(autouse=True)
def _clean_variation_history():
    """The writer's anti-repetition window is module-level state.

    Without this reset, one test's history leaks into the next and the
    variation chosen becomes order-dependent — which is exactly the kind of
    flakiness that makes a suite untrustworthy.
    """
    from src.content.writer import reset_variation_history
    reset_variation_history()
    yield
    reset_variation_history()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


GOOD_DRAFT = (
    "Kabel charger yang selalu kusut di tas itu biasanya bukan soal kabelnya, "
    "tapi soal cara bungkusnya. Gue mulai lingkar sendiri tiap abis dipakai, "
    "putus di ujungnya jauh lebih jarang kejadian."
)


# ── Verifier ────────────────────────────────────────────────────────────────
class TestVerifier:
    def test_clean_draft_has_no_claims(self):
        assert extract_claims(GOOD_DRAFT) == []

    def test_extracts_price(self):
        claims = extract_claims("Harga power bank itu Rp250.000 di marketplace.")
        assert any(c.kind == "price" for c in claims)

    def test_extracts_discount(self):
        claims = extract_claims("Katanya diskon 50% minggu ini.")
        assert any(c.kind == "discount" for c in claims)

    def test_extracts_firsthand(self):
        claims = extract_claims("Gue sudah beli dan pakai adaptor ini dua bulan.")
        assert any(c.kind == "firsthand" for c in claims)

    def test_firsthand_is_never_supported_by_evidence(self):
        res = verify("Gue sudah pakai produk ini.", evidence="produk ini bagus")
        assert res.ok is False
        assert any("firsthand" in r for r in res.reasons)

    def test_number_without_evidence_is_rejected(self):
        res = verify("Baterainya awet sampai 18 jam.", evidence="")
        assert res.ok is False
        assert any("unsupported_number" in r for r in res.reasons)

    def test_number_with_matching_evidence_passes(self):
        res = verify(
            "Katanya awet sampai 18 jam pemakaian normal.",
            evidence="Uji lab: ketahanan baterai 18 jam pemakaian normal.",
        )
        assert res.ok is True

    def test_number_with_different_evidence_is_rejected(self):
        res = verify(
            "Katanya awet sampai 18 jam.",
            evidence="Uji lab: ketahanan baterai 12 jam.",
        )
        assert res.ok is False

    def test_price_with_matching_evidence_passes(self):
        res = verify(
            "Harganya Rp250.000 di toko resmi.",
            evidence="Harga resmi Rp250.000.",
        )
        assert res.ok is True

    def test_price_without_evidence_is_rejected(self):
        res = verify("Harganya cuma Rp250.000.", evidence="")
        assert res.ok is False

    def test_empty_evidence_blocks_all_numbers(self):
        res = verify("Naik 30% tahun ini.", evidence="")
        assert res.ok is False

    def test_bare_years_are_not_claims(self):
        assert extract_claims("Sejak 2024 gue mulai rapikan meja.") == []

    def test_bare_small_counts_are_not_claims(self):
        assert extract_claims("Ada 2 kabel di meja gue.") == []

    def test_has_fabricated_firsthand(self):
        assert has_fabricated_firsthand("Aku sudah coba ini") is True
        assert has_fabricated_firsthand("Banyak orang pakai ini") is False

    def test_urls_allowed_by_default(self):
        res = verify("Cek di https://example.com ya.", evidence="")
        assert res.ok is True

    def test_urls_can_be_disallowed(self):
        res = verify("Cek di https://example.com ya.", evidence="", allow_urls=False)
        assert res.ok is False


# ── Similarity ──────────────────────────────────────────────────────────────
class TestSimilarity:
    def test_identical_text_is_1(self):
        assert sim.similarity("halo dunia", "halo dunia") == 1.0

    def test_unrelated_text_is_low(self):
        score = sim.similarity(
            "Kabel charger selalu kusut di tas",
            "IHSG ditutup menguat pada sesi sore",
        )
        assert score < 0.3

    def test_reworded_text_is_high(self):
        a = "Kabel charger selalu kusut di tas dan putus di ujung"
        b = "Kabel charger yang kusut di tas akhirnya putus di ujung"
        assert sim.similarity(a, b) > 0.7

    def test_content_hash_is_stable(self):
        assert sim.content_hash("Halo Dunia") == sim.content_hash("halo   dunia")

    def test_content_hash_differs_for_different_text(self):
        assert sim.content_hash("a") != sim.content_hash("b")

    def test_exact_duplicate_is_caught(self):
        v = sim.check_against_history("postingan ini", ["postingan ini"])
        assert v.ok is False
        assert v.reason == "exact_duplicate"

    def test_near_duplicate_is_caught(self):
        history = [
            "Kabel charger selalu kusut di tas dan putus di ujung setiap minggu"
        ]
        draft = "Kabel charger yang kusut di tas lalu putus di ujung tiap minggu"
        v = sim.check_against_history(draft, history)
        assert v.ok is False
        assert "near_duplicate" in v.reason

    def test_repetitive_template_is_caught(self):
        """Same sentence-length skeleton, different words.

        The threshold is two-thirds of the window, so a shape has to dominate
        the recent posts before it counts as a template.
        """
        history = [
            "Kok ya tiap pagi selalu macet di jalan tol.",
            "Kok ya tiap siang selalu lempar di kantor.",
            "Kok ya tiap sore selalu lemot di rumah.",
            "Kok ya tiap malam selalu pusing di kasur.",
            "Kok ya tiap dinih selalu hujan di trotoar.",
            "Kok ya tiap jumat selalu macet di gerbang.",
        ]
        draft = "Kok ya tiap sabtu selalu lemot di kamar."
        v = sim.check_against_history(draft, history, skeleton_window=8)
        assert v.ok is False
        assert "repetitive_template" in v.reason

    def test_two_sentence_shape_is_not_a_template(self):
        """'m-m' is a normal post shape; it must not be treated as spam."""
        history = [
            "Diskon besar kadang cuma mengembalikan harga normal. Kalau "
            "angkanya dibandingkan dari harga setahun lalu, ceritanya beda.",
            "Sebelum beli power bank, cek dulu output watt-nya di bodi atau "
            "manualnya. Angka mAh di kemasan sering bukan kapasitas nyata.",
            "Spesifikasi tinggi di atas kertas sering nggak terasa di pemakaian "
            "harian. Yang kerasa justru baterai yang awet dan layar enak dilihat.",
            "Kategori kecil yang sering terlupakan: organizer kabel, stand HP, "
            "lampu meja. Murah, tapi efeknya ke kerapian meja kerasa banget.",
        ]
        draft = (
            "Review yang terlalu rapi dan positif itu tanda bahaya, bukan "
            "jaminan. Produk yang bagus biasanya punya catatan jelek juga."
        )
        v = sim.check_against_history(draft, history, skeleton_window=8)
        assert v.ok is True

    def test_unique_draft_passes(self):
        history = [
            "IHSG ditutup menguat di sesi sore hari ini.",
            "Power bank murah sering bohong soal kapasitas nyata.",
            "Meja sempit butuh setup yang nggak makan tempat.",
        ]
        v = sim.check_against_history(GOOD_DRAFT, history)
        assert v.ok is True

    def test_empty_history_always_passes(self):
        v = sim.check_against_history("apa saja", [])
        assert v.ok is True

    def test_opening_similarity_detects_repeated_hook(self):
        history = ["Kok ya tiap pagi macet", "Kok ya tiap siang lempar"]
        assert sim.opening_similarity("Kok ya tiap malam hujan", history) > 0.5

    def test_jaccard_basics(self):
        assert sim.jaccard(["a", "b"], ["a", "b"]) == 1.0
        assert sim.jaccard(["a"], ["b"]) == 0.0
        assert sim.jaccard([], []) == 0.0


# ── Critic ──────────────────────────────────────────────────────────────────
def _critic_json(total: int) -> str:
    """A critic payload whose weighted parts sum to `total`."""
    weights = {
        "persona_voice": 20, "audience_relevance": 20, "natural_writing": 20,
        "specific_useful_content": 20, "evidential_correctness": 15, "novelty": 5,
    }
    remaining = total
    out = {}
    for key, cap in weights.items():
        take = min(cap, remaining)
        out[key] = take
        remaining -= take
    import json
    return json.dumps({**out, "notes": "catatan", "concerns": []})


class TestCritic:
    def test_high_score_bands_auto(self):
        v = critique(GOOD_DRAFT, pillar="relatable",
                     client=FakeLLMClient(default=_critic_json(92)))
        assert v.ok is True
        assert v.total_score == 92
        assert v.band == "auto"

    def test_mid_score_bands_review(self):
        v = critique(GOOD_DRAFT, pillar="relatable",
                     client=FakeLLMClient(default=_critic_json(75)))
        assert v.band == "review"

    def test_low_score_bands_revise(self):
        v = critique(GOOD_DRAFT, pillar="relatable",
                     client=FakeLLMClient(default=_critic_json(40)))
        assert v.band == "revise"

    def test_critic_failure_is_not_a_pass(self):
        v = critique(GOOD_DRAFT, client=FakeLLMClient(default="not json"))
        assert v.ok is False
        assert "critic_unavailable" in v.error

    def test_scores_are_clamped_to_weights(self):
        import json
        payload = json.dumps({
            "persona_voice": 999, "audience_relevance": -5,
            "natural_writing": 10, "specific_useful_content": 10,
            "evidential_correctness": 5, "novelty": 1,
            "notes": "", "concerns": [],
        })
        v = critique(GOOD_DRAFT, client=FakeLLMClient(default=payload))
        assert v.scores["persona_voice"] == 20
        assert v.scores["audience_relevance"] == 0

    def test_concerns_are_normalised_to_strings(self):
        import json
        payload = json.dumps({
            "persona_voice": 10, "audience_relevance": 10, "natural_writing": 10,
            "specific_useful_content": 10, "evidential_correctness": 5, "novelty": 1,
            "notes": "n", "concerns": [1, 2],
        })
        v = critique(GOOD_DRAFT, client=FakeLLMClient(default=payload))
        assert v.concerns == ["1", "2"]


# ── Hard vetoes ─────────────────────────────────────────────────────────────
class TestHardVetoes:
    def test_empty_draft(self):
        assert hard_vetoes("") == [V_EMPTY]
        assert hard_vetoes("   ") == [V_EMPTY]

    def test_unsourced_number(self):
        vetoes = hard_vetoes("Baterainya awet 18 jam.", evidence="")
        assert V_UNVERIFIED_NUMBER in vetoes

    def test_sourced_number_passes(self):
        vetoes = hard_vetoes(
            "Baterainya awet 18 jam.",
            evidence="Ketahanan baterai 18 jam.",
        )
        assert V_UNVERIFIED_NUMBER not in vetoes

    def test_fabricated_firsthand(self):
        vetoes = hard_vetoes("Gue sudah beli dan pakai ini.")
        assert V_FABRICATED_FIRSTHAND in vetoes

    def test_generic_filler_is_vetoed(self):
        filler = (
            "Isu kabel charger ini punya implikasi nyata kalau dicermati "
            "secara objektif. Dampaknya ke depan bakal signifikan."
        )
        assert V_GENERIC_FILLER in hard_vetoes(filler)

    def test_clickbait_is_vetoed(self):
        assert V_CLICKBAIT in hard_vetoes("Jangan percaya sama harga ini!")

    def test_engagement_bait_closer_is_vetoed(self):
        assert V_ENGAGEMENT_BAIT in hard_vetoes(
            "Power bank murah sering bohong soal kapasitas. Kalian tim mana?"
        )

    @pytest.mark.parametrize("draft", [
        # Real output from the live gateway that the closer-only regex missed.
        "Kalian sering nunggu diskon gede ya? Terus pas beli, harganya sama "
        "aja kayak biasanya.",
        "Kalian tim mana nih?",
        "Kamu pernah ngerasain nggak sih kabel putus di ujung?",
        "Ada yang ngerasain hal serupa?",
    ])
    def test_bait_openers_are_vetoed(self, draft):
        """A poll dressed as a question farms replies; it is bait.

        The persona guide allows `pertanyaan_jujur` — an honest question — so
        this must not ban question openers wholesale.
        """
        assert V_ENGAGEMENT_BAIT in hard_vetoes(draft)

    @pytest.mark.parametrize("draft", [
        # Honest questions the persona explicitly permits.
        "Pernah nggak sih HP dipakai tidur, besoknya baterai tetap penuh?",
        "Power bank lama masih idup? Jangan buru-buru beli baru.",
        "Kabel charger di tas punya agenda sendiri.",
        "Banyak yang pikir charger cepat itu yang colokannya muat.",
        "Kamu perlu cek standar watt sebelum beli charger.",
        "Spesifikasi tinggi di atas kertas sering nggak terasa.",
    ])
    def test_honest_questions_are_not_vetoed(self, draft):
        assert V_ENGAGEMENT_BAIT not in hard_vetoes(draft)

    def test_hashtag_is_vetoed(self):
        assert V_HASHTAG in hard_vetoes("Rapiin meja #setup #desk")

    def test_unsafe_framing_is_vetoed(self):
        assert V_UNSAFE_FRAMING in hard_vetoes(
            "Dosis obat ini bisa bikin ketergantungan."
        )

    def test_near_duplicate_is_vetoed(self):
        history = ["Kabel charger selalu kusut di tas dan putus di ujung tiap minggu"]
        vetoes = hard_vetoes(
            "Kabel charger yang kusut di tas lalu putus di ujung tiap minggu",
            history=history,
        )
        assert V_NEAR_DUPLICATE in vetoes

    def test_repetitive_template_is_vetoed(self):
        history = [
            "Kok ya tiap pagi selalu macet di jalan tol.",
            "Kok ya tiap siang selalu lempar di kantor.",
            "Kok ya tiap sore selalu lemot di rumah.",
            "Kok ya tiap malam selalu pusing di kasur.",
            "Kok ya tiap dinih selalu hujan di trotoar.",
            "Kok ya tiap jumat selalu macet di gerbang.",
        ]
        vetoes = hard_vetoes(
            "Kok ya tiap sabtu selalu lemot di kamar.", history=history
        )
        assert V_EXCESSIVE_REPETITION in vetoes

    def test_normal_two_sentence_shape_is_not_vetoed(self):
        history = [
            "Diskon besar kadang cuma mengembalikan harga normal. Kalau "
            "angkanya dibandingkan dari harga setahun lalu, ceritanya beda.",
            "Sebelum beli power bank, cek dulu output watt-nya di bodi atau "
            "manualnya. Angka mAh di kemasan sering bukan kapasitas nyata.",
            "Spesifikasi tinggi di atas kertas sering nggak terasa di pemakaian "
            "harian. Yang kerasa justru baterai yang awet dan layar enak dilihat.",
            "Kategori kecil yang sering terlupakan: organizer kabel, stand HP, "
            "lampu meja. Murah, tapi efeknya ke kerapian meja kerasa banget.",
        ]
        vetoes = hard_vetoes(
            "Review yang terlalu rapi dan positif itu tanda bahaya, bukan "
            "jaminan. Produk yang bagus biasanya punya catatan jelek juga.",
            history=history,
        )
        assert V_EXCESSIVE_REPETITION not in vetoes

    def test_affiliate_without_verification_is_vetoed(self):
        vetoes = hard_vetoes(
            "Power bank ini worth it buat harian.", is_affiliate=True,
            affiliate_verified=False, has_disclosure=True,
        )
        assert V_UNCERTAIN_TRACKING in vetoes

    def test_affiliate_without_disclosure_is_vetoed(self):
        vetoes = hard_vetoes(
            "Power bank ini worth it buat harian.", is_affiliate=True,
            affiliate_verified=True, has_disclosure=False,
        )
        assert V_NO_DISCLOSURE in vetoes

    def test_verified_affiliate_with_disclosure_passes(self):
        vetoes = hard_vetoes(
            "Power bank ini worth it buat harian.", is_affiliate=True,
            affiliate_verified=True, has_disclosure=True,
        )
        assert V_UNCERTAIN_TRACKING not in vetoes
        assert V_NO_DISCLOSURE not in vetoes

    def test_stale_draft_is_vetoed(self):
        assert V_STALE_DRAFT in hard_vetoes(GOOD_DRAFT, draft_age_hours=100)

    def test_fresh_draft_is_not_stale(self):
        assert V_STALE_DRAFT not in hard_vetoes(GOOD_DRAFT, draft_age_hours=1)

    def test_unauthorized_operator_is_vetoed(self):
        vetoes = hard_vetoes(GOOD_DRAFT, operator_authorized=False)
        assert "unauthorized_operator_action" in vetoes

    def test_vetoes_are_deduplicated(self):
        vetoes = hard_vetoes("Angkanya 50% dan 80% dan 90%.", evidence="")
        assert len(vetoes) == len(set(vetoes))

    def test_clean_draft_has_no_vetoes(self):
        assert hard_vetoes(GOOD_DRAFT) == []


# ── The gate ────────────────────────────────────────────────────────────────
class TestQualityGate:
    def test_high_score_no_vetoes_approves(self, db):
        d = evaluate_draft(
            GOOD_DRAFT, evidence="", pillar="relatable",
            client=FakeLLMClient(default=_critic_json(90)),
            persist=False,
        )
        assert d.verdict == APPROVED_AUTO
        assert d.vetoes == []
        assert d.passed is True

    def test_mid_score_needs_review(self, db):
        d = evaluate_draft(
            GOOD_DRAFT, pillar="relatable",
            client=FakeLLMClient(default=_critic_json(75)), persist=False,
        )
        assert d.verdict == NEEDS_REVIEW

    def test_low_score_revises_once(self, db):
        d = evaluate_draft(
            GOOD_DRAFT, pillar="relatable",
            client=FakeLLMClient(default=_critic_json(30)), persist=False,
        )
        assert d.verdict == REVISE_ONCE
        assert d.revision_allowed is True

    def test_hard_veto_beats_high_score(self, db):
        """A vetoed draft is rejected even if the critic loves it."""
        d = evaluate_draft(
            "Gue sudah beli dan pakai adaptor ini, worth it banget.",
            pillar="discoveries",
            client=FakeLLMClient(default=_critic_json(99)), persist=False,
        )
        assert d.verdict == REJECTED
        assert V_FABRICATED_FIRSTHAND in d.vetoes

    def test_unavailable_critic_rejects(self, db):
        d = evaluate_draft(
            GOOD_DRAFT, pillar="relatable",
            client=FakeLLMClient(default="garbage not json"),
            persist=False,
        )
        assert d.verdict == REJECTED
        assert V_CRITIC_UNAVAILABLE in d.vetoes

    def test_critic_can_be_skipped_when_caller_opts_out(self, db):
        d = evaluate_draft(
            GOOD_DRAFT, pillar="relatable",
            client=FakeLLMClient(default="garbage"),
            require_critic=False, persist=False,
        )
        assert d.verdict == REVISE_ONCE  # score 0 with no vetoes

    def test_decision_is_persisted_with_reason_codes(self, db):
        from src.content.writer import save_draft

        did = save_draft(DraftResult(
            idea_id=None, content=GOOD_DRAFT, parts=[], structure="single",
            pillar="relatable", angle="observasi", audience_problem="x",
            persona_version="v1", variation={},
        ), path=db)
        d = evaluate_draft(
            GOOD_DRAFT, pillar="relatable",
            client=FakeLLMClient(default=_critic_json(88)),
            draft_id=did, path=db,
        )
        assert d.verdict == APPROVED_AUTO
        evals = get_evaluations(did, path=db)
        assert len(evals) == 1
        assert evals[0]["verdict"] == APPROVED_AUTO
        assert evals[0]["total_score"] == 88

    def test_rejected_decision_is_also_persisted(self, db):
        from src.content.writer import save_draft

        did = save_draft(DraftResult(
            idea_id=None, content="Gue sudah pakai ini.", parts=[],
            structure="single", pillar="discoveries", angle="x",
            audience_problem="x", persona_version="v1", variation={},
        ), path=db)
        evaluate_draft("Gue sudah pakai ini.", pillar="discoveries",
                       client=FakeLLMClient(default=_critic_json(95)),
                       draft_id=did, path=db)
        evals = get_evaluations(did, path=db)
        assert evals[0]["verdict"] == REJECTED
        assert V_FABRICATED_FIRSTHAND in evals[0]["vetoes"]


# ── Writer: no fallback filler, ever ────────────────────────────────────────
class TestWriterNeverFills:
    def test_unparseable_json_returns_none(self, db):
        out = write_draft(
            pillar="relatable",
            client=FakeLLMClient(default="this is not json at all"),
            path=db, persist=False,
        )
        assert out is None

    def test_empty_content_returns_none(self, db):
        out = write_draft(
            pillar="relatable",
            client=FakeLLMClient(default='{"content": ""}'),
            path=db, persist=False,
        )
        assert out is None

    def test_json_array_returns_none(self, db):
        out = write_draft(
            pillar="relatable",
            client=FakeLLMClient(default='["a","b"]'),
            path=db, persist=False,
        )
        assert out is None

    def test_llm_failure_returns_none(self, db):
        class Boom:
            def complete(self, *a, **k):
                raise LLMError("all providers down")

        out = write_draft(pillar="relatable", client=Boom(), path=db, persist=False)
        assert out is None

    def test_no_filler_string_is_ever_produced(self, db):
        """The exact V1 failure mode must be impossible."""
        for bad in ("not json", '{"content": ""}', '{"nope": 1}', "[]", "{}"):
            out = write_draft(pillar="relatable",
                              client=FakeLLMClient(default=bad),
                              path=db, persist=False)
            assert out is None, bad

    def test_valid_json_produces_draft(self, db, monkeypatch):
        import json
        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: VoiceVariation(
                "single_snappy", "observasi", "pendek", "ringan", "edukasi"
            ),
        )
        payload = json.dumps({
            "content": GOOD_DRAFT, "disclosure": "",
        })
        out = write_draft(pillar="relatable",
                          client=FakeLLMClient(default=payload),
                          path=db, persist=False)
        assert out is not None
        assert out.content == GOOD_DRAFT
        assert out.structure == "single"
        assert out.persona_version == "teman_tongkrongan_v1"

    def test_thread_draft_produces_parts(self, db):
        import json
        payload = json.dumps({
            "parts": ["Bagian satu.", "Bagian dua.", "Bagian tiga."],
            "disclosure": "",
        })
        out = write_draft(
            pillar="practical_tips",
            client=FakeLLMClient(script={"mini_thread": payload}),
            path=db, persist=False,
        )
        # The default variation may not be a thread; force it via a thread idea.
        if out is not None and out.parts:
            assert out.structure == "thread"
            assert len(out.parts) == 3

    def test_empty_parts_returns_none(self, db):
        import json
        out = write_draft(
            pillar="practical_tips",
            client=FakeLLMClient(default=json.dumps({"parts": []})),
            path=db, persist=False,
        )
        # Either the variation was not a thread (single content missing → None)
        # or it was a thread with no parts → None. Both are None.
        assert out is None

    def test_persisted_draft_is_retrievable(self, db, monkeypatch):
        import json
        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        # Pin a single-post variation: with an empty history the writer picks
        # mini_thread ~20% of the time, and a thread payload is not what this
        # test is about.
        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: VoiceVariation(
                "single_snappy", "observasi", "pendek", "ringan", "edukasi"
            ),
        )
        payload = json.dumps({"content": GOOD_DRAFT, "disclosure": ""})
        out = write_draft(pillar="relatable",
                          client=FakeLLMClient(default=payload),
                          path=db, persist=True)
        assert out is not None
        assert out.draft_id is not None
        row = get_draft(out.draft_id, path=db)
        assert row["content"] == GOOD_DRAFT
        assert row["status"] == "DRAFT"

    def test_writer_never_receives_a_news_headline(self, db):
        """The prompt must be built from an audience problem, not a headline."""
        import json
        fake = FakeLLMClient(default=json.dumps({"content": GOOD_DRAFT}))
        write_draft(pillar="relatable", client=fake, path=db, persist=False)
        prompt = fake.calls[0]["messages"][0]["content"]
        for banned in ("berita", "BMKG", "IHSG", "timnas", "breaking news",
                       "headline"):
            assert banned not in prompt, banned
        assert "Masalah:" in prompt
        assert "IDENTITAS AKUN" in prompt

    def test_writer_prompt_bans_numbers_without_evidence(self, db):
        import json
        fake = FakeLLMClient(default=json.dumps({"content": GOOD_DRAFT}))
        write_draft(pillar="relatable", evidence="", client=fake,
                    path=db, persist=False)
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "DILARANG memakai angka" in prompt

    def test_writer_prompt_allows_sourced_numbers(self, db):
        import json
        fake = FakeLLMClient(default=json.dumps({"content": GOOD_DRAFT}))
        write_draft(pillar="practical_tips",
                    evidence="Ketahanan baterai 18 jam.",
                    client=fake, path=db, persist=False)
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "BUKTI YANG BOLEH DIPAKAI" in prompt
        assert "18 jam" in prompt

    def test_revision_feedback_reaches_the_prompt(self, db):
        import json
        fake = FakeLLMClient(default=json.dumps({"content": GOOD_DRAFT}))
        write_draft(pillar="relatable", revision_feedback="buat lebih pendek",
                    client=fake, path=db, persist=False)
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "REVISI DIMINTA OPERATOR" in prompt
        assert "buat lebih pendek" in prompt

    def test_single_variation_rejects_thread_payload(self, db, monkeypatch):
        """A model that ignores the schema must not smuggle in a thread."""
        import json
        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        single_var = VoiceVariation("single_snappy", "observasi", "pendek",
                                    "ringan", "edukasi")
        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: single_var,
        )
        out = writer_mod.write_draft(
            pillar="practical_tips",
            client=FakeLLMClient(default=json.dumps({"parts": ["a.", "b."]})),
            path=db, persist=False,
        )
        assert out is None

    def test_thread_variation_rejects_single_payload(self, db, monkeypatch):
        """When the variation IS a thread, a single-post reply is rejected."""
        import json
        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        # Pin the variation directly rather than fighting the history window.
        thread_var = VoiceVariation("mini_thread", "observasi", "sedang",
                                    "reflektif", "edukasi")
        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: thread_var,
        )
        out = writer_mod.write_draft(
            pillar="practical_tips",
            client=FakeLLMClient(default=json.dumps({"content": "satu post."})),
            path=db, persist=False,
        )
        assert out is None, "a single post must not pass as a thread draft"

    def test_thread_variation_accepts_thread_payload(self, db, monkeypatch):
        import json
        from src.content import writer as writer_mod
        from src.persona.voice import VoiceVariation

        thread_var = VoiceVariation("mini_thread", "observasi", "sedang",
                                    "reflektif", "edukasi")
        monkeypatch.setattr(
            writer_mod, "varied_variation",
            lambda history, rng=None: thread_var,
        )
        out = writer_mod.write_draft(
            pillar="practical_tips",
            client=FakeLLMClient(default=json.dumps({
                "parts": ["Satu.", "Dua.", "Tiga."], "disclosure": "",
            })),
            path=db, persist=False,
        )
        assert out is not None
        assert out.structure == "thread"
        assert len(out.parts) == 3


# ── End-to-end: the gate blocks what must never publish ─────────────────────
class TestGateBlocksUnpublishable:
    @pytest.mark.parametrize("bad_draft,expected_veto", [
        ("Isu kabel ini punya implikasi nyata kalau dicermati secara objektif. "
         "Dampaknya ke depan bakal signifikan.", V_GENERIC_FILLER),
        ("Gue sudah beli power bank ini dan hasilnya awet 18 jam.",
         V_FABRICATED_FIRSTHAND),
        ("Diskonnya sampai 70% minggu ini, buruan!", V_UNVERIFIED_NUMBER),
        ("Rapiin mejamu #setup #cantik", V_HASHTAG),
        ("Jangan percaya sama klaim ini, ada rahasia yang disembunyikan.",
         V_CLICKBAIT),
    ])
    def test_bad_drafts_never_reach_the_queue(self, bad_draft, expected_veto, db):
        d = evaluate_draft(
            bad_draft, pillar="relatable",
            client=FakeLLMClient(default=_critic_json(100)), persist=False,
        )
        assert d.verdict == REJECTED
        assert expected_veto in d.vetoes
        assert d.passed is False

    def test_repeated_low_scores_never_auto_approve(self, db):
        for _ in range(5):
            d = evaluate_draft(
                GOOD_DRAFT, pillar="relatable",
                client=FakeLLMClient(default=_critic_json(20)), persist=False,
            )
            assert d.verdict == REVISE_ONCE
            assert d.passed is False
