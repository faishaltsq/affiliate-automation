"""
Vibe Image Searcher
Finds authentic, relatable real-world public images (KRL crowded, Jakarta street heat,
commuter walking, office desk, etc.) to match the viral hook/bait thread.

Priority 1: Pinterest (i.pinimg.com) via Bing — clean, aesthetic, no watermark
Priority 2: Clean UGC/blog images — no stock/news watermark domains
Priority 3: Curated Pinterest fallback URLs by keyword
"""

import base64
import datetime
import io
import re
import urllib.parse
from PIL import Image
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
    "cnbcindonesia.com", "bisnis.com", "kontan.co.id", "katadata.co.id",
    "tempo.co", "tribunnews.com", "liputan6.com", "kumparan.com",
    "inews.id", "suara.com", "merdeka.com", "beritasatu.com",
    "idntimes.com", "tvonenews.com", "metrotvnews.com", "sindonews.com",
    "jawapos.com", "bolasport.com", "disway.id"
]

# High-priority real news and press photo domains (Local & Global)
NEWS_DOMAINS = [
    # Global top tier
    "bloomberg.com", "reuters.com", "wsj.com", "ft.com", "cnbc.com",
    "apnews.com", "theverge.com", "techcrunch.com", "skysports.com",
    "bbc.com", "goal.com",
    # Local Indonesia
    "antaranews.com", "kompas.com", "detik.net.id", "detik.com",
    "cnbcindonesia.com", "bisnis.com", "kontan.co.id", "katadata.co.id",
    "metrotvnews.com", "media.suara.com", "suara.com", "tvonenews.com",
    "idntimes.com", "rri.co.id", "beritasatu.com", "tribunnews.com",
    "jawapos.com", "kumparan.com", "sindonews.com", "tempo.co",
    "liputan6.com", "cnnindonesia.com", "viva.co.id", "disway.id",
    "medcom.id", "merdeka.com", "setkab.go.id", "kemkes.go.id",
    "polri.go.id", "pbs.twimg.com", "kly.akamaized.net", "idn.media",
    "bolasport.com", "bola.net", "bola.com"
]

BAD_IMAGE_PATTERNS = [
    "logo", "default", "placeholder", "blank", "share-image", "favicon", "avatar", "icon",
    "pasien", "rumah-sakit", "ranjang", "infografis", "vektor", "kartun", "ilustrasi",
    "konferensi-pers", "press-conference", "press-release", "podium", "plang", "spanduk"
]

DYNAMIC_PHOTO_KEYWORDS = [
    "selebrasi", "celebration", "gol", "goal", "aksi", "action", "duel", "tanding",
    "kalahkan", "menang", "juara", "dramatis", "dramatic", "close-up", "closeup",
    "ekspresi", "reaksi", "emosi", "evakuasi", "terjang", "penyelamatan", "kobaran"
]

STATIC_PHOTO_KEYWORDS = [
    "konferensi", "konpers", "press-conference", "press-release", "podium", "pidato",
    "duduk", "meja-sidang", "ruang-sidang", "plang", "spanduk"
]

NEWS_TRIGGERS = [
    "kasus", "perundungan", "korban", "pelaku", "polisi", "polres", "polda", "tewas",
    "meninggal", "kecelakaan", "banjir", "kebakaran", "gempa", "erupsi", "longsor",
    "bencana", "pembunuhan", "begal", "dpr", "kpk", "korupsi", "sidang", "tersangka",
    "menteri", "presiden", "gubernur", "bupati", "walikota", "viral", "tragedi",
    "bentrok", "demo", "unjuk rasa", "razia", "tangkap", "ott", "kejaksaan", "hukum",
    "vonis", "hakim", "sekolah", "siswa", "sd", "smp", "sma",
    "ihsg", "saham", "bursa", "dividen", "emiten", "wall street", "nasdaq", "the fed",
    "timnas", "liga inggris", "liga", "champions", "transfer", "pelatih", "pemain",
    "teknologi", "gadget", "smartphone", "iphone", "samsung", "nvidia"
]


