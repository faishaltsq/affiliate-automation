"""Context must override the clock and persona before a caption can ship."""

import json
from unittest.mock import patch

import pytest

from src.organic.generator import generate_organic_post
from src.organic.planner import _get_post_style


TOPIC = "Asap karhutla makin tebal, anak-anak menjadi kelompok paling rentan"
GOOD = "Anak-anak jadi kelompok paling rentan saat asap karhutla makin tebal. Perlindungan mereka perlu jadi prioritas penanganan."
BAD = "morning wargaa threadss, udah pada ngopi belom? Asap karhutla makin tebal."


@pytest.mark.parametrize("hour", [7, 12, 16, 21])
def test_disaster_style_never_comes_from_time_of_day(hour):
    assert _get_post_style(hour, "breaking_disaster", TOPIC) == "empathetic_update"


def test_sensitive_story_in_another_pillar_uses_summary():
    assert _get_post_style(
        7, "football_sports", "Pertandingan dihentikan",
        "Seorang penonton meninggal dunia di stadion.",
    ) == "empathetic_update"


def test_light_story_keeps_its_own_style():
    with patch("src.organic.planner.random.choices", return_value=["morning_greeting"]):
        assert _get_post_style(7, "tech_gadget_ai", "Peluncuran ponsel baru") == "morning_greeting"


@pytest.mark.parametrize("persona", ["default", "julid", "calm", "nerd", "energic", "angry"])
def test_manual_persona_and_morning_style_cannot_override_disaster_context(persona):
    with patch("src.organic.generator._llm", return_value=json.dumps({"content": GOOD})) as llm:
        out = generate_organic_post(
            TOPIC, "breaking_disaster", "text_only", "morning_greeting", persona=persona,
        )
    prompt = llm.call_args.args[0][0]["content"]
    assert out["post_style"] == "empathetic_update"
    assert out["content"] == GOOD
    assert "KONTEKS SENSITIF MENGALAHKAN PERSONA DAN JAM POSTING" in prompt
    assert "Contoh pembuka:" not in prompt
    assert "Anggaran mitigasi triliunan" not in prompt
    assert "ISPU 300+" not in prompt


def test_insensitive_caption_is_rewritten_before_image_search():
    with patch("src.organic.generator._llm", side_effect=[
        json.dumps({"content": BAD}), json.dumps({"content": GOOD}),
    ]) as llm:
        out = generate_organic_post(TOPIC, "breaking_disaster", "text_only", "morning_greeting")
    assert llm.call_count == 2
    assert out["content"] == GOOD
    assert "TULIS ULANG" in llm.call_args.args[0][-1]["content"]


@pytest.mark.parametrize("bad", [
    BAD,
    "Selamat pagi! Anak-anak terdampak asap karhutla.",
    "Udah sarapan belum? Anak-anak terdampak asap.",
    "Wkwk, asap karhutla makin tebal.",
    "Karhutla makin tebal 😂",
])
def test_repeated_insensitive_output_stops_before_card_or_publish(bad):
    with patch("src.organic.generator._llm", return_value=json.dumps({"content": bad})) as llm, \
         patch("src.organic.generator.search_vibe_image", return_value="") as image:
        with pytest.raises(ValueError, match="tone"):
            generate_organic_post(TOPIC, "breaking_disaster", "with_image")
    assert llm.call_count == 2
    image.assert_not_called()


def test_sober_caption_is_not_blocked_for_ordinary_words():
    text = "Kabut asap mengganggu aktivitas pagi anak-anak. Warga menunggu penanganan yang jelas."
    with patch("src.organic.generator._llm", return_value=json.dumps({"content": text})) as llm:
        out = generate_organic_post(TOPIC, "breaking_disaster", "text_only")
    assert out["content"] == text
    llm.assert_called_once()


def test_sensitive_chain_checks_bonus_too():
    parts = [GOOD, "Kabar ini perlu dipantau.", "Utamakan warga terdampak.", "Penanganannya perlu jelas."]
    with patch("src.organic.generator._llm", return_value=json.dumps({
        "parts": parts, "bonus_reply": "Wkwk udah ngopi belum?",
    })):
        with pytest.raises(ValueError, match="tone"):
            generate_organic_post(TOPIC, "breaking_disaster", "text_only", post_structure="chain")


def test_malformed_sensitive_output_does_not_become_generic_filler():
    with patch("src.organic.generator._llm", return_value="not json"):
        with pytest.raises(ValueError):
            generate_organic_post(TOPIC, "breaking_disaster", "text_only")


def test_number_regeneration_cannot_swallow_tone_failure():
    replies = [
        json.dumps({"content": "Asap karhutla berdampak ke 99 anak."}),
        json.dumps({"content": BAD}),
        json.dumps({"content": BAD}),
    ]
    with patch("src.organic.generator._llm", side_effect=replies):
        with pytest.raises(ValueError, match="tone"):
            generate_organic_post(TOPIC, "breaking_disaster", "text_only")


def test_planner_passes_story_context_for_hot_and_batch_posts(monkeypatch):
    from src.organic import planner

    summary = "Seorang penonton meninggal dunia di stadion."
    monkeypatch.setattr(planner, "_get_wib_hour", lambda: 7)
    monkeypatch.setattr(planner, "get_today_post_count", lambda: 0)
    monkeypatch.setattr(planner, "_select_hot_topic", lambda _: ("Pertandingan dihentikan", "football_sports"))
    monkeypatch.setattr(planner, "_select_pillar", lambda *_: "football_sports")
    monkeypatch.setattr(planner, "_select_topic_for_pillar", lambda *_: ("Pertandingan dihentikan", "trending"))
    monkeypatch.setattr(planner, "_topic_summary", lambda _: summary)
    monkeypatch.setattr(planner, "_get_post_format", lambda *_: "text_only")
    plans = [planner.plan_single_post()] + planner.plan_daily_batch(count=2)
    for plan in plans:
        assert plan["post_style"] == "empathetic_update"
        assert plan["source_summary"] == summary


def test_tone_failure_does_not_reach_publisher(monkeypatch):
    from src.organic import publisher

    monkeypatch.setattr(publisher, "init_organic_db", lambda: None)
    monkeypatch.setattr(publisher, "get_today_post_count", lambda: 0)
    monkeypatch.setattr(publisher, "plan_single_post", lambda **_: {
        "topic": TOPIC, "pillar": "breaking_disaster", "source": "test", "post_format": "text_only",
    })
    with patch("src.organic.generator._llm", return_value=json.dumps({"content": BAD})), \
         patch.object(publisher, "post_threads") as publish, \
         patch.object(publisher, "post_thread_chain") as chain, \
         patch.object(publisher, "log_organic_post") as record:
        with pytest.raises(ValueError, match="tone"):
            publisher.execute_organic_post()
    publish.assert_not_called()
    chain.assert_not_called()
    record.assert_not_called()

