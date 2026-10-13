"""scheduler.py — Autonomous scheduler: runs pipeline every N hours and notifies Telegram."""

import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger("tiktok.scheduler")

# Interval in seconds (default 2 hours)
DEFAULT_INTERVAL_HOURS = 2
_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _notify_telegram(msg: str, thread_id: str | None = None):
    """Send a message to Telegram TikTok topic."""
    try:
        import requests
        from src.tiktok.config import load_config
        cfg = load_config()
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg.get("telegram_bot_token", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID") or cfg.get("telegram_chat_id", "")
        tid = thread_id or os.environ.get("TG_TIKTOK_THREAD_ID") or cfg.get("telegram_tiktok_thread_id", "83")

        if not token or not chat_id:
            # Try loading from .env
            from pathlib import Path
            from dotenv import dotenv_values
            env = dotenv_values(Path(os.environ.get("HERMES_HOME", Path.home() / "AppData/Local/hermes")) / ".env")
            env2 = dotenv_values(".env")
            token = env2.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN", token)
            chat_id = env2.get("TELEGRAM_CHAT_ID") or env.get("TELEGRAM_CHAT_ID", chat_id)
            tid = env2.get("TG_TIKTOK_THREAD_ID") or env.get("TG_TIKTOK_THREAD_ID", tid)

        if not token or not chat_id:
            logger.warning("Telegram credentials not found, skipping notification")
            return

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "message_thread_id": int(tid),
                "text": msg,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
    except Exception as e:
        logger.error("Failed to send Telegram notification: %s", e)


def _run_scheduled_pipeline():
    """Execute one pipeline run as scheduled job."""
    from src.tiktok.pipeline import run_pipeline

    now = datetime.now(timezone.utc).astimezone()
    time_str = now.strftime("%H:%M")
    logger.info("Scheduled pipeline run at %s", time_str)

    _notify_telegram(
        f"⏰ <b>Jadwal Otomatis Aktif</b> — {time_str}\n"
        f"🎬 Memulai pipeline pencarian + render + upload..."
    )

    def progress_cb(text: str):
        if any(k in text for k in ["Melompati intro", "Merender", "Mengunggah", "Sukses", "Shorts", "gagal"]):
            _notify_telegram(text)

    try:
        res = run_pipeline(
            dry_run=False,
            count=1,
            progress_callback=progress_cb,
            upload_youtube=True,
        )

        if res.get("success"):
            vids = res.get("videos", [])
            v = vids[0] if vids else {}
            yt_url = v.get("youtube_url", "")
            yt_line = f"\n📺 <b>YouTube Shorts:</b> <a href='{yt_url}'>{yt_url}</a>" if yt_url else ""
            _notify_telegram(
                f"✅ <b>Pipeline Otomatis Selesai!</b>\n"
                f"📌 <b>Judul:</b> {v.get('title', 'Video baru')}\n"
                f"👤 <b>Channel:</b> {v.get('channel', '-')}\n"
                f"🎵 <b>TikTok:</b> Live di @pochitalkshop"
                f"{yt_line}\n\n"
                f"⏰ <i>Run berikutnya dalam {DEFAULT_INTERVAL_HOURS} jam.</i>"
            )
        else:
            errors = "; ".join(res.get("errors", [])) or "Tidak ada kandidat video"
            _notify_telegram(f"⚠️ <b>Pipeline Otomatis Gagal:</b>\n{errors}")

    except Exception as e:
        logger.exception("Scheduled pipeline error: %s", e)
        _notify_telegram(f"❌ <b>Error Pipeline Otomatis:</b>\n<code>{e}</code>")


def _scheduler_loop(interval_hours: float):
    """Main scheduler loop — runs every interval_hours hours."""
    interval_sec = interval_hours * 3600
    logger.info("Scheduler started — running every %.1f hours", interval_hours)

    _notify_telegram(
        f"🤖 <b>Scheduler Aktif!</b>\n"
        f"⏰ Pipeline otomatis setiap <b>{int(interval_hours)} jam</b>\n"
        f"🎬 Upload ke TikTok + YouTube Shorts\n"
        f"📅 Run pertama dimulai sekarang..."
    )

    # Run immediately on start
    _run_scheduled_pipeline()

    while not _stop_event.is_set():
        # Wait for next interval, checking stop every 60s
        elapsed = 0
        while elapsed < interval_sec and not _stop_event.is_set():
            time.sleep(min(60, interval_sec - elapsed))
            elapsed += 60

        if not _stop_event.is_set():
            _run_scheduled_pipeline()

    logger.info("Scheduler stopped.")


def start_scheduler(interval_hours: float = DEFAULT_INTERVAL_HOURS):
    """Start the scheduler in a daemon thread."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.warning("Scheduler already running")
        return

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        args=(interval_hours,),
        daemon=True,
        name="tiktok-scheduler",
    )
    _scheduler_thread.start()
    logger.info("Scheduler thread started (interval=%.1fh)", interval_hours)


def stop_scheduler():
    """Signal the scheduler to stop after current run."""
    _stop_event.set()
    logger.info("Scheduler stop signal sent")


def is_running() -> bool:
    return bool(_scheduler_thread and _scheduler_thread.is_alive())
