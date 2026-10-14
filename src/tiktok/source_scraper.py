"""source_scraper.py — Multi-platform vertical video scraper.

Supports:
  - YouTube Shorts (existing)
  - TikTok channel/hashtag scraping
  - Instagram Reels via yt-dlp
  - Facebook Reels via yt-dlp
  - Direct URL list (any platform)

All results normalized to the same dict shape as youtube.search_videos():
  {id, title, channel, duration, webpage_url, source_platform}
"""

import os
import re
import json
import random
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("tiktok.source_scraper")

# ─────────────────────────────────────────────
# Curated vertical-native content pool
# Groups: Satisfying Craft, ASMR/Food, Nature/Survival,
#         Aesthetic/Lifestyle, Animals, Art/Paint
# ─────────────────────────────────────────────

YOUTUBE_SHORTS_POOL = [
    # Satisfying craft / restoration
    "satisfying car restoration barn find #shorts",
    "rusty knife restoration ASMR #shorts",
    "satisfying rug cleaning pressure wash #shorts",
    "woodturning epoxy resin bowl #shorts",
    "blacksmith forging knife sword #shorts",
    "CNC machining precision satisfying #shorts",
    "Japanese carpentry joinery woodwork #shorts",
    "sneaker restoration ASMR satisfying #shorts",
    "watch restoration antique ASMR #shorts",
    "satisfying bricklaying masonry construction #shorts",
    "pottery wheel satisfying clay throwing #shorts",
    "satisfying soap cutting ASMR #shorts",
    "glass blowing art satisfying #shorts",
    "metal casting molten satisfying #shorts",
    "chocolate making satisfying ASMR #shorts",
    "satisfying noodle making ramen #shorts",
    "gem stone cutting polishing satisfying #shorts",
    "satisfying factory production automated #shorts",
    "leather crafting handmade satisfying #shorts",
    "satisfying weaving loom textile #shorts",
    # Nature / survival / outdoor
    "primitive survival building shelter #shorts",
    "bushcraft wilderness solo camping #shorts",
    "catch cook fish outdoor satisfying #shorts",
    "satisfying garden harvest vegetables #shorts",
    "rainforest creek stream nature relaxing #shorts",
    "mountain hiking drone scenic vertical #shorts",
    "campfire ASMR rain forest relaxing #shorts",
    "underwater coral reef fish ASMR #shorts",
    "dog rescue transformation #shorts",
    # Aesthetic / satisfying compilation
    "oddly satisfying slime pressing #shorts",
    "kinetic sand cutting ASMR satisfying #shorts",
    "satisfying liquid pouring paint art #shorts",
    "sand art satisfying timelapse #shorts",
    "calligraphy brush lettering satisfying #shorts",
    "aquascaping planted tank timelapse #shorts",
    "satisfying domino chain reaction #shorts",
    "origami paper folding satisfying #shorts",
    # Cooking ASMR
    "satisfying Korean street food cooking #shorts",
    "Japanese food preparation knife skills #shorts",
    "satisfying bread baking sourdough #shorts",
    "sushi roll making ASMR satisfying #shorts",
    "BBQ meat grilling ASMR satisfying #shorts",
]

# TikTok: trending hashtags that return native vertical content
TIKTOK_HASHTAGS = [
    "satisfying",
    "asmr",
    "oddlysatisfying",
    "restoration",
    "woodworking",
    "crafts",
    "cooking",
    "nature",
    "camping",
    "pottery",
    "leatherwork",
    "glassblowing",
    "bonsai",
    "timelapse",
]

# TikTok: curated channels (consistently vertical, no watermark issues)
TIKTOK_CHANNELS = [
    "https://www.tiktok.com/@oddlysatisfying",
    "https://www.tiktok.com/@restorationasmr",
    "https://www.tiktok.com/@satisfyingworld",
    "https://www.tiktok.com/@woodworkingasmr",
    "https://www.tiktok.com/@calmingcraft",
    "https://www.tiktok.com/@naturelover_id",
    "https://www.tiktok.com/@asmrcooking",
    "https://www.tiktok.com/@satisfyingcraft",
]

# Instagram Reels: curated accounts with satisfying/craft vertical content
INSTAGRAM_REELS_ACCOUNTS = [
    "https://www.instagram.com/oddlysatisfying/reels/",
    "https://www.instagram.com/satisfying/reels/",
    "https://www.instagram.com/craftasmr/reels/",
    "https://www.instagram.com/woodworking_asmr/reels/",
    "https://www.instagram.com/asmr.satisfying/reels/",
    "https://www.instagram.com/restorationmaster/reels/",
    "https://www.instagram.com/campfire.asmr/reels/",
    "https://www.instagram.com/pottery.art/reels/",
    "https://www.instagram.com/foodasmr/reels/",
]

