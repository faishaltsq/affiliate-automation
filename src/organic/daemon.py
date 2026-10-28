"""
24/7 Autonomous Rara Kinanti Daemon
Continuously publishes organic Threads posts with humanlike timing.
- ~80-110 minute intervals + random jitter (±15 min)
- Night mode (01:00-06:30): silent or 1 late-night thought
- Graceful shutdown on Ctrl+C
- Resumes correctly after PC sleep/restart
"""

import sys
import time
import signal
import random
from datetime import datetime, timezone, timedelta

from src.organic.publisher import execute_organic_post
from src.organic.db import init_organic_db, get_today_post_count, get_last_post_time
from src.logger import get_logger
from src.singleton import ensure_single_instance


def _check_affiliate_queue():
    """
    Check affiliate_queue for pending items.
    For each: check if any of our posts hits threshold.
    If yes → post affiliate comment + mark fired.
    """
    try:
        from src.affiliate.queue_manager import get_pending, mark_fired
        from src.affiliate.comment_engine import check_and_comment_our_posts

        pending = get_pending()
        if not pending:
            return

        log.info(f"🛒 Affiliate queue: {len(pending)} pending item(s). Checking post thresholds...")

        for item in pending:
            # Thresholds come from comment_engine defaults: the post must have a
            # live discussion (>=5 organic replies) and be past its initial push
            # window (>=45m) before a link goes anywhere near it.
            results = check_and_comment_our_posts(affiliate_url=item["affiliate_url"])
            fired = [r for r in results if r.get("status") == "commented"]
            if fired:
                for f in fired:
                    mark_fired(item["id"], f["post_id"], f.get("reply_id", ""))
                    log.info(
                        f"✅ Affiliate comment queued #{item['id']} fired on post {f['post_id']}"
                    )
    except Exception as e:
        log.warning(f"Affiliate queue check error: {e}")

log = get_logger("rara_daemon")

# Timing config
MIN_INTERVAL = 80 * 60   # 80 minutes in seconds
MAX_INTERVAL = 110 * 60  # 110 minutes
JITTER_RANGE = 15 * 60   # ±15 minutes
NIGHT_START = 1           # 01:00 WIB — go quiet
NIGHT_END = 5             # 05:00 WIB — wake up (catches early morning active viewer peak)
NIGHT_END_MINUTE = 0
DAILY_TARGET = 12
NIGHT_MAX_POSTS = 1       # At most 1 late-night thought

WIB = timezone(timedelta(hours=7))

_running = True


def _signal_handler(signum, frame):
    global _running
    log.info("Shutdown signal received. Finishing gracefully...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _now_wib():
    return datetime.now(WIB)


def _is_night(dt):
    if dt.hour < NIGHT_START:
        return False
    if dt.hour < NIGHT_END:
        return True
    if dt.hour == NIGHT_END and dt.minute < NIGHT_END_MINUTE:
        return True
    return False


def _seconds_until_morning():
    """Calculate seconds until NIGHT_END:NIGHT_END_MINUTE WIB."""
    now = _now_wib()
    wake = now.replace(hour=NIGHT_END, minute=NIGHT_END_MINUTE, second=0, microsecond=0)
    if wake <= now:
        wake += timedelta(days=1)
    return (wake - now).total_seconds()


def _calc_next_interval():
    base = random.randint(MIN_INTERVAL, MAX_INTERVAL)
    jitter = random.randint(-JITTER_RANGE, JITTER_RANGE)
    return max(60 * 30, base + jitter)  # minimum 30 min safety floor


def run_daemon(single: bool = False):
    """
    Main daemon loop.
    single=True: execute exactly one post and exit.
    """
    ensure_single_instance("rara_daemon")
    init_organic_db()
    used_today = []

    log.info("=" * 60)
    log.info("  RARA KINANTI AUTONOMOUS DAEMON STARTING")
    log.info(f"  Target: {DAILY_TARGET} posts/day, interval ~80-110min")
    log.info(f"  Night mode: {NIGHT_START:02d}:00 – {NIGHT_END:02d}:{NIGHT_END_MINUTE:02d} WIB")
    log.info("=" * 60)

    while _running:
        now = _now_wib()

        # Auto-sync with Threads API to discard posts deleted manually by user
        try:
            from src.organic.db import sync_with_threads_status
            sync_with_threads_status()
        except Exception as e:
            log.debug(f"Sync error: {e}")

        # Pull Threads engagement for published posts; feeds pillar weighting.
        try:
            from src.organic.feedback import refresh_post_metrics
            refresh_post_metrics()
        except Exception as e:
            log.debug(f"Metrics refresh error: {e}")

        today_count = get_today_post_count()

        # Reset used_today at midnight
        if now.hour == 0 and now.minute < 5:
            used_today = []

        # Night mode check
        if _is_night(now):
            if today_count > 0:
                # Already posted today, sleep until morning
                sleep_secs = _seconds_until_morning()
                log.info(f"🌙 Night mode active. Sleeping {sleep_secs/3600:.1f}h until {NIGHT_END:02d}:{NIGHT_END_MINUTE:02d} WIB...")
                _safe_sleep(sleep_secs)
                continue
            else:
                # Haven't posted yet — allow 1 late-night thought
                log.info("🌙 Night mode — allowing 1 late-night post")

        # Daily limit check
        if today_count >= DAILY_TARGET:
            sleep_secs = _seconds_until_morning()
            log.info(f"✅ Daily target reached ({today_count}/{DAILY_TARGET}). Sleeping until tomorrow morning...")
            _safe_sleep(sleep_secs)
            continue

        # Execute one organic post
        try:
            log.info(f"📝 Posting organic content ({today_count + 1}/{DAILY_TARGET})...")
            result = execute_organic_post(used_today=used_today)

            if result.get("skipped"):
                log.info(f"Skipped: {result.get('reason')}")
            else:
                topic = result.get("topic", "")
                threads_id = result.get("threads_id", "")
                used_today.append(topic)
                log.info(f"✅ Posted: '{topic[:40]}' → Threads: {threads_id}")

        except Exception as e:
            log.error(f"❌ Error during organic post: {e}")

        # Check affiliate queue for pending items
        _check_affiliate_queue()

        if single:
            log.info("Single mode — exiting after 1 post.")
            break

        # Calculate next post time
        interval = _calc_next_interval()
        next_time = _now_wib() + timedelta(seconds=interval)
        log.info(f"⏰ Next post in {interval/60:.0f} min (at ~{next_time.strftime('%H:%M')} WIB)")
        _safe_sleep(interval)

    log.info("Daemon stopped.")


def _safe_sleep(seconds: float):
    """Sleep in short chunks so Ctrl+C can interrupt."""
    global _running
    end = time.time() + seconds
    while _running and time.time() < end:
        remaining = end - time.time()
        time.sleep(min(30, remaining))


def show_status():
    """Print current daemon/posting status."""
    init_organic_db()
    count = get_today_post_count()
    last = get_last_post_time()
    now = _now_wib()
    night = _is_night(now)

    print(f"{'='*50}")
    print(f"  Rara Kinanti Daemon Status")
    print(f"  Time (WIB): {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Night mode: {'YES 🌙' if night else 'NO ☀️'}")
    print(f"  Posts today: {count}/{DAILY_TARGET}")
    print(f"  Last post:   {last or 'Never'}")
    print(f"{'='*50}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        show_status()
    elif "--once" in sys.argv:
        run_daemon(single=True)
    else:
        run_daemon()
