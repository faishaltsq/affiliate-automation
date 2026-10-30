"""telegram_listener.py — Telegram listener for the V2 persona-first account.

Routing after the V2 cutover:
- Thread 9 (Threads) -> the V2 command set (src.bot.v2_commands)
- Thread 83 (TikTok) and Thread 246 (Clipper) -> disabled, with a notice

The legacy Threads, TikTok and Clipper agents are still importable and their
data is untouched, but nothing routes to them any more. Re-enabling one is a
deliberate edit here, not a flag flip.
"""
import logging
import os
import time
from pathlib import Path
import requests
from dotenv import dotenv_values
from src.logger import get_logger
from src.singleton import ensure_single_instance
from src.bot.menu_buttons import send_threads_menu

log = get_logger("tg_listener")

# Feature switchboard for the cutover. Each entry names a pipeline that is
# still built but no longer wired to the account.
LEGACY_PIPELINES_ENABLED = {
    "threads_legacy": False,
    "tiktok": False,
    "clipper": False,
    "affiliate": False,
}

DISABLED_NOTICE = (
    "⛔ <b>Pipeline ini dimatikan.</b>\n\n"
    "Automation Threads sekarang berjalan lewat V2 (persona-first).\n"
    "TikTok, Clipper, dan affiliate sedang nonaktif."
)

HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))


def _load_tg_config():
    hermes_vals = dotenv_values(Path(HERMES_HOME) / ".env")
    project_vals = dotenv_values(Path(__file__).parent.parent.parent / ".env")
    return {**hermes_vals, **project_vals}


