"""
Telegram Dispatcher for Affiliate Content Pipeline
- Sends generated briefs to a Telegram thread (forum topic)
- Bot API based (no userbot needed)
- Creates forum topic on first run, reuses thread_id after
"""

import os
import re
import json
import html
import requests
from pathlib import Path

from src.config import DATA_DIR
from src.logger import get_logger

log = get_logger("telegram")

# Load Telegram config from Hermes .env or project .env
HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))

def _load_tg_config():
    from dotenv import dotenv_values
    hermes_vals = dotenv_values(Path(HERMES_HOME) / ".env")
    project_vals = dotenv_values(Path(__file__).parent.parent.parent / ".env")
    # Project .env overrides hermes .env
    merged = {**hermes_vals, **project_vals}
    return merged

_cfg = _load_tg_config()

BOT_TOKEN = _cfg.get("TELEGRAM_BOT_TOKEN", "")
# Chat ID where bot is admin (group with topics enabled, or private chat)
CHAT_ID = _cfg.get("TG_AFFILIATE_CHAT_ID") or _cfg.get("TELEGRAM_HOME_CHANNEL", "")
# Persisted thread/topic ID
THREAD_ID_FILE = DATA_DIR / "tg_thread_id.json"

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _get_thread_id() -> int | None:
    """Load persisted thread_id from disk."""
    if THREAD_ID_FILE.exists():
        try:
            data = json.loads(THREAD_ID_FILE.read_text())
            return data.get("thread_id")
        except Exception:
            pass
    return None


def _save_thread_id(thread_id: int):
    THREAD_ID_FILE.write_text(json.dumps({"thread_id": thread_id}))


def create_forum_topic(name: str = "📦 Affiliate Content") -> int | None:
    """
    Creates a forum topic in the target chat.
    Returns message_thread_id or None if not a forum.
    """
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram credentials not set")
        return None

    r = requests.post(f"{API}/createForumTopic", json={
        "chat_id": int(CHAT_ID),
        "name": name,
        "icon_color": 7322096,  # green
    })

    if r.ok:
        thread_id = r.json()["result"]["message_thread_id"]
        _save_thread_id(thread_id)
        log.info(f"Created forum topic '{name}' → thread_id={thread_id}")
        return thread_id
    else:
        log.warning(f"createForumTopic failed: {r.text}")
        return None


def get_or_create_thread() -> int | None:
    """Returns thread_id from env, disk cache, or creates a new topic."""
    # 1. Env var (highest priority — set after manual setup)
    env_tid = _cfg.get("TG_AFFILIATE_THREAD_ID")
    if env_tid:
        return int(env_tid)
    # 2. Disk cache
    tid = _get_thread_id()
    if tid:
        return tid
    # 3. Create new
    return create_forum_topic()


def _escape(text: str) -> str:
    """Escape HTML special chars for Telegram HTML parse mode."""
    return html.escape(text, quote=False)


def _truncate(text: str, limit: int = 4000) -> str:
    """Truncate to Telegram's 4096 char limit with safety margin."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... (truncated)"


def send_brief_to_telegram(
    topic_name: str,
    brief: str,
    product: dict | None = None,
    image_url: str = "",
    compliance_score: int = 0,
) -> dict:
    """
    Sends a formatted affiliate brief to the Telegram thread.
    Returns API response dict.
    """
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("Telegram not configured — skipping send")
        return {"error": "not_configured"}

    thread_id = get_or_create_thread()

    # Build formatted message
    prod_name = _escape(product.get("name", "N/A")) if product else "N/A"
    prod_price = f"${product['price']:.2f}" if product and product.get("price") else "N/A"
    prod_url = product.get("affiliate_url", "") if product else ""

    # Extract title from brief (first # line)
    title = topic_name
    for line in brief.split("\n"):
        line = line.strip()
        if line.startswith("# "):
            title = line.lstrip("# ").strip()
            break

    # Brief preview (first 800 chars)
    preview = _truncate(brief, 800)

    msg = (
        f"📦 <b>{_escape(title)}</b>\n"
        f"\n"
        f"🏷 <b>Product:</b> {prod_name}\n"
        f"💰 <b>Price:</b> {prod_price}\n"
        f"📊 <b>Compliance Score:</b> {compliance_score}/100\n"
        f"\n"
        f"<b>Brief Preview:</b>\n"
        f"<pre>{_escape(preview)}</pre>\n"
    )

    if prod_url:
        msg += f"\n🔗 <a href=\"{prod_url}\">Affiliate Link</a>\n"

    msg = _truncate(msg)

    payload = {
        "chat_id": int(CHAT_ID),
        "text": msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=10)
        if r.ok:
            msg_id = r.json()["result"]["message_id"]
            log.info(f"Telegram sent: msg_id={msg_id}, thread={thread_id}")
        else:
            log.error(f"Telegram send failed: {r.text}")
        return r.json()
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return {"error": str(e)}


def send_image_to_telegram(image_url: str, caption: str = "") -> dict:
    """Sends image with optional caption to the affiliate thread."""
    if not BOT_TOKEN or not CHAT_ID:
        return {"error": "not_configured"}

    thread_id = get_or_create_thread()

    # Convert basic markdown *bold* to <b>bold</b> if present
    formatted_caption = re.sub(r"\*([^\*]+)\*", r"<b>\1</b>", caption)

    payload = {
        "chat_id": int(CHAT_ID),
        "photo": image_url,
        "caption": _truncate(formatted_caption, 1024),
        "parse_mode": "HTML",
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        r = requests.post(f"{API}/sendPhoto", json=payload, timeout=10)
        if not r.ok:
            # If HTML parsing failed, retry as plain text
            payload.pop("parse_mode", None)
            payload["caption"] = _truncate(caption, 1024)
            r = requests.post(f"{API}/sendPhoto", json=payload, timeout=10)

        if r.ok:
            log.info(f"Telegram image sent to thread={thread_id}")
        else:
            log.warning(f"Telegram sendPhoto failed: {r.text}")
        return r.json()
    except Exception as e:
        log.error(f"Telegram image error: {e}")
        return {"error": str(e)}


def send_status_update(text: str) -> dict:
    """Quick text notification to the thread (pipeline status, errors)."""
    if not BOT_TOKEN or not CHAT_ID:
        return {"error": "not_configured"}

    thread_id = get_or_create_thread()
    payload = {
        "chat_id": int(CHAT_ID),
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if thread_id:
        payload["message_thread_id"] = thread_id

    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    # Quick test
    print(f"Bot token: {'set' if BOT_TOKEN else 'MISSING'}")
    print(f"Chat ID: {CHAT_ID}")
    print(f"Thread ID: {_get_thread_id()}")
    result = send_status_update("🔧 <b>Affiliate Pipeline</b> — Telegram integration test OK")
    print("Result:", result)
