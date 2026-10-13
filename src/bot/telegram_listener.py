"""telegram_listener.py — Unified Telegram Listener for Social Automate Forum.

Routes messages intelligently based on Telegram message_thread_id:
- Thread 83 (🎬 TikTok Automate) -> src.bot.tiktok_agent
- Thread 9 (Threads & Affiliate) -> src.bot.threads_agent
"""

import logging
import os
import time
from pathlib import Path
import requests
from dotenv import dotenv_values

from src.logger import get_logger
from src.singleton import ensure_single_instance
from src.affiliate.queue_manager import init_affiliate_queue
from src.bot.tiktok_agent import handle_tiktok_message
from src.bot.threads_agent import handle_threads_message
from src.bot.clipper_agent import handle_clipper_message
from src.bot.menu_buttons import send_tiktok_menu, send_threads_menu, send_clipper_menu

log = get_logger("tg_listener")

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


def handle_message(msg: dict):
    """Route message to specific topic agent based on message_thread_id."""
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

    log.info("Incoming message [Chat: %s, Thread: %s, ID: %s]: %s", chat_id, thread_id, msg_id, text[:60])

    # Route based on thread_id
    if thread_id == CLIPPER_THREAD_ID:
        handle_clipper_message(msg, text, msg_id, int(thread_id))
    elif thread_id == TIKTOK_THREAD_ID:
        handle_tiktok_message(msg, text, msg_id, int(thread_id))
    elif thread_id == THREADS_THREAD_ID:
        handle_threads_message(msg, text, msg_id, int(thread_id))
    else:
        # Fallback for General topic or Private DM:
        # Inspect content to infer domain
        text_lower = text.lower()
        if any(w in text_lower for w in ["tiktok", "/upload", "/dryrun", "video", "yt", "youtube"]):
            target_tid = int(TIKTOK_THREAD_ID) if thread_id is None else raw_thread_id
            handle_tiktok_message(msg, text, msg_id, target_tid)
        else:
            target_tid = int(THREADS_THREAD_ID) if thread_id is None else raw_thread_id
            handle_threads_message(msg, text, msg_id, target_tid)


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
    """Register bot commands in Telegram UI specifically for this group."""
    commands = [
        {"command": "menu", "description": "Tampilkan panel tombol kontrol"},
        {"command": "status", "description": "Cek status & jadwal pipeline"},
        {"command": "upload", "description": "Download, render & upload TikTok + Shorts"},
        {"command": "dryrun", "description": "Tes render video tanpa upload"},
        {"command": "keywords", "description": "Daftar keyword TikTok satisfying"},
        {"command": "campaigns", "description": "Daftar campaign aktif Clippo.id"},
        {"command": "accounts", "description": "Daftar akun TikTok & status cookies"},
        {"command": "trending", "description": "Cari produk trending affiliate"},
        {"command": "help", "description": "Panduan lengkap perintah bot"},
    ]
    try:
        requests.post(
            f"{API}/setMyCommands",
            json={"commands": commands, "scope": {"type": "chat", "chat_id": int(CHAT_ID)}},
            timeout=10,
        )
    except Exception as e:
        log.warning("Could not set bot commands: %s", e)


