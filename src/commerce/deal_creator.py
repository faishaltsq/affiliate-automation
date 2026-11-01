"""Deal post creator for Telegram affiliate workflow.

Supports downloading screenshots from Telegram, uploading to public CDN,
generating Sol-conversational deal posts with validation asks, and saving
audited drafts with image metadata.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Optional

import requests

from src.commerce.disclosure import DISCLOSURE_SHORT
from src.content.quality_gate import GateDecision, evaluate_draft
from src.content.writer import DraftResult, save_draft
from src.core.config import get_settings
from src.core.llm import HttpLLMClient
log = logging.getLogger("deal_creator")


def download_telegram_photo(token: str, file_id: str, timeout: int = 20) -> Optional[bytes]:
    """Download the raw bytes of a Telegram photo by file_id."""
    if not token or not file_id:
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
            timeout=timeout,
        )
        if not r.ok:
            log.warning("getFile failed (%d): %s", r.status_code, r.text)
            return None
        file_path = r.json().get("result", {}).get("file_path")
        if not file_path:
            return None

        down = requests.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}",
            timeout=timeout,
        )
        if down.ok and len(down.content) > 100:
            return down.content
        log.warning("Download photo failed (%d)", down.status_code)
    except Exception as exc:
        log.warning("download_telegram_photo error: %s", exc)
    return None


def upload_deal_image(raw_bytes: bytes, timeout: int = 25) -> Optional[str]:
    """Upload raw image bytes to a public host (Uguu -> Freeimage fallback)."""
    if not raw_bytes:
        return None

    # 1. Primary: uguu.se (fast, serves raw image directly)
    try:
        up = requests.post(
            "https://uguu.se/upload?output=text",
            files={"files[]": ("deal.jpg", raw_bytes, "image/jpeg")},
            timeout=timeout,
        )
        url = up.text.strip()
        if up.status_code == 200 and url.startswith("http"):
            log.info("Uploaded deal image to uguu: %s", url)
            return url
    except Exception as exc:
        log.warning("uguu upload error: %s", exc)

    # 2. Fallback: freeimage.host
    try:
        b64 = base64.b64encode(raw_bytes).decode("utf-8")
        from src.image.card_generator import FREEIMAGE_API_KEY

        up = requests.post(
            "https://freeimage.host/api/1/upload",
            data={
                "key": FREEIMAGE_API_KEY,
                "action": "upload",
                "source": b64,
                "format": "json",
            },
            timeout=timeout,
        )
        if up.status_code == 200:
            url = up.json().get("image", {}).get("url")
            if url and url.startswith("http"):
                log.info("Uploaded deal image to freeimage: %s", url)
                return url
    except Exception as exc:
        log.warning("freeimage upload error: %s", exc)

    return None


def generate_deal_text(url: str, notes: str = "", client=None) -> str:
    """Generate a conversational deal post in the Sol-inspired voice."""
    llm = client or HttpLLMClient()
    prompt = f"""Kamu adalah penulis konten Threads untuk akun 'Teman Tongkrongan' (@pochita9887) dengan persona sol_inspired_conversational_v1.
Tulis satu post Threads gaya 'deal & validasi komunitas' berdasarkan produk dan link berikut:

Produk / Catatan: {notes or 'Produk teknologi harian pilihan'}
Link Affiliate: {url}

STRUKTUR WAJIB (ikuti gaya santai warga Threads):
1. Baris 1: Hook deal atau plot twist ringkas huruf kapital (misal: "PLOT TWIST: [BARANG / KEBIASAAN] LAGI DISKON WARGA THREADS" atau variasi segar yang relevan).
2. Paragraf 2: 1-2 kalimat observasi santai kenapa barang ini menarik (biasanya mahal/bikin mikir dua kali, eh pas dicek lagi diskon gede/drop harga).
3. Paragraf 3: Pertanyaan validasi ke audiens Threads yang sudah pakai ("Yang udah punya, validasi dong: beneran awet apa cuma gimmick doang?").
4. Paragraf 4: Baris link dan disclosure wajib:
cek: {url}
{DISCLOSURE_SHORT}

ATURAN KETAT:
- Panjang total 40-70 kata.
- 0 emoji.
- Bahasa Indonesia santai, mengalir natural.
- Output HANYA teks post final, tanpa komentar pembuka atau tanda kutip pembungkus.
"""
    resp = llm.complete([{"role": "user", "content": prompt}], max_tokens=300)
    text = (resp.text or "").strip()
    # Ensure disclosure is present
    if DISCLOSURE_SHORT not in text:
        text = text + "\n\n" + DISCLOSURE_SHORT
    return text


def create_deal_draft(
    url: str,
    notes: str = "",
    image_url: str = "",
    client=None,
    path=None,
) -> tuple[int, DraftResult, GateDecision]:
    """Generate, evaluate, and save a deal draft with affiliate metadata."""
    content = generate_deal_text(url, notes=notes, client=client)
    evidence = f"Produk/Catatan: {notes}. Link: {url}. Gambar: {bool(image_url)}"
    draft = DraftResult(
        content=content,
        parts=[content],
        structure="single",
        pillar="affiliate",
        angle=notes or "deal tiktokshop",
        audience_problem=notes or "harga promo affiliate",
        persona_version="sol_inspired_conversational_v1",
        variation={
            "format": "deal_validation",
            "opening": "plot_twist_deal",
            "image_url": image_url,
            "link": url,
        },
        evidence=evidence,
        disclosure=DISCLOSURE_SHORT,
    )

    # Persist draft with editorial metadata containing image_url
    draft_id = save_draft(draft, path=path)

    # Evaluate with quality gate and persist evaluation
    decision = evaluate_draft(
        content,
        evidence=evidence,
        pillar="affiliate",
        draft_id=draft_id,
        client=client,
        path=path,
        persist=True,
    )

    # Ensure editorial_metadata_json stores the image_url and link
    from src.core.storage import connect, transaction
    with connect(path) as conn:
        with transaction(conn):
            meta = {
                "format": "deal_validation",
                "opening": "plot_twist_deal",
                "image_url": image_url,
                "link": url,
                "notes": notes,
                "source": "telegram_deal",
            }
            conn.execute(
                "UPDATE content_drafts SET status = 'NEEDS_REVIEW', "
                "editorial_metadata_json = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), draft_id),
            )

    return draft_id, draft, decision
