"""
Multi-Source Real-Time Trend Engine for Indonesia
Aggregates and cleans trending topics focused STRICTLY on:
1. Bencana Alam, Krisis & Cuaca Ekstrem (BMKG, BNPB, Gempa, Banjir)
2. Sepak Bola & Olahraga (Timnas Indonesia, Liga 1, EPL, UCL, Bursa Transfer)
3. Teknologi, Gadget & AI (Generative AI, Smartphone Baru, Chip, Tech Industry)
4. Pasar Saham & Ekonomi (IHSG, Saham Lokal IDX, Wall Street, S&P 500, Nasdaq, Dividen)

STRICTLY BANNED:
- Gosip artis / seleb / sensasi perceraian / drama rumah tangga selebriti
- KRL Manggarai / kosan / anak kos clichés
- Judi / slot / penipuan online
"""

import re
import random
import time
import urllib.parse
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from lxml import html
from src.logger import get_logger

log = get_logger("multi_source_trends")

ALL_PILLARS = [
    "breaking_disaster",
    "football_sports",
    "tech_gadget_ai",
    "market_finance_stocks",
]

_SUMMARY_CACHE = {}
SUMMARY_CACHE_MAX = 2000
SUMMARY_CHARS = 400

# Impact/urgency signals: a headline carrying these is a live national story,
# not routine coverage. Weight = how strongly it signals "everyone is talking".
HOT_SIGNALS = {
    "trading halt": 14, "halt": 8, "suspensi": 7, "msci": 10,
    "anjlok": 8, "ambruk": 8, "runtuh": 7, "krismon": 10, "resesi": 9,
    "karhutla": 12, "kebakaran hutan": 11, "kabut asap": 10, "asap": 5,
    "darurat": 9, "evakuasi": 8, "tsunami": 12, "gempa": 9, "erupsi": 9,
    "banjir bandang": 10, "banjir": 6, "longsor": 6, "kekeringan": 6,
    "tewas": 6, "meninggal": 6, "korban": 5, "meledak": 8, "tenggelam": 5,
    "phk": 7, "inflasi": 5, "default": 8, "rupiah": 6, "dolar": 4,
    "demo": 5, "bentrok": 6, "korupsi": 5, "ott": 6,
}

# Generic news filler — excluded from topic signatures so unrelated headlines
# don't merge into one cluster.
SIG_STOPWORDS = set("""
yang dan di ke dari untuk pada dengan ini itu akan sudah telah juga karena agar
atau tapi tetapi saat usai hari tahun kata menurut jadi bisa tidak belum masih
lebih paling para bagi secara hingga sampai sejak dalam antara tanpa oleh sebagai
adalah ada nya warga daerah pemerintah indonesia terbaru terkini update berita
hari ini bakal soal usai jelang respons tanggapi terkait dalam negeri
""".split())

# Routine coverage / institutional filler — high volume, zero virality.
ROUTINE_SIGNALS = {
    "simulasi": 12, "ikuti": 8, "sosialisasi": 12, "edukasi": 8, "pelatihan": 10,
    "perkuat mitigasi": 12, "siagakan": 10, "rakor": 12, "kunjungan kerja": 12,
    "apel": 12, "bimtek": 12, "penanaman pohon": 12, "kerja bakti": 10,
    "imbau warga lakukan ini": 8, "begini cara": 8, "ini kata pakar": 3,
    "resmi dibuka": 8, "hadiri": 8, "siapkan": 4, "waspada": 2,
}