# Facebook Reels: curated page IDs / URLs
FACEBOOK_REELS_PAGES = [
    "https://www.facebook.com/OddlySatisfying/videos/",
    "https://www.facebook.com/satisfyingvideos/videos/",
    "https://www.facebook.com/restorationmaster/videos/",
    "https://www.facebook.com/woodworkingasmr/videos/",
]

_YTDLP_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "tiktok" / "youtube_cookies.txt"


def _run_ytdlp_flat(url: str, max_items: int = 20, extra_args: Optional[List[str]] = None, cookies_path: Optional[str] = None) -> List[dict]:
    """Run yt-dlp --flat-playlist on a URL and return list of entries."""
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print-json",
        "--no-warnings",
        "--playlist-end", str(max_items),
        "--no-check-certificate",
    ]
    if cookies_path and os.path.exists(cookies_path):
        cmd.extend(["--cookies", cookies_path])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        entries = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                entries.append(entry)
            except json.JSONDecodeError:
                continue
        return entries
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp flat-playlist timed out for: %s", url)
        return []
    except Exception as e:
        logger.warning("yt-dlp flat-playlist failed for %s: %s", url, e)
        return []


def _normalize_entry(entry: dict, platform: str) -> Optional[dict]:
    """Normalize a yt-dlp entry to standard video dict."""
    vid_id = entry.get("id") or entry.get("display_id")
    if not vid_id:
        return None

    duration = entry.get("duration")
    # Skip if clearly not short-form vertical (>300s), allow None to pass through
    if duration and duration > 300:
        return None

    url = entry.get("webpage_url") or entry.get("url") or ""
    if not url:
        if platform == "youtube":
            url = f"https://www.youtube.com/shorts/{vid_id}"
        elif platform == "tiktok":
            url = f"https://www.tiktok.com/@unknown/video/{vid_id}"
        elif platform == "instagram":
            url = f"https://www.instagram.com/reel/{vid_id}/"
        elif platform == "facebook":
            url = f"https://www.facebook.com/reel/{vid_id}"

    return {
        "id": f"{platform}_{vid_id}",
        "raw_id": vid_id,
        "title": entry.get("title") or entry.get("fulltitle") or "Untitled",
        "channel": entry.get("channel") or entry.get("uploader") or entry.get("creator") or "",
        "duration": duration or 0,
        "webpage_url": url,
        "source_platform": platform,
        "width": entry.get("width"),
        "height": entry.get("height"),
    }


def scrape_youtube_shorts(keywords: List[str], max_results: int = 10, db=None) -> List[dict]:
    """Search YouTube Shorts for given keywords. Already-uploaded IDs filtered via db."""
    from src.tiktok.youtube import search_videos as yt_search
    results = []
    seen = set()
    for kw in keywords:
        videos = yt_search([kw], max_results=max_results * 3, max_duration=180, prefer_vertical=True)
        for v in videos:
            raw_id = v["id"]
            norm_id = f"youtube_{raw_id}"
            if norm_id in seen:
                continue
            if db and db.is_uploaded(raw_id):
                continue
            seen.add(norm_id)
            v["source_platform"] = "youtube"
            v["raw_id"] = raw_id
            v["id"] = norm_id
            results.append(v)
            if len(results) >= max_results:
                return results
    return results


def scrape_tiktok_hashtag(hashtags: List[str], max_results: int = 10, db=None) -> List[dict]:
    """Scrape TikTok hashtag feed for vertical videos via yt-dlp."""
    results = []
    seen = set()
    random.shuffle(hashtags)
    for tag in hashtags:
        url = f"https://www.tiktok.com/tag/{tag.lstrip('#')}"
        entries = _run_ytdlp_flat(url, max_items=max_results * 3)
        for e in entries:
            norm = _normalize_entry(e, "tiktok")
            if not norm:
                continue
            if norm["id"] in seen:
                continue
            if db and db.is_uploaded(norm["raw_id"]):
                continue
            seen.add(norm["id"])
            results.append(norm)
            if len(results) >= max_results:
                return results
    return results


def scrape_tiktok_channels(channels: List[str], max_results: int = 10, db=None) -> List[dict]:
    """Scrape specific TikTok channels for recent videos."""
    results = []
    seen = set()
    random.shuffle(channels)
    for ch_url in channels:
        entries = _run_ytdlp_flat(ch_url, max_items=max_results * 2)
        for e in entries:
            norm = _normalize_entry(e, "tiktok")
            if not norm:
                continue
            if norm["id"] in seen:
                continue
            if db and db.is_uploaded(norm["raw_id"]):
                continue
            seen.add(norm["id"])
            results.append(norm)
            if len(results) >= max_results:
                return results
    return results


