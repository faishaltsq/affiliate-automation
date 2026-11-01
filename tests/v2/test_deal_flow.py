"""tests/v2/test_deal_flow.py — Test Telegram affiliate deal & reminder flow.

Covers:
1. Deal hook permission in quality gate (_check_clickbait ignores 'plot twist' for deals).
2. Deal creation & drafting (generate_deal_text, create_deal_draft with NEEDS_REVIEW).
3. Deal reminder scheduling (12:00 & 19:00 WIB slot detection, idempotency).
4. Command handling (/deal, /remind, photo caption extraction, photo preview).
"""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from src.bot.v2_commands import handle_v2_command
from src.commerce.deal_creator import (
    create_deal_draft,
    download_telegram_photo,
    generate_deal_text,
    upload_deal_image,
)
from src.commerce.deal_reminder import (
    format_reminder_text,
    record_reminder_sent,
    should_send_reminder,
)
from src.content.quality_gate import _check_clickbait
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
def flags_on(monkeypatch):
    cfg.reset_settings_cache()
    monkeypatch.setenv("V2_TELEGRAM_COMMANDS_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_OPERATOR_IDS", "111")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1003757350269")
    s = cfg.Settings()
    monkeypatch.setattr(cfg, "_settings", s, raising=False)
    yield s
    cfg.reset_settings_cache()
def _critic_json(total: int = 88) -> str:
    import json
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
    return json.dumps({**out, "notes": "bagus", "concerns": []})


def test_quality_gate_allows_plot_twist_for_deals():
    text = (
        "PLOT TWIST: Micellar water yang sering diskon ini ternyata formula lamanya balik lagi. "
        "Kemarin sempat ganti dan bikin breakout, sekarang aman banget buat kulit sensitif.\n\n"
        "Yang udah repurchase batch terbaru, formulanya kerasa beda nggak?\n\n"
        "cek: https://vt.tokopedia.com/t/test/ (link afiliasi)"
    )
    # Generic pillar flags clickbait
    hits_organic = _check_clickbait(text, pillar="general")
    assert "clickbait_pattern" in hits_organic

    # Affiliate and deal pillars allow plot twist
    hits_affiliate = _check_clickbait(text, pillar="affiliate")
    assert hits_affiliate == []

    hits_deal = _check_clickbait(text, pillar="deal")
    assert hits_deal == []


def test_deal_reminder_schedule_and_idempotency(db):
    wib = dt.timezone(dt.timedelta(hours=7))

    # At 12:15 WIB: should remind
    t_1215 = dt.datetime(2026, 10, 31, 12, 15, tzinfo=wib)
    should, slot_key = should_send_reminder(path=db, now_dt=t_1215)
    assert should is True
    assert slot_key == "2026-10-31:12"

    # Record sent
    record_reminder_sent(slot_key, path=db)

    # Next check in same slot returns False
    t_1230 = dt.datetime(2026, 10, 31, 12, 30, tzinfo=wib)
    should_again, slot_key_again = should_send_reminder(path=db, now_dt=t_1230)
    assert should_again is False

    # At 15:00 WIB: not a scheduled slot
    t_1500 = dt.datetime(2026, 10, 31, 15, 0, tzinfo=wib)
    should_15, _ = should_send_reminder(path=db, now_dt=t_1500)
    assert should_15 is False

    # At 19:05 WIB: second slot fires
    t_1905 = dt.datetime(2026, 10, 31, 19, 5, tzinfo=wib)
    should_19, slot_key_19 = should_send_reminder(path=db, now_dt=t_1905)
    assert should_19 is True
    assert slot_key_19 == "2026-10-31:19"


