"""
Live Trends Fetcher for Indonesia
Fetches real-time trending topics from:
1. Trends24 Indonesia (Twitter / X live trends)
2. Google Trends Indonesia RSS
"""

import random
import requests
import xml.etree.ElementTree as ET
from lxml import html
from src.logger import get_logger

log = get_logger("live_trends")

# Curated authentic Indonesian relatable topics (always viral on X/Threads)
EVERGREEN_ID_TOPICS = [
    "struggle budak korporat WFO & transit neraka KRL Manggarai jam sibuk",
    "overthinking jam 1 malem di kamar kosan mikirin karir & gaji numpang lewat",
    "cuaca panas ekstrem Jakarta BMKG 37 derajat aspal berasa mendidih",
    "drama self reward berkedok boros pas checkout olshop ujungnya nyesel",
    "perjuangan pejuang skripsi & revisi dosen pembimbing yang suka ghosting",
    "antre gate festival konser musik outdoor dari siang bolong",
    "debat receh netizen selera makan & kebiasaan unik anak kos",
]

# Words that indicate foreign sports/racing/irrelevant queries to drop completely
EXCLUDE_KEYWORDS = [
    " vs ", "fc", "united", "city", "league", "bundesliga", "serie a", "laliga",
    "madrid", "barcelona", "bayern", "chelsea", "arsenal", "liverpool", "juventus",
    "milan", "inter", "hertha", "lecce", "bologna", "sociedad", "vigo", "hamilton",
    "f1", "motogp", "sports", "bein"
]


def get_live_id_trends() -> list[str]:
    """
    Returns relevant trending topics in Indonesia, filtering out foreign football fixtures.
    Falls back to high-engagement local topics if live feeds only contain sports.
    """
    trends = []

    # 1. Trends24 (Twitter/X Indonesia)
    try:
        url = "https://trends24.in/indonesia/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            tree = html.fromstring(r.content)
            items = tree.xpath('//ol[contains(@class, "trend-card__list")]/li/a/text()')
            for item in items[:15]:
                cleaned = item.strip()
                if cleaned and len(cleaned) > 2:
                    lower = cleaned.lower()
                    if not any(bad in lower for bad in EXCLUDE_KEYWORDS):
                        if cleaned not in trends:
                            trends.append(cleaned)
    except Exception as e:
        log.warning(f"Failed to fetch Trends24: {e}")

    # 2. Google Trends ID RSS (Filtered)
    try:
        rss_url = "https://trends.google.com/trending/rss?geo=ID"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(rss_url, headers=headers, timeout=8)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:10]:
                title = item.find("title")
                if title is not None and title.text:
                    cleaned = title.text.strip()
                    lower = cleaned.lower()
                    if not any(bad in lower for bad in EXCLUDE_KEYWORDS):
                        if cleaned and cleaned not in trends:
                            trends.append(cleaned)
    except Exception as e:
        log.warning(f"Failed to fetch Google Trends RSS: {e}")

    # If trends list is empty or mostly generic, inject authentic evergreen topics
    if len(trends) < 3:
        sample_topics = random.sample(EVERGREEN_ID_TOPICS, 3)
        trends.extend(sample_topics)

    log.info(f"Resolved {len(trends)} Indonesian market trends: {trends[:5]}")
    return trends