_hot_cache = {"ts": 0.0, "items": []}
HOT_CACHE_TTL = 600  # seconds — RSS feeds don't change faster than this


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Strict blacklist: Gambling, Celebrity gossip/entertainment drama, KRL clichés
EXCLUDE_PATTERNS = [
    # Gambling / Spam
    r"\bjudi\b", r"\bslot\b", r"\bgacor\b", r"\bpragmatic\b", r"\bmaxwin\b",
    r"\btogel\b", r"\bzeus\b", r"\bdepo\b", r"\bscatter\b",
    # Celebrity / Gossip / Infotainment / Romance Drama
    r"\bartis\b", r"\bseleb\b", r"\bselebriti\b", r"\bgosip\b", r"\binfotainmen\b",
    r"\bpacaran\b", r"\bcerai\b", r"\bselingkuh\b", r"\brujuk\b", r"\bhamil\b",
    r"\bmantan istri\b", r"\bmantan suami\b", r"\blamaran\b", r"\bpernikahan\b",
    r"\bdrakor\b", r"\bk-pop\b", r"\bkpop\b", r"\bsinetron\b",
    r"\bmaia estianty\b", r"\bahmad dhani\b", r"\bmulan jameela\b", r"\bnikita mirzani\b",
    r"\blesti\b", r"\brizky billar\b", r"\braffi ahmad\b", r"\bnagita\b",
    r"\batta halilintar\b", r"\baurel\b", r"\bthariq\b", r"\bfuji\b",
    r"\bbaim wong\b", r"\bpaula verhoeven\b", r"\bruben onsu\b", r"\bsarwendah\b",
    r"\bayu ting ting\b", r"\bvia vallen\b", r"\binul\b", r"\bboy william\b",
    r"\bdensu\b", r"\bdenny sumargo\b", r"\bthe virgin\b", r"\bdara the virgin\b",
    # Clichés
    r"\bmanggarai\b", r"\banak kos\b", r"\bkamar kosan\b", r"\bbudak korporat\b",
]

# Evergreen topics per pillar if RSS is slow or empty
EVERGREEN_BY_PILLAR = {
    "breaking_disaster": [
        "Peringatan dini potensi cuaca ekstrem dan hujan lebat dari BMKG untuk beberapa wilayah",
        "Kesiapsiagaan mitigasi bencana hidrometeorologi banjir dan longsor di musim pancaroba",
        "Pentingnya sistem peringatan dini gempa dan tsunami di daerah pesisir rawan bencana",
        "Evaluasi jalur evakuasi dan kesiapan logistik BPBD saat terjadi musibah bencana alam",
        "Upaya pemadaman dan pencegahan kabut asap akibat kebakaran hutan dan lahan (karhutla)"
    ],
    "football_sports": [
        "Evaluasi taktik dan kedalaman skuad Timnas Indonesia jelang lanjutan laga internasional",
        "Persaingan ketat perebutan puncak klasemen Premier League musim ini",
        "Performa pemain abroad Indonesia di kompetisi liga luar negeri",
        "Manuver klub-klub top Eropa dalam bursa transfer dan perburuan talenta muda",
        "Analisis taktik pressing modern dan intensitas permainan di Liga Champions"
    ],
    "tech_gadget_ai": [
        "Perkembangan pesat adopsi generative AI di tempat kerja dan efisiensi produktivitas",
        "Persaingan efisiensi chipset 3nm pada smartphone flagship generasi terbaru",
        "Ancaman keamanan data pribadi dan maraknya teknik penipuan digital social engineering",
        "Perkembangan model AI open source yang mulai menandingi ekosistem proprietary global",
        "Inovasi baterai dan manajemen termal pada laptop generasi terbaru"
    ],
    "market_finance_stocks": [
        "Pergerakan IHSG dan sentimen capital inflow investor asing di saham-saham big cap BEI",
        "Strategi diversifikasi saham pembagi dividen konsisten di tengah fluktuasi indeks",
        "Dampak kebijakan suku bunga The Fed dan Bank Indonesia terhadap pasar modal dan nilai tukar",
        "Kinerja bursa saham Wall Street AS: saham teknologi Nvidia, Apple, dan indeks S&P 500",
        "Psikologi pasar dan disiplin money management saat bursa saham mengalami koreksi wajar"
    ]
}


def _is_clean(text: str) -> bool:
    if not text or len(text.strip()) < 3:
        return False
    lower = text.lower()
    for pat in EXCLUDE_PATTERNS:
        if re.search(pat, lower):
            return False
    return True


def _clean_headline(title: str) -> str:
    # Drop publisher suffix like ' - detikNews' or ' - CNBC Indonesia'
    title = re.sub(r"\s*-\s*[A-Za-z0-9\.\s]+$", "", title)
    title = re.sub(r"\s*\|\s*.*$", "", title)
    return title.strip()


