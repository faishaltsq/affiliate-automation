"""
Editorial News Card Generator (4:5 / 1080x1350)
Produces high-engagement editorial cards matching modern media outlets (Katadata, CNBC, Bloomberg):
- Upper 55-60%: Real news photo with smooth dark gradient fade
- Top-left: Pillar badge pill with vibrant accent dot
- Lower 40-45%: Sleek dark card container with bold high-contrast headline + subtitle
- Footer: @trendid_ handle + current date
"""

import io
import os
import re
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter
import requests
from src.logger import get_logger

log = get_logger("card_generator")

# Editorial Photo 4:5 Template Constants (Full Photo + Source Footer Only)
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1350
BOTTOM_BAR_H = 68
PHOTO_HEIGHT = CANVAS_HEIGHT - BOTTOM_BAR_H  # 1282px
FREEIMAGE_API_KEY = "6d207e02198a847aa98d0a2a901485a5"

MONTHS_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
]

def _get_font(font_path: str, size: int):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()

def _wrap_text(text: str, font, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    words = text.split()
    lines = []
    current_line = []
    for word in words:
        test_line = " ".join(current_line + [word])
        bbox = draw.textbbox((0, 0), test_line, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
    if current_line:
        lines.append(" ".join(current_line))
    return lines

def _keep_best_collage_panel(photo: Image.Image) -> tuple:
    """If the photo is a 2-panel collage (hard vertical seam in the middle third),
    crop to the single strongest panel so the card never renders half-dark.
    Returns (photo, width, height); unchanged when no seam is found.
    """
    try:
        import numpy as np

        pw, ph = photo.size
        if pw < 400:
            return photo, pw, ph

        # Brightness profile along x, downscaled for speed
        col_mean = np.asarray(photo.convert("L").resize((200, 40)), dtype=np.float32).mean(axis=0)
        jumps = np.abs(np.diff(col_mean))  # 199 gaps
        lo, hi = int(200 * 0.30), int(200 * 0.70)
        seg = jumps[lo:hi]
        idx = int(np.argmax(seg)) + lo
        if float(seg.max()) < 30.0:
            return photo, pw, ph

        seam_x = int((idx + 1) / 200 * pw)
        if not (0.25 * pw <= seam_x <= 0.75 * pw):
            return photo, pw, ph

        left = photo.crop((0, 0, seam_x, ph))
        right = photo.crop((seam_x, 0, pw, ph))

        def strength(panel: Image.Image) -> float:
            g = np.asarray(panel.convert("L"), dtype=np.float32)
            return float(g.mean() + 0.5 * g.std())

        best = left if strength(left) >= strength(right) else right
        bpw, bph = best.size
        if bpw < 400:
            return photo, pw, ph
        log.info(f"Collage seam detected at x={seam_x}/{pw}px — kept stronger panel {bpw}x{bph}")
        return best, bpw, bph
    except Exception as e:
        log.warning(f"Collage seam detection failed: {e}")
        return photo, pw, ph


def _verify_hosted_image(url: str, min_width: int = 800) -> bool:
    """Fetch the hosted URL back and confirm it serves full resolution.

    freeimage.host/iili.io started serving 160x160 thumbnails at the canonical
    URL for fresh uploads (older uploads still serve full size), so an upload
    response alone is not proof the card is actually published at HD.
    """
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            return False
        im = Image.open(io.BytesIO(r.content))
        return im.size[0] >= min_width
    except Exception:
        return False


def _upload_card_bytes(raw_bytes: bytes) -> str | None:
    """Upload card bytes to a public host that really serves full resolution.

    Primary: freeimage.host (kept for backward compatibility).
    Fallback: uguu.se — verified to serve 1080x1350. Retention is short
    (hours), which is fine because Threads/Telegram fetch the image at
    publish/preview time and then host their own copy.
    """
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")
    try:
        up_res = requests.post(
            "https://freeimage.host/api/1/upload",
            data={
                "key": FREEIMAGE_API_KEY,
                "action": "upload",
                "source": b64_data,
                "format": "json",
            },
            timeout=15,
        )
        if up_res.status_code == 200:
            url = up_res.json().get("image", {}).get("url")
            if url and url.startswith("http"):
                if _verify_hosted_image(url):
                    return url
                log.warning(f"Freeimage served a thumbnail for {url}; trying fallback host")
    except Exception as e:
        log.warning(f"Freeimage upload failed: {e}")

    try:
        up_res = requests.post(
            "https://uguu.se/upload?output=text",
            files={"files[]": ("card.jpg", raw_bytes, "image/jpeg")},
            timeout=30,
        )
        url = up_res.text.strip()
        if up_res.status_code == 200 and url.startswith("http") and _verify_hosted_image(url):
            return url
        log.warning(f"uguu fallback upload unusable ({up_res.status_code}): {url[:100]}")
    except Exception as e:
        log.warning(f"uguu fallback upload failed: {e}")

    return None


def _clean_title(title: str) -> str:
    # Only strip trailing portal branding like " - Detikcom", " - Kompas.com", " | CNBC"
    title = re.sub(
        r"\s*-\s*(?:Detik[A-Za-z]*|Kompas[A-Za-z\.]*|CNBC[A-Za-z\s]*|Antara[A-Za-z\s]*|CNN[A-Za-z\s]*|Tempo|Kumparan|Tribun[A-Za-z]*|Viva|Liputan6|Bisnis|Kontan|Suara|IDN\s*Times)[A-Za-z0-9\.\s]*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    title = re.sub(r"\s*\|\s*.*$", "", title)
    return title.strip()

def _get_source_name(source_hint: str, pillar: str) -> str:
    s_lower = (source_hint or "").lower()
    if "yahoo finance" in s_lower:
        return "Yahoo Finance (data pasar real-time)"
    if "bloomberg" in s_lower:
        return "Bloomberg"
    if "reuters" in s_lower:
        return "Reuters"
    if "wsj" in s_lower or "wall street journal" in s_lower:
        return "Wall Street Journal"
    if "ft.com" in s_lower or "financial times" in s_lower:
        return "Financial Times"
    if "cnbcindonesia" in s_lower:
        return "CNBC Indonesia"
    if "cnbc" in s_lower:
        return "CNBC Global"
    if "detik" in s_lower:
        return "Detik.com"
    if "kompas" in s_lower:
        return "Kompas.com"
    if "antara" in s_lower:
        return "Antara News"
    if "cnn" in s_lower:
        return "CNN Indonesia"
    if "liputan6" in s_lower:
        return "Liputan6"
    if "tempo" in s_lower:
        return "Tempo.co"
    if "goal.com" in s_lower or "bola" in s_lower:
        return "Media Sepak Bola"

    defaults = {
        "breaking_disaster": "BMKG / BNPB",
        "market_finance_stocks": "Bursa Efek Indonesia / CNBC",
        "football_sports": "Timnas / Media Olahraga",
        "tech_gadget_ai": "Tech Review / Bloomberg",
    }
    return defaults.get(pillar, "Berita Terkini")

def generate_editorial_card(
    photo_url: str,
    headline: str = "",
    summary: str = "",
    pillar: str = "default",
    source_hint: str = "",
    handle: str = "@pochita9887",
    photo_bytes: bytes | None = None,
) -> str:
    """
    Renders a clean full-photo editorial card (4:5 / 1080x1350):
    - 95% full frame (1080x1282): Full HD authentic news photo, enhanced & watermark-free.
    - 5% bottom bar (1080x68): Solid black source attribution bar (Sumber: [Media] ([Tanggal])).
    - NO headline text or white box inside the picture.
    """
    try:
        log.info(f"Generating full-photo card with source footer for pillar='{pillar}'")

        # 1. Base Canvas
        canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), color="#0A0C10")
        draw = ImageDraw.Draw(canvas)

        # 2. Fetch & Render Real News Photo (Full HD with built-in portal watermark removal)
        #    photo_bytes short-circuits the network path: a rendered market chart is
        #    already 1080x1282 with no watermark to strip, and cropping/upscaling it
        #    would blur the axis labels.
        photo_loaded = False
        if photo_bytes:
            try:
                photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
                pw, ph = photo.size
                if (pw, ph) != (CANVAS_WIDTH, PHOTO_HEIGHT):
                    photo = photo.resize((CANVAS_WIDTH, PHOTO_HEIGHT), Image.Resampling.LANCZOS)
                canvas.paste(photo, (0, 0))
                photo_loaded = True
                log.info("Card photo injected from pre-rendered bytes (chart)")
            except Exception as e:
                log.warning(f"Failed to load injected photo bytes: {e}")

        if not photo_loaded and photo_url and photo_url.startswith("http"):
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                resp = requests.get(photo_url, headers=headers, timeout=10)
                if resp.status_code == 200 and len(resp.content) > 3000:
                    photo = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    pw, ph = photo.size

                    # Collage guard: portal thumbnails often composite 2 photos
                    # side by side; center-cropping them splits the card into a
                    # dark half + a bright half. Keep only the strongest panel.
                    if ph > 300:
                        photo, pw, ph = _keep_best_collage_panel(photo)

                    # Automatic portal watermark removal: crop bottom banner/wedge
                    if ph > 300:
                        crop_ratio = 0.77 if "detik" in photo_url.lower() else 0.88
                        photo = photo.crop((0, 0, pw, int(ph * crop_ratio)))
                        pw, ph = photo.size

                    scale = max(CANVAS_WIDTH / pw, PHOTO_HEIGHT / ph)
                    new_w = int(pw * scale)
                    new_h = int(ph * scale)
                    photo_resized = photo.resize((new_w, new_h), Image.Resampling.LANCZOS)

                    left = (new_w - CANVAS_WIDTH) // 2
                    max_top = max(0, new_h - PHOTO_HEIGHT)
                    top = min(max_top, int(max_top * 0.15))

                    photo_cropped = photo_resized.crop((left, top, left + CANVAS_WIDTH, top + PHOTO_HEIGHT))

                    # HD clarity & upscale enhancement pipeline
                    photo_sharp = photo_cropped.filter(
                        ImageFilter.UnsharpMask(radius=1.5, percent=115, threshold=3)
                    )
                    photo_enhanced = ImageEnhance.Sharpness(photo_sharp).enhance(1.22)
                    photo_enhanced = ImageEnhance.Contrast(photo_enhanced).enhance(1.06)
                    photo_enhanced = ImageEnhance.Color(photo_enhanced).enhance(1.05)

                    canvas.paste(photo_enhanced, (0, 0))
                    photo_loaded = True
            except Exception as e:
                log.warning(f"Failed to fetch photo for card: {e}")

        # Fallback dark background if photo load fails
        if not photo_loaded:
            draw.rectangle([(0, 0), (CANVAS_WIDTH, PHOTO_HEIGHT)], fill="#1E232D")

        # 3. Bottom Black Source Attribution Bar
        footer_y = PHOTO_HEIGHT
        draw.rectangle([(0, footer_y), (CANVAS_WIDTH, CANVAS_HEIGHT)], fill="#000000")
        draw.line([(0, footer_y), (CANVAS_WIDTH, footer_y)], fill="#1F242E", width=1)

        now_wib = datetime.now(timezone.utc) + timedelta(hours=7)
        date_str = f"{now_wib.day} {MONTHS_ID[now_wib.month]} {now_wib.year}"
        source_name = _get_source_name(source_hint or photo_url, pillar)
        footer_text = f"Sumber: {source_name} ({date_str})"

        footer_font = _get_font("C:/Windows/Fonts/arial.ttf", 25)
        draw.text((36, footer_y + 18), footer_text, font=footer_font, fill="#E5E7EB")

        # 4. Encode & Save local copy
        buf = io.BytesIO()
        canvas.save(buf, format="JPEG", quality=95)
        raw_bytes = buf.getvalue()

        try:
            local_save = os.path.join(os.path.dirname(__file__), "../../data/editorial_latest.jpg")
            with open(local_save, "wb") as f:
                f.write(raw_bytes)
        except Exception:
            pass

        card_url = _upload_card_bytes(raw_bytes)
        if card_url:
            log.info(f"Full-photo card generated & hosted: {card_url}")
            return card_url

        log.warning("Card upload failed on all hosts, returning raw photo URL")
        return photo_url

    except Exception as e:
        log.error(f"Editorial card generation failed: {e}")
        return photo_url