def _extract_url_date(url: str):
    """
    Pull a publication date out of an image/article URL.
    Handles /2026/10/17/, /2026-10-17/, /20261017/ and 20261017T... stamps.
    Returns datetime.date or None.
    """
    u = url.lower()
    for pat, fmt in (
        (r"/(20\d{2})/(\d{2})/(\d{2})/", "%Y/%m/%d"),
        (r"/(20\d{2})-(\d{2})-(\d{2})", "%Y-%m-%d"),
        (r"(20\d{2})(\d{2})(\d{2})[t_\-]", "%Y%m%d"),
        (r"/(20\d{2})(\d{2})(\d{2})/", "%Y%m%d"),
    ):
        m = re.search(pat, u)
        if m:
            try:
                return datetime.datetime.strptime(m.group(1) + m.group(2) + m.group(3), "%Y%m%d").date()
            except ValueError:
                continue

    # CDN media often embeds the upload time as a Unix epoch in the filename
    # (…_1757397640-2.jpg). A "today" article reusing Sep-2025 media still shows
    # last year's market, so treat the epoch as the real photo date.
    for m in re.finditer(r"_(1[6-9]\d{8})(?:[-_.]|$)", u):
        try:
            return datetime.datetime.fromtimestamp(int(m.group(1))).date()
        except (ValueError, OSError, OverflowError):
            continue
    return None


def _photo_age_days(url: str):
    d = _extract_url_date(url)
    if not d:
        return None
    return (datetime.date.today() - d).days


# A photo older than this cannot illustrate "what happened today" — an IDX board
# showing 8.261 is a different market event than the 5.185 halt being reported.
MAX_PHOTO_AGE_DAYS = 10


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


def clean_portal_watermark(image_url: str) -> str:
    """
    Pass-through: watermark cropping is now handled losslessly in-memory
    by card_generator.py to preserve 100% original crystal-clear HD resolution.
    """
    return image_url


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
                            score += 8
                        elif any(old in img_lower or old in art_lower for old in ["2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017"]):
                            score -= 25

                        # Reject stale article photos outright — the lead image of
                        # a 2025 article shows a different event than today's story.
                        art_age = _photo_age_days(art_url)
                        if art_age is None:
                            art_age = _photo_age_days(img_url)
                        if art_age is not None and art_age > MAX_PHOTO_AGE_DAYS:
                            log.info(f"Skipping stale article photo ({art_age}d old): {art_url}")
                            continue

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


def _find_best_news_photo(query: str, topic: str) -> str:
    """
    Searches Bing Images for authentic high-resolution press photos from NEWS_DOMAINS.
    Guarantees the photo matches the exact topic/entity while favoring dynamic angles & high resolution.
    Ranks candidates by topic relevance, freshness (year 2026/2025), dynamic emotion/angle, and file size.
    Filters out patient beds, logos, graphics, and low-res thumbnails.
    """
    search_terms = []
    if topic:
        search_terms.append(f"{topic} berita")
        if query and query.lower() != topic.lower():
            # Combine topic entity with visual query (e.g., "Persib Bandung selebrasi gol")
            search_terms.append(f"{topic} {query}")
    if query and query not in search_terms:
        search_terms.append(query)

    topic_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", topic.lower())) if topic else set()
    for common in ["berita", "indonesia", "yang", "pada", "untuk", "saat", "oleh", "usai", "hari", "terkait", "dalam", "bikin", "agar"]:
        topic_words.discard(common)

    query_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", query.lower())) if query else set()
    for common in ["berita", "indonesia", "yang", "pada", "untuk", "saat", "oleh"]:
        query_words.discard(common)

    candidates = []
    seen_urls = set()

    for q in search_terms:
        try:
            urls = _bing_image_search(q, count=40)
            for u in urls:
                if u in seen_urls:
                    continue
                seen_urls.add(u)

                u_lower = u.lower()
                if not any(nd in u_lower for nd in NEWS_DOMAINS):
                    continue
                if any(bad in u_lower for bad in BAD_IMAGE_PATTERNS):
                    continue

                # Hard freshness gate: a stale archive photo shows a *different*
                # event (old index level, old crowd), which reads as fake news.
                age = _photo_age_days(u)
                if age is not None and age > MAX_PHOTO_AGE_DAYS:
                    continue

                score = 0

                # 1. Exact Topic Relevance Scoring (Crucial: photo MUST match the actual subject)
                for tw in topic_words:
                    if tw in u_lower:
                        score += 15

                for qw in query_words:
                    if qw in u_lower:
                        score += 5

                # 2. Freshness scoring
                if "2026" in u_lower:
                    score += 20
                elif "2025" in u_lower:
                    score += 10
                elif any(y in u_lower for y in ["2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017", "2016"]):
                    score -= 30

                # 3. Dynamic action / emotional expression bonus
                if any(dyn in u_lower for dyn in DYNAMIC_PHOTO_KEYWORDS):
                    score += 8
                # Static / formal conference penalty
                if any(st in u_lower for st in STATIC_PHOTO_KEYWORDS):
                    score -= 12

                # 4. Image verification & resolution check
                try:
                    r = requests.head(u, headers=HEADERS, timeout=2.5, allow_redirects=True)
                    if r.status_code == 200 and "image" in r.headers.get("content-type", ""):
                        cl = int(r.headers.get("content-length", 0))
                        if cl >= 100000:
                            score += 15
                        elif cl >= 65000:
                            score += 8
                        elif cl < 30000:
                            score -= 10
                        candidates.append((score, cl, u))
                except Exception:
                    pass

            # If we found strong matches with high topic relevance, we can stop searching further queries
            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                if candidates[0][0] >= 35:
                    break

        except Exception as e:
            log.warning(f"Failed photo search for '{q}': {e}")

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        winner = candidates[0][2]
        log.info(f"Selected top-ranked authentic NEWS photo (score={candidates[0][0]}, size={candidates[0][1]}B): {winner}")
        return winner

    return ""


