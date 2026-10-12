"""
Affiliate Product Layer (Shopee Open API v2)
- 4.1 Token retrieval (handles HMAC-SHA256 signature required by v2)
- 4.2 Product search + affiliate deep-link builder
- 4.3 Brief injection (replaces {{placeholders}})
"""

import hmac
import time
import hashlib
import requests

from src.config import (
    SHOPEE_CLIENT_ID,
    SHOPEE_CLIENT_SECRET,
    SHOPEE_AFFILIATE_CODE,
)
from src.logger import get_logger

log = get_logger("affiliate")

SHOPEE_BASE = "https://partner.shopeemobile.com"


# ── Signature helper ─────────────────────────────────────────────────────────

def _sign(path: str, partner_id: int, timestamp: int, access_token: str = "") -> str:
    """
    Shopee v2 HMAC-SHA256 signature formula:
      sign = hmac_sha256(partner_key, partner_id + path + timestamp + access_token)
    """
    base = f"{partner_id}{path}{timestamp}{access_token}"
    return hmac.new(
        SHOPEE_CLIENT_SECRET.encode(),
        base.encode(),
        hashlib.sha256,
    ).hexdigest()


# ── 4.1 Token Retrieval ───────────────────────────────────────────────────────

def get_shopee_token() -> str:
    """
    Fetches an access token via Shopee Open API v2 OAuth.
    Returns access_token string.
    """
    path = "/api/v2/auth/token/get"
    timestamp = int(time.time())
    partner_id = int(SHOPEE_CLIENT_ID)
    sign = _sign(path, partner_id, timestamp)

    url = f"{SHOPEE_BASE}{path}?partner_id={partner_id}&timestamp={timestamp}&sign={sign}"
    payload = {
        "code": "YOUR_AUTH_CODE",          # exchanged during one-time shop authorization
        "partner_id": partner_id,
        "shop_id": 0,                      # 0 for affiliate / open account
    }

    log.info("Requesting Shopee access token...")
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("error"):
        raise RuntimeError(f"Shopee auth error: {data.get('message')}")

    token = data.get("access_token", "")
    log.info("Shopee token acquired")
    return token


# ── 4.2 Product Search ────────────────────────────────────────────────────────

def search_shopee(keyword: str, token: str = "", limit: int = 1) -> dict | None:
    """
    Searches items by keyword.
    Returns dict: {name, price, image, affiliate_url} or None if not found.
    """
    path = "/api/v2/product/search_item"
    timestamp = int(time.time())
    partner_id = int(SHOPEE_CLIENT_ID) if SHOPEE_CLIENT_ID else 0
    sign = _sign(path, partner_id, timestamp, token)

    url = f"{SHOPEE_BASE}{path}?partner_id={partner_id}&timestamp={timestamp}&access_token={token}&sign={sign}"
    params = {"keyword": keyword, "page_size": limit}

    log.info(f"Searching Shopee: '{keyword}'")
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        items = r.json().get("response", {}).get("item_list", [])

        if not items:
            log.warning(f"No items found for '{keyword}'")
            return None

        item = items[0]
        # Shopee price is returned in currency cents / smallest unit
        price = float(item.get("price_info", [{}])[0].get("current_price", 0)) / 100

        # Build trackable affiliate link
        item_id = item.get("item_id")
        affiliate_url = f"https://shopee.sg/item/{item_id}/?af_code={SHOPEE_AFFILIATE_CODE}"

        product = {
            "name": item.get("item_name", keyword),
            "price": price,
            "image": item.get("image", {}).get("image_url_list", [""])[0],
            "affiliate_url": affiliate_url,
        }
        log.info(f"Shopee product found: '{product['name']}' (${product['price']:.2f})")
        return product

    except Exception as e:
        log.error(f"Shopee search failed: {e}")
        return None


# ── 4.3 Brief Injection ───────────────────────────────────────────────────────

def insert_affiliate(brief: str, product: dict) -> str:
    """Replaces placeholders in the Markdown brief with real product data."""
    price_str = f"${product['price']:.2f}" if product.get("price") else "Best Price"
    brief = brief.replace("{{product_name}}", product.get("name", "Recommended Product"))
    brief = brief.replace("{{price}}", price_str)
    brief = brief.replace("{{affiliate_url}}", product.get("affiliate_url", "#"))
    return brief


if __name__ == "__main__":
    # Smoke test placeholder replacement
    sample = "Buy {{product_name}} for {{price}} here: {{affiliate_url}}"
    mock_prod = {
        "name": "Samsung T7 Shield 1TB",
        "price": 89.99,
        "affiliate_url": "https://shopee.sg/item/123/?af_code=MYCODE",
    }
    print(insert_affiliate(sample, mock_prod))