def handle_callback_query(cbq: dict):
    """Route inline button presses to the correct thread agent."""
    cbq_id = cbq.get("id")
    raw_data = (cbq.get("data") or "").strip()
    sender_msg = cbq.get("message") or {}
    thread_id = sender_msg.get("message_thread_id")
    chat_id = str(sender_msg.get("chat", {}).get("id", "")) or CHAT_ID
    msg_id = sender_msg.get("message_id")
    user_info = cbq.get("from", {}).get("username") or cbq.get("from", {}).get("first_name", "user")

    log.info("Callback Query received from @%s: data='%s', msg_id=%s, thread=%s", user_info, raw_data, msg_id, thread_id)

    target_thread = thread_id
    cmd = raw_data

    if raw_data.startswith("tt:"):
        target_thread = int(TIKTOK_THREAD_ID)
        cmd = raw_data[3:]
    elif raw_data.startswith("th:"):
        target_thread = int(THREADS_THREAD_ID)
        cmd = raw_data[3:]
    elif raw_data.startswith("cl:"):
        target_thread = int(CLIPPER_THREAD_ID)
        cmd = raw_data[3:]
    else:
        # Backward-compatibility fallback for older unprefixed button callbacks
        cmd = raw_data
        if thread_id:
            target_thread = int(thread_id)
        elif any(raw_data.startswith(c) for c in ("/campaigns", "/accounts", "/addaccount")):
            target_thread = int(CLIPPER_THREAD_ID)
        elif any(raw_data.startswith(c) for c in ("/trending", "/affiliate", "/threads", "/queue")):
            target_thread = int(THREADS_THREAD_ID)
        else:
            target_thread = int(TIKTOK_THREAD_ID)

    if not cmd:
        _answer_callback(cbq_id)
        return

    _answer_callback(cbq_id, f"Menjalankan {cmd}...")

    target_thread_int = int(target_thread) if target_thread else int(TIKTOK_THREAD_ID)
    target_thread_str = str(target_thread_int)
    log.info("Dispatching callback to Thread %s: cmd=%s (raw=%s)", target_thread_str, cmd, raw_data)

    # Build synthetic message dict for agent handlers
    fake_msg = {
        "message_id": msg_id,
        "chat": sender_msg.get("chat", {}),
        "message_thread_id": target_thread_int,
        "from": cbq.get("from", {}),
    }

    try:
        if target_thread_str == CLIPPER_THREAD_ID:
            handle_clipper_message(fake_msg, cmd, msg_id, target_thread_int)
        elif target_thread_str == TIKTOK_THREAD_ID:
            handle_tiktok_message(fake_msg, cmd, msg_id, target_thread_int)
        elif target_thread_str == THREADS_THREAD_ID:
            handle_threads_message(fake_msg, cmd, msg_id, target_thread_int)
        else:
            log.warning("Unknown target thread %s for callback %s", target_thread_str, raw_data)
    except Exception as exc:
        log.error("Error executing callback command '%s': %s", cmd, exc, exc_info=True)


def run_telegram_listener():
    """Long-polling loop for Telegram incoming messages."""
    ensure_single_instance("telegram_listener")
    init_affiliate_queue()

    # Start scheduler
    from src.tiktok.config import load_config
    from src.tiktok.scheduler import start_scheduler
    cfg = load_config()
    sched_cfg = cfg.get("scheduler", {})
    if sched_cfg.get("enabled", True):
        start_scheduler(
            mode=sched_cfg.get("mode", "fyp"),
            fyp_slots=sched_cfg.get("fyp_slots", ["12:00", "16:30", "19:30"]),
            interval_hours=float(sched_cfg.get("interval_hours", 2)),
        )
        log.info("Scheduler started — mode=%s (fyp_slots=%s)", sched_cfg.get("mode", "fyp"), sched_cfg.get("fyp_slots"))

    # Menu buttons are sent only on /menu command, not on startup
    _sync_telegram_commands()

    # Flush old updates on startup while ensuring allowed_updates is set
    try:
        r = requests.post(
            f"{API}/getUpdates",
            json={"offset": -1, "timeout": 0, "allowed_updates": ["message", "callback_query"]},
            timeout=10
        )
        updates = r.json().get("result", [])
        if updates:
            flush_offset = updates[-1]["update_id"] + 1
            requests.post(
                f"{API}/getUpdates",
                json={"offset": flush_offset, "timeout": 0, "allowed_updates": ["message", "callback_query"]},
                timeout=10
            )
            log.info("Flushed %d old update(s)", len(updates))
    except Exception as exc:
        log.warning("Flush updates failed: %s", exc)

    log.info("Telegram Bot Listener running... Topic 83=TikTok, Topic 9=Threads, Topic 246=Clipper")

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
                # Handle inline button press
                if "callback_query" in upd:
                    handle_callback_query(upd["callback_query"])
                    continue
                msg = upd.get("message") or upd.get("channel_post")
                if msg:
                    # Handle /menu command — resend button panel to current thread
                    raw_text = (msg.get("text") or "").strip()
                    text_lower = raw_text.lower().split("@")[0].strip()
                    if text_lower in ("/menu", "menu", "/panel", "/control", "/tombol"):
                        tid = msg.get("message_thread_id")
                        tid_str = str(tid) if tid else ""
                        if tid_str == TIKTOK_THREAD_ID:
                            send_tiktok_menu(API, int(CHAT_ID), int(tid))
                        elif tid_str == THREADS_THREAD_ID:
                            send_threads_menu(API, int(CHAT_ID), int(tid))
                        elif tid_str == CLIPPER_THREAD_ID:
                            send_clipper_menu(API, int(CHAT_ID), int(tid))
                        else:
                            send_tiktok_menu(API, int(CHAT_ID), int(TIKTOK_THREAD_ID))
                        continue
                    handle_message(msg)

        except Exception as e:
            log.warning("Telegram polling loop exception: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_listener()
