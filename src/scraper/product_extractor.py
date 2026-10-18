import re
import json
import requests
import lxml.html
from urllib.parse import urlparse, parse_qs, unquote
from src.logger import get_logger

log = get_logger("scraper")

SOCIAL_USER_AGENT = (
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.html)"
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def extract_product_from_html(html_text: str, original_url: str) -> dict:
    """Extracts product details from raw HTML using lxml."""
    tree = lxml.html.fromstring(html_text)

    # 1. Title
    title = ""
    og_title = tree.xpath('//meta[@property="og:title"]/@content')
    tw_title = tree.xpath('//meta[@name="twitter:title"]/@content')
    doc_title = tree.xpath('//title/text()')

    if og_title and og_title[0].strip():
        title = og_title[0].strip()
    elif tw_title and tw_title[0].strip():
        title = tw_title[0].strip()
    elif doc_title and doc_title[0].strip():
        title = doc_title[0].strip()
    else:
        title = "Featured Product"

    # Clean title junk
    title = re.sub(r"\s*\|\s*(TikTok Shop|Shopee Indonesia|Tokopedia).*$", "", title, flags=re.I)

    # 2. Image URL
    image_url = ""
    og_image = tree.xpath('//meta[@property="og:image"]/@content')
    tw_image = tree.xpath('//meta[@name="twitter:image"]/@content')
    if og_image and og_image[0].strip():
        image_url = og_image[0].strip()
    elif tw_image and tw_image[0].strip():
        image_url = tw_image[0].strip()

    # 3. Description
    desc = ""
    og_desc = tree.xpath('//meta[@property="og:description"]/@content')
    meta_desc = tree.xpath('//meta[@name="description"]/@content')
    if og_desc and og_desc[0].strip():
        desc = og_desc[0].strip()
    elif meta_desc and meta_desc[0].strip():
        desc = meta_desc[0].strip()

    # 4. Price (Optional extraction)
    price_str = ""
    og_price = tree.xpath('//meta[@property="product:price:amount"]/@content')
    if og_price and og_price[0].strip():
        try:
            price_val = int(float(og_price[0]))
            price_str = f"Rp {price_val:,}".replace(",", ".")
        except Exception:
            price_str = str(og_price[0])
    else:
        # Regex search in description/title for Rp...
        m = re.search(r"Rp\s?[\d.,]+", f"{title} {desc}")
        if m:
            price_str = m.group(0)

    return {
        "name": title,
        "image_url": image_url,
        "description": desc,
        "price": price_str,
        "affiliate_url": original_url,
    }


def extract_product_metadata(url: str, timeout: int = 15) -> dict:
    """
    Follows redirects and extracts metadata from an affiliate URL.
    Attempts social crawler User-Agent first, falls back to browser UA.
    """
    log.info(f"Extracting metadata from URL: {url}")
    session = requests.Session()

    headers = {
        "User-Agent": SOCIAL_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    try:
        resp = session.get(url, headers=headers, allow_redirects=True, timeout=timeout)
        final_url = resp.url
        log.info(f"Resolved final URL: {final_url} (HTTP {resp.status_code})")

        # If bot agent was blocked (403/401), retry with browser UA
        if resp.status_code in (401, 403) or len(resp.text) < 500:
            log.warning("Bot UA received minimal response, retrying with standard browser UA")
            headers["User-Agent"] = BROWSER_USER_AGENT
            resp = session.get(url, headers=headers, allow_redirects=True, timeout=timeout)
            final_url = resp.url

        data = extract_product_from_html(resp.text, url)
        data["final_url"] = final_url

        # Check if final_url contains og_info (common in TikTok Shop / Tokopedia Shop share links)
        try:
            parsed_final = urlparse(final_url)
            qs = parse_qs(parsed_final.query)
            if "og_info" in qs:
                og_raw = qs["og_info"][0]
                og_json = json.loads(og_raw)
                if og_json.get("title") and (data.get("name") in ("TikTok Shop", "Tokopedia", "Shopping On TikTok Shop", "") or not data.get("name")):
                    data["name"] = og_json["title"].replace("+", " ")
                if og_json.get("image") and (not data.get("image_url") or "tts-logo" in data.get("image_url", "")):
                    data["image_url"] = og_json["image"]
        except Exception as e:
            log.debug(f"Failed parsing og_info from final_url: {e}")

        log.info(f"Extraction successful: '{data['name']}', Image: {bool(data['image_url'])}")
        return data

    except Exception as e:
        log.error(f"Failed to fetch affiliate link: {e}")
        # Fallback dictionary
        parsed = urlparse(url)
        path_name = parsed.path.strip("/").split("/")[-1] or "Affiliate Product"
        return {
            "name": path_name.replace("-", " ").title(),
            "image_url": "",
            "description": "",
            "price": "",
            "affiliate_url": url,
            "final_url": url,
            "error": str(e),
        }
