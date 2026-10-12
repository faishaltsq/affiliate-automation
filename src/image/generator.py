"""
Image Selection Layer
- Priority 1: Curated real product photography (verified Unsplash URLs, no AI)
- Priority 2: Pollinations.ai AI generation fallback (niche/unlisted products)
- Priority 3: Generic tech default
- Optional: Cloudinary upload if configured
"""

import sqlite3
import urllib.parse
import requests
from datetime import date

from src.config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_API_KEY,
    CLOUDINARY_API_SECRET,
    DATA_DIR,
)
from src.logger import get_logger
from src.guardrails import (
    get_cached_image,
    save_image_cache,
    log_failure,
)

log = get_logger("image")

GUARDRAILS_DB = DATA_DIR / "guardrails.db"
MAX_STORAGE_BYTES = 20 * 1024 * 1024 * 1024

REAL_PRODUCT_PHOTOS = {
    "gaming mouse":        "https://images.unsplash.com/photo-1563297007-0686b7003af7?w=1200&h=630&fit=crop&q=80",
    "mouse":               "https://images.unsplash.com/photo-1527814050087-3793815479db?w=1200&h=630&fit=crop&q=80",
    "mechanical keyboard": "https://images.unsplash.com/photo-1595225476474-87563907a212?w=1200&h=630&fit=crop&q=80",
    "keyboard":            "https://images.unsplash.com/photo-1618384887929-16ec33fab9ef?w=1200&h=630&fit=crop&q=80",
    "webcam":              "https://images.unsplash.com/photo-1587826080692-f439cd0b70da?w=1200&h=630&fit=crop&q=80",
    "monitor":             "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=1200&h=630&fit=crop&q=80",
    "desk setup":          "https://images.unsplash.com/photo-1593062096033-9a26b09da705?w=1200&h=630&fit=crop&q=80",
    "wireless earbuds":    "https://images.unsplash.com/photo-1606220588913-b3aacb4d2f46?w=1200&h=630&fit=crop&q=80",
    "earbuds":             "https://images.unsplash.com/photo-1606220588913-b3aacb4d2f46?w=1200&h=630&fit=crop&q=80",
    "headphones":          "https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=1200&h=630&fit=crop&q=80",
    "speaker":             "https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=1200&h=630&fit=crop&q=80",
    "portable ssd":        "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?w=1200&h=630&fit=crop&q=80",
    "ssd":                 "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?w=1200&h=630&fit=crop&q=80",
    "portable charger":    "https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?w=1200&h=630&fit=crop&q=80",
    "power bank":          "https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?w=1200&h=630&fit=crop&q=80",
    "charger":             "https://images.unsplash.com/photo-1609091839311-d5365f9ff1c5?w=1200&h=630&fit=crop&q=80",
    "smart watch":         "https://images.unsplash.com/photo-1579586337278-3befd40fd17a?w=1200&h=630&fit=crop&q=80",
    "smartwatch":          "https://images.unsplash.com/photo-1579586337278-3befd40fd17a?w=1200&h=630&fit=crop&q=80",
    "laptop":              "https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=1200&h=630&fit=crop&q=80",
    "tablet":              "https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=1200&h=630&fit=crop&q=80",
    "phone case":          "https://images.unsplash.com/photo-1541877944-ac82a091518a?w=1200&h=630&fit=crop&q=80",
    "camera":              "https://images.unsplash.com/photo-1516035069371-29a1b244cc32?w=1200&h=630&fit=crop&q=80",
    "gaming":              "https://images.unsplash.com/photo-1542751371-adc38448a05e?w=1200&h=630&fit=crop&q=80",
    "tech":                "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&h=630&fit=crop&q=80",
    "gadget":              "https://images.unsplash.com/photo-1519389950473-47ba0277781c?w=1200&h=630&fit=crop&q=80",
    "default":             "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200&h=630&fit=crop&q=80",
}

def find_real_product_photo(keyword: str):
    kw = keyword.lower().strip()
    if kw in REAL_PRODUCT_PHOTOS:
        return REAL_PRODUCT_PHOTOS[kw]
    for key, url in REAL_PRODUCT_PHOTOS.items():
        if key in kw or kw in key:
            return url
    tokens = set(kw.split())
    for key, url in REAL_PRODUCT_PHOTOS.items():
        if tokens & set(key.split()):
            return url
    return None

def _get_and_increment_cloudinary_bytes(size_bytes: int) -> int:
    today = date.today().isoformat()
    con = sqlite3.connect(GUARDRAILS_DB)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO daily_counters (date, cloudinary_bytes) VALUES (?, 0)", (today,))
    cur.execute("SELECT cloudinary_bytes FROM daily_counters WHERE date = ?", (today,))
    current = cur.fetchone()[0]
    if current + size_bytes > MAX_STORAGE_BYTES:
        con.close()
        raise RuntimeError("Cloudinary storage cap reached")
    new_val = current + size_bytes
    cur.execute("UPDATE daily_counters SET cloudinary_bytes = ? WHERE date = ?", (new_val, today))
    con.commit()
    con.close()
    return new_val

def upload_to_cloudinary(image_url: str, folder: str = "affiliate_images") -> str:
    import cloudinary, cloudinary.uploader
    cloudinary.config(cloud_name=CLOUDINARY_CLOUD_NAME, api_key=CLOUDINARY_API_KEY, api_secret=CLOUDINARY_API_SECRET)
    r = requests.get(image_url, stream=True, timeout=30)
    r.raise_for_status()
    content_len = int(r.headers.get("content-length", 150_000))
    _get_and_increment_cloudinary_bytes(content_len)
    result = cloudinary.uploader.upload(r.raw, folder=folder)
    log.info(f"Cloudinary URL: {result['secure_url']}")
    return result["secure_url"]

def generate_and_host_image(prompt: str, fallback_keyword: str = "gadget") -> str:
    cached = get_cached_image(fallback_keyword)
    if cached:
        log.info(f"Cache hit for '{fallback_keyword}'")
        return cached

    real_url = find_real_product_photo(fallback_keyword)
    if real_url:
        log.info(f"Real product photo matched: '{fallback_keyword}'")
        save_image_cache(fallback_keyword, real_url)
        return real_url

    try:
        encoded = urllib.parse.quote(prompt)
        ai_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1200&height=630&nologo=true"
        log.info(f"No real photo for '{fallback_keyword}' — Pollinations fallback")
        if CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY:
            try:
                ai_url = upload_to_cloudinary(ai_url)
            except Exception as e:
                log.warning(f"Cloudinary failed: {e}")
        save_image_cache(fallback_keyword, ai_url)
        return ai_url
    except Exception as e:
        log.warning(f"All image sources failed: {e}")
        log_failure(fallback_keyword, "image_gen_failed", str(e))
        default = REAL_PRODUCT_PHOTOS["default"]
        save_image_cache(fallback_keyword, default)
        return default