def _is_recent(pub_date_str: str, max_hours: int = 36) -> bool:
    """
    Validates that the article was published in the last `max_hours` (e.g. today or yesterday).
    Strictly discards stale news from 2023, 2024, or older weeks.
    """
    if not pub_date_str:
        return False
    try:
        dt = parsedate_to_datetime(pub_date_str)
        now = datetime.now(timezone.utc)
        diff_hours = (now - dt).total_seconds() / 3600
        # Allow up to 14 hours in future (for local timezone offsets like WIB mislabeled as GMT)
        return -14 <= diff_hours <= max_hours
    except Exception:
        return False


def _remember_summary(headline: str, item) -> None:
    """Keeps the RSS <description> that arrived with a headline.

    The headline alone carries no data, so a generator asked to "use the
    figures from the source" would have nothing to ground them in and invent
    them instead. The description travels alongside, keyed by headline.
    """
    if len(_SUMMARY_CACHE) > SUMMARY_CACHE_MAX:
        _SUMMARY_CACHE.clear()
    try:
        if headline in _SUMMARY_CACHE:
            return
        desc_elem = item.find("description")
        if desc_elem is None or not desc_elem.text:
            return
        desc = re.sub(r"<[^>]+>", " ", desc_elem.text)
        desc = re.sub(r"\s+", " ", desc).strip()
        if desc:
            _SUMMARY_CACHE[headline] = desc[:SUMMARY_CHARS]
    except Exception:
        pass


def get_source_summary(headline: str) -> str:
    """Returns the RSS description stored for a headline, or empty string.

    Callers must treat an empty string as "no source data available", never as
    permission to supply their own figures.
    """
    return _SUMMARY_CACHE.get(headline, "")


def _fetch_rss(url: str, limit: int = 10, require_recent: bool = True) -> list[str]:
    """Helper to fetch and parse RSS feeds with strict date freshness verification."""
    headlines = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200 and (r.content.startswith(b"<?xml") or b"<rss" in r.content[:200]):
            root = ET.fromstring(r.content)
            for item in root.findall(".//item"):
                elem_title = item.find("title")
                elem_date = item.find("pubDate")
                date_str = elem_date.text if elem_date is not None else ""

                if require_recent and date_str and not _is_recent(date_str, max_hours=36):
                    continue

                if elem_title is not None and elem_title.text:
                    cleaned = _clean_headline(elem_title.text)
                    if _is_clean(cleaned) and cleaned not in headlines:
                        headlines.append(cleaned)
                        _remember_summary(cleaned, item)
                        if len(headlines) >= limit:
                            break
    except Exception as e:
        log.warning(f"Failed RSS fetch from {url[:60]}: {e}")
    return headlines


def fetch_disaster_trends() -> list[str]:
    """Fetch real-time disaster, BMKG, natural disaster, and national emergency news."""
    results = []
    # 1. Google News Search strictly constrained to past 24 hours (when:1d) - Primary
    queries = [
        "bencana alam OR gempa bumi OR banjir OR longsor OR BMKG cuaca ekstrem when:1d",
        "peringatan dini BMKG OR tanggap darurat BNPB OR erupsi gunung when:1d"
    ]
    for q in queries:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=id&gl=ID&ceid=ID:id"
        results.extend(_fetch_rss(url, limit=8))

    # 2. Antara News & Section NATION filtered by disaster keywords
    disaster_kws = ["bencana", "gempa", "banjir", "longsor", "bmkg", "cuaca", "hujan", "erupsi", "angin", "karhutla", "bnpb", "bpbd", "tsunami"]
    general_rss = []
    general_rss.extend(_fetch_rss("https://www.antaranews.com/rss/terkini.xml", limit=8))
    general_rss.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/NATION?hl=id&gl=ID&ceid=ID:id", limit=8))
    for r in general_rss:
        if any(kw in r.lower() for kw in disaster_kws):
            results.append(r)

    return list(dict.fromkeys([r for r in results if _is_clean(r)]))


