"""
Intelligent Pillar & Topic Planner for Organic Posting.
Balances 4 core content pillars:
1. breaking_disaster     (Bencana alam, krisis nasional, BMKG, cuaca ekstrem)
2. football_sports       (Sepak bola Indonesia & dunia, Timnas, EPL, UCL)
3. tech_gadget_ai        (Teknologi, inovasi digital, smartphone, AI)
4. market_finance_stocks (IHSG, saham lokal IDX, Wall Street, dividen, makroekonomi)
"""

import random
from datetime import datetime, timezone, timedelta
from src.trends.multi_source import (
    fetch_trends_for_pillar,
    fetch_hot_topics,
    pillar_for_topic,
    get_source_summary,
    EVERGREEN_BY_PILLAR,
)
from src.organic.db import is_topic_recent, get_today_post_count, get_today_chain_count, get_pillar_performance
from src.organic.generator import CHAIN_PILLARS
from src.organic.personas import is_sensitive_topic
from src.logger import get_logger

log = get_logger("organic_planner")

ALL_PILLARS = [
    "breaking_disaster",
    "football_sports",
    "tech_gadget_ai",
    "market_finance_stocks"
]

EVERGREEN_THRESHOLD_DAYS = 3

# A headline this hot outranks the pillar weighting — a live national story
# (karhutla, IHSG trading halt) beats the usual football-heavy mix.
HOT_TOPIC_MIN_HEAT = 18

# Chain quota. Four-part threads earn more dwell time and more replies, but a
# feed made only of them reads like a content farm; the account keeps its
# spontaneous texture by staying mostly single posts.
CHAIN_MAX_PER_DAY = 5
CHAIN_PROBABILITY = 0.75


def _topic_summary(topic: str) -> str:
    """Feed description captured for this headline, or '' when none was stored.

    Evergreen topics are invented by this module rather than fetched, so there
    is no feed behind them — the empty string is correct there, and the
    generator treats it as "no figures allowed".
    """
    try:
        return get_source_summary(topic)
    except Exception:
        return ""


def _get_wib_hour() -> int:
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).hour


def _get_post_format(hour: int, post_count_today: int) -> str:
    """
    Data-backed format selection (from Threads Insights):
    Editorial image cards drive ~50x-100x higher FYP reach than plain text.
    80% with_image (editorial card), 20% text_only.
    """
    return random.choices(["with_image", "text_only"], weights=[80, 20])[0]


def _get_post_style(
    hour: int, pillar: str = "", topic: str = "", source_summary: str = ""
) -> str:
    """
    Selects direct, human netizen style based on time of day (WIB).
    Heavy bias towards snappy_take (to the point) to avoid boilerplate preamble.

    A sensitive story overrides the clock: greetings, banter and "closing bell"
    framing are wrong next to a death toll, whatever time it is.
    """
    if is_sensitive_topic(pillar, topic, source_summary):
        return "empathetic_update"
    if 5 <= hour < 10:
        return random.choices(["snappy_take", "morning_greeting"], weights=[7, 3])[0]
    elif 11 <= hour < 14:
        return random.choices(["snappy_take", "midday_banter"], weights=[7, 3])[0]
    elif 15 <= hour < 18:
        return random.choices(["snappy_take", "afternoon_wrap"], weights=[7, 3])[0]
    elif 19 <= hour < 24 or 0 <= hour < 2:
        return random.choices(["snappy_take", "night_scrolling"], weights=[7, 3])[0]
    else:
        return "snappy_take"


def _get_post_structure(pillar: str) -> str:
    """Picks single vs 4-part chain for one slot.

    Only the monetisable pillars (tech & market) get chains: those are the
    threads where a held-back fragment and a binary prediction have something to
    sell. Bencana and football stay single — urgency wants one post, and a
    football take lands harder short.
    """
    if pillar not in CHAIN_PILLARS:
        return "single"
    try:
        if get_today_chain_count() >= CHAIN_MAX_PER_DAY:
            return "single"
    except Exception as e:
        # Quota lookup failed → do not risk an uncapped chain day.
        log.warning(f"Chain quota check failed ({e}); defaulting to single post.")
        return "single"
    return "chain" if random.random() < CHAIN_PROBABILITY else "single"


