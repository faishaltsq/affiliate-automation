"""
Trend Research Layer
- 1.1 Google Trends (unofficial, pytrends-based workaround)
- 1.2 Reddit Rising Posts (praw)
- 1.3 TikTok Trending Hashtags (RapidAPI / unofficial fallback)
- 1.4 Consolidate & Rank
"""

import json
import time
import hashlib
import requests
import pandas as pd

from src.config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT, DATA_DIR
from src.logger import get_logger

log = get_logger("trends")

# ── 1.1 Google Trends ─────────────────────────────────────────────────────────

def get_google_trends(keyword: str, days: int = 30) -> list[dict]:
    """
    Uses the unofficial Google Trends explore endpoint.
    Falls back to pytrends if the direct request fails.
    Returns list of {title, value} dicts (value: 0-100 scale).
    """
    # Primary: unofficial explore endpoint
    url = (
        "https://trends.google.com/trends/api/explore"
        f"?hl=en-US&tz=0"
        f"&req={{\"comparisonItem\":[{{\"keyword\":\"{keyword}\",\"geo\":\"US\",\"time\":\"{days}d\"}}],"
        f"\"category\":0,\"property\":\"\"}}"
    )
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = json.loads(r.text[5:])  # strip JSONP )]}'
        queries = (
            data.get("default", {})
            .get("rankedList", [{}])[0]
            .get("rankedKeyword", [])
        )
        results = [{"title": q["query"], "value": q["value"]} for q in queries[:10]]
        log.info(f"Google Trends: {len(results)} results for '{keyword}'")
        return results
    except Exception as e:
        log.warning(f"Google Trends direct fetch failed ({e}), trying pytrends fallback")
        return _google_trends_pytrends(keyword, days)


def _google_trends_pytrends(keyword: str, days: int) -> list[dict]:
    """Pytrends fallback — returns related queries as {title, value} list."""
    try:
        from pytrends.request import TrendReq
        import time as _t

        # Fix urllib3 v2 incompatibility with pytrends (method_whitelist -> allowed_methods)
        import urllib3.util.retry
        if not hasattr(urllib3.util.retry.Retry, "method_whitelist_patched"):
            orig_init = urllib3.util.retry.Retry.__init__
            def patched_init(self, *args, **kwargs):
                if "method_whitelist" in kwargs:
                    kwargs["allowed_methods"] = kwargs.pop("method_whitelist")
                orig_init(self, *args, **kwargs)
            urllib3.util.retry.Retry.__init__ = patched_init
            urllib3.util.retry.Retry.method_whitelist_patched = True

        pt = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        pt.build_payload([keyword], timeframe="today 1-m")
        _t.sleep(1)  # polite delay
        related = pt.related_queries()
        top = related.get(keyword, {}).get("top")
        if top is not None and not top.empty:
            return [{"title": row["query"], "value": int(row["value"])} for _, row in top.head(10).iterrows()]
        # fallback: interest over time interest_by_region
        iot = pt.interest_over_time()
        if not iot.empty:
            avg_val = int(iot[keyword].mean())
            return [{"title": keyword, "value": avg_val}]
    except Exception as e2:
        log.error(f"pytrends fallback also failed: {e2}")
    return []


# ── 1.2 Reddit Rising Posts ──────────────────────────────────────────────────

def get_reddit_rising(limit: int = 10) -> list[dict]:
    """Returns list of {title, subreddit, score, url} from r/all rising."""
    try:
        import praw
        reddit = praw.Reddit(
            client_id=REDDIT_CLIENT_ID,
            client_secret=REDDIT_CLIENT_SECRET,
            user_agent=REDDIT_USER_AGENT,
        )
        rising = []
        for post in reddit.subreddit("all").rising(limit=limit):
            rising.append({
                "title": post.title,
                "subreddit": post.subreddit.display_name,
                "score": post.score,
                "url": post.url,
            })
        log.info(f"Reddit: {len(rising)} rising posts")
        return rising
    except Exception as e:
        log.error(f"Reddit fetch failed: {e}")
        return []


# ── 1.3 TikTok Trending Hashtags ─────────────────────────────────────────────

def get_tiktok_trending(limit: int = 10) -> list[dict]:
    """
    Tries the unofficial mobile API first.
    Falls back to empty list with warning — TikTok requires dynamic
    signing headers (X-Gorgon/X-Khronos) for stable access.
    Replace with a RapidAPI 'tiktok-trending-hashtags' subscription for prod.
    """
    try:
        url = "https://api.tiktok.com/aweme/v1/trending/hashtag/"
        headers = {
            "User-Agent": "com.zhiliaoapp.musically/2022600030 (Linux; U; Android 5.0; en_US; SM-N900T; Build/LRX21V; Cronet/TTNetVersion:6d74a9c8 2020-11-11 QuicVersion:0144d358 2020-09-14)",
        }
        r = requests.get(url, params={"count": limit}, headers=headers, timeout=10)
        data = r.json()
        results = [
            {"hashtag": d["hashtag"]["title"], "count": d["hashtag"]["view_count"]}
            for d in data.get("data", [])
        ]
        log.info(f"TikTok: {len(results)} trending hashtags")
        return results
    except Exception as e:
        log.warning(
            f"TikTok trending fetch failed ({e}). "
            "Replace with RapidAPI 'tiktok-trending-hashtags' for reliable access."
        )
        return []


# ── 1.4 Consolidate & Rank ───────────────────────────────────────────────────

def rank_topics(
    google: list[dict],
    reddit: list[dict],
    tiktok: list[dict],
    top_n: int = 8,
) -> list[dict]:
    """
    Normalizes and merges scores from all 3 sources.
    Deduplicates by keyword fingerprint (lowercased first 3 words).
    Returns top_n topics sorted by score desc.
    """
    rows = []

    for t in google:
        rows.append({
            "topic": t["title"],
            "source": "Google Trends",
            "score": float(t.get("value", 0)),
        })

    max_reddit = max((r["score"] for r in reddit), default=1) or 1
    for r in reddit:
        rows.append({
            "topic": r["title"],
            "source": "Reddit",
            "score": (r["score"] / max_reddit) * 100,
        })

    max_tiktok = max((t["count"] for t in tiktok), default=1) or 1
    for t in tiktok:
        rows.append({
            "topic": t["hashtag"],
            "source": "TikTok",
            "score": (t["count"] / max_tiktok) * 100,
        })

    if not rows:
        log.warning("No topics collected from any source")
        return []

    df = pd.DataFrame(rows)

    def _fingerprint(text: str) -> str:
        words = text.lower().split()[:3]
        return hashlib.md5(" ".join(words).encode()).hexdigest()

    df["fp"] = df["topic"].apply(_fingerprint)
    df = df.sort_values("score", ascending=False).drop_duplicates("fp").head(top_n)

    result = df.drop(columns=["fp"]).to_dict(orient="records")
    log.info(f"Ranked {len(result)} topics")

    # persist for worker consumption
    out = DATA_DIR / "ranked_topics.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    log.info(f"Saved → {out}")

    return result


# ── Entry point (standalone) ─────────────────────────────────────────────────

if __name__ == "__main__":
    keyword = "portable SSD"
    g = get_google_trends(keyword)
    re = get_reddit_rising()
    tk = get_tiktok_trending()
    ranked = rank_topics(g, re, tk)
    print(json.dumps(ranked, indent=2, ensure_ascii=False))
