"""
End-to-End Pipeline Worker (Section 8)
Reads ranked_topics.json and runs the full pipeline per topic:
  1. generate_brief → compliance loop
  2. search_shopee → insert affiliate data
  3. generate_image → upload to Cloudinary
  4. publish to Facebook + Threads
  5. log post IDs to data/posts.db (SQLite)
"""

import json
import sqlite3
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from src.config import DATA_DIR
from src.logger import get_logger
from src.brief.generator import run_brief_pipeline
from src.brief.social_copy import generate_social_bundle
from src.scraper.product_extractor import extract_product_metadata
from src.image.generator import generate_and_host_image, find_real_product_photo
from src.affiliate.tiktok import search_product, insert_affiliate
from src.publish.social import publish_facebook
from src.publish.threads import post_threads
from src.publish.telegram import send_brief_to_telegram, send_image_to_telegram
from src.guardrails import log_failure, retry_with_backoff
from src.config import TIKTOK_AFFILIATE_ID

log = get_logger("worker")

DB_PATH = DATA_DIR / "posts.db"


# ── DB setup ──────────────────────────────────────────────────────────────────

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT,
            keyword     TEXT,
            fb_post_id  TEXT,
            threads_id  TEXT,
            cloudinary_url TEXT,
            created_at  TEXT
        )
    """)
    con.commit()
    con.close()


def log_post(topic: str, keyword: str, fb_id: str, threads_id: str, img_url: str):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO posts (topic, keyword, fb_post_id, threads_id, cloudinary_url, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (topic, keyword, fb_id, threads_id, img_url, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
    )
    con.commit()
    con.close()


# ── Per-topic pipeline ────────────────────────────────────────────────────────

def process_topic(topic: dict, _token: str = "") -> bool:
    """
    Runs the full pipeline for a single ranked topic.
    Returns True on success, False on failure.
    """
    label = topic["topic"]
    keyword = label

    log.info(f"━━ Processing: {label} ━━")

    try:
        # Step 1: Product search (Tokopedia / TikTok Shop)
        product = search_product(keyword)
        if not product:
            product = search_product(f"best {keyword}")

        if not product:
            log.warning(f"No product found for '{keyword}' — using generic link")
            product = {
                "name": keyword.title(),
                "price": 0,
                "image": "",
                "affiliate_url": f"https://www.tiktok.com/search?q={urllib.parse.quote_plus(keyword)}",
            }

        if not TIKTOK_AFFILIATE_ID:
            log_failure(label, "missing_af_id", "TIKTOK_AFFILIATE_ID not set in .env")

        # Step 2: Brief generation + compliance loop
        brief = run_brief_pipeline(label, keyword)
        brief = insert_affiliate(brief, product)

        # Step 3: Image (real photo or AI fallback)
        img_prompt = (
            f"A clean, modern hero image for a '{label}' product review. "
            "Minimal background, professional product photography style, "
            "space for text overlay at the top."
        )
        cloudinary_url = generate_and_host_image(img_prompt, fallback_keyword=label)

        # Step 4a: Send to Telegram thread
        send_brief_to_telegram(
            topic_name=label,
            brief=brief,
            product=product,
            image_url=cloudinary_url,
        )
        if cloudinary_url:
            send_image_to_telegram(cloudinary_url, caption=f"🖼 Hero image for: <b>{label}</b>")

        # Step 4b: Publish to Facebook + Threads (skipped if no credentials)
        fb_message = brief[:500] + "\n\n" + product.get("affiliate_url", "")
        fb_resp = publish_facebook(fb_message, image_url=cloudinary_url)
        fb_post_id = fb_resp.get("id", "")

        threads_caption = brief[:280].split("\n")[0]
        th_resp = publish_threads(threads_caption, image_url=cloudinary_url)
        threads_id = th_resp.get("id", "")

        # Step 5: Persist
        log_post(label, keyword, fb_post_id, threads_id, cloudinary_url)
        log.info(f"✓ Done: fb_id={fb_post_id}, threads_id={threads_id}")
        return True

    except Exception as e:
        log.error(f"✗ Failed '{label}': {e}\n{traceback.format_exc()}")
        log_failure(label, "pipeline_exception", str(e))
        return False


# ── Direct Affiliate Link Pipeline ───────────────────────────────────────────

def process_affiliate_link(url: str, trend_hint: str = "") -> bool:
    """
    Executes the Link-to-Content pipeline:
    1. Scrapes real product name, image, description from affiliate URL.
    2. Generates multi-platform copy (Telegram, Facebook, Threads, Video Script).
    3. Dispatches real image + copy to Telegram Thread 9.
    4. Publishes to Facebook Page.
    5. Saves to SQLite.
    """
    init_db()
    log.info(f"━━ Processing Affiliate Link: {url} (trend='{trend_hint}') ━━")

    try:
        # Step 1: Extract Metadata & Real Image
        product = extract_product_metadata(url)
        title = product.get("name", "Featured Product")
        image_url = product.get("image_url", "")
        if not image_url:
            fallback_img = find_real_product_photo(title)
            if fallback_img:
                log.info(f"Using fallback curated photo for '{title}'")
                image_url = fallback_img
                product["image_url"] = image_url

        # Step 2: Generate High-Converting Social Bundle
        bundle = generate_social_bundle(product, trend_hint=trend_hint)

        # Step 3: Telegram Dispatch (2-Card Visual Package)
        vibe_img = bundle.get("vibe_image_url", "")
        prod_img = bundle.get("product_image_url") or image_url
        main_cap = bundle.get("telegram_main_caption", "")
        reply_cap = bundle.get("telegram_reply_caption", "")

        if vibe_img:
            # 3.1 Send Vibe / Context Image for Main Post
            log.info("Sending Post Utama (Vibe Photo) to Telegram...")
            send_image_to_telegram(vibe_img, caption=main_cap)

        if prod_img:
            # 3.2 Send Real Product Image for First Reply / Spill
            log.info("Sending Komen Pertama (Product Photo) to Telegram...")
            send_image_to_telegram(prod_img, caption=reply_cap)
        elif not vibe_img:
            # Fallback text if no images
            send_brief_to_telegram(
                topic_name=title,
                brief=bundle.get("telegram_caption", ""),
                product=product,
                image_url="",
            )

        # Step 4: Facebook Publish
        fb_message = bundle.get("facebook_post", "")
        fb_resp = publish_facebook(fb_message, image_url=image_url, link=url)
        fb_post_id = fb_resp.get("id", "")

        # Step 5: Threads Publish (2-post: hook + reply with link)
        threads_hook = bundle.get("threads_main", "")
        threads_reply = bundle.get("threads_reply", "")
        th_resp = post_threads(
            hook_text=threads_hook,
            reply_text=threads_reply,
            vibe_image_url=vibe_img or None,
            product_image_url=prod_img or None,
        )
        threads_id = th_resp.get("post_id", "")

        # Step 6: Log
        log_post(title, "link_pipeline", fb_post_id, threads_id, image_url)
        log.info(f"✓ Link pipeline finished: title='{title}', fb_id={fb_post_id}, img={bool(image_url)}")
        return True

    except Exception as e:
        log.error(f"✗ Failed affiliate link '{url}': {e}\n{traceback.format_exc()}")
        return False


# ── Main worker ───────────────────────────────────────────────────────────────

def run_worker():
    init_db()

    ranked_file = DATA_DIR / "ranked_topics.json"
    if not ranked_file.exists():
        log.error(f"ranked_topics.json not found at {ranked_file}. Run trend_job first.")
        return

    topics = json.loads(ranked_file.read_text(encoding="utf-8"))
    log.info(f"Processing {len(topics)} topics...")

    # Process topics
    ok = 0
    for topic in topics:
        # Edge Case 10: Retry with exponential backoff on transient failures
        success = False
        delay = 2.0
        for attempt in range(3):
            success = process_topic(topic)
            if success:
                break
            if attempt < 2:
                log.warning(f"Retrying topic '{topic['topic']}' in {delay}s (attempt {attempt+2}/3)")
                time.sleep(delay)
                delay *= 2
        if success:
            ok += 1
        time.sleep(5)  # respectful delay between topics

    log.info(f"Worker complete: {ok}/{len(topics)} topics published")


if __name__ == "__main__":
    run_worker()
