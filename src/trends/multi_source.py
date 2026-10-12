"""
Multi-Source Real-Time Trend Engine for Indonesia
Aggregates and cleans trending topics from:
1. Twitter / X Trends (Trends24 Indonesia)
2. Google Trends ID (Daily Trends RSS)
3. Google News ID Viral Topics (Lifestyle & Entertainment RSS)
4. Fallback: Curated authentic Indonesian relatable topics
"""

import re
import random
import requests
import xml.etree.ElementTree as ET
from lxml import html
from src.logger import get_logger

log = get_logger("multi_source_trends")

EXCLUDE_PATTERNS = [
    r"\bvs\b", r"\bfc\b", r"\bunited\b", r"\bcity\b", r"\bleague\b",
    r"\bbundesliga\b", r"\bserie a\b", r"\blaliga\b", r"\bmadrid\b",
    r"\bbarcelona\b", r"\bbayern\b", r"\bchelsea\b", r"\barsenal\b",
    r"\bliverpool\b", r"\bjuventus\b", r"\bmilan\b", r"\binter\b",
    r"\bf1\b", r"\bmotogp\b", r"\bsports\b", r"\bbein\b", r"\bjudi\b",
    r"\bslot\b", r"\bgacor\b", r"\bpragmatic\b", r"\bmaxwin\b"
]

EVERGREEN_ID_TOPICS = [
    "struggle budak korporat WFO & transit neraka KRL Manggarai jam sibuk",
    "overthinking jam 1 malem di kamar kosan mikirin karir & gaji numpang lewat",
    "cuaca panas ekstrem Jakarta BMKG 37 derajat aspal berasa mendidih",
    "drama self reward berkedok boros pas checkout olshop ujungnya nyesel",
    "perjuangan pejuang skripsi & revisi dosen pembimbing yang suka ghosting",
    "antre gate festival konser musik outdoor dari siang bolong",
    "debat receh netizen selera makan & kebiasaan unik anak kos",
    "dilema mau resign tapi tabungan darurat baru cukup buat beli seblak 3 bulan",
    "kebiasaan scroll e-commerce sampe subuh padahal besok morning meeting jam 8",
    "perdebatan bubur diaduk vs gak diaduk yang gak pernah ada ujungnya",
]


def _is_clean(text: str) -> bool:
    if not text or len(text.strip()) < 3:
        return False
    lower = text.lower()
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, lower):
            return False
    return True


def _clean_headline(title: str) -> str:
    # Drop publisher suffix like ' - detikHOT' or ' - Kompas.com'
    title = re.sub(r"\s*-\s*[A-Za-z0-9\.\s]+$", "", title)
    return title.strip()


def fetch_twitter_trends() -> list[str]:
    """Fetch Twitter/X trends from Trends24 Indonesia."""
    trends = []
    try:
        url = "https://trends24.in/indonesia/"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200:
            tree = html.fromstring(r.content)
            items = tree.xpath('//ol[contains(@class, "trend-card__list")]/li/a/text()')
            for item in items[:20]:
                cleaned = item.strip().lstrip("#")
                if _is_clean(cleaned) and cleaned not in trends:
                    trends.append(cleaned)
    except Exception as e:
        log.warning(f"Failed Trends24: {e}")
    return trends


def fetch_google_trends() -> list[str]:
    """Fetch Google Trends daily RSS for Indonesia."""
    trends = []
    try:
        rss_url = "https://trends.google.com/trending/rss?geo=ID"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(rss_url, headers=headers, timeout=8)
        if r.status_code == 200:
            root = ET.fromstring(r.content)
            for item in root.findall(".//item")[:15]:
                title = item.find("title")
                if title is not None and title.text:
                    cleaned = title.text.strip()
                    if _is_clean(cleaned) and cleaned not in trends:
                        trends.append(cleaned)
    except Exception as e:
        log.warning(f"Failed Google Trends RSS: {e}")
    return trends


def fetch_google_news_entertainment() -> list[str]:
    """Fetch viral lifestyle & entertainment news headlines in Indonesia."""
    trends = []
    urls = [
        "https://news.google.com/rss/headlines/section/topic/ENTERTAINMENT?hl=id&gl=ID&ceid=ID:id",
        "https://news.google.com/rss/headlines/section/topic/NATION?hl=id&gl=ID&ceid=ID:id",
    ]
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=8)
            if r.status_code == 200 and r.content.startswith(b"<?xml"):
                root = ET.fromstring(r.content)
                for item in root.findall(".//item")[:8]:
                    title_elem = item.find("title")
                    if title_elem is not None and title_elem.text:
                        cleaned = _clean_headline(title_elem.text)
                        if _is_clean(cleaned) and cleaned not in trends:
                            trends.append(cleaned)
        except Exception as e:
            log.warning(f"Failed news fetch: {e}")
    return trends


def fetch_all_trends() -> list[str]:
    """
    Returns aggregated deduplicated list of real-time trending topics in Indonesia.
    Prioritizes real-time feeds, backfilled by evergreen topics if needed.
    """
    aggregated = []

    # 1. Twitter / X
    tw = fetch_twitter_trends()
    aggregated.extend(tw)

    # 2. Google Trends
    gt = fetch_google_trends()
    for t in gt:
        if t not in aggregated:
            aggregated.append(t)

    # 3. Google Entertainment & Viral News
    gn = fetch_google_news_entertainment()
    for t in gn:
        if t not in aggregated:
            aggregated.append(t)

    # 4. Fallback if feeds are sparse
    if len(aggregated) < 5:
        samples = random.sample(EVERGREEN_ID_TOPICS, 5)
        for s in samples:
            if s not in aggregated:
                aggregated.append(s)

    log.info(f"Aggregated {len(aggregated)} Indonesian real-time trends (Twitter: {len(tw)}, GT: {len(gt)}, News: {len(gn)})")
    return aggregated


if __name__ == "__main__":
    results = fetch_all_trends()
    print(f"Total results: {len(results)}")
    for i, res in enumerate(results[:10], 1):
        print(f"{i}. {res}")
