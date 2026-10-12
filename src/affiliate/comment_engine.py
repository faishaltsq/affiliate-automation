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
import requests
from pathlib import Path
from dotenv import load_dotenv

from src.brief.generator import _llm
from src.scraper.product_extractor import extract_product_metadata
from src.logger import get_logger

load_dotenv(Path(__file__).parent.parent.parent / '.env')

log = get_logger("comment_engine")

USER_ID = os.getenv('THREADS_USER_ID')
ACCESS_TOKEN = os.getenv('THREADS_ACCESS_TOKEN')
BASE_URL = 'https://graph.threads.net/v1.0'


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
    """Returns likes and replies count for a given Threads post."""
    r = requests.get(
        f"{BASE_URL}/{post_id}/insights",
        params={
            "metric": "likes,replies",
            "access_token": ACCESS_TOKEN,
        },
        timeout=15,
    )
    if not r.ok:
        log.warning(f"Failed metrics for {post_id}: {r.text}")
        return {"likes": 0, "replies": 0}

    metrics = {}
    for item in r.json().get("data", []):
        name = item["name"]  # "likes" or "replies"
        value = item["values"][0]["value"] if item.get("values") else 0
        metrics[name] = value
    return metrics


def extract_thread_id_from_url(url: str) -> str | None:
    """
    Extract Threads post ID from a Threads URL.
    Format: https://www.threads.net/@username/post/<post_shortcode>
    We need to resolve the shortcode to a numeric media ID.
    """
    # Try to extract shortcode
    match = re.search(r'threads\.net/@[\w.]+/post/([\w-]+)', url)
    if not match:
        match = re.search(r'threads\.com/@[\w.]+/post/([\w-]+)', url)
    if not match:
        return None

    shortcode = match.group(1)

    # Resolve shortcode → numeric ID via Threads oEmbed or permalink lookup
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

    # Fallback: search our own posts for matching shortcode (won't work for others)
    log.warning(f"Cannot resolve thread ID from URL: {url}")
    return shortcode  # return shortcode as best effort


def post_reply_to_thread(target_post_id: str, comment_text: str) -> str | None:
    """Post a reply to any Threads post. Returns reply ID."""
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
            timeout=15,
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
            timeout=15,
        )
        r2.raise_for_status()
        reply_id = r2.json()["id"]
        log.info(f"Reply published: {reply_id}")
        return reply_id

    except Exception as e:
        log.error(f"Failed to post reply: {e}")
        return None


# ── LLM: Generate affiliate comment ───────────────────────────────────────────

def generate_affiliate_comment(
    post_text: str,
    product: dict,
) -> str:
    """
    Generate a contextual affiliate comment that fits the original post topic.
    Soft-sell approach: start with something relevant to the post, naturally spill product.
    """
    title = product.get("name", "produk ini")
    desc = product.get("description", "")
    price = product.get("price", "")
    url = product.get("affiliate_url", "")

    prompt = f"""Kamu netizen Indonesia yang mau komentar di postingan Threads.

ISI POSTINGAN YANG MAU DIKOMENTARI:
"{post_text[:500]}"

PRODUK YANG MAU DISPILL (jangan buat iklan kaku):
- Nama: {title}
- Fungsi: {desc[:200] if desc else 'N/A'}
- Harga: {price or 'cek di link'}
- Link: {url}

TUGAS:
Tulis SATU komentar yang:
1. Buka dengan sesuatu yang NYAMBUNG dengan isi postingan di atas — jangan langsung jualan
2. Transisi natural ke produk ini, kayak "oh ngomong-ngomong..." atau "nah kebetulan..." atau "btw soal ini..."
3. Spill produk santai kayak temen yang rekomendasiin barang yang emang dipake sendiri
4. Sertakan link di akhir (jangan pake shortener, pakai link asli)
5. JANGAN alay, JANGAN spam, JANGAN template jualan kaku

Contoh pola yang bagus:
- Post soal panas/gerah → komentar soal kipas mini portable yang kamu pake
- Post soal harga mahal/inflasi → komentar soal barang worth it yang murah
- Post soal lelah kerja → komentar soal yang bikin lebih ringan
- Post soal bencana/musibah → JANGAN dijadiin jualan. Skip kalau ga relevan sama sekali.

Panjang: 2-4 kalimat. Bahasa Indonesia santai. Output hanya teks komentar saja, tanpa penjelasan tambahan."""

    log.info(f"Generating affiliate comment for post: '{post_text[:50]}...'")
    resp = _llm([{"role": "user", "content": prompt}], temperature=0.75, max_tokens=300)
    return resp.strip()


# ── Main functions ─────────────────────────────────────────────────────────────

def check_and_comment_our_posts(
    affiliate_url: str,
    likes_threshold: int = 20,
    replies_threshold: int = 10,
) -> list[dict]:
    """
    Check our recent posts for those that hit engagement threshold.
    For each qualifying post, generate and post an affiliate comment.
    Returns list of results.
    """
    results = []

    log.info(f"Checking our posts for engagement ≥ {likes_threshold} likes / {replies_threshold} replies...")

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

    qualifying = []
    for post in posts:
        pid = post["id"]
        metrics = get_post_metrics(pid)
        likes = metrics.get("likes", 0)
        replies_count = metrics.get("replies", 0)

        log.info(f"Post {pid}: likes={likes}, replies={replies_count} | '{post.get('text','')[:40]}'")

        if likes >= likes_threshold or replies_count >= replies_threshold:
            qualifying.append({**post, "likes": likes, "replies": replies_count})

    if not qualifying:
        log.info("No posts hit engagement threshold yet.")
        # Return the top performer so caller can queue it
        if posts:
            top = max(posts, key=lambda p: (
                get_post_metrics(p["id"]).get("likes", 0)
            ))
            results.append({
                "status": "below_threshold",
                "post_id": top["id"],
                "post_text": top.get("text", ""),
                "message": "No qualifying posts. Saved to queue."
            })
        return results

    for post in qualifying:
        pid = post["id"]
        post_text = post.get("text", "")

        # Don't comment on disaster/bencana posts (irrelevant & insensitive)
        bencana_keywords = ["banjir", "gempa", "kebakaran", "longsor", "tsunami", "korban", "meninggal", "duka"]
        if any(kw in post_text.lower() for kw in bencana_keywords):
            log.info(f"Skipping bencana post {pid} — not appropriate for affiliate comment")
            continue

        comment = generate_affiliate_comment(post_text, product)
        if not comment:
            continue

        reply_id = post_reply_to_thread(pid, comment)
        results.append({
            "status": "commented",
            "post_id": pid,
            "post_text": post_text[:80],
            "likes": post["likes"],
            "replies": post["replies"],
            "comment": comment,
            "reply_id": reply_id,
        })
        log.info(f"✅ Affiliate comment posted on {pid} (reply_id={reply_id})")

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