def _pillar_weights(hour: int, perf: dict) -> dict:
    """Static clock weights scaled by measured engagement per pillar.

    ratio = a pillar's avg_views / best-effort overall avg_views. A pillar
    pulling above-average reach gets boosted (capped 2x), an underperformer is
    cut down (floored at 0.4x, and never below 15% of its original weight so
    no pillar silently dies). Empty/absent perf → exact static weights.
    """
    if 5 <= hour < 10:
        # Pagi (05:00-10:00 WIB): Hasil bola semalam/EPL/UCL + pasar Wall St + Tech
        weights = {
            "football_sports": 7,
            "market_finance_stocks": 3,
            "tech_gadget_ai": 2,
            "breaking_disaster": 1,
        }
    elif 10 <= hour < 16:
        # Siang (10:00-16:00 WIB): Info bola & bursa transfer + IHSG sesi 1 + AI tech
        weights = {
            "football_sports": 6,
            "market_finance_stocks": 4,
            "tech_gadget_ai": 3,
            "breaking_disaster": 1,
        }
    elif 16 <= hour < 21:
        # Sore - Prime time malam: Bola/Timnas (peak traffic virality) + Tech + Makro
        weights = {
            "football_sports": 8,
            "tech_gadget_ai": 3,
            "market_finance_stocks": 2,
            "breaking_disaster": 1,
        }
    else:
        # Larut malam: Bola Eropa/taktik live + Wall Street opening + Tech
        weights = {
            "football_sports": 7,
            "tech_gadget_ai": 3,
            "market_finance_stocks": 3,
            "breaking_disaster": 1,
        }

    if not perf:
        return weights

    avg_views_all = sum(p["avg_views"] * p["posts"] for p in perf.values()) / max(
        sum(p["posts"] for p in perf.values()), 1
    )
    scaled = {}
    for pillar, w in weights.items():
        stats = perf.get(pillar)
        if stats and avg_views_all > 0:
            ratio = stats["avg_views"] / avg_views_all
        else:
            ratio = 1.0
        new_w = w * min(2.0, max(0.4, 0.6 + 0.8 * ratio))
        new_w = max(new_w, w * 0.15)  # floor: every pillar stays alive
        scaled[pillar] = new_w
    return scaled

def _select_pillar(hour: int, post_count_today: int) -> str:
    """
    Picks content pillar based on real performance analytics:
    - Football/Sports is the primary traffic & virality driver (~50-60% of volume).
    - Market/Stocks (Local IDX & Global Bloomberg/Reuters/Wall Street).
    - Tech/AI (Emerging gadgets, AI tools, chips).
    - Disaster/BMKG reserved for urgent national events.

    Real engagement shifts the mix when at least 3 scored posts exist;
    otherwise the static clock weights stand (zero data must not kill a pillar).
    """
    perf = {}
    total_posts = 0
    try:
        perf = get_pillar_performance()
        total_posts = sum(p["posts"] for p in perf.values())
    except Exception as e:
        log.warning(f"Pillar performance lookup failed ({e}); using static weights.")
    if total_posts < 3:
        perf = {}

    weights = _pillar_weights(hour, perf)

    pool = []
    for pillar, weight in weights.items():
        pool.extend([pillar] * max(1, round(weight)))

    return random.choice(pool)


def _select_hot_topic(used_today: list[str]) -> tuple[str, str] | None:
    """
    Returns the hottest *usable* live story, or None.

    A story is usable when it clears HOT_TOPIC_MIN_HEAT, hasn't been posted
    today, and wasn't covered in the last 7 days. Ranked by heat (impact +
    cross-source corroboration), so karhutla/IHSG-halt outrank routine coverage.
    """
    try:
        ranked = fetch_hot_topics(top_n=12)
    except Exception as e:
        log.warning(f"Hot topic fetch failed: {e}")
        return None

    for r in ranked:
        topic = r["topic"]
        if r["heat"] < HOT_TOPIC_MIN_HEAT:
            break
        if topic in used_today or is_topic_recent(topic, days=7):
            continue
        return topic, pillar_for_topic(topic)
    return None


