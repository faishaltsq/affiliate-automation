"""
Muse AI Client (muse.ai)
Drives headless Chromium via Playwright using user session cookies
to generate bespoke 4:5 frontier AI images for Threads content.
Falls back seamlessly if cookies expire or generation times out.
"""

import time
import base64
from pathlib import Path
from typing import Optional
import requests
from playwright.sync_api import sync_playwright

from src.config import DATA_DIR
from src.logger import get_logger

log = get_logger("muse_client")

COOKIES_PATHS = [
    DATA_DIR / "muse_cookies.txt",
    Path("C:/Users/cubeb/Downloads/muse.ai_cookies.txt"),
]
FREEIMAGE_API_KEY = "6d207e02198a847aa98d0a2a901485a5"


def _load_cookies() -> list[dict]:
    """Parses Netscape cookies for muse.ai."""
    for cp in COOKIES_PATHS:
        if cp.exists():
            cookies = []
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("\t")
                        if len(parts) >= 7:
                            domain, include_subdomains, path, secure, expiry, name, value = parts[:7]
                            cookies.append({
                                "name": name,
                                "value": value,
                                "domain": domain,
                                "path": path,
                                "secure": secure.upper() == "TRUE",
                                "expires": float(expiry) if expiry.isdigit() else -1,
                            })
                if cookies:
                    log.info(f"Loaded {len(cookies)} cookies from {cp.name}")
                    return cookies
            except Exception as e:
                log.warning(f"Error reading cookies from {cp}: {e}")
    return []


def generate_image_via_muse(prompt: str, timeout_seconds: int = 60) -> Optional[str]:
    """
    Submits an image generation prompt to Muse AI via Playwright.
    Returns the direct public CDN image URL, or None if failed.
    """
    cookies = _load_cookies()
    if not cookies:
        log.warning("No valid cookies found for Muse AI. Skipping.")
        return None

    # Enforce Indonesian context & 4:5 vertical framing in prompt
    full_prompt = f"{prompt.strip()}, aspek rasio 4:5 vertical, fotorealistik kualitas tinggi"
    log.info(f"Submitting prompt to Muse AI: '{full_prompt[:60]}...'")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
            )
            context.add_cookies(cookies)
            page = context.new_page()

            # Navigate to Muse AI
            page.goto("https://muse.ai/", wait_until="domcontentloaded", timeout=20000)
            page.wait_for_selector("textarea", timeout=15000)
            time.sleep(1)

            # Allow existing chat history and media to settle
            time.sleep(4)

            # Record initial image sources already present in the chat
            initial_srcs = page.evaluate(
                """() => {
                return Array.from(document.querySelectorAll('img[src^="blob:"]')).map(i => i.src);
            }"""
            )
            initial_count = len(initial_srcs)
            log.info(f"Existing images in chat history: {initial_count}")

            # Fill textarea via React prototype setter
            page.evaluate(
                """text => {
                const ta = document.querySelector('textarea');
                if (!ta) return;
                ta.focus();
                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
                nativeSetter.call(ta, text);
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                ta.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
                full_prompt,
            )

            time.sleep(1)

            # Click send
            send_clicked = page.evaluate(
                """() => {
                const btn = document.querySelector('button[aria-label="Send"]');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }"""
            )

            if not send_clicked:
                log.warning("Could not find Send button in Muse AI.")
                browser.close()
                return None

            log.info("Prompt sent. Waiting for Muse AI generation cycle...")

            # Wait 6 seconds for generation to kick off
            time.sleep(6)

            # Wait loop: wait until generation is finished AND a new image appears
            start_time = time.time()
            extracted_b64 = None

            while time.time() - start_time < timeout_seconds:
                time.sleep(3)
                status = page.evaluate(
                    """(oldSrcs) => {
                    const stopBtn = document.querySelector('button[aria-label="Stop"]');
                    const imgs = Array.from(document.querySelectorAll('img[src^="blob:"]'));
                    const newImgs = imgs.filter(i => !oldSrcs.includes(i.src));
                    return {
                        isGenerating: Boolean(stopBtn),
                        totalCount: imgs.length,
                        newCount: newImgs.length
                    };
                }""",
                    initial_srcs,
                )

                log.info(
                    f"Muse polling ({int(time.time() - start_time)}s): "
                    f"generating={status['isGenerating']}, new_images={status['newCount']}"
                )

                if status["newCount"] > 0 and not status["isGenerating"]:
                    time.sleep(2)
                    # Extract the newest image
                    b64 = page.evaluate(
                        """(oldSrcs) => {
                        const imgs = Array.from(document.querySelectorAll('img[src^="blob:"]'));
                        const newImgs = imgs.filter(i => !oldSrcs.includes(i.src));
                        const target = newImgs.length > 0 ? newImgs[newImgs.length - 1] : imgs[imgs.length - 1];
                        if (!target || !target.complete || (target.naturalWidth || target.width) < 200) return null;
                        
                        const canvas = document.createElement('canvas');
                        canvas.width = target.naturalWidth;
                        canvas.height = target.naturalHeight;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(target, 0, 0);
                        return canvas.toDataURL('image/jpeg', 0.95);
                    }""",
                        initial_srcs,
                    )
                    if b64 and len(b64) > 10000:
                        extracted_b64 = b64
                        log.info(f"Captured fresh Muse AI image after {int(time.time() - start_time)}s!")
                        break

            browser.close()

            if not extracted_b64:
                log.warning("Muse AI generation timed out or image not found.")
                return None

            # Upload to public CDN
            header, enc = extracted_b64.split(",", 1)
            raw_bytes = base64.b64decode(enc)

            # Save local backup
            backup_path = DATA_DIR / "muse_latest.jpg"
            backup_path.write_bytes(raw_bytes)

            up_res = requests.post(
                "https://freeimage.host/api/1/upload",
                data={
                    "key": FREEIMAGE_API_KEY,
                    "action": "upload",
                    "source": enc,
                    "format": "json",
                },
                timeout=20,
            )
            if up_res.status_code == 200:
                cdn_url = up_res.json().get("image", {}).get("url")
                if cdn_url and cdn_url.startswith("http"):
                    log.info(f"Muse AI image hosted on CDN: {cdn_url}")
                    return cdn_url

            log.warning("Failed to host Muse image on CDN.")
            return None

    except Exception as e:
        log.error(f"Playwright error during Muse AI generation: {e}")
        return None