_cfg = _load_tg_config()
BOT_TOKEN = _cfg.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = str(_cfg.get("TG_AFFILIATE_CHAT_ID", "-1003757350269"))
THREADS_THREAD_ID = str(_cfg.get("TG_THREADS_THREAD_ID", _cfg.get("TG_AFFILIATE_THREAD_ID", "9")))
TIKTOK_THREAD_ID = str(_cfg.get("TG_TIKTOK_THREAD_ID", "83"))
CLIPPER_THREAD_ID = str(_cfg.get("TG_CLIPPER_THREAD_ID", "246"))

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _send_plain(chat_id: str, thread_id, text: str, reply_to=None) -> None:
    """Send one HTML message. Used for routing notices, never for content."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if thread_id is not None:
        payload["message_thread_id"] = int(thread_id)
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as exc:  # noqa: BLE001
        log.warning("sendMessage failed: %s", exc)


def handle_message(msg: dict):
    """Route one message to the V2 command set.

    Thread 9 is the only live route. Threads 83 and 246 answer with a notice
    so an operator who types there is told the pipeline is off rather than
    getting silence or a legacy action.
    """
    text = msg.get("text", "").strip()
    if not text:
        return

    msg_id = msg.get("message_id")
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    raw_thread_id = msg.get("message_thread_id")
    thread_id = str(raw_thread_id) if raw_thread_id is not None else None

    # Filter messages from other groups if group chat
    if CHAT_ID and chat_id != CHAT_ID and chat.get("type") in ("group", "supergroup"):
        log.debug("Ignoring message from unexpected group chat %s", chat_id)
        return

    log.info("Incoming message [Chat: %s, Thread: %s, ID: %s]: %s",
             chat_id, thread_id, msg_id, text[:60])

    # Disabled topics answer and stop. No legacy handler is reachable.
    if thread_id == TIKTOK_THREAD_ID and not LEGACY_PIPELINES_ENABLED["tiktok"]:
        _send_plain(chat_id, raw_thread_id, DISABLED_NOTICE, reply_to=msg_id)
        return
    if thread_id == CLIPPER_THREAD_ID and not LEGACY_PIPELINES_ENABLED["clipper"]:
        _send_plain(chat_id, raw_thread_id, DISABLED_NOTICE, reply_to=msg_id)
        return

    # V2 command center. It only claims messages it recognises AND is enabled
    # for; anything else gets the disabled notice rather than a legacy action,
    # because no legacy handler is wired to the account any more.
    try:
        from src.bot.v2_commands import handle_v2_command

        v2_result = handle_v2_command(msg, text, msg_id, raw_thread_id)
        if v2_result.handled:
            if v2_result.text:
                target = int(thread_id) if thread_id else int(THREADS_THREAD_ID)
                _send_plain(chat_id, target, v2_result.text, reply_to=msg_id)
            return
    except Exception as e:  # noqa: BLE001 — a broken command must not kill the bot
        log.error("V2 command routing failed: %s", e, exc_info=True)
        _send_plain(chat_id, raw_thread_id,
                    "⚠️ Perintah gagal diproses. Cek log pipeline.", reply_to=msg_id)
        return

    if text.startswith("/"):
        _send_plain(
            chat_id, raw_thread_id,
            "Perintah itu tidak dikenal di V2. Kirim <code>/help</code> "
            "untuk daftar perintah.",
            reply_to=msg_id,
        )
        return

    # Free text in Thread 9 is a strategy question for the operator, not a
    # publish trigger. V2 has no conversational publish path by design.
    _send_plain(
        chat_id, raw_thread_id,
        "V2 hanya menerima perintah. Coba <code>/help</code>, "
        "<code>/draft</code>, atau <code>/status</code>.",
        reply_to=msg_id,
    )



def _answer_callback(callback_query_id: str, text: Optional[str] = None):
    """Acknowledge a callback query to stop loading spinner and optionally show toast."""
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        requests.post(f"{API}/answerCallbackQuery", json=payload, timeout=5)
    except Exception:
        pass


def _sync_telegram_commands():
    """Register the V2 command set in the Telegram UI for this group."""
    commands = [
        {"command": "menu", "description": "Tampilkan panel tombol kontrol"},
        {"command": "persona", "description": "Identitas aktif & aturan suara"},
        {"command": "plan", "description": "Rencana & ide slot berikutnya"},
        {"command": "ideas", "description": "Kumpulan ide terkini"},
        {"command": "draft", "description": "Pratinjau draf (tanpa publish)"},
        {"command": "schedule", "description": "Jadwal 7 hari (WIB)"},
        {"command": "analytics", "description": "Metrik terverifikasi"},
        {"command": "products", "description": "Katalog & status afiliasi"},
        {"command": "status", "description": "Kesehatan sistem & antrean"},
        {"command": "help", "description": "Panduan perintah V2"},
    ]
    try:
        requests.post(
            f"{API}/setMyCommands",
            json={"commands": commands,
                  "scope": {"type": "chat", "chat_id": int(CHAT_ID)}},
            timeout=10,
        )
    except Exception as e:
        log.warning("Could not set bot commands: %s", e)



def handle_callback_query(cbq: dict):
    """Route an inline button press into the V2 command set.

    V2 buttons carry an opaque token (see src/adapters/telegram.py), not a
    draft id, so the payload is validated and consumed there. Legacy button
    prefixes (tt:/th:/cl:) are answered with the disabled notice instead of
    being dispatched to an agent that is no longer wired up.
    """
    cbq_id = cbq.get("id")
    raw_data = (cbq.get("data") or "").strip()
    sender_msg = cbq.get("message") or {}
    thread_id = sender_msg.get("message_thread_id")
    chat_id = str(sender_msg.get("chat", {}).get("id", "")) or CHAT_ID
    msg_id = sender_msg.get("message_id")
    user_info = (cbq.get("from", {}).get("username")
                 or cbq.get("from", {}).get("first_name", "user"))

    log.info("Callback Query from @%s: data='%s', msg_id=%s, thread=%s",
             user_info, raw_data, msg_id, thread_id)

    if raw_data.startswith(("tt:", "th:", "cl:")):
        _answer_callback(cbq_id, "Pipeline ini nonaktif")
        _send_plain(chat_id, thread_id, DISABLED_NOTICE)
        return

    if not raw_data:
        _answer_callback(cbq_id)
        return

    _answer_callback(cbq_id, "Memproses...")

    fake_msg = {
        "message_id": msg_id,
        "chat": sender_msg.get("chat", {}),
        "message_thread_id": thread_id,
        "from": cbq.get("from", {}),
    }

    try:
        from src.bot.v2_commands import handle_v2_command

        result = handle_v2_command(fake_msg, raw_data, msg_id, thread_id)
        if result.handled and result.text:
            _send_plain(chat_id, thread_id, result.text)
        elif not result.handled:
            log.warning("Unhandled callback payload: %s", raw_data)
    except Exception as exc:  # noqa: BLE001
        log.error("Callback '%s' failed: %s", raw_data, exc, exc_info=True)



def run_telegram_listener():
    """Long-polling loop for Telegram messages.

    No legacy scheduler is started here. The TikTok FYP scheduler and the
    affiliate queue used to be booted from this function, which meant the
    listener process was also a publisher. Content now belongs to
    `python -m src.v2_runtime`; this process only routes commands.
    """
    ensure_single_instance("telegram_listener")

    _sync_telegram_commands()

    # Flush old updates on startup while ensuring allowed_updates is set
    try:
        r = requests.post(
            f"{API}/getUpdates",
            json={"offset": -1, "timeout": 0,
                  "allowed_updates": ["message", "callback_query"]},
            timeout=10,
        )
        updates = r.json().get("result", [])
        if updates:
            flush_offset = updates[-1]["update_id"] + 1
            requests.post(
                f"{API}/getUpdates",
                json={"offset": flush_offset, "timeout": 0,
                      "allowed_updates": ["message", "callback_query"]},
                timeout=10,
            )
            log.info("Flushed %d old update(s)", len(updates))
    except Exception as exc:
        log.warning("Flush updates failed: %s", exc)

    log.info(
        "Telegram listener running — V2 command set on thread %s. "
        "TikTok (%s) and Clipper (%s) are disabled.",
        THREADS_THREAD_ID, TIKTOK_THREAD_ID, CLIPPER_THREAD_ID,
    )

    offset = None
    while True:
        try:
            payload = {
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            }
            if offset:
                payload["offset"] = offset

            r = requests.post(f"{API}/getUpdates", json=payload, timeout=40)
            if not r.ok:
                time.sleep(5)
                continue

            updates = r.json().get("result", [])
            for upd in updates:
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    handle_callback_query(upd["callback_query"])
                    continue
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue

                raw_text = (msg.get("text") or "").strip()
                text_lower = raw_text.lower().split("@")[0].strip()
                if text_lower in ("/menu", "menu", "/panel", "/control", "/tombol"):
                    tid = msg.get("message_thread_id")
                    if str(tid) == THREADS_THREAD_ID:
                        send_threads_menu(API, int(CHAT_ID), int(tid))
                    else:
                        _send_plain(
                            str(msg.get("chat", {}).get("id", "")) or CHAT_ID,
                            tid,
                            "Panel tombol hanya tersedia di topic Threads.",
                        )
                    continue

                handle_message(msg)

        except Exception as e:
            log.warning("Telegram polling loop exception: %s", e)
            time.sleep(5)



if __name__ == "__main__":
    run_telegram_listener()
