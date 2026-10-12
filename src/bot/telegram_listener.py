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
from src.affiliate.queue_manager import init_affiliate_queue
from src.bot.tiktok_agent import handle_tiktok_message
from src.bot.threads_agent import handle_threads_message

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
    if thread_id == TIKTOK_THREAD_ID:
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


def run_telegram_listener():
    """Long-polling loop for Telegram incoming messages."""
    init_affiliate_queue()

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
                msg = upd.get("message") or upd.get("channel_post")
                if msg:
                    handle_message(msg)

        except Exception as e:
            log.warning("Telegram polling loop exception: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    run_telegram_listener()