def fetch_football_trends() -> list[str]:
    """
    Fetch real-time football & sports news (High Engagement Driver):
    - Timnas Indonesia & Garuda squad
    - BRI Liga 1 & rivalries
    - Premier League, UCL, La Liga, Serie A
    - Transfer updates (Fabrizio Romano / clubs)
    """
    results = []
    # 1. Antara News Sepakbola
    results.extend(_fetch_rss("https://www.antaranews.com/rss/sepakbola.xml", limit=10))
    # 2. Google News Section SPORTS Indonesia (Real-time curated)
    results.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/SPORTS?hl=id&gl=ID&ceid=ID:id", limit=10))
    # 3. Targeted high-engagement football searches past 24h
    queries = [
        "Timnas Indonesia OR PSSI OR Shin Tae-yong OR pemain abroad when:1d",
        "Liga 1 Indonesia OR Persib OR Persija OR Persebaya OR Arema when:1d",
        "Premier League OR Liga Inggris OR Manchester United OR Arsenal OR Liverpool OR Man City when:1d",
        "Liga Champions OR Real Madrid OR Barcelona OR Bayern Munich when:1d",
        "bursa transfer pemain sepak bola OR rumor transfer pemain when:1d"
    ]
    for q in queries:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=id&gl=ID&ceid=ID:id"
        results.extend(_fetch_rss(url, limit=8))
    return list(dict.fromkeys([r for r in results if _is_clean(r)]))


def fetch_tech_trends() -> list[str]:
    """Fetch real-time technology, AI, gadgets, chips, and digital industry news (Local & Global)."""
    results = []
    # 1. CNBC Indonesia Tech (Real-time RSS)
    results.extend(_fetch_rss("https://www.cnbcindonesia.com/tech/rss", limit=10))
    # 2. Antara News Tekno
    results.extend(_fetch_rss("https://www.antaranews.com/rss/tekno.xml", limit=8))
    # 3. Google News Section TECHNOLOGY (Global & Local)
    results.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=id&gl=ID&ceid=ID:id", limit=8))
    results.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en", limit=8))
    # 4. Global tech portals (Bloomberg, Reuters, The Verge)
    queries = [
        "teknologi AI OR artificial intelligence OR gadget baru OR Nvidia OR iPhone when:1d",
        "site:bloomberg.com OR site:reuters.com OR site:theverge.com (OpenAI OR Nvidia OR AI chip OR Apple) when:1d"
    ]
    for q in queries:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=id&gl=ID&ceid=ID:id"
        results.extend(_fetch_rss(url, limit=6))
    return list(dict.fromkeys([r for r in results if _is_clean(r)]))


def fetch_market_trends() -> list[str]:
    """
    Fetch real-time market, finance, and macro economy news (Local IDX & Global Wall Street):
    - IHSG, emiten lokal IDX, dividen interim, perbankan big cap
    - Global: Bloomberg, Reuters, CNBC Global, Wall Street, The Fed, S&P 500, Nasdaq
    """
    results = []
    # 1. CNBC Indonesia Market (Real-time RSS)
    results.extend(_fetch_rss("https://www.cnbcindonesia.com/market/rss", limit=10))
    # 2. Antara News Ekonomi Finansial
    results.extend(_fetch_rss("https://www.antaranews.com/rss/ekonomi-finansial.xml", limit=8))
    # 3. Google News US / Global Business (Bloomberg, Reuters, Wall Street)
    results.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en", limit=8))
    # 4. Google News Indonesia Business
    results.extend(_fetch_rss("https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=id&gl=ID&ceid=ID:id", limit=8))
    # 5. Targeted Global & Local Market queries past 24h
    queries = [
        "IHSG OR Bursa Efek Indonesia OR saham dividen OR dividen interim when:1d",
        "site:bloomberg.com OR site:reuters.com (Wall Street OR Federal Reserve OR stocks OR interest rate) when:1d",
        "pasar saham AS OR Wall Street OR S&P 500 OR Nasdaq OR The Fed suku bunga when:1d"
    ]
    for q in queries:
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl=id&gl=ID&ceid=ID:id"
        results.extend(_fetch_rss(url, limit=6))
    return list(dict.fromkeys([r for r in results if _is_clean(r)]))