def scrape_instagram_reels(accounts: List[str], max_results: int = 10, db=None) -> List[dict]:
    """Scrape Instagram Reels from curated accounts via yt-dlp.
    Requires cookies for auth (instagram blocks anonymous heavily).
    """
    cookies = str(_YTDLP_COOKIES_PATH) if _YTDLP_COOKIES_PATH.exists() else None
    results = []
    seen = set()
    random.shuffle(accounts)
    for acc_url in accounts:
        entries = _run_ytdlp_flat(
            acc_url,
            max_items=max_results * 3,
            cookies_path=cookies,
            extra_args=["--extractor-args", "instagram:api_version=v1"],
        )
        for e in entries:
            norm = _normalize_entry(e, "instagram")
            if not norm:
                continue
            if norm["id"] in seen:
                continue
            if db and db.is_uploaded(norm["raw_id"]):
                continue
            seen.add(norm["id"])
            results.append(norm)
            if len(results) >= max_results:
                return results
    return results


def scrape_facebook_reels(pages: List[str], max_results: int = 10, db=None) -> List[dict]:
    """Scrape Facebook Reels/Videos from curated pages via yt-dlp."""
    cookies = str(_YTDLP_COOKIES_PATH) if _YTDLP_COOKIES_PATH.exists() else None
    results = []
    seen = set()
    random.shuffle(pages)
    for page_url in pages:
        entries = _run_ytdlp_flat(
            page_url,
            max_items=max_results * 3,
            cookies_path=cookies,
        )
        for e in entries:
            norm = _normalize_entry(e, "facebook")
            if not norm:
                continue
            if norm["id"] in seen:
                continue
            if db and db.is_uploaded(norm["raw_id"]):
                continue
            seen.add(norm["id"])
            results.append(norm)
            if len(results) >= max_results:
                return results
    return results


def collect_multi_source(
    count: int = 5,
    db=None,
    platforms: Optional[List[str]] = None,
    keywords: Optional[List[str]] = None,
    prefer_vertical: bool = True,
) -> List[dict]:
    """
    Main entry point. Collect `count` vertical videos from multiple platforms.

    platforms: subset of ['youtube', 'tiktok_hashtag', 'tiktok_channel', 'instagram', 'facebook']
               default: all, weighted by reliability
    Returns: list of normalized video dicts, deduplicated, never-uploaded.
    """
    if platforms is None:
        # Weight by reliability: YouTube most reliable, IG/FB need cookies
        platforms = ["youtube", "tiktok_hashtag", "tiktok_channel", "instagram", "facebook"]

    per_platform = max(count, 3)
    results = []
    seen_ids = set()

    kws = keywords or random.sample(YOUTUBE_SHORTS_POOL, min(6, len(YOUTUBE_SHORTS_POOL)))

    platform_scrapers = {
        "youtube": lambda: scrape_youtube_shorts(kws, max_results=per_platform, db=db),
        "tiktok_hashtag": lambda: scrape_tiktok_hashtag(
            random.sample(TIKTOK_HASHTAGS, min(5, len(TIKTOK_HASHTAGS))),
            max_results=per_platform, db=db
        ),
        "tiktok_channel": lambda: scrape_tiktok_channels(
            random.sample(TIKTOK_CHANNELS, min(4, len(TIKTOK_CHANNELS))),
            max_results=per_platform, db=db
        ),
        "instagram": lambda: scrape_instagram_reels(
            random.sample(INSTAGRAM_REELS_ACCOUNTS, min(4, len(INSTAGRAM_REELS_ACCOUNTS))),
            max_results=per_platform, db=db
        ),
        "facebook": lambda: scrape_facebook_reels(
            random.sample(FACEBOOK_REELS_PAGES, min(3, len(FACEBOOK_REELS_PAGES))),
            max_results=per_platform, db=db
        ),
    }

    random.shuffle(platforms)  # vary order each run
    for platform in platforms:
        if len(results) >= count * 3:
            break
        scraper = platform_scrapers.get(platform)
        if not scraper:
            continue
        try:
            videos = scraper()
            for v in videos:
                if v["id"] not in seen_ids:
                    seen_ids.add(v["id"])
                    results.append(v)
            logger.info("source_scraper: %s yielded %d videos", platform, len(videos))
        except Exception as e:
            logger.warning("source_scraper: %s failed: %s", platform, e)

    random.shuffle(results)
    return results[:count * 3]  # return buffer; pipeline picks count
