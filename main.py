"""
CLI launcher — runs full pipeline or individual stages.

Usage:
  python main.py link "<url>"       # Run Link-to-Content pipeline from affiliate URL
  python main.py organic --once     # Post 1 organic Threads post now
  python main.py organic --daemon   # Run 24/7 autonomous daemon
  python main.py organic --status   # Show today's posting status
  python main.py organic --once --dry-run  # Generate content without posting
  python main.py trends             # Run trend research only
  python main.py worker             # Run worker on ranked topics
  python main.py full               # Trends → Worker
  python main.py monitor            # Save metrics snapshot
  python main.py single "SSD"       # Run single topic end-to-end
"""

import sys
from src.trend_job import run_trend_job
from src.worker import run_worker, process_topic, process_affiliate_link
from src.monitor.analytics import save_metrics_snapshot
from src.affiliate.shopee import get_shopee_token
from src.logger import get_logger

log = get_logger("main")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "full"

    if cmd == "organic":
        from src.organic.daemon import run_daemon, show_status
        from src.organic.publisher import execute_organic_post
        subcmd = sys.argv[2] if len(sys.argv) > 2 else "--daemon"

        if subcmd == "--status":
            show_status()
        elif subcmd == "--once":
            dry = "--dry-run" in sys.argv
            result = execute_organic_post(dry_run=dry)
            print("Result:", result)
        elif subcmd == "--daemon":
            run_daemon()
        else:
            print("Usage: python main.py organic [--once|--daemon|--status] [--dry-run]")

    elif cmd == "bot":
        from src.bot.telegram_listener import run_telegram_listener
        run_telegram_listener()

    elif cmd == "tiktok":
        from src.tiktok.pipeline import run_pipeline
        from src.tiktok.state import VideoStateDB
        from src.tiktok.config import load_config
        cfg = load_config()

        subcmd = sys.argv[2] if len(sys.argv) > 2 else "--upload"
        dry_run = "--dry-run" in sys.argv or subcmd == "--dry-run"

        if subcmd == "--status":
            db = VideoStateDB(cfg["db_path"])
            stats = db.get_stats(days=7)
            recent = db.get_recent_uploads(5)
            print("=== TikTok Video Automation Status ===")
            print(f"Total uploaded videos: {stats['total_uploaded']}")
            print(f"Total runs logged: {stats['total_runs']}")
            print(f"Pending clips in queue: {db.get_queued_count()}")
            if stats["last_run"]:
                print(f"Last run: {stats['last_run']['run_at']} (uploaded: {stats['last_run']['videos_uploaded']})")
            if recent:
                print("\nRecent uploads:")
                for r in recent:
                    print(f"  • {r['uploaded_at'][:19]} — {r['title'][:55]} ({r['video_id']})")
            db.close()
        else:
            count = None
            if "--count" in sys.argv:
                try:
                    idx = sys.argv.index("--count")
                    count = int(sys.argv[idx + 1])
                except (ValueError, IndexError):
                    pass
            kw = None
            if "--keyword" in sys.argv:
                try:
                    idx = sys.argv.index("--keyword")
                    kw = sys.argv[idx + 1]
                except (ValueError, IndexError):
                    pass

            res = run_pipeline(dry_run=dry_run, count=count, keyword_override=kw)
            print(f"TikTok Pipeline Result: success={res['success']}, uploaded={res['uploaded']}, found={res['found']}")

    elif cmd == "ops":
        from src.content_ops.pipeline import run_batch_production
        niche = "mancing"
        if "--niche" in sys.argv:
            try:
                idx = sys.argv.index("--niche")
                niche = sys.argv[idx + 1]
            except (ValueError, IndexError):
                pass
        count = 3
        if "--count" in sys.argv:
            try:
                idx = sys.argv.index("--count")
                count = int(sys.argv[idx + 1])
            except (ValueError, IndexError):
                pass

        print(f"=== Starting Content Operations [{niche}] — Target: {count} clips ===")
        res = run_batch_production(niche=niche, target_count=count)
        print(f"=== Content Ops Completed: {res['total_produced']} clips ready for scheduling! ===")

    elif cmd == "link":
        if len(sys.argv) < 3:
            print("Usage: python main.py link <affiliate_url> [optional_trend_hint]")
            sys.exit(1)
        url = sys.argv[2]
        trend_hint = sys.argv[3] if len(sys.argv) > 3 else ""
        process_affiliate_link(url, trend_hint=trend_hint)

    elif cmd == "trends":
        keyword = sys.argv[2] if len(sys.argv) > 2 else "gadgets"
        run_trend_job(keyword)

    elif cmd == "worker":
        run_worker()

    elif cmd == "full":
        keyword = sys.argv[2] if len(sys.argv) > 2 else "gadgets"
        run_trend_job(keyword)
        run_worker()

    elif cmd == "monitor":
        path = save_metrics_snapshot()
        print(f"Metrics saved to: {path}")

    elif cmd == "single":
        topic_name = sys.argv[2] if len(sys.argv) > 2 else "Portable SSD"
        try:
            tok = get_shopee_token()
        except Exception:
            tok = ""
        process_topic({"topic": topic_name}, tok)

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