def fetch_trends_for_pillar(pillar: str) -> list[str]:
    """Fetch targeted fresh real-time news headlines for a specific pillar."""
    if pillar == "breaking_disaster":
        items = fetch_disaster_trends()
    elif pillar == "football_sports":
        items = fetch_football_trends()
    elif pillar == "tech_gadget_ai":
        items = fetch_tech_trends()
    elif pillar == "market_finance_stocks":
        items = fetch_market_trends()
    else:
        items = []

    if not items:
        items = EVERGREEN_BY_PILLAR.get(pillar, [])[:]
        random.shuffle(items)

    log.info(f"Pillar '{pillar}' fetched {len(items)} items")
    return items


def fetch_all_trends() -> list[str]:
    """
    Returns aggregated list of topics across all 4 authorized categories.
    """
    all_items = []
    for p in ALL_PILLARS:
        all_items.extend(fetch_trends_for_pillar(p))
    return list(dict.fromkeys(all_items))


# ---------------------------------------------------------------------------
# Heat ranking — "what is actually trending right now"
# ---------------------------------------------------------------------------

def _signature(headline: str) -> str:
    """Lowercase content-word signature used to cluster headlines about the same story."""
    words = re.findall(r"[a-zA-Z]{3,}", headline.lower())
    keys = [w for w in words if w not in SIG_STOPWORDS]
    return " ".join(sorted(set(keys)))


def _heat_score(headline: str) -> int:
    lower = headline.lower()
    hot = sum(w for sig, w in HOT_SIGNALS.items() if sig in lower)
    routine = sum(w for sig, w in ROUTINE_SIGNALS.items() if sig in lower)
    return hot - routine


def fetch_hot_topics(top_n: int = 6, per_pillar_limit: int = 14) -> list[dict]:
    """
    Ranks live headlines across all pillars by real-world impact + cross-source corroboration.

    heat = summed impact signals of clustered headlines + corroboration bonus
           (how many independent headlines tell the same story) + recency.
    Returns [{"topic", "heat", "pillar", "corroboration"}], hottest first.
    """
    now = time.time()
    if _hot_cache["items"] and (now - _hot_cache["ts"]) < HOT_CACHE_TTL:
        return _hot_cache["items"][:top_n]

    clusters: dict[str, dict] = {}
    for pillar in ALL_PILLARS:
        try:
            headlines = fetch_trends_for_pillar(pillar)
        except Exception as e:
            log.warning(f"hot topics: pillar {pillar} fetch failed: {e}")
            continue
        # Rank within the pillar before truncating, otherwise a 46-item feed
        # pushes the single hottest story past the cutoff and it never competes.
        headlines.sort(key=_heat_score, reverse=True)
        for h in headlines[:per_pillar_limit]:
            sig = _signature(h)
            if not sig:
                continue
            c = clusters.setdefault(sig, {"topic": h, "heat": 0, "corroboration": 0, "pillar": pillar})
            c["corroboration"] += 1
            c["heat"] += _heat_score(h)
            # Prefer the longest, most informative headline as the cluster label
            if len(h) > len(c["topic"]):
                c["topic"] = h

    ranked = []
    for c in clusters.values():
        if not _is_clean(c["topic"]):
            continue
        c["heat"] += min(c["corroboration"], 5) * 6  # corroboration bonus
        ranked.append(c)
    ranked.sort(key=lambda x: x["heat"], reverse=True)

    log.info(f"Hot topics ranked ({len(ranked)} clusters). Top: "
             + " | ".join(f"{r['topic'][:38]} ({r['heat']})" for r in ranked[:3]))
    _hot_cache["ts"] = now
    _hot_cache["items"] = ranked
    return ranked[:top_n]


