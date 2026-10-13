"""scheduler.py — Autonomous scheduler: runs pipeline on FYP peak hours and notifies Telegram."""

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("tiktok.scheduler")

# Defaults: Prime FYP windows in Indonesia (WIB)
# 12:00 = Istirahat siang
# 16:30 = Sore pulang kantor / santai
# 19:30 = Golden hour prime time malam
DEFAULT_FYP_SLOTS = ["12:00", "16:30", "19:30"]
DEFAULT_INTERVAL_HOURS = 2

_scheduler_thread: threading.Thread | None = None
_stop_event = threading.Event()
_current_mode: str = "fyp"
_active_fyp_slots: list[str] = list(DEFAULT_FYP_SLOTS)
_active_interval_hours: float = DEFAULT_INTERVAL_HOURS


def _format_wait(seconds: float) -> str:
    """Format seconds into readable human string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h} jam {m} menit"
    return f"{m} menit"


def get_next_fyp_run(slots: list[str], now: datetime | None = None) -> tuple[datetime, str, float]:
    """Calculate the next upcoming FYP run slot from the slot list."""
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    candidates = []
    for s in slots:
        parts = s.strip().split(":")
        if len(parts) != 2:
            continue
        try:
            h, m = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        dt_today = now.replace(hour=h, minute=m, second=0, microsecond=0)
        # Small 10s leeway
        if dt_today > now + timedelta(seconds=10):
            candidates.append((dt_today, s.strip()))
        else:
            dt_tomorrow = dt_today + timedelta(days=1)
            candidates.append((dt_tomorrow, s.strip()))

    if not candidates:
        fallback_dt = now + timedelta(hours=4)
        return fallback_dt, fallback_dt.strftime("%H:%M"), 14400.0

    candidates.sort(key=lambda x: x[0])
    next_dt, slot_str = candidates[0]
    wait_sec = max(0.0, (next_dt - now).total_seconds())
    return next_dt, slot_str, wait_sec


def get_next_run_info() -> dict:
    """Return dict describing scheduler current state and next planned execution."""
    running = is_running()
    now = datetime.now(timezone.utc).astimezone()

    if not running:
        return {"running": False, "mode": _current_mode}

    if _current_mode == "fyp":
        next_dt, slot_str, wait_sec = get_next_fyp_run(_active_fyp_slots, now)
        return {
            "running": True,
            "mode": "fyp",
            "slots": _active_fyp_slots,
            "next_dt": next_dt,
            "next_slot": slot_str,
            "wait_sec": wait_sec,
            "wait_str": _format_wait(wait_sec),
        }
    else:
        return {
            "running": True,
            "mode": "interval",
            "interval_hours": _active_interval_hours,
        }


def get_status_str() -> str:
    """Return compact status string for /status dashboard."""
    if not is_running():
        return "🔴 Nonaktif"
    info = get_next_run_info()
    if info.get("mode") == "fyp":
        slots_str = ", ".join(info.get("slots", []))
        next_s = info.get("next_slot", "-")
        wait_s = info.get("wait_str", "-")
        return f"🟢 Aktif — Mode FYP ({slots_str} WIB) | Slot Terdekat: {next_s} WIB ({wait_s})"
    return f"🟢 Aktif (Interval {info.get('interval_hours', 2)} jam)"


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


def _run_scheduled_pipeline(slot_name: str | None = None):
    """Execute one pipeline run as scheduled job (anti-spam, only start + finish notifications)."""
    from src.tiktok.pipeline import run_pipeline

    now = datetime.now(timezone.utc).astimezone()
    time_str = now.strftime("%H:%M")
    slot_label = f" (Slot {slot_name} WIB)" if slot_name else ""
    logger.info("Scheduled pipeline run at %s%s", time_str, slot_label)

    _notify_telegram(
        f"⏰ <b>Jadwal FYP Otomatis Aktif</b> — {time_str}{slot_label}\n"
        f"🎬 Memulai pencarian video + render lanczos + dual upload TikTok & Shorts..."
    )

    try:
        res = run_pipeline(
            dry_run=False,
            count=1,
            progress_callback=None,  # Anti-spam: do not send intermediate progress
            upload_youtube=True,
        )

        if res.get("success"):
            vids = res.get("videos", [])
            v = vids[0] if vids else {}
            yt_url = v.get("youtube_url", "")
            yt_line = f"\n📺 <b>YouTube Shorts:</b> <a href='{yt_url}'>{yt_url}</a>" if yt_url else ""

            next_info_str = ""
            if _current_mode == "fyp":
                _, next_slot, next_wait = get_next_fyp_run(_active_fyp_slots)
                next_info_str = f"\n\n⏰ <i>Jadwal FYP berikutnya: <b>{next_slot} WIB</b> ({_format_wait(next_wait)})</i>"
            else:
                next_info_str = f"\n\n⏰ <i>Run berikutnya dalam {_active_interval_hours} jam.</i>"

            _notify_telegram(
                f"✅ <b>Upload FYP Berhasil!</b>\n"
                f"📌 <b>Judul:</b> {v.get('title', 'Video baru')}\n"
                f"👤 <b>Channel:</b> {v.get('channel', '-')}\n"
                f"🎵 <b>TikTok:</b> Live di @pochitalkshop"
                f"{yt_line}"
                f"{next_info_str}"
            )
        else:
            errors = "; ".join(res.get("errors", [])) or "Tidak ada kandidat video"
            _notify_telegram(f"⚠️ <b>Pipeline Otomatis Gagal:</b>\n{errors}")

    except Exception as e:
        logger.exception("Scheduled pipeline error: %s", e)
        _notify_telegram(f"❌ <b>Error Pipeline Otomatis:</b>\n<code>{e}</code>")


def _scheduler_loop():
    """Main scheduler loop."""
    logger.info("Scheduler loop started: mode=%s", _current_mode)

    # Announce scheduler activation
    if _current_mode == "fyp":
        next_dt, slot_str, wait_sec = get_next_fyp_run(_active_fyp_slots)
        slots_display = ", ".join(_active_fyp_slots)
        _notify_telegram(
            f"🤖 <b>Scheduler TikTok &amp; Shorts Aktif!</b>\n"
            f"🎯 <b>Mode:</b> FYP Prime Time (Anti-Spam)\n"
            f"⏰ <b>Jadwal Slot:</b> {slots_display} WIB\n"
            f"⏳ <b>Jadwal Terdekat:</b> <b>{slot_str} WIB</b> (dalam {_format_wait(wait_sec)})\n"
            f"🎬 Dual-Upload: TikTok (@pochitalkshop) + YouTube Shorts"
        )
    else:
        _notify_telegram(
            f"🤖 <b>Scheduler Aktif!</b>\n"
            f"⏰ Pipeline otomatis setiap <b>{int(_active_interval_hours)} jam</b>\n"
            f"🎬 Upload ke TikTok + YouTube Shorts"
        )

    while not _stop_event.is_set():
        if _current_mode == "fyp":
            next_dt, slot_str, wait_sec = get_next_fyp_run(_active_fyp_slots)
            logger.info("Next FYP slot: %s at %s (wait: %.0fs / %s)", slot_str, next_dt.strftime("%Y-%m-%d %H:%M"), wait_sec, _format_wait(wait_sec))

            # Sleep in chunks of 30s until target time arrives
            while wait_sec > 10 and not _stop_event.is_set():
                sleep_chunk = min(30.0, wait_sec)
                time.sleep(sleep_chunk)
                now = datetime.now(timezone.utc).astimezone()
                wait_sec = max(0.0, (next_dt - now).total_seconds())

            if not _stop_event.is_set():
                logger.info("Triggering FYP scheduled run for slot %s", slot_str)
                _run_scheduled_pipeline(slot_name=slot_str)
                # Sleep 70 seconds so we don't re-trigger the same minute
                time.sleep(70)
        else:
            interval_sec = _active_interval_hours * 3600
            elapsed = 0
            while elapsed < interval_sec and not _stop_event.is_set():
                time.sleep(min(30, interval_sec - elapsed))
                elapsed += 30

            if not _stop_event.is_set():
                _run_scheduled_pipeline()

    logger.info("Scheduler loop terminated.")


def start_scheduler(
    mode_or_interval=None,
    fyp_slots: list[str] | None = None,
    interval_hours: float = DEFAULT_INTERVAL_HOURS,
    mode: str | None = None,
):
    """Start the scheduler daemon thread."""
    global _scheduler_thread, _current_mode, _active_fyp_slots, _active_interval_hours

    if _scheduler_thread and _scheduler_thread.is_alive():
        logger.warning("Scheduler already running")
        return

    # Resolve arguments
    if isinstance(mode_or_interval, (int, float)):
        _current_mode = "interval"
        _active_interval_hours = float(mode_or_interval)
    elif isinstance(mode_or_interval, str):
        _current_mode = mode_or_interval
    elif mode:
        _current_mode = mode
    else:
        try:
            from src.tiktok.config import load_config
            cfg = load_config()
            sc = cfg.get("scheduler", {})
            _current_mode = sc.get("mode", "fyp")
            if "fyp_slots" in sc:
                _active_fyp_slots = list(sc["fyp_slots"])
            if "interval_hours" in sc:
                _active_interval_hours = float(sc["interval_hours"])
        except Exception as e:
            logger.warning("Could not load scheduler config: %s", e)
            _current_mode = "fyp"

    if fyp_slots:
        _active_fyp_slots = list(fyp_slots)
    if interval_hours and not isinstance(mode_or_interval, (int, float)):
        _active_interval_hours = float(interval_hours)

    _stop_event.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop,
        daemon=True,
        name="tiktok-scheduler",
    )
    _scheduler_thread.start()
    logger.info(
        "Scheduler thread started (mode=%s, fyp_slots=%s, interval=%.1fh)",
        _current_mode,
        _active_fyp_slots,
        _active_interval_hours,
    )


def stop_scheduler():
    """Signal the scheduler to stop."""
    _stop_event.set()
    logger.info("Scheduler stop signal sent")


def is_running() -> bool:
    """Check if scheduler thread is alive."""
    return bool(_scheduler_thread and _scheduler_thread.is_alive())
