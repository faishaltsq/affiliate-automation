"""menu_buttons.py — Send command menu buttons to each forum thread on startup."""

import logging
import requests
from typing import Optional

logger = logging.getLogger("bot.menu_buttons")


def _send_message(api: str, chat_id: int, thread_id: int, text: str, reply_markup: dict) -> Optional[int]:
    """Send a message with inline keyboard and return message_id."""
    try:
        r = requests.post(
            f"{api}/sendMessage",
            json={
                "chat_id": chat_id,
                "message_thread_id": thread_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
            },
            timeout=10,
        )
        if r.ok:
            return r.json()["result"]["message_id"]
        else:
            logger.warning("sendMessage failed (%d): %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.error("Error sending menu: %s", e)
    return None


def send_tiktok_menu(api: str, chat_id: int, thread_id: int):
    """Send command menu buttons to TikTok Automate thread (83)."""
    text = (
        "🎬 <b>TikTok Automate — Panel Kontrol</b>\n"
        "Pilih perintah di bawah atau ketik langsung:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📋 Status Bot", "callback_data": "/status"},
                {"text": "⚙️ Jadwal Upload", "callback_data": "/schedule"},
            ],
            [
                {"text": "🎬 Upload Video", "callback_data": "/upload"},
                {"text": "🧪 Dry Run", "callback_data": "/dryrun"},
            ],
            [
                {"text": "🔄 Scheduler ON/OFF", "callback_data": "/togglescheduler"},
                {"text": "📝 List Keyword", "callback_data": "/keywords"},
            ],
            [
                {"text": "❓ Bantuan Lengkap", "callback_data": "/help"},
            ],
        ]
    }
    msg_id = _send_message(api, chat_id, thread_id, text, keyboard)
    if msg_id:
        logger.info("TikTok menu sent (msg_id=%d)", msg_id)


def send_threads_menu(api: str, chat_id: int, thread_id: int):
    """Send command menu buttons to Threads & Affiliate thread (9)."""
    text = (
        "🧵 <b>Affiliate Content — Panel Kontrol</b>\n"
        "Pilih perintah di bawah atau ketik langsung:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Cari Produk Trending", "callback_data": "/trending"},
                {"text": "🔗 Generate Link Afiliasi", "callback_data": "/affiliate"},
            ],
            [
                {"text": "✍️ Buat Konten Threads", "callback_data": "/threads"},
                {"text": "📅 Jadwal Posting", "callback_data": "/schedule"},
            ],
            [
                {"text": "📋 Status Queue", "callback_data": "/status"},
                {"text": "❓ Bantuan Lengkap", "callback_data": "/help"},
            ],
        ]
    }
    msg_id = _send_message(api, chat_id, thread_id, text, keyboard)
    if msg_id:
        logger.info("Threads menu sent (msg_id=%d)", msg_id)


def send_clipper_menu(api: str, chat_id: int, thread_id: int):
    """Send command menu buttons to Clipper Automate thread (246)."""
    text = (
        "✂️ <b>Clipper Automate — Panel Kontrol</b>\n"
        "Pilih perintah di bawah atau ketik langsung:"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🔥 Daftar Campaign", "callback_data": "/campaigns"},
                {"text": "👥 Daftar Akun TikTok", "callback_data": "/accounts"},
            ],
            [
                {"text": "📋 Analisis Brief", "callback_data": "/brief "},
                {"text": "✂️ Buat & Upload Klip", "callback_data": "/clip "},
            ],
            [
                {"text": "➕ Tambah Akun", "callback_data": "/addaccount "},
                {"text": "❓ Bantuan Lengkap", "callback_data": "/help"},
            ],
        ]
    }
    msg_id = _send_message(api, chat_id, thread_id, text, keyboard)
    if msg_id:
        logger.info("Clipper menu sent (msg_id=%d)", msg_id)


def setup_all_menus(api: str, chat_id: int, tiktok_thread: int, threads_thread: int, clipper_thread: int):
    """Send menu buttons to all 3 forum threads (no pinning)."""
    send_tiktok_menu(api, chat_id, tiktok_thread)
    send_threads_menu(api, chat_id, threads_thread)
    send_clipper_menu(api, chat_id, clipper_thread)
    logger.info("All thread menus sent.")
