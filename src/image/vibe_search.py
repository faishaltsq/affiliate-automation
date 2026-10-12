"""
Vibe Image Searcher
Finds authentic, relatable real-world public images (KRL crowded, Jakarta street heat,
commuter walking, office desk, etc.) to match the viral hook/bait thread.

Priority 1: Pinterest (i.pinimg.com) via Bing — clean, aesthetic, no watermark
Priority 2: Clean UGC/blog images — no stock/news watermark domains
Priority 3: Curated Pinterest fallback URLs by keyword
"""

import datetime
import re
import urllib.parse
import requests
from src.logger import get_logger

log = get_logger("vibe_search")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
}

# Domains with intrusive commercial watermarks OR rejected by Meta API
BLOCKED_DOMAINS = [
    # Commercial stock photo watermarks
    "alamy.com", "dreamstime.com", "shutterstock", "gettyimages",
    "istockphoto", "depositphotos", "stock.adobe", "123rf.com",
    "freepik.com", "vecteezy.com", "envato.com", "canva.com",
    "sciencephoto.com",
    # Meta Graph API incompatible CDNs (Error 2207052 URI rejected)
    "wikimedia.org", "wikipedia.org", "wiktionary.org",
]

# Trusted Indonesian news domains for direct article scraping
NEWS_ARTICLE_DOMAINS = [
    "detik.com", "antaranews.com", "kompas.com", "cnnindonesia.com",
    "tempo.co", "tribunnews.com", "liputan6.com", "kumparan.com",
    "inews.id", "suara.com", "merdeka.com", "beritasatu.com",
    "idntimes.com", "tvonenews.com", "metrotvnews.com", "sindonews.com",
    "jawapos.com", "disway.id"
]

# High-priority real news and press photo domains
NEWS_DOMAINS = [
    "antaranews.com", "kompas.com", "detik.net.id", "detik.com",
    "metrotvnews.com", "media.suara.com", "suara.com", "tvonenews.com",
    "idntimes.com", "rri.co.id", "beritasatu.com", "tribunnews.com",
    "jawapos.com", "kumparan.com", "sindonews.com", "tempo.co",
    "liputan6.com", "cnnindonesia.com", "viva.co.id", "disway.id",
    "medcom.id", "merdeka.com", "setkab.go.id", "kemkes.go.id",
    "polri.go.id", "pbs.twimg.com", "kly.akamaized.net", "idn.media"
]

BAD_IMAGE_PATTERNS = [
    "logo", "default", "placeholder", "blank", "share-image", "favicon", "avatar", "icon"
]

NEWS_TRIGGERS = [
    "kasus", "perundungan", "korban", "pelaku", "polisi", "polres", "polda", "tewas",
    "meninggal", "kecelakaan", "banjir", "kebakaran", "gempa", "erupsi", "longsor",
    "bencana", "pembunuhan", "begal", "dpr", "kpk", "korupsi", "sidang", "tersangka",
    "menteri", "presiden", "gubernur", "bupati", "walikota", "viral", "tragedi",
    "bentrok", "demo", "unjuk rasa", "razia", "tangkap", "ott", "kejaksaan", "hukum",
    "vonis", "hakim", "sekolah", "siswa", "sd", "smp", "sma"
]


def _bing_image_search(query: str, count: int = 50) -> list[str]:
    """Search Bing Images and return list of direct image URLs (murl)."""
    url = (
        f"https://www.bing.com/images/search"
        f"?q={urllib.parse.quote(query)}&FORM=HDRSC2&first=1&count={count}"
    )
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()

    murls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', r.text)
    if not murls:
        murls = re.findall(r'"murl":"(https?://[^"]+)"', r.text)

    return [urllib.parse.unquote(u) for u in murls]


def _is_clean_url(url: str) -> bool:
    """True if URL is a direct image file and not from a blocked/stock watermark domain."""
    url_lower = url.lower()
    if any(bad in url_lower for bad in BLOCKED_DOMAINS):
        return False
    return any(ext in url_lower for ext in [".jpg", ".jpeg", ".png", ".webp"])


