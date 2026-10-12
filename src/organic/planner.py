"""
Intelligent Pillar & Topic Planner for Organic Posting.
Balances content pillars, avoids duplicate topics, selects fresh trends.
"""

import random
from datetime import datetime, timezone, timedelta
from src.trends.multi_source import fetch_all_trends, EVERGREEN_ID_TOPICS
from src.organic.db import is_topic_recent, get_today_post_count
from src.organic.generator import PILLARS
from src.logger import get_logger

log = get_logger("organic_planner")

# How many consecutive posts can share the same pillar before rotating
MAX_SAME_PILLAR = 2

# Topics that can always be fresh even if posted recently (evergreen OK to repeat faster)
EVERGREEN_THRESHOLD_DAYS = 3  # only 3 days for evergreen vs 7 days for trending


def _get_wib_hour() -> int:
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).hour


def _select_pillar(hour: int, post_count_today: int) -> str:
    """
    Picks content pillar based on time of day.
    Ensures variety by cycling through categories.
    """
    # Time-of-day weights: higher weight = more likely
    if 6 <= hour < 10:
        # Morning rush
        weights = {
            "relatable_struggle": 4,
            "hot_take_debate": 2,
            "menfess_qna": 2,
            "breaking_trend": 1,
            "pop_culture_meme": 1,
            "lifestyle_dump": 1,
        }
    elif 10 <= hour < 14:
        # Siang kerja
        weights = {
            "hot_take_debate": 3,
            "breaking_trend": 3,
            "relatable_struggle": 2,
            "menfess_qna": 1,
            "pop_culture_meme": 1,
            "lifestyle_dump": 1,
        }
    elif 14 <= hour < 18:
        # Sore
        weights = {
            "breaking_trend": 3,
            "pop_culture_meme": 3,
            "hot_take_debate": 2,
            "relatable_struggle": 2,
            "lifestyle_dump": 1,
            "menfess_qna": 1,
        }
    elif 18 <= hour < 22:
        # Malam prime time
        weights = {
            "pop_culture_meme": 4,
            "menfess_qna": 3,
            "lifestyle_dump": 2,
            "hot_take_debate": 2,
            "breaking_trend": 2,
            "relatable_struggle": 1,
        }
    else:
        # Late night / midnight
        weights = {
            "menfess_qna": 4,
            "lifestyle_dump": 3,
            "relatable_struggle": 2,
            "pop_culture_meme": 2,
            "hot_take_debate": 1,
            "breaking_trend": 1,
        }

    pool = []
    for pillar, weight in weights.items():
        pool.extend([pillar] * weight)

    return random.choice(pool)


def _select_topic(trends: list[str], used_today: list[str]) -> tuple[str, str]:
    """
    Picks a fresh topic not used today and not in recent 7-day SQLite history.
    Falls back to evergreen topics if all trending topics are stale.
    Returns (topic, source) where source is 'trending' or 'evergreen'.
    """
    random.shuffle(trends)

    # Try fresh trending topics first
    for t in trends:
        if t not in used_today and not is_topic_recent(t, days=7):
            return t, "trending"

    # Try evergreen (shorter cooldown)
    evergreen_shuffled = EVERGREEN_ID_TOPICS[:]
    random.shuffle(evergreen_shuffled)
    for t in evergreen_shuffled:
        if t not in used_today and not is_topic_recent(t, days=EVERGREEN_THRESHOLD_DAYS):
            return t, "evergreen"

    # Last resort: pick least-used evergreen
    log.warning("All topics recently used — picking random evergreen as last resort")
    fallback = random.choice(EVERGREEN_ID_TOPICS)
    return fallback, "evergreen_forced"


def plan_single_post(used_today: list[str] = None) -> dict:
    """
    Plans a single organic post item.
    Returns dict: {topic, pillar, source}
    """
    if used_today is None:
        used_today = []

    hour = _get_wib_hour()
    today_count = get_today_post_count()

    # Fetch current trends
    trends = fetch_all_trends()

    # Select pillar and topic
    pillar = _select_pillar(hour, today_count)
    topic, source = _select_topic(trends, used_today)

    log.info(f"Planned post: pillar={pillar}, topic='{topic[:40]}', source={source}, wib_hour={hour}")
    return {
        "topic": topic,
        "pillar": pillar,
        "source": source,
    }


def plan_daily_batch(count: int = 10) -> list[dict]:
    """
    Plans a full day's worth of organic posts with diversity.
    Returns list of {topic, pillar, source} dicts.
    """
    trends = fetch_all_trends()
    used_topics = []
    posts = []

    for i in range(count):
        # Simulate hour spread across the day (07:00–23:00)
        simulated_hour = 7 + int((i / count) * 16)

        pillar = _select_pillar(simulated_hour, i)
        topic, source = _select_topic(trends, used_topics)
        used_topics.append(topic)

        posts.append({
            "topic": topic,
            "pillar": pillar,
            "source": source,
            "planned_hour": simulated_hour,
        })

    log.info(f"Planned {len(posts)} posts for today with {len(set(p['pillar'] for p in posts))} distinct pillars")
    return posts


if __name__ == "__main__":
    batch = plan_daily_batch(count=10)
    for i, item in enumerate(batch, 1):
        print(f"{i:02d}. [{item['pillar']:20s}] [{item['source']:10s}] {item['topic'][:60]}")