def _select_topic_for_pillar(pillar: str, used_today: list[str]) -> tuple[str, str]:
    """
    Picks a fresh topic from the specific pillar trends.
    Falls back to pillar evergreen if trending list is exhausted.
    """
    trends = fetch_trends_for_pillar(pillar)
    random.shuffle(trends)

    for t in trends:
        if t not in used_today and not is_topic_recent(t, days=7):
            return t, "trending"

    # Fallback to evergreen for this specific pillar
    evergreen_list = EVERGREEN_BY_PILLAR.get(pillar, [])[:]
    random.shuffle(evergreen_list)
    for t in evergreen_list:
        if t not in used_today and not is_topic_recent(t, days=EVERGREEN_THRESHOLD_DAYS):
            return t, "evergreen"

    if evergreen_list:
        return random.choice(evergreen_list), "evergreen_forced"

    return f"Update tren terkini seputar {pillar.replace('_', ' ')}", "fallback"


def plan_single_post(used_today: list[str] = None) -> dict:
    """
    Plans a single organic post item.
    Returns dict: {topic, pillar, source, post_format, post_style}
    """
    if used_today is None:
        used_today = []

    hour = _get_wib_hour()
    today_count = get_today_post_count()

    # Hot-topic override: a live national story (karhutla, IHSG trading halt, etc.)
    # takes the slot instead of the routine pillar mix, so the timeline reacts to
    # what is actually happening now.
    hot = _select_hot_topic(used_today)
    if hot:
        topic, pillar = hot
        source = "hot_trending"
    else:
        # Select pillar, format (text vs image), and netizen style
        pillar = _select_pillar(hour, today_count)
        topic, source = _select_topic_for_pillar(pillar, used_today)
    post_format = _get_post_format(hour, today_count)
    # The story context decides whether this is a sensitive post, which overrides
    # both the clock style and the persona. It has to be resolved before the
    # style is chosen, not after.
    source_summary = _topic_summary(topic)
    post_style = _get_post_style(hour, pillar, topic, source_summary)
    post_structure = _get_post_structure(pillar)

    log.info(
        f"Planned post: pillar={pillar}, topic='{topic[:40]}', source={source}, "
        f"format={post_format}, style={post_style}, structure={post_structure}, wib_hour={hour}"
    )
    return {
        "topic": topic,
        "pillar": pillar,
        "source": source,
        "post_format": post_format,
        "post_style": post_style,
        "post_structure": post_structure,
        # Without this the generator is told "use figures from the source" and
        # handed only a headline, so it invents the figures. Empty string means
        # no data was captured, and the generator then bans numbers outright.
        "source_summary": _topic_summary(topic),
    }


def plan_daily_batch(count: int = 10) -> list[dict]:
    """
    Plans a full day's worth of organic posts with diversity across all 4 pillars.
    """
    used_topics = []
    posts = []

    for i in range(count):
        simulated_hour = 7 + int((i / count) * 16)
        pillar = _select_pillar(simulated_hour, i)
        topic, source = _select_topic_for_pillar(pillar, used_topics)
        post_format = _get_post_format(simulated_hour, i)
        source_summary = _topic_summary(topic)
        post_style = _get_post_style(simulated_hour, pillar, topic, source_summary)
        used_topics.append(topic)

        posts.append({
            "topic": topic,
            "pillar": pillar,
            "source": source,
            "post_format": post_format,
            "post_style": post_style,
            "post_structure": _get_post_structure(pillar),
            "planned_hour": simulated_hour,
            "source_summary": source_summary,
        })

    log.info(f"Planned {len(posts)} posts for today across 4 core pillars")
    return posts


if __name__ == "__main__":
    batch = plan_daily_batch(count=8)
    for i, item in enumerate(batch, 1):
        print(f"{i:02d}. [{item['pillar']:22s}] [{item['source']:10s}] {item['topic'][:60]}")
