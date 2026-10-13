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


def _answer_callback(callback_query_id: str):
    """Acknowledge a callback query to stop the loading spinner on button."""
    try:
        requests.post(f"{API}/answerCallbackQuery", json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception:
        pass


def handle_callback_query(cbq: dict):
    """Route inline button presses to the correct thread agent."""
    cbq_id = cbq.get("id")
    data = (cbq.get("data") or "").strip()
    sender_msg = cbq.get("message", {})
    thread_id = sender_msg.get("message_thread_id")
    chat_id = str(sender_msg.get("chat", {}).get("id", ""))
    msg_id = sender_msg.get("message_id")

    _answer_callback(cbq_id)

    if not data or not thread_id:
        return

    thread_str = str(thread_id)
    log.info("Callback [Thread: %s] data=%s", thread_str, data)

    # Build a synthetic message dict for the agent handlers
    fake_msg = {"message_id": msg_id, "chat": sender_msg.get("chat", {}), "message_thread_id": thread_id}

    if thread_str == CLIPPER_THREAD_ID:
        handle_clipper_message(fake_msg, data, msg_id, thread_id)
    elif thread_str == TIKTOK_THREAD_ID:
        handle_tiktok_message(fake_msg, data, msg_id, thread_id)
    elif thread_str == THREADS_THREAD_ID:
        handle_threads_message(fake_msg, data, msg_id, thread_id)


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

    # Flush old updates on startup
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": -1, "timeout": 0}, timeout=10)
        updates = r.json().get("result", [])
        if updates:
            flush_offset = updates[-1]["update_id"] + 1
            requests.get(f"{API}/getUpdates", params={"offset": flush_offset, "timeout": 0}, timeout=10)
            log.info("Flushed %d old update(s)", len(updates))
    except Exception as exc:
        log.warning("Flush updates failed: %s", exc)

    log.info("Telegram Bot Listener running... Topic 83=TikTok, Topic 9=Threads")

    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset

            r = requests.get(f"{API}/getUpdates", params=params, timeout=40)
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
                    text = (msg.get("text") or "").strip().lower()
                    if text == "/menu":
                        tid = msg.get("message_thread_id")
                        if tid:
                            tid_str = str(tid)
                            if tid_str == TIKTOK_THREAD_ID:
                                send_tiktok_menu(API, int(CHAT_ID), tid)
                            elif tid_str == THREADS_THREAD_ID:
                                send_threads_menu(API, int(CHAT_ID), tid)
                            elif tid_str == CLIPPER_THREAD_ID:
                                send_clipper_menu(API, int(CHAT_ID), tid)
                        continue
                    handle_message(msg)

        except Exception as e:
            log.warning("Telegram polling loop exception: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_listener()
