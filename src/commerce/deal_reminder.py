"""Deal reminder: automated notifications for operator to submit TikTok Shop deals & screenshots."""
from __future__ import annotations

import datetime
from datetime import timedelta, timezone
import logging
from typing import Optional

import requests

from src.core.storage import connect, transaction

log = logging.getLogger("deal_reminder")

WIB = timezone(timedelta(hours=7))
DEFAULT_REMINDER_HOURS = (12, 19)  # 12:00 WIB and 19:00 WIB


def _ensure_reminder_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deal_reminders (
            slot_key TEXT PRIMARY KEY,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def format_reminder_text() -> str:
    now_str = datetime.datetime.now(WIB).strftime("%H:%M WIB")
    return (
        f"⏰ <b>Pengingat Slot Deal & Afiliasi ({now_str})</b>\n\n"
        "Waktunya kurasi barang diskon & rekomendasi hari ini!\n"
        "Kirim screenshot keranjang / harga promo TikTok Shop dengan caption:\n"
        "<code>/deal &lt;link_tiktokshop&gt; [nama barang / catatan diskon]</code>\n\n"
        "<i>Bot akan otomatis menyusun draf Threads (Hook deal + pertanyaan diskusi) "
        "dan melampirkan screenshot untuk kamu review sebelum posting.</i>"
    )


def should_send_reminder(path=None, now_dt: Optional[datetime.datetime] = None) -> tuple[bool, str]:
    """Check if current time is within reminder window and hasn't been sent yet."""
    now = now_dt or datetime.datetime.now(WIB)
    hour = now.hour
    minute = now.minute

    # Within the first 20 minutes of a reminder hour
    if hour not in DEFAULT_REMINDER_HOURS or minute > 20:
        return False, ""

    slot_key = f"{now.strftime('%Y-%m-%d')}:{hour:02d}"
    with connect(path) as conn:
        _ensure_reminder_table(conn)
        row = conn.execute(
            "SELECT slot_key FROM deal_reminders WHERE slot_key = ?",
            (slot_key,),
        ).fetchone()
        if row:
            return False, slot_key

    return True, slot_key


def record_reminder_sent(slot_key: str, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            _ensure_reminder_table(conn)
            conn.execute(
                "INSERT OR IGNORE INTO deal_reminders (slot_key) VALUES (?)",
                (slot_key,),
            )


def send_deal_reminder(
    bot_token: str,
    chat_id: str,
    thread_id: Optional[int] = None,
    path=None,
    force: bool = False,
) -> bool:
    """Send reminder notification to Telegram if due or forced."""
    if not bot_token or not chat_id:
        return False

    now = datetime.datetime.now(WIB)
    due, slot_key = should_send_reminder(path=path, now_dt=now)

    if not due and not force:
        return False

    if force and not slot_key:
        slot_key = f"manual:{now.strftime('%Y-%m-%d:%H%M%S')}"

    text = format_reminder_text()
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if thread_id is not None:
        payload["message_thread_id"] = int(thread_id)

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=15,
        )
        if r.ok:
            if slot_key and not force:
                record_reminder_sent(slot_key, path=path)
            log.info("Sent deal reminder for %s", slot_key)
            return True
        log.warning("send_deal_reminder failed (%d): %s", r.status_code, r.text)
    except Exception as exc:
        log.warning("send_deal_reminder error: %s", exc)

    return False
