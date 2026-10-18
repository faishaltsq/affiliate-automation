"""
Affiliate Comment Engine
Generates contextual affiliate comments and posts them as replies to Threads posts.

Two modes:
1. Reply to OUR OWN posts that hit engagement threshold (≥20 likes or ≥10 replies)
2. Reply to a SPECIFIC thread URL provided by the user
"""

import os
import re
import json
import time
import sqlite3
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from src.config import DATA_DIR
from src.brief.generator import _llm
from src.scraper.product_extractor import extract_product_metadata
from src.logger import get_logger

load_dotenv(Path(__file__).parent.parent.parent / '.env')

log = get_logger("comment_engine")

USER_ID = os.getenv('THREADS_USER_ID')
ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
BASE_URL = 'https://graph.threads.net/v1.0'
DB_PATH = DATA_DIR / "posts.db"


def init_comment_history():
    """Ensure affiliate_comment_history table exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS affiliate_comment_history (
            post_id TEXT PRIMARY KEY,
            reply_id TEXT,
            affiliate_url TEXT,
            product_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def record_affiliate_comment(post_id: str, reply_id: str, affiliate_url: str, product_name: str = ""):
    """Record that a post has received an affiliate comment."""
    init_comment_history()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO affiliate_comment_history (post_id, reply_id, affiliate_url, product_name)
        VALUES (?, ?, ?, ?)
    """, (str(post_id), str(reply_id), str(affiliate_url), str(product_name)))
    conn.commit()
    conn.close()
    log.info(f"Recorded affiliate comment history for post {post_id}")


def get_commented_post_ids() -> set[str]:
    """Get all post IDs that already have an affiliate comment."""
    init_comment_history()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT post_id FROM affiliate_comment_history")
    rows = c.fetchall()
    conn.close()
    return {r[0] for r in rows}


# ── Threads API helpers ────────────────────────────────────────────────────────

