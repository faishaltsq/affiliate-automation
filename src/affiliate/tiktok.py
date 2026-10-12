"""
TikTok Shop Affiliate Layer
- search_tiktok_product() — scrape TikTok Shop search for product info
- build_affiliate_url()  — generate trackable deep link
- insert_affiliate()     — replace {{placeholders}} in brief with product data

Two modes:
  1. API mode (if TIKTOK_ACCESS_TOKEN set): official TikTok Shop Open API
  2. Deep link mode (default): construct trackable URL from keyword
     Format: https://tokopedia.link/product?af_id={TIKTOK_AFFILIATE_ID}
     Or TikTok: https://www.tiktok.com/search?q={keyword} (discovery link)
"""

import re
import urllib.parse
import requests

from src.config import (
    TIKTOK_AFFILIATE_ID,
    TIKTOK_APP_ID,
    TIKTOK_APP_SECRET,
    TIKTOK_ACCESS_TOKEN,
    SHOPEE_AFFILIATE_CODE,
    SHOPEE_CLIENT_ID,
    SHOPEE_CLIENT_SECRET,
)
from src.logger import get_logger

log = get_logger("affiliate")

# ── URL builders ──────────────────────────────────────────────────────────────

def build_tiktok_search_url(keyword: str) -> str:
    """Build TikTok Shop search deep link — user discovers products via TikTok."""
    q = urllib.parse.quote_plus(keyword)
    return f"https://www.tiktok.com/search?q={q}"


def build_tokopedia_affiliate_url(keyword: str) -> str:
    """Build Tokopedia search affiliate URL with tracking."""
    q = urllib.parse.quote_plus(keyword)
    base = f"https://www.tokopedia.com/search?q={q}"
    if TIKTOK_AFFILIATE_ID:
        base += f"&af_id={TIKTOK_AFFILIATE_ID}"
    return base


# ── Product search (best-effort, no API key needed) ───────────────────────────

def search_product(keyword: str, limit: int = 1) -> dict | None:
    """
    Tries to find a real product name and price via Tokopedia search.
    Returns {name, price, image, affiliate_url} or None.
    Falls back to constructing a generic affiliate URL.
    """
    log.info(f"Searching product: '{keyword}'")

    # Try Tokopedia search scrape (public, no auth)
    try:
        product = _scrape_tokopedia(keyword)
        if product:
            return product
    except Exception as e:
        log.warning(f"Tokopedia scrape failed: {e}")

    # Fallback: construct affiliate search URL
    log.info(f"Using generic affiliate search link for '{keyword}'")
    return {
        "name": _titlecase(keyword),
        "price": 0,
        "image": "",
        "affiliate_url": build_tiktok_search_url(keyword),
    }


def _scrape_tokopedia(keyword: str) -> dict | None:
    """
    Scrape Tokopedia search results (public API, no auth needed).
    Returns first product with name, price, image, and affiliate URL.
    """
    url = "https://gql.tokopedia.com/graphql/SearchProductQueryV4"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Origin": "https://www.tokopedia.com",
        "Referer": "https://www.tokopedia.com",
        "x-source": "tokopedia-lite",
        "x-device": "desktop",
        "x-tkpd-lite-service": "zeus",
    }

    payload = [{
        "operationName": "SearchProductQueryV4",
        "variables": {
            "params": f"device=desktop&navsource=home&ob=23&page=1&q={keyword}&rows=1&scheme=https&source=search&st=product&unique_id=abc123"
        },
        "query": """query SearchProductQueryV4($params: String!) {
            ace_search_product_v4(params: $params) {
                data {
                    products {
                        id
                        name
                        price {
                            text
                            number
                        }
                        imageUrl
                        url
                        shop {
                            name
                            city
                        }
                    }
                }
            }
        }"""
    }]

    r = requests.post(url, json=payload, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    products = (
        data[0]
        .get("data", {})
        .get("ace_search_product_v4", {})
        .get("data", {})
        .get("products", [])
    )

    if not products:
        log.warning(f"No Tokopedia products for '{keyword}'")
        return None

    p = products[0]
    price_num = p.get("price", {}).get("number", 0)
    product_url = p.get("url", "")

    # Append affiliate tracking if available
    if TIKTOK_AFFILIATE_ID and product_url:
        sep = "&" if "?" in product_url else "?"
        product_url += f"{sep}af_id={TIKTOK_AFFILIATE_ID}"

    result = {
        "name": p.get("name", keyword),
        "price": price_num,
        "image": p.get("imageUrl", ""),
        "affiliate_url": product_url or build_tiktok_search_url(keyword),
    }
    log.info(f"Product found: '{result['name']}' (Rp{result['price']:,.0f})")
    return result


def _titlecase(s: str) -> str:
    """Convert keyword to title case for display."""
    return " ".join(w.capitalize() for w in s.split())


# ── Brief injection ───────────────────────────────────────────────────────────

def insert_affiliate(brief: str, product: dict) -> str:
    """Replaces {{placeholders}} in the Markdown brief with real product data."""
    price = product.get("price", 0)
    if price and price > 0:
        price_str = f"Rp{price:,.0f}"
    else:
        price_str = "Harga Terbaik"

    brief = brief.replace("{{product_name}}", product.get("name", "Produk Rekomendasi"))
    brief = brief.replace("{{price}}", price_str)
    brief = brief.replace("{{affiliate_url}}", product.get("affiliate_url", "#"))
    return brief


if __name__ == "__main__":
    # Smoke test
    print("=== Search test ===")
    result = search_product("mechanical keyboard")
    if result:
        print(f"Name:  {result['name']}")
        print(f"Price: Rp{result['price']:,.0f}")
        print(f"URL:   {result['affiliate_url']}")

    print("\n=== Injection test ===")
    sample = "Buy {{product_name}} for {{price}} here: {{affiliate_url}}"
    mock = {"name": "Keychron K2", "price": 899000, "affiliate_url": "https://tokopedia.com/x?af_id=test"}
    print(insert_affiliate(sample, mock))