def test_deal_text_generation_and_draft_creation(db):
    deal_body = (
        "PLOT TWIST: Skintific Mugwort Stick turun jadi 69rb di live siang ini.\n\n"
        "Buat yang mager maskeran colek-colek, bentuk stick gini paling praktis kalau kulit lagi rewel komedoan.\n\n"
        "Yang udah punya, stik kalian habis berapa bulan pemakaian?\n\n"
        "cek: https://vt.tokopedia.com/t/test1234/ (link afiliasi)"
    )
    fake_client = FakeLLMClient(replies=[deal_body, _critic_json(88)])

    draft_id, draft, decision = create_deal_draft(
        url="https://vt.tokopedia.com/t/test1234/",
        notes="skintific mugwort promo 69rb",
        image_url="https://uguu.se/mock_image.png",
        client=fake_client,
        path=db,
    )
    assert draft_id > 0
    assert "https://vt.tokopedia.com/t/test1234/" in draft.content
    assert draft.variation.get("image_url") == "https://uguu.se/mock_image.png"
    assert draft.pillar == "affiliate"
    assert decision.passed is True

    from src.content.writer import get_draft
    saved = get_draft(draft_id, path=db)
    assert saved is not None
    assert saved["status"] == "NEEDS_REVIEW"
    assert saved["editorial_metadata"].get("image_url") == "https://uguu.se/mock_image.png"

def test_cmd_deal_help_on_missing_url(flags_on, db):
    actor_op = {"id": 111, "is_bot": False, "username": "operator"}
    msg = {
        "message_id": 100,
        "from": actor_op,
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "message_thread_id": 9,
    }

    res = handle_v2_command(msg, "/deal", msg_id=100, thread_id=9, path=db)
    assert res.handled is True
    assert "Cara pakai /deal" in res.text


def test_cmd_deal_non_operator_denied(flags_on, db):
    actor_intruder = {"id": 999, "is_bot": False, "username": "intruder"}
    msg = {
        "message_id": 101,
        "from": actor_intruder,
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "message_thread_id": 9,
    }

    res = handle_v2_command(
        msg,
        "/deal https://vt.tokopedia.com/t/sample/",
        msg_id=101,
        thread_id=9,
        path=db,
    )
    assert res.handled is True
    assert "Tidak diizinkan" in res.text or "operator terdaftar" in res.text


def test_cmd_deal_with_link_and_photo(flags_on, db):
    deal_body = (
        "PLOT TWIST: Flash sale Glad2Glow moisturizer cuma 39rb sore ini.\n\n"
        "Tekstur gelnya ringan banget buat pagi hari sebelum sunscreen.\n\n"
        "Warga yang kulitnya oily combo, moisturizer ini nahan minyak seharian nggak?\n\n"
        "cek: https://vt.tokopedia.com/t/g2g/ (link afiliasi)"
    )
    fake_client = FakeLLMClient(replies=[deal_body, _critic_json(88)])

    actor_op = {"id": 111, "is_bot": False, "username": "operator"}
    msg = {
        "message_id": 102,
        "from": actor_op,
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "message_thread_id": 9,
        "photo": [{"file_id": "thumb123"}, {"file_id": "full_photo_456"}],
    }

    with patch("src.commerce.deal_creator.download_telegram_photo", return_value=b"fake_jpeg"):
        with patch("src.commerce.deal_creator.upload_deal_image", return_value="https://uguu.se/deal123.jpg"):
            res = handle_v2_command(
                msg,
                "/deal https://vt.tokopedia.com/t/g2g/ glad2glow moisturizer",
                msg_id=102,
                thread_id=9,
                client=fake_client,
                path=db,
            )

    assert res.handled is True
    assert "Draf Deal Baru Dibuat" in res.text
    assert "NEEDS_REVIEW" in res.text
    assert res.image_url == "https://uguu.se/deal123.jpg"


def test_cmd_remind(flags_on, db):
    actor_op = {"id": 111, "is_bot": False, "username": "operator"}
    msg = {
        "message_id": 103,
        "from": actor_op,
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "message_thread_id": 9,
    }

    res = handle_v2_command(msg, "/remind", msg_id=103, thread_id=9, path=db)
    assert res.handled is True
    assert "Pengingat Slot Deal" in res.text


def test_telegram_listener_photo_prompt_without_caption():
    from src.bot.telegram_listener import handle_message

    msg = {
        "message_id": 201,
        "from": {"id": 111, "is_bot": False, "username": "operator"},
        "chat": {"id": -1003757350269, "type": "supergroup"},
        "message_thread_id": 9,
        "photo": [{"file_id": "photo_123"}],
    }

    with patch("src.bot.telegram_listener._send_plain") as mock_send:
        handle_message(msg)
        assert mock_send.called
        args, kwargs = mock_send.call_args
        assert "Foto diterima!" in args[2]
        assert "/deal" in args[2]