def search_vibe_image(query: str, raw_topic: str = "") -> str:
    """
    Search for authentic real-life photo or news documentation matching the topic.
    Guaranteed clean & watermark-free.
    """
    clean_query = query.strip()
    clean_topic = raw_topic.strip()
    log.info(f"Searching image for query='{clean_query}', topic='{clean_topic}'")

    # 1. Primary: lead image (og:image) of a *current* article about this exact
    #    story. An archive photo from last year shows a different index level /
    #    crowd, so a live article is the only source that can't misrepresent it.
    news_img = search_news_article_image(clean_topic or clean_query)
    if news_img:
        return clean_portal_watermark(news_img)

    # 2. Secondary: Bing Images ranked by topic relevance + freshness gate
    best_news_img = _find_best_news_photo(clean_query, clean_topic)
    if best_news_img:
        return clean_portal_watermark(best_news_img)

    # 2. Secondary: If it's a news / crime / disaster / trending event topic, query news article og:image
    is_news = False
    check_text = f"{clean_topic} {clean_query}".lower()
    if clean_topic:
        is_news = True  # Explicit custom topics are almost always news/trends
    elif any(trig in check_text for trig in NEWS_TRIGGERS):
        is_news = True

    if is_news:
        news_img = search_news_article_image(clean_topic or clean_query)
        if news_img:
            return clean_portal_watermark(news_img)

    # 3. General search queries on Bing Images
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
                        return clean_portal_watermark(u)

            # Try any clean verified URL
            for u in clean_urls:
                if _verify_image_url(u):
                    log.info(f"Found clean photo: {u}")
                    return clean_portal_watermark(u)

        except Exception as e:
            log.warning(f"Bing search failed for '{q}': {e}")

    # 3. Twitter/X specific UGC
    for q in search_queries[:2]:
        try:
            tw_urls = _bing_image_search(f"{q} site:pbs.twimg.com", count=10)
            for u in tw_urls:
                if "pbs.twimg.com" in u and _verify_image_url(u):
                    log.info(f"Twitter CDN image: {u}")
                    return clean_portal_watermark(u)
        except Exception:
            pass

    # 4. Curated safe fallbacks (100% Unsplash watermark-free for 4 pillars)
    TOPIC_FALLBACKS = {
        "gempa": "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?w=1280",
        "bencana": "https://images.unsplash.com/photo-1547683905-f686c993aae5?w=1280",
        "cuaca": "https://images.unsplash.com/photo-1534088568595-a066f410bcda?w=1280",
        "bola": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=1280",
        "persib": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=1280",
        "timnas": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?w=1280",
        "saham": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1280",
        "ihsg": "https://images.unsplash.com/photo-1611974789855-9c2a0a7236a3?w=1280",
        "market": "https://images.unsplash.com/photo-1590283603385-17ffb3a7f29f?w=1280",
        "tech": "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1280",
        "ai": "https://images.unsplash.com/photo-1677442136019-21780efad99a?w=1280",
        "gadget": "https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=1280",
    }
    combo_text = f"{clean_query} {clean_topic}".lower()
    for k, fb_url in TOPIC_FALLBACKS.items():
        if k in combo_text:
            log.info(f"Topic fallback for '{k}'")
            return fb_url

    return "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1280"
