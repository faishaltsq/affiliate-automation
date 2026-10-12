"""
Organic Post Publisher — orchestrates one full organic post cycle:
1. Plan topic + pillar (via planner)
2. Generate Rara Kinanti copy + Pinterest vibe query (via generator)
3. Post to Threads (image + caption)
4. Send Telegram notification to Topic 9
5. Log to SQLite
"""

import time
from src.organic.planner import plan_single_post
from src.organic.generator import generate_organic_post
from src.organic.db import init_organic_db, log_organic_post, get_today_post_count
from src.publish.threads import post_threads
from src.publish.telegram import send_image_to_telegram
from src.logger import get_logger

log = get_logger("organic_publisher")

DAILY_MAX = 12
TELEGRAM_TOPIC_9 = 9  # 📦 Affiliate Content topic in supergroup


def execute_organic_post(used_today: list[str] = None, dry_run: bool = False) -> dict:
    """
    Executes one complete organic post cycle.
    dry_run=True: generates content but does NOT post to Threads.
    Returns result dict with content, image, threads_id (or dry_run flag).
    """
    init_organic_db()

    today_count = get_today_post_count()
    if today_count >= DAILY_MAX:
        log.info(f"Daily limit reached ({today_count}/{DAILY_MAX}). Skipping.")
        return {"skipped": True, "reason": "daily_limit", "today_count": today_count}

    # 1. Plan
    plan = plan_single_post(used_today=used_today or [])
    topic = plan["topic"]
    pillar = plan["pillar"]
    source = plan["source"]

    # 2. Generate copy + image
    post_data = generate_organic_post(topic=topic, pillar=pillar)
    content = post_data["content"]
    image_url = post_data["image_url"]

    log.info(f"Generated organic post for '{topic[:40]}' | pillar={pillar}")
    log.info(f"Content: {content[:100]}...")
    log.info(f"Image: {image_url}")

    if dry_run:
        log.info("[DRY RUN] Skipping Threads + Telegram dispatch.")
        return {
            "dry_run": True,
            "topic": topic,
            "pillar": pillar,
            "source": source,
            "content": content,
            "image_url": image_url,
        }

    # 3. Post to Threads (single post, no affiliate reply)
    threads_result = {}
    threads_id = ""
    try:
        threads_result = post_threads(
            hook_text=content,
            reply_text=None,
            vibe_image_url=image_url or None,
            product_image_url=None,
        )
        threads_id = threads_result.get("post_id", "")
        
        # If posting failed (e.g. image rejected by Meta), retry without image
        if not threads_id and image_url:
            err_msg = threads_result.get("error", "")
            log.warning(f"Threads post failed with image (error: {err_msg[:100]}). Retrying without image...")
            threads_result = post_threads(
                hook_text=content,
                reply_text=None,
                vibe_image_url=None,
                product_image_url=None,
            )
            threads_id = threads_result.get("post_id", "")
            image_url = None  # clear so Telegram sends text
            
        if threads_id:
            log.info(f"Threads post published: {threads_id}")
        else:
            log.error(f"Threads publish failed completely: {threads_result.get('error')}")
    except Exception as e:
        log.error(f"Threads publish unexpected exception: {e}")
        threads_id = ""

    # 4. Telegram notification to Topic 9
    try:
        pillar_emoji = {
            "breaking_trend": "🔥",
            "relatable_struggle": "😭",
            "hot_take_debate": "🌶️",
            "pop_culture_meme": "🎵",
            "menfess_qna": "💬",
            "lifestyle_dump": "✨",
        }.get(pillar, "📝")

        caption = (
            f"{pillar_emoji} <b>[ORGANIC — {pillar.replace('_', ' ').upper()}]</b>\n\n"
            f"<i>{content}</i>"
        )

        if image_url:
            send_image_to_telegram(image_url, caption=caption)
        else:
            from src.publish.telegram import send_text_to_telegram
            send_text_to_telegram(caption)
    except Exception as e:
        log.warning(f"Telegram notification failed (non-fatal): {e}")

    # 5. Log to SQLite
    if threads_id:
        log_organic_post(
            source=source,
            pillar=pillar,
            topic=topic,
            content=content,
            image_url=image_url,
            threads_id=threads_id,
        )

    return {
        "topic": topic,
        "pillar": pillar,
        "content": content,
        "image_url": image_url,
        "threads_id": threads_id,
    }


def execute_organic_post_custom(custom_topic: str, pillar: str = "breaking_trend") -> dict:
    """Post an organic post with a user-supplied topic."""
    init_organic_db()

    post_data = generate_organic_post(topic=custom_topic, pillar=pillar)
    content = post_data["content"]
    image_url = post_data["image_url"]

    threads_result = post_threads(
        hook_text=content,
        reply_text=None,
        vibe_image_url=image_url or None,
        product_image_url=None,
    )
    threads_id = threads_result.get("post_id", "")

    if not threads_id:
        log.warning(f"Custom post Threads initial attempt failed ({threads_result.get('error')}). Retrying...")
        time.sleep(3)
        threads_result = post_threads(
            hook_text=content,
            reply_text=None,
            vibe_image_url=image_url or None,
            product_image_url=None,
        )
        threads_id = threads_result.get("post_id", "")
        if not threads_id and image_url:
            log.warning("Retrying custom post without image...")
            threads_result = post_threads(
                hook_text=content,
                reply_text=None,
                vibe_image_url=None,
                product_image_url=None,
            )
            threads_id = threads_result.get("post_id", "")
            image_url = None

    # Telegram notification
    try:
        caption = f"🔥 <b>[CUSTOM TOPIC — {custom_topic}]</b>\n\n<i>{content}</i>"
        if image_url:
            send_image_to_telegram(image_url, caption=caption)
        else:
            from src.publish.telegram import send_text_to_telegram
            send_text_to_telegram(caption)
    except Exception as e:
        log.warning(f"Telegram notification failed: {e}")

    if threads_id:
        log_organic_post(
            source="telegram_command",
            pillar=pillar,
            topic=custom_topic,
            content=content,
            image_url=image_url,
            threads_id=threads_id,
        )

    return {
        "topic": custom_topic,
        "pillar": pillar,
        "content": content,
        "image_url": image_url,
        "threads_id": threads_id,
    }


if __name__ == "__main__":
    result = execute_organic_post(dry_run=True)
    print("\n=== DRY RUN RESULT ===")
    for k, v in result.items():
        print(f"  {k}: {str(v)[:100]}")
