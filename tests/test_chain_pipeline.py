"""tests/test_chain_pipeline.py — 4-part thread chain: generator, planner, publisher.

The chain is the retention mechanic: part 1 must withhold the answer, part 4
must end in a trigger specific enough that answering it requires typing, and the
bonus reply parks one held-back fragment in the reply section. These tests pin
the contract so a prompt edit cannot quietly collapse it back to a hot take.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from src.organic.generator import (
    CHAIN_PART_COUNT,
    CHAIN_PILLARS,
    CHAIN_RULES,
    _normalize_chain,
    generate_organic_post,
)
from src.organic.planner import _get_post_structure, CHAIN_MAX_PER_DAY, CHAIN_PROBABILITY
from src.publish.threads import post_thread_chain

PARTS = [
    "IHSG ditutup merah padahal Wall Street lagi hijau. Ada 3 alasannya, dan yang ketiga jarang dibahas.",
    "Asing net sell Rp 1,8 triliun pekan ini, terbesar sejak Maret. Jadi bukan sentimen global, tapi keluarnya dana mereka sendiri.",
    "Buat lo yang nabung lewat reksa dana pasar uang, imbal hasilnya ikut tertekan kalau suku bunga turun. Nunggu gajian makin kerasa.",
    "Menurutku IHSG tutup di atas 7.000 akhir tahun ini. Yang udah masuk sejak Januari, hold atau cut loss?",
]
BONUS = "Katanya dana asing bakal balik kalau Rupiah tembus 15.900, tapi itu masih kabar angin."


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _llm_returning(payload: dict):
    return patch(
        "src.organic.generator._llm",
        return_value=json.dumps(payload),
    )


# ── Prompt contract ──────────────────────────────────────────────────────────

def test_chain_rules_define_four_ordered_parts():
    assert "PART 1 — HOOK" in CHAIN_RULES
    assert "PART 2 — KONTRADIKSI" in CHAIN_RULES
    assert "PART 3 — IMPLIKASI" in CHAIN_RULES
    assert "PART 4 — HOT TAKE" in CHAIN_RULES
    assert "DILARANG memberi kesimpulan" in CHAIN_RULES


def test_chain_rules_ban_cliche_triggers():
    """The cliché list stays banned — a question anyone could paste on any post
    is exactly what fails to earn a comment."""
    for banned in ("Menurut kalian?", "Tim mana?", "Ada yang ngerasain?"):
        assert banned in CHAIN_RULES


def test_chain_rules_allow_the_two_working_triggers():
    assert "prediksi biner" in CHAIN_RULES
    assert "pengalaman sempit" in CHAIN_RULES


def test_chain_rules_forbid_invented_numbers():
    """Never fabricate figures — the chain dies on the first fact-check."""
    assert "DILARANG SEKALI-KALI MENGARANG ANGKA" in CHAIN_RULES


def test_chain_prompt_is_really_sent_to_the_llm():
    """A failed chain retries as a single post, so every prompt must be kept and
    the chain one inspected first."""
    prompts, tokens = [], []

    def fake_llm(messages, **kw):
        prompts.append(messages[0]["content"])
        tokens.append(kw.get("max_tokens"))
        # First call renders a broken chain so the retry path is exercised too.
        if len(prompts) == 1:
            return json.dumps({"parts": PARTS[:2]})
        return json.dumps({"content": "satu post padat", "image_query": ""})

    with patch("src.organic.generator._llm", side_effect=fake_llm):
        generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                              post_format="text_only", post_structure="chain")

    assert len(prompts) >= 2, "chain prompt should retry as a single post"
    chain_prompt = prompts[0]
    assert "STRUKTUR THREAD CHAIN" in chain_prompt
    assert f"TEPAT {CHAIN_PART_COUNT} ELEMEN" in chain_prompt
    assert '"parts"' in chain_prompt
    assert "bonus_reply" in chain_prompt
    # 600 tokens truncates four parts plus a bonus mid-sentence.
    assert tokens[0] > 600


def test_single_prompt_does_not_get_chain_rules():
    prompts = []

    def fake_llm(messages, **kw):
        prompts.append(messages[0]["content"])
        return json.dumps({"content": "satu post", "image_query": ""})

    with patch("src.organic.generator._llm", side_effect=fake_llm):
        generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                              post_format="text_only", post_structure="single")

    assert "STRUKTUR THREAD CHAIN" not in prompts[0]
    assert '"content"' in prompts[0]


# ── Chain normalisation ──────────────────────────────────────────────────────

def test_normalize_chain_accepts_exactly_four():
    parts, err = _normalize_chain(list(PARTS))
    assert err == ""
    assert len(parts) == CHAIN_PART_COUNT


def test_normalize_chain_rejects_short_chain():
    parts, err = _normalize_chain(PARTS[:3])
    assert parts == []
    assert "need 4" in err


def test_normalize_chain_rejects_non_list():
    parts, err = _normalize_chain("satu paragraf panjang")
    assert parts == []
    assert "expected a list" in err


def test_normalize_chain_rejects_blank_parts():
    parts, err = _normalize_chain([PARTS[0], "  ", PARTS[1], ""])
    assert parts == []


def test_normalize_chain_truncates_extra_parts():
    """A 6-part answer means part 1 failed to withhold anything."""
    parts, err = _normalize_chain(PARTS + ["part 5", "part 6"])
    assert err == ""
    assert len(parts) == CHAIN_PART_COUNT


# ── Generated chain ──────────────────────────────────────────────────────────

def test_generate_chain_returns_parts_and_bonus():
    with _llm_returning({"parts": PARTS, "bonus_reply": BONUS, "image_query": ""}):
        out = generate_organic_post(
            topic="IHSG sesi 1 melemah",
            pillar="market_finance_stocks",
            post_format="text_only",
            post_structure="chain",
        )

    assert out["post_structure"] == "chain"
    assert out["parts"] == PARTS
    assert out["bonus_reply"] == BONUS
    assert out["content"] == "\n\n".join(PARTS)


def test_generate_chain_tolerates_missing_bonus():
    with _llm_returning({"parts": PARTS, "image_query": ""}):
        out = generate_organic_post(topic="chip AI baru", pillar="tech_gadget_ai",
                                    post_format="text_only", post_structure="chain")
    assert out["parts"] == PARTS
    assert out["bonus_reply"] == ""


def test_generate_chain_falls_back_to_single_on_bad_json():
    """A broken chain must never reach a live account as a broken thread."""
    replies = [
        json.dumps({"parts": PARTS[:2]}),                       # too short
        json.dumps({"content": "satu post padat", "image_query": ""}),  # fallback call
    ]
    with patch("src.organic.generator._llm", side_effect=replies):
        out = generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                                    post_format="text_only", post_structure="chain")

    assert out["post_structure"] == "single"
    assert out["parts"] == []
    assert out["content"] == "satu post padat"


def test_generate_chain_falls_back_when_llm_returns_garbage():
    replies = ["bukan json sama sekali", json.dumps({"content": "fallback", "image_query": ""})]
    with patch("src.organic.generator._llm", side_effect=replies):
        out = generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                                    post_format="text_only", post_structure="chain")
    assert out["post_structure"] == "single"


def test_chain_card_carries_part_one_not_the_whole_thread():
    """Part 1 is what sells the scroll; four parts on one image is a poster."""
    def fake_llm(messages, **kw):
        return json.dumps({"parts": PARTS, "bonus_reply": BONUS, "image_query": "pialang saham tegang"})

    captured = {}

    def fake_card(**kw):
        captured.update(kw)
        return "https://cdn.example/card.jpg"

    with patch("src.organic.generator._llm", side_effect=fake_llm), \
         patch("src.trends.multi_source.fetch_topic_image_query",
               return_value="pialang saham tegang layar indeks"), \
         patch("src.image.market_chart.symbol_for_topic", return_value=None), \
         patch("src.organic.generator.search_vibe_image",
               return_value="https://cdn.example/photo.jpg"), \
         patch("src.image.card_generator.generate_editorial_card", side_effect=fake_card):
        out = generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                                    post_format="with_image", post_structure="chain")

    assert captured["summary"] == PARTS[0]
    assert PARTS[3] not in captured["summary"]
    assert out["image_url"] == "https://cdn.example/card.jpg"


def test_single_post_has_no_parts_list():
    with _llm_returning({"content": "hot take padat", "image_query": ""}):
        out = generate_organic_post(topic="IHSG sesi 1", pillar="market_finance_stocks",
                                    post_format="text_only", post_structure="single")
    assert out["parts"] == []
    assert out["bonus_reply"] == ""


# ── Planner cadence ──────────────────────────────────────────────────────────

def test_planner_never_chains_bencana():
    with patch("src.organic.planner.get_today_chain_count", return_value=0), \
         patch("src.organic.planner.random.random", return_value=0.0):
        assert _get_post_structure("breaking_disaster") == "single"


def test_planner_never_chains_football():
    with patch("src.organic.planner.get_today_chain_count", return_value=0), \
         patch("src.organic.planner.random.random", return_value=0.0):
        assert _get_post_structure("football_sports") == "single"


def test_planner_chains_eligible_pillars():
    with patch("src.organic.planner.get_today_chain_count", return_value=0), \
         patch("src.organic.planner.random.random", return_value=0.0):
        for pillar in CHAIN_PILLARS:
            assert _get_post_structure(pillar) == "chain"


def test_planner_honours_daily_chain_quota():
    with patch("src.organic.planner.get_today_chain_count", return_value=CHAIN_MAX_PER_DAY):
        for pillar in CHAIN_PILLARS:
            assert _get_post_structure(pillar) == "single"


def test_planner_chain_probability_is_respected():
    with patch("src.organic.planner.get_today_chain_count", return_value=0):
        with patch("src.organic.planner.random.random", return_value=CHAIN_PROBABILITY + 0.05):
            assert _get_post_structure("tech_gadget_ai") == "single"
        with patch("src.organic.planner.random.random", return_value=CHAIN_PROBABILITY - 0.05):
            assert _get_post_structure("tech_gadget_ai") == "chain"


def test_planner_fails_closed_when_quota_lookup_breaks():
    """A broken quota lookup must not open the tap on an uncapped chain day."""
    with patch("src.organic.planner.get_today_chain_count", side_effect=OSError("db locked")):
        assert _get_post_structure("tech_gadget_ai") == "single"


# ── Publishing the chain ─────────────────────────────────────────────────────

@pytest.fixture
def published():
    """Records (text, image_url, reply_to_id) in publish order, mints new ids."""
    calls = []

    def fake_publish(text, image_url=None, reply_to_id=None):
        pid = f"p{len(calls) + 1}"
        calls.append({"id": pid, "text": text, "image_url": image_url, "reply_to": reply_to_id})
        return pid

    return calls, fake_publish


def test_chain_parts_are_linked_in_order(published):
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain(list(PARTS), image_url="https://cdn.example/card.jpg")

    assert [c["text"] for c in calls] == PARTS
    assert out["post_id"] == "p1"
    assert out["part_ids"] == ["p1", "p2", "p3", "p4"]
    # Each part replies to the PREVIOUS one: first-level siblings get reshuffled
    # by Threads' ranking, a real chain always renders in order.
    assert [c["reply_to"] for c in calls] == ["", "p1", "p2", "p3"]


def test_chain_image_rides_on_the_root_only(published):
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        post_thread_chain(list(PARTS), image_url="https://cdn.example/card.jpg")

    assert [c["image_url"] for c in calls] == [
        "https://cdn.example/card.jpg", None, None, None
    ]


def test_bonus_reply_attaches_to_root_not_last_part(published):
    """It lands as a sibling in the reply section — where readers arrive after
    finishing the chain."""
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain(list(PARTS), bonus_reply=BONUS)

    assert calls[-1]["text"] == BONUS
    assert calls[-1]["reply_to"] == "p1"
    assert out["bonus_reply_id"] == "p5"


def test_chain_without_bonus_stops_after_last_part(published):
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain(list(PARTS), bonus_reply="")

    assert len(calls) == CHAIN_PART_COUNT
    assert out["bonus_reply_id"] == ""


def test_blank_parts_are_dropped_not_published(published):
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain([PARTS[0], "   ", PARTS[1], None, PARTS[2], PARTS[3]])

    assert len(calls) == CHAIN_PART_COUNT


def test_empty_chain_publishes_nothing(published):
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain([])

    assert calls == []
    assert out["error"]


def test_midchain_failure_keeps_published_parts(published):
    """A resume-safe partial: already-live parts stay, the caller sees the ids."""
    calls, fake_publish = published

    def flaky(text, image_url=None, reply_to_id=None):
        if text == PARTS[2]:
            raise RuntimeError("rate limited")
        return fake_publish(text, image_url, reply_to_id)

    with patch("src.publish.threads._publish_text", side_effect=flaky):
        out = post_thread_chain(list(PARTS), bonus_reply=BONUS)

    assert out["error"]
    assert out["part_ids"] == ["p1", "p2"]
    assert out["bonus_reply_id"] == ""


def test_root_image_failure_retries_without_image(published):
    calls, fake_publish = published

    def reject_images(text, image_url=None, reply_to_id=None):
        if image_url:
            raise RuntimeError("image rejected")
        return fake_publish(text, image_url, reply_to_id)

    with patch("src.publish.threads._publish_text", side_effect=reject_images):
        out = post_thread_chain(list(PARTS), image_url="https://cdn.example/card.jpg")

    assert out["post_id"] == "p1"
    assert out["part_ids"] == ["p1", "p2", "p3", "p4"]
    assert calls[0]["image_url"] is None


def test_bonus_failure_does_not_fail_the_thread(published):
    """The chain is already live; a lost nudge is not a broken post."""
    calls, fake_publish = published

    def flaky_bonus(text, image_url=None, reply_to_id=None):
        if text == BONUS:
            raise RuntimeError("rate limited")
        return fake_publish(text, image_url, reply_to_id)

    with patch("src.publish.threads._publish_text", side_effect=flaky_bonus):
        out = post_thread_chain(list(PARTS), bonus_reply=BONUS)

    assert not out.get("error")
    assert out["part_ids"] == ["p1", "p2", "p3", "p4"]
    assert out["bonus_reply_id"] == ""
    assert out["bonus_reply_error"]


def test_chain_reports_parts_after_filtering_blank_entries(published):
    """Two blank parts must not turn a 4-part thread into a 2-part one."""
    calls, fake_publish = published
    with patch("src.publish.threads._publish_text", side_effect=fake_publish):
        out = post_thread_chain([PARTS[0], "", PARTS[1], "   ", PARTS[2], PARTS[3]])

    assert len(out["part_ids"]) == CHAIN_PART_COUNT
