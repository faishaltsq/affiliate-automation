"""pipeline.py — End-to-end autonomous clipping pipeline for Clippo campaigns."""

import os
import logging
from typing import Optional, Callable
from src.clipper.client import ClippoClient
from src.clipper.brief_analyzer import analyze_campaign_brief
from src.clipper.downloader import download_asset_video
from src.clipper.editor import render_clipper_video
from src.tiktok.uploader import TikTokUploader

logger = logging.getLogger("clipper.pipeline")


def run_clipper_pipeline(
    campaign_id: str,
    source_url: Optional[str] = None,
    dry_run: bool = False,
    fill_mode: bool = False,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Execute full clipper pipeline for a given Clippo campaign ID.
    If source_url is not provided, automatically picks the primary asset from the campaign.
    """
    def notify(msg: str):
        logger.info(msg)
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    notify(f"✂️ <b>Memulai Clipper Pipeline</b> untuk Campaign <code>{campaign_id}</code>...")

    # 1. Fetch Campaign Details
    client = ClippoClient()
    detail = client.get_campaign_detail(campaign_id)
    if not detail or not detail.get("title"):
        err = f"Campaign {campaign_id} tidak ditemukan di Clippo."
        notify(f"⚠️ {err}")
        return {"success": False, "error": err}

    title = detail.get("title")
    cpm = detail.get("cpm", 0)
    hashtags = detail.get("hashtags", [])
    cta = detail.get("cta", "")
    notify(f"📋 <b>Campaign Ditemukan:</b> {title} (CPM: Rp {cpm:,})")

    # 2. Analyze Brief via AI
    notify("🤖 Menganalisis syarat, do's & don'ts, dan winning hook...")
    brief = analyze_campaign_brief(detail)
    hook = brief.get("winning_hooks", [f"Wajib tahu ini: {title}"])[0]
    caption_rec = brief.get("caption_recommendation", f"{title} {' '.join(hashtags)}")

    # 3. Determine Video Source
    target_url = source_url
    if not target_url:
        assets = detail.get("assets", [])
        # Look for required or youtube/tiktok assets
        for a in assets:
            u = a.get("url", "")
            if any(k in u for k in ["youtube.com", "youtu.be", "tiktok.com", "instagram.com"]):
                target_url = u
                break
        if not target_url and assets:
            target_url = assets[0].get("url")

    if not target_url:
        err = "Tidak ada bahan video (footage) yang dapat diunduh dari campaign ini."
        notify(f"⚠️ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 4. Download Footage
    notify(f"📥 Mengunduh footage bahan dari: <code>{target_url[:55]}...</code>")
    if dry_run:
        notify("🧪 Dry-run aktif: skip download, render, dan upload.")
        return {
            "success": True,
            "dry_run": True,
            "campaign_id": campaign_id,
            "title": title,
            "cpm": cpm,
            "brief": brief,
            "source_url": target_url,
        }

    raw_path = download_asset_video(target_url)
    if not raw_path or not os.path.exists(raw_path):
        err = f"Gagal mengunduh footage dari {target_url}"
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 5. Render Video (Vertical 9:16)
    notify(f"🎬 Merender klip vertikal 9:16 (Hook: <i>\"{hook[:40]}...\"</i>)...")
    clip_dur = min(float(detail.get("max_duration", 60)), 60.0)
    edited_path = render_clipper_video(
        input_path=raw_path,
        duration_sec=clip_dur,
        hook_text=hook,
        cta_text=cta,
        fill_mode=fill_mode,
    )
    if not edited_path or not os.path.exists(edited_path):
        err = "Gagal merender video klip"
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 6. Upload to TikTok
    notify("🚀 Mengunggah ke TikTok @pochitalkshop...")
    uploader = TikTokUploader("data/tiktok/cookies.txt")
    # Pakai caption_recommendation dari AI yang sudah rapi, bukan dump hook mentah
    clean_caption = caption_rec.strip()
    # Pastikan hashtags terpasang jika belum ada di caption_rec
    for tag in hashtags:
        if tag.lower() not in clean_caption.lower():
            clean_caption += f" {tag}"
    tiktok_caption = clean_caption
    tiktok_ok = uploader.upload_video(edited_path, tiktok_caption)

    notify("✅ <b>Upload TikTok selesai!</b> Siap disubmit ke Clippo.")

    return {
        "success": True,
        "campaign_id": campaign_id,
        "title": title,
        "cpm": cpm,
        "tiktok_uploaded": tiktok_ok,
        "caption": tiktok_caption,
        "hashtags": hashtags,
        "brief": brief,
        "video_path": edited_path,
    }