def get_our_posts(limit: int = 20) -> list[dict]:
    """Fetch our recent Threads posts with IDs."""
    r = requests.get(
        f"{BASE_URL}/{USER_ID}/threads",
        params={
            "fields": "id,text,timestamp",
            "limit": limit,
            "access_token": ACCESS_TOKEN,
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("data", [])


def get_post_metrics(post_id: str) -> dict:
    """Returns views, likes and replies count for a given Threads post."""
    r = requests.get(
        f"{BASE_URL}/{post_id}/insights",
        params={
            "metric": "views,likes,replies",
            "access_token": ACCESS_TOKEN,
        },
        timeout=15,
    )
    if not r.ok:
        log.warning(f"Failed metrics for {post_id}: {r.text}")
        return {"views": 0, "likes": 0, "replies": 0}

    metrics = {"views": 0, "likes": 0, "replies": 0}
    for item in r.json().get("data", []):
        name = item["name"]  # "views", "likes", or "replies"
        value = item["values"][0]["value"] if item.get("values") else 0
        metrics[name] = value
    return metrics


def extract_thread_id_from_url(url: str) -> str | None:
    """
    Extract Threads post ID from a Threads URL or raw ID.
    Format: https://www.threads.net/@username/post/<post_shortcode_or_id>
    """
    if not url:
        return None
    url = url.strip()
    if url.isdigit():
        return url

    # Try to extract shortcode or numeric ID from URL
    match = re.search(r'threads\.(?:net|com)/@[\w.]+/post/([\w-]+)', url)
    if not match:
        match = re.search(r'threads\.(?:net|com)/t/([\w-]+)', url)
    if not match:
        return None

    shortcode = match.group(1)
    if shortcode.isdigit():
        return shortcode

    # Resolve alphanumeric shortcode → numeric ID via Threads oEmbed
    try:
        r = requests.get(
            f"{BASE_URL}/oembed",
            params={
                "url": url,
                "access_token": ACCESS_TOKEN,
            },
            timeout=15,
        )
        if r.ok:
            data = r.json()
            media_id = data.get("media_id") or data.get("id")
            if media_id:
                log.info(f"Resolved URL → media_id={media_id}")
                return str(media_id)
    except Exception as e:
        log.warning(f"Error querying oEmbed for {url}: {e}")

    return shortcode


# ── Gate 3: engagement gate ───────────────────────────────────────────────────
# Konsep: post harus selesai dibaca DAN diskusi sudah hidup dulu, baru affiliate.
# Logika lama pakai OR (views>=250 saja sudah cukup), jadi link bisa nyangkut
# sebelum ada satu pun komentar masuk — justru di momen post lagi di-push awal.
ORGANIC_REPLIES_MIN = 5
MIN_POST_AGE_MINUTES = 45
DEFAULT_VIEWS_THRESHOLD = 250
DEFAULT_LIKES_THRESHOLD = 20

# Komentar pembaca yang menandakan niat butuh barang. Tempel affiliate sebagai
# balasan komentar itu (nested) — keluar dari alur baca utama dan terbaca sebagai
# jawaban kontekstual, bukan lapak yang menempel di root post.
NEED_KEYWORDS = [
    "cari", "nyari", "butuh", "rekomendasi", "rekomen", "recommend",
    "murah", "worth", "awet", "bagus gak", "aman gak", "diskon", "promo",
    "link", "beli di", "di mana beli", "ada yang jual",
]

# Bencana = irrelevant, insensitive, and a magnet for grief tourists. Never a
# place to park a product link.
BENCANA_KEYWORDS = [
    "banjir", "gempa", "kebakaran", "longsor", "tsunami",
    "korban", "meninggal", "duka",
]


def post_text_lower(post: dict) -> str:
    return (post.get("text") or "").lower()


_OWN_USERNAMES = {
    (os.getenv("THREADS_USERNAME") or "").lower().lstrip("@"),
    "pochita9887",
}


def _parse_timestamp(ts) -> "datetime | None":
    """Threads returns ISO-8601 with a 'Z' or '+0700' suffix; normalise both."""
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", s):
        s = s[:-2] + ":" + s[-2:]
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def post_age_minutes(post: dict) -> float | None:
    """Minutes since the post was created. None when the timestamp is missing."""
    dt = _parse_timestamp(post.get("timestamp"))
    if not dt:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60.0


def get_post_replies(post_id: str) -> list[dict]:
    """Raw replies under a post: [{id, text, timestamp, username}]."""
    try:
        r = requests.get(
            f"{BASE_URL}/{post_id}/replies",
            params={
                "fields": "id,text,timestamp,username",
                "limit": 50,
                "access_token": ACCESS_TOKEN,
            },
            timeout=15,
        )
        if not r.ok:
            log.warning(f"Failed to fetch replies for {post_id}: {r.text[:150]}")
            return []
        return r.json().get("data", []) or []
    except Exception as e:
        log.warning(f"get_post_replies({post_id}) error: {e}")
        return []


def organic_replies(post_id: str) -> list[dict]:
    """Replies from OTHER people.

    Our own chain parts and the bonus self-reply are replies too, so counting
    raw `replies` would let a brand new post pass the discussion gate on its own
    self-talk. Anything authored by this account is dropped.
    """
    out = []
    for reply in get_post_replies(post_id):
        name = (reply.get("username") or "").lower().lstrip("@")
        if name and name in _OWN_USERNAMES:
            continue
        out.append(reply)
    return out


def pick_affiliate_reply_target(post_id: str, replies: list[dict] | None = None) -> str:
    """Pick which comment the affiliate should answer.

    Preference order:
      1. a comment that signals intent to buy (NEED_KEYWORDS) — the product
         answers a question actually asked in the thread,
      2. otherwise the earliest organic comment, so we don't jump the queue of
         people mid-discussion.
    Falls back to the root post when there is no organic comment at all.

    `replies` lets a caller hand over the list the gate already fetched instead
    of pulling the thread twice.
    """
    replies = organic_replies(post_id) if replies is None else replies
    if not replies:
        return post_id

    for reply in replies:
        text = (reply.get("text") or "").lower()
        if any(kw in text for kw in NEED_KEYWORDS):
            log.info(f"Affiliate will answer buying-intent comment {reply.get('id')}")
            return reply.get("id") or post_id

    replies.sort(key=lambda x: x.get("timestamp") or "")
    return replies[0].get("id") or post_id


def post_qualifies(
    views: int,
    likes: int,
    organic_reply_count: int,
    age_minutes: float | None,
    views_threshold: int = DEFAULT_VIEWS_THRESHOLD,
    likes_threshold: int = DEFAULT_LIKES_THRESHOLD,
    replies_min: int = ORGANIC_REPLIES_MIN,
    min_age: int = MIN_POST_AGE_MINUTES,
) -> tuple[bool, str]:
    """Returns (passes_gate, reason).

    All conditions must hold — discussion live AND distribution window passed AND
    post performing. A missing timestamp skips only the age leg (logged upstream),
    never the discussion leg: an unanswered post is never a place for a link.
    """
    if organic_reply_count < replies_min:
        return False, f"organic replies {organic_reply_count} < {replies_min}"
    if age_minutes is not None and age_minutes < min_age:
        return False, f"post age {age_minutes:.0f}m < {min_age}m"
    if views < views_threshold and likes < likes_threshold:
        return False, f"views {views} < {views_threshold} and likes {likes} < {likes_threshold}"
    return True, "ok"


def post_reply_to_thread(target_post_id: str, comment_text: str) -> str | None:
    """Post a reply to any Threads post. Returns reply ID."""
    for attempt in range(3):
        try:
            # Step 1: create container
            r1 = requests.post(
                f"{BASE_URL}/{USER_ID}/threads",
                params={
                    "media_type": "TEXT",
                    "text": comment_text,
                    "reply_to_id": target_post_id,
                    "access_token": ACCESS_TOKEN,
                },
                timeout=30,
            )
            r1.raise_for_status()
            container_id = r1.json()["id"]
            log.info(f"Reply container created: {container_id}")

            time.sleep(5)  # give API time to process

            # Step 2: publish
            r2 = requests.post(
                f"{BASE_URL}/{USER_ID}/threads_publish",
                params={
                    "creation_id": container_id,
                    "access_token": ACCESS_TOKEN,
                },
                timeout=30,
            )
            r2.raise_for_status()
            reply_id = r2.json()["id"]
            log.info(f"Reply published: {reply_id}")
            return reply_id

        except requests.exceptions.Timeout as e:
            log.warning(f"Timeout posting reply (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
        except Exception as e:
            log.error(f"Failed to post reply: {e}")
            return None

    log.error("All 3 attempts to post reply timed out.")
    return None


# ── Product name sanitizer ────────────────────────────────────────────────────

_SANITIZE_WORDS = [
    # kata eksplisit / 18+
    r"\bsexy\b", r"\bseksual\b", r"\blingerie\b", r"\bporno\b", r"\berotic\b",
    r"\berotis\b", r"\btelanjang\b", r"\bnude\b",
    # kata "wanita" / gender body-shaming dalam judul produk
    r"\bsex\b",
    # bra / pakaian dalam berkonotasi negatif saat dipajang
    # (simpan istilah umum saja, hapus yang vulgar)
]

_SANITIZE_PATTERN = None


def _sanitize_product_name(name: str) -> str:
    """Strip 18+ / explicit words from product name before feeding to LLM."""
    global _SANITIZE_PATTERN
    if _SANITIZE_PATTERN is None:
        import re as _re
        _SANITIZE_PATTERN = _re.compile(
            "|".join(_SANITIZE_WORDS),
            flags=_re.IGNORECASE,
        )
    cleaned = _SANITIZE_PATTERN.sub("", name)
    # collapse extra whitespace
    import re as _re2
    cleaned = _re2.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned


# ── LLM: Generate affiliate comment ───────────────────────────────────────────

def generate_affiliate_comment(
    post_text: str,
    product: dict,
) -> str:
    """
    Generate a contextual affiliate comment that fits the original post topic.
    Soft-sell approach: start with something relevant to the post, naturally spill product.
    """
    title = _sanitize_product_name(product.get("name", "produk ini"))
    desc = product.get("description", "")
    price = product.get("price", "")
    url = product.get("affiliate_url", "")

    prompt = f"""Kamu netizen Indonesia yang mau komentar di postingan Threads.

ISI POSTINGAN YANG MAU DIKOMENTARI:
"{post_text[:500]}"

PRODUK YANG MAU DISPILL:
- Nama: {title}
- Fungsi: {desc[:200] if desc else 'N/A'}
- Harga: {price or 'cek di link'}
- Link: {url}

TUGAS:
Tulis SATU komentar netizen yang santai, ceplas-ceplos, dan to the point:
1. Awali dengan celetukan santai yang nyambung atau transisi natural dari postingan (jangan langsung jualan kaku).
2. Rekomendasikan / spill produknya dengan gaya santai kayak teman sharing promo menarik, gunakan variasi kalimat seperti:
   - "Nih ada promo {title}..."
   - "Btw buat yang nyari {title}, lagi ada promo lumayan..."
   - "Nih buat yang nyari baju/barang santai tapi adem, lagi ada promo..."
   - "Kemarin nemu promo {title} ini lumayan banget..."
3. Sertakan link di akhir (jangan pake shortener, pakai link asli): {url}
4. JANGAN alay, JANGAN spam, JANGAN template sales kaku, JANGAN pakai hashtag (#).
5. JANGAN pakai kata-kata 18+ / vulgar / eksplisit seperti: sexy, lingerie, nude, erotis, dll. Ganti dengan kata netral seperti "stylish", "keren", "trendy".
6. Maksimal 1-2 kalimat pendek dan mengalir. Output hanya teks komentar saja, tanpa penjelasan tambahan."""

    log.info(f"Generating affiliate comment for post: '{post_text[:50]}...'")
    resp = _llm([{"role": "user", "content": prompt}], temperature=0.75, max_tokens=300)
    return resp.strip()


# ── Main functions ─────────────────────────────────────────────────────────────

def check_and_comment_our_posts(
    affiliate_url: str,
    likes_threshold: int = DEFAULT_LIKES_THRESHOLD,
    replies_threshold: int = ORGANIC_REPLIES_MIN,
    views_threshold: int = DEFAULT_VIEWS_THRESHOLD,
    min_age_minutes: int = MIN_POST_AGE_MINUTES,
) -> list[dict]:
    """Qualify our recent posts under the full gate, then comment on them.

    Gate (all must hold):
      organic replies >= replies_threshold   # discussion actually alive
      post age       >= min_age_minutes      # past the initial push window
      views >= views_threshold OR likes >= likes_threshold

    `replies_threshold` counts replies from OTHER people only — our own chain
    parts do not earn the right to place a link.

    The affiliate is posted as a reply to the best organic comment (usually one
    that signals intent to buy), not to the root post, so it never interrupts the
    reading order of the thread.
    """
    results = []

    log.info(
        f"Gate: organic replies >= {replies_threshold}, age >= {min_age_minutes}m, "
        f"and views >= {views_threshold} or likes >= {likes_threshold}..."
    )

    try:
        product = extract_product_metadata(affiliate_url)
    except Exception as e:
        log.error(f"Failed to extract product from {affiliate_url}: {e}")
        return results

    try:
        posts = get_our_posts(limit=25)
    except Exception as e:
        log.error(f"Failed to fetch our posts: {e}")
        return results

    already_commented = get_commented_post_ids()
    qualifying = []
    metrics_by_post: dict[str, dict] = {}
    for post in posts:
        pid = post["id"]
        if pid in already_commented:
            log.info(f"Skipping post {pid} — already has affiliate comment recorded.")
            continue

        # Disaster posts are never a place for a link — filter before spending
        # metrics and replies calls on them.
        if any(kw in post_text_lower(post) for kw in BENCANA_KEYWORDS):
            log.info(f"Skipping bencana post {pid} — not appropriate for affiliate comment")
            continue

        metrics = get_post_metrics(pid)
        metrics_by_post[pid] = metrics
        views = metrics.get("views", 0)
        likes = metrics.get("likes", 0)
        replies_count = metrics.get("replies", 0)

        age = post_age_minutes(post)
        organic = organic_replies(pid)
        ok, reason = post_qualifies(
            views=views,
            likes=likes,
            organic_reply_count=len(organic),
            age_minutes=age,
            views_threshold=views_threshold,
            likes_threshold=likes_threshold,
            replies_min=replies_threshold,
            min_age=min_age_minutes,
        )

        age_txt = "unknown" if age is None else f"{age:.0f}m"
        if age is None:
            log.warning(f"Post {pid} has no usable timestamp — age gate skipped.")
        log.info(
            f"Post {pid}: views={views}, likes={likes}, replies={replies_count}, "
            f"organic={len(organic)}, age={age_txt} | "
            f"{'PASS' if ok else 'HOLD: ' + reason} | '{post.get('text','')[:40]}'"
        )

        if ok:
            qualifying.append({
                **post, "views": views, "likes": likes, "replies": replies_count,
                "organic_replies": organic,
            })

    if not qualifying:
        log.info("No posts passed the gate yet.")
        # Hand back the best candidate so the caller can queue it. Reuses the
        # metrics fetched above — no second round of calls.
        if metrics_by_post:
            top_id = max(metrics_by_post, key=lambda k: metrics_by_post[k].get("views", 0))
            top = next((p for p in posts if p["id"] == top_id), None)
            if top is not None:
                results.append({
                    "status": "below_threshold",
                    "post_id": top["id"],
                    "post_text": top.get("text", ""),
                    "message": "No qualifying posts. Saved to queue.",
                })
        return results

    for post in qualifying:
        pid = post["id"]
        post_text = post.get("text", "")

        comment = generate_affiliate_comment(post_text, product)
        if not comment:
            continue

        # Nested under a real comment — keeps the link out of the reading flow.
        target_id = pick_affiliate_reply_target(pid, post.get("organic_replies"))
        reply_id = post_reply_to_thread(target_id, comment)
        if reply_id:
            record_affiliate_comment(pid, reply_id, affiliate_url, product.get("name", ""))
        results.append({
            "status": "commented",
            "post_id": pid,
            "reply_to": target_id,
            "post_text": post_text[:80],
            "views": post.get("views", 0),
            "likes": post["likes"],
            "replies": post["replies"],
            "organic_replies": len(post.get("organic_replies", [])),
            "comment": comment,
            "reply_id": reply_id,
        })
        log.info(
            f"✅ Affiliate comment posted on {pid} (reply_to={target_id}, "
            f"views={post.get('views',0)}, likes={post['likes']}, reply_id={reply_id})"
        )

        time.sleep(10)  # Respectful delay between replies

    return results


def comment_on_specific_thread(
    thread_url: str,
    affiliate_url: str,
) -> dict:
    """
    Comment affiliate on a SPECIFIC Threads post URL provided by user.
    Used when user sends: '<thread_url> <affiliate_url>' in Telegram.
    """
    log.info(f"Targeting specific thread: {thread_url}")

    try:
        product = extract_product_metadata(affiliate_url)
    except Exception as e:
        log.error(f"Failed to extract product: {e}")
        return {"status": "error", "message": str(e)}

    # Try to get post text for context (best effort — may not work for others' posts)
    post_text = ""
    media_id = extract_thread_id_from_url(thread_url)
    if not media_id:
        return {"status": "error", "message": f"Cannot extract thread ID from URL: {thread_url}"}

    # Try to fetch post text (works for our own posts, may fail for others)
    try:
        r = requests.get(
            f"{BASE_URL}/{media_id}",
            params={"fields": "id,text", "access_token": ACCESS_TOKEN},
            timeout=10,
        )
        if r.ok:
            post_text = r.json().get("text", "")
    except Exception:
        pass

    comment = generate_affiliate_comment(post_text or "postingan yang sedang ramai", product)
    if not comment:
        return {"status": "error", "message": "LLM failed to generate comment"}

    reply_id = post_reply_to_thread(media_id, comment)
    return {
        "status": "commented" if reply_id else "failed",
        "target_url": thread_url,
        "media_id": media_id,
        "comment": comment,
        "reply_id": reply_id,
    }