def _verify_image_url(url: str, timeout: int = 4) -> bool:
    """HEAD check: reachable and actually image content-type."""
    try:
        chk = requests.head(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        return chk.status_code == 200 and "image" in chk.headers.get("content-type", "")
    except Exception:
        return False


def search_news_article_image(topic: str) -> str:
    """
    Search Indonesian news portals (Detik, Kompas, Antaranews, Liputan6, etc.)
    via Bing News to extract the exact editorial/lead image (og:image) of the article.
    """
    clean_topic = topic.strip()
    if not clean_topic:
        return ""

    log.info(f"Searching actual news website articles for topic: '{clean_topic}'")
    topic_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', clean_topic.lower()))
    for common in ["kasus", "yang", "dan", "dari", "pada", "untuk", "anak", "kecil", "berita"]:
        topic_words.discard(common)

    current_year = str(datetime.datetime.now().year)

    queries = [clean_topic]
    if "berita" not in clean_topic.lower():
        queries.append(f"{clean_topic} berita")

    for q in queries:
        try:
            encoded_q = urllib.parse.quote(q)
            bing_url = f"https://www.bing.com/news/search?q={encoded_q}&setmkt=id-ID&setlang=id"
            r = requests.get(bing_url, headers=HEADERS, timeout=8)
            urls = re.findall(r'href=\"(https?://[^\"]+)\"', r.text)

            article_links = []
            for u in urls:
                if any(nd in u for nd in NEWS_ARTICLE_DOMAINS):
                    if any(skip in u.lower() for skip in ["/tag/", "/topik/", "/search", "/indeks", "facebook.com", "twitter.com"]):
                        continue
                    if any(slug in u for slug in ["/read/", "/berita/", "/d-", ".html", "/detail/"]) or re.search(r'/\d{4}/', u):
                        article_links.append(u)

            article_links = list(dict.fromkeys(article_links))
            log.info(f"Bing News '{q}' returned {len(article_links)} article candidates")

            candidates = []
            for art_url in article_links[:8]:
                try:
                    art_res = requests.get(art_url, headers=HEADERS, timeout=6)
                    og_match = re.search(r'<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', art_res.text, re.I)
                    if not og_match:
                        og_match = re.search(r'<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:image[\"\']', art_res.text, re.I)

                    if og_match:
                        img_url = og_match.group(1).strip()
                        img_lower = img_url.lower()

                        if any(bad in img_lower for bad in BAD_IMAGE_PATTERNS):
                            continue
                        if any(blocked in img_lower for blocked in BLOCKED_DOMAINS):
                            continue

                        # Verify image HEAD reachable
                        if not _verify_image_url(img_url, timeout=4):
                            continue

                        # Scoring
                        score = 0
                        art_lower = art_url.lower()

                        for w in topic_words:
                            if w in img_lower:
                                score += 5
                            if w in art_lower:
                                score += 2

                        if current_year in img_lower or current_year in art_lower:
                            score += 4
                        elif "2025" in img_lower or "2025" in art_lower:
                            score += 2
                        elif any(old in img_lower for old in ["2020", "2019", "2018", "2017"]):
                            score -= 6

                        if "detik.net.id" in img_lower or "detik.com" in art_lower:
                            score += 3
                        if "antaranews" in img_lower or "antaranews.com" in art_lower:
                            score += 2
                        if "kly.akamaized.net" in img_lower:
                            score += 2

                        candidates.append((score, img_url, art_url))
                except Exception as e:
                    log.debug(f"Failed to fetch article {art_url}: {e}")

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                winner_score, winner_img, winner_art = candidates[0]
                log.info(f"Selected news article image (score={winner_score}) from {winner_art}: {winner_img}")
                return winner_img

        except Exception as e:
            log.warning(f"Failed news search for '{q}': {e}")

    return ""


def search_vibe_image(query: str, raw_topic: str = "") -> str:
    """
    Search for authentic real-life photo or news documentation matching the topic.
    """
    clean_query = query.strip()
    clean_topic = raw_topic.strip()
    log.info(f"Searching image for query='{clean_query}', topic='{clean_topic}'")

    # 1. If it's a news / crime / disaster / trending event topic, query news websites first!
    is_news = False
    check_text = f"{clean_topic} {clean_query}".lower()
    if clean_topic:
        is_news = True  # Explicit custom topics are almost always news/trends
    elif any(trig in check_text for trig in NEWS_TRIGGERS):
        is_news = True

    if is_news:
        news_img = search_news_article_image(clean_topic or clean_query)
        if news_img:
            return news_img

    # 2. General search queries on Bing Images
    search_queries = []
    if clean_topic:
        search_queries.append(f"{clean_topic} berita")
        search_queries.append(clean_topic)
    if clean_query and clean_query not in search_queries:
        search_queries.append(clean_query)

    for q in search_queries:
        try:
            urls = _bing_image_search(q, count=35)
            clean_urls = [u for u in urls if _is_clean_url(u)]
            log.info(f"Query '{q}' returned {len(clean_urls)} clean candidates")

            # Try NEWS priority domains first (real journalists, police, government)
            for u in clean_urls:
                if any(nd in u.lower() for nd in NEWS_DOMAINS):
                    if _verify_image_url(u):
                        log.info(f"Found authentic NEWS photo: {u}")
                        return u

            # Try any clean verified URL
            for u in clean_urls:
                if _verify_image_url(u):
                    log.info(f"Found clean photo: {u}")
                    return u

        except Exception as e:
            log.warning(f"Bing search failed for '{q}': {e}")

    # 3. Twitter/X specific UGC
    for q in search_queries[:2]:
        try:
            tw_urls = _bing_image_search(f"{q} site:pbs.twimg.com", count=10)
            for u in tw_urls:
                if "pbs.twimg.com" in u and _verify_image_url(u):
                    log.info(f"Twitter CDN image: {u}")
                    return u
        except Exception:
            pass

    # 4. Curated safe fallbacks
    TOPIC_FALLBACKS = {
        "krl": "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?w=1280",
        "banjir": "https://cdn.antaranews.com/cache/800x533/2020/02/20/20200220banjir1.jpg",
        "macet": "https://images.unsplash.com/photo-1506521781263-d8422e82f27a?w=1280",
        "kantor": "https://images.unsplash.com/photo-1497366216548-37526070297c?w=1280",
        "cuaca": "https://images.unsplash.com/photo-1534088568595-a066f410bcda?w=1280",
        "panas": "https://images.unsplash.com/photo-1504701954957-2010ec3bcec1?w=1280",
        "malam": "https://images.unsplash.com/photo-1555993539-1732b0258235?w=1280",
        "kopi": "https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=1280",
        "food": "https://images.unsplash.com/photo-1565299585323-38d6b0865b47?w=1280",
        "kerja": "https://images.unsplash.com/photo-1497366216548-37526070297c?w=1280",
    }
    combo_text = f"{clean_query} {clean_topic}".lower()
    for k, fb_url in TOPIC_FALLBACKS.items():
        if k in combo_text:
            log.info(f"Topic fallback for '{k}'")
            return fb_url

    return "https://images.unsplash.com/photo-1555993539-1732b0258235?w=1280"