def pillar_for_topic(topic: str) -> str:
    """Best-effort pillar classification for an arbitrary (user-supplied) topic."""
    lower = topic.lower()
    if any(k in lower for k in ["saham", "ihsg", "bursa", "idx", "emiten", "dividen", "rupiah",
                                "dolar", "inflasi", "ekonomi", "keuangan", "bank", "obligasi",
                                "msci", "fed", "wall street", "nasdaq", "s&p", "crypto", "bitcoin"]):
        return "market_finance_stocks"
    if any(k in lower for k in ["bola", "sepak", "liga", "timnas", "pemain", "klub", "transfer",
                                "goal", "gol", "piala", "fifa", "ucl", "epl", "persib", "persija",
                                "badminton", "bulu tangkis", "motogp", "f1", "basket"]):
        return "football_sports"
    if any(k in lower for k in ["bencana", "banjir", "gempa", "longsor", "karhutla", "kebakaran",
                                "asap", "bmkg", "bnpb", "cuaca", "hujan", "erupsi", "tsunami",
                                "kekeringan", "angin", "kabut", "gunung"]):
        return "breaking_disaster"
    if any(k in lower for k in ["ai", "teknologi", "gadget", "hp", "smartphone", "chip", "nvidia",
                                "apple", "samsung", "internet", "data", "robot", "startup"]):
        return "tech_gadget_ai"
    return "breaking_disaster"


def fetch_topic_image_query(topic: str, pillar: str = "") -> str:
    """
    Builds a visual image_search query grounded in the topic's *news entities* —
    not a generic scene. 'Asap Karhutla Kalimantan Sampai Jakarta'
    → 'kebakaran hutan lahan asap tebal kalimantan'.
    """
    lower = topic.lower()
    if any(k in lower for k in ["karhutla", "kebakaran hutan", "kabut asap", "lahan terbakar"]):
        return "kebakaran hutan lahan asap tebal petugas pemadaman"
    if any(k in lower for k in ["trading halt", "halt", "suspensi", "anjlok", "ambruk", "ihsg", "msci", "saham"]):
        return "layar indeks saham merah bursa efek tegang"
    if any(k in lower for k in ["banjir", "genangan", "luapan"]):
        return "evakuasi warga terjang arus banjir permukiman"
    if any(k in lower for k in ["gempa", "reruntuhan", "longsor"]):
        return "reruntuhan bangunan pasca gempa tim penyelamat"
    if any(k in lower for k in ["erupsi", "gunung", "abu vulkanik"]):
        return "erupsi gunung abu vulkanik kolom asap"
    if "rupiah" in lower or "dolar" in lower:
        return "kurs rupiah dolar papan nilai tukar money changer"
    if any(k in lower for k in ["inflasi", "harga", "pangan", "sembako"]):
        return "pedagang pasar harga kebutuhan pokok"
    if any(k in lower for k in ["phk", "buruh", "pabrik"]):
        return "buruh pabrik unjuk rasa spanduk"
    if "korupsi" in lower or "kpk" in lower or "ott" in lower:
        return "tersangka korupsi dievakuasi kpk"
    if any(k in lower for k in ["ai", "robot", "artificial intelligence"]):
        return "tangan robotik chip ai futuristik close up"
    if any(k in lower for k in ["chip", "nvidia", "semikonduktor", "data center"]):
        return "sirkuit chip semikonduktor close up makro"
    if any(k in lower for k in ["saham", "bursa", "wall street", "nasdaq"]):
        return "pialang saham layar bursa ekspresi tegang"
    if any(k in lower for k in ["hp ", "smartphone", "iphone", "samsung", "gadget"]):
        return "smartphone flagship layar menyala close up"
    if any(k in lower for k in ["sepak", "bola", "liga", "timnas", "pemain", "gol", "piala",
                                "ucl", "epl", "fifa", "persib", "persija", "transfer"]):
        return "aksi duel pemain sepak bola pertandingan"
    # fallback: keep the entities, append a neutral news-visual suffix
    ent = re.sub(r"\s+", " ", topic).strip()
    ent = re.sub(r"[^\w\s\-]", "", ent, flags=re.UNICODE)[:70]
    return f"{ent} berita"


if __name__ == "__main__":
    print("\n=== HOT TOPICS NOW ===")
    for r in fetch_hot_topics(top_n=8):
        print(f"  [{r['heat']:3d}] [{r['pillar']:22s}] {r['topic'][:78]}")


