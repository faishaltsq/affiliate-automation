"""
Publishing Layer
- 5.1 Facebook Graph API (Page Post)
- 5.2 Threads API (graph.threads.net, correct endpoint — NOT instagram media)
"""

import requests
from src.config import (
    FACEBOOK_PAGE_ID,
    FACEBOOK_ACCESS_TOKEN,
    INSTAGRAM_BUSINESS_ID,
    INSTAGRAM_ACCESS_TOKEN,
)
from src.logger import get_logger

log = get_logger("publish")


# ── 5.1 Facebook Page Post ────────────────────────────────────────────────────

def publish_facebook(message: str, image_url: str = "", link: str = "") -> dict:
    """
    Posts to a Facebook Page via Graph API.
    Returns full API response dict (contains 'id' on success).
    """
    if not FACEBOOK_PAGE_ID or not FACEBOOK_ACCESS_TOKEN:
        log.warning("Facebook credentials not set — skipping publish")
        return {"error": "missing_credentials"}

    # Truncate to safe length and strip markdown formatting
    clean_msg = message[:2000].replace("**", "").replace("##", "").replace("# ", "")
    url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/feed"
    payload = {
        "message": clean_msg,
        "access_token": FACEBOOK_ACCESS_TOKEN,
    }
    if link:
        payload["link"] = link

    try:
        log.info(f"Publishing to Facebook Page {FACEBOOK_PAGE_ID}...")
        r = requests.post(url, data=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        log.info(f"Facebook post ID: {data.get('id')}")
        return data
    except Exception as e:
        log.error(f"Facebook publish failed: {e}")
        return {"error": str(e)}


# ── 5.2 Threads Publishing ────────────────────────────────────────────────────

def publish_threads(caption: str, image_url: str = "") -> dict:
    """
    Posts to Threads via the Threads API (graph.threads.net).
    Two-step: create media container → publish.
    Returns publish response dict.
    """
    if not INSTAGRAM_BUSINESS_ID or not INSTAGRAM_ACCESS_TOKEN:
        log.warning("Threads/Instagram credentials not set — skipping publish")
        return {"error": "missing_credentials"}

    token = INSTAGRAM_ACCESS_TOKEN
    user_id = INSTAGRAM_BUSINESS_ID

    try:
        # Step 1: Create a media container
        create_url = f"https://graph.threads.net/v1.0/{user_id}/threads"
        create_payload = {
            "text": caption,
            "access_token": token,
        }
        # ponytail: text-only post; add media_type=IMAGE + image_url when needed
        if image_url:
            create_payload["media_type"] = "IMAGE"
            create_payload["image_url"] = image_url
        else:
            create_payload["media_type"] = "TEXT"

        log.info("Creating Threads container...")
        r1 = requests.post(create_url, data=create_payload, timeout=30)
        r1.raise_for_status()
        creation_id = r1.json()["id"]

        # Step 2: Publish the container
        publish_url = f"https://graph.threads.net/v1.0/{user_id}/threads_publish"
        pub_payload = {
            "creation_id": creation_id,
            "access_token": token,
        }

        log.info(f"Publishing Threads container {creation_id}...")
        r2 = requests.post(publish_url, data=pub_payload, timeout=30)
        r2.raise_for_status()
        data = r2.json()
        log.info(f"Threads post ID: {data.get('id')}")
        return data

    except Exception as e:
        log.error(f"Threads publish failed: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    # Smoke test — will fail without real tokens
    print(publish_facebook("Test post from affiliate automation pipeline"))
    print(publish_threads("Test thread from affiliate automation pipeline"))
