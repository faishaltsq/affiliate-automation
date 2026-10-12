"""
Trend Job — runs as the scheduled cron entry point.
Calls all three trend sources, ranks topics, writes ranked_topics.json.
"""

import json
from src.trends.research import get_google_trends, get_reddit_rising, get_tiktok_trending, rank_topics
from src.logger import get_logger
from src.config import DATA_DIR

log = get_logger("trend_job")


def run_trend_job(seed_keyword: str = "gadgets") -> list[dict]:
    log.info(f"Trend job starting (seed: '{seed_keyword}')...")

    google = get_google_trends(seed_keyword)
    reddit = get_reddit_rising()
    tiktok = get_tiktok_trending()

    log.info(f"Collected: google={len(google)}, reddit={len(reddit)}, tiktok={len(tiktok)}")

    ranked = rank_topics(google, reddit, tiktok)
    log.info(f"Ranked {len(ranked)} topics written to {DATA_DIR / 'ranked_topics.json'}")
    return ranked


if __name__ == "__main__":
    result = run_trend_job()
    print(json.dumps(result, indent=2, ensure_ascii=False))
