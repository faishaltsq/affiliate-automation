"""pipeline.py — End-to-end autonomous clipping pipeline for Clippo campaigns."""

import os
import re
import logging
import requests
from typing import Optional, Callable
from src.clipper.client import ClippoClient
from src.clipper.brief_analyzer import analyze_campaign_brief, LLM_URL, LLM_API_KEY, MODEL
from src.clipper.downloader import download_asset_video
from src.clipper.editor import render_clipper_video
from src.clipper.account_manager import list_accounts, get_account
from src.tiktok.uploader import TikTokUploader

logger = logging.getLogger("clipper.pipeline")


def _generate_variant_caption(brief: dict, account_style: str, account_index: int) -> str:
    """Generate unique caption variant per account to avoid duplicate content flags."""
    title = brief.get("title", "")
    hashtags = brief.get("hashtags", [])
    hooks = brief.get("winning_hooks", [])
    base_hook = hooks[min(account_index, len(hooks) - 1)] if hooks else title

    style_hint = {
        "energetic": "gaya santai, penuh semangat, pakai emoji, dan emosi positif",
        "calm": "gaya tenang dan informatif, minimal emoji, fokus ke manfaat produk",
        "funny": "gaya humor ringan dan relatable, satu kalimat lucu di awal",
        "default": "gaya natural, singkat dan to the point",
    }.get(account_style, "gaya natural")

    prompt = (
        f"Tulis caption TikTok singkat (max 150 karakter sebelum hashtag) untuk campaign: {title}.\n"
        f"Style: {style_hint}.\n"
        f"Hook dasar: {base_hook}\n"
        f"Pastikan terdengar natural, bukan copypaste caption lain. Akhiri dengan: {' '.join(hashtags)}\n"
        f"Output hanya teks caption saja tanpa penjelasan."
    )

    try:
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        r = requests.post(
            LLM_URL,
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "temperature": 0.75,
            },
            headers=headers,
            timeout=12,
        )
        if r.ok:
            caption = r.json()["choices"][0]["message"]["content"].strip()
            return re.sub(r'^["\']|["\']$', '', caption)
    except Exception as e:
        logger.warning("Variant caption LLM failed: %s", e)

    # Fallback
    fallback_hook = base_hook.strip('"\'')
    return f"{fallback_hook} {' '.join(hashtags)}"


def _ensure_hashtags(caption: str, hashtags: list[str]) -> str:
    """Append any missing required hashtags to caption."""
    clean = caption.strip()
    for tag in hashtags:
        if tag.lower() not in clean.lower():
            clean += f" {tag}"
    return clean


def run_clipper_pipeline(
    campaign_id: str,
    source_url: Optional[str] = None,
    dry_run: bool = False,
    fill_mode: bool = False,
    target_accounts: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Execute full clipper pipeline for a Clippo campaign.
    Uploads to all active TikTok accounts with unique caption per account.
    target_accounts: list of account folder names; None = all active accounts.
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

    # 2. Resolve target accounts
    if target_accounts:
        accounts = [a for name in target_accounts if (a := get_account(name)) and a["has_cookies"]]
    else:
        accounts = list_accounts(only_active=True)

    # Legacy fallback: single default cookies
    if not accounts:
        legacy = "data/tiktok/cookies.txt"
        if os.path.exists(legacy):
            accounts = [{
                "name": "default",
                "display_name": "pochitalkshop",
                "username": "@pochitalkshop",
                "active": True,
                "style": "default",
                "cookies_path": legacy,
                "has_cookies": True,
            }]
        else:
            err = "Tidak ada akun TikTok aktif. Tambahkan cookies via /addaccount."
            notify(f"⚠️ {err}")
            return {"success": False, "error": err}

    notify(f"👤 <b>Akun target:</b> {', '.join(a['username'] for a in accounts)}")

    # 3. Analyze Brief via AI
    notify("🤖 Menganalisis brief, do's & don'ts, dan winning hooks...")
    brief = analyze_campaign_brief(detail)
    brief["title"] = title
    hook = brief.get("winning_hooks", [f"Wajib tahu ini: {title}"])[0]
    caption_rec = brief.get("caption_recommendation", f"{title}\n\n{' '.join(hashtags)}")

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
        err = "Tidak ada bahan video yang dapat diunduh dari campaign ini."
        notify(f"⚠️ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 5. Download Footage (once, shared across all accounts)
    notify(f"📥 Mengunduh footage dari: <code>{target_url[:60]}</code>")
    if dry_run:
        notify("🧪 Dry-run aktif: skip download, render, dan upload.")
        return {
            "success": True,
            "dry_run": True,
            "campaign_id": campaign_id,
            "title": title,
            "cpm": cpm,
            "accounts": [a["username"] for a in accounts],
            "brief": brief,
            "source_url": target_url,
        }

    import time as _time
    t0 = _time.time()
    raw_path = download_asset_video(target_url)
    dl_sec = _time.time() - t0
    if not raw_path or not os.path.exists(raw_path):
        err = f"Gagal mengunduh footage dari {target_url}"
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}
    notify(f"📥 Download selesai ({dl_sec:.0f}s)")

    # 6. Render base video (once, shared)
    notify(f"🎬 Merender klip 9:16 HD (hook: <i>\"{hook[:45]}...\"</i>)...")
    t1 = _time.time()
    clip_dur = min(float(detail.get("max_duration", 60)), 60.0)
    edited_path = render_clipper_video(
        input_path=raw_path,
        duration_sec=clip_dur,
        hook_text=hook,
        cta_text=cta,
        fill_mode=fill_mode,
    )
    render_sec = _time.time() - t1
    if not edited_path or not os.path.exists(edited_path):
        err = "Gagal merender video klip."
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}
    notify(f"🎬 Render selesai ({render_sec:.0f}s)")

    # 7. Upload ke masing-masing akun dengan caption unik per akun
    results = []
    for idx, account in enumerate(accounts):
        acc_username = account["username"]
        acc_style = account.get("style", "default")
        cookies = account["cookies_path"]

        notify(f"🚀 <b>Upload ke {acc_username}</b> ({idx + 1}/{len(accounts)})...")

        # Akun pertama pakai caption_rec utama, akun berikutnya generate variasi unik
        if idx == 0:
            caption = _ensure_hashtags(caption_rec, hashtags)
        else:
            caption = _generate_variant_caption(brief, acc_style, idx)

        try:
            uploader = TikTokUploader(cookies)
            ok = uploader.upload_video(edited_path, caption)
            results.append({"account": acc_username, "success": ok, "caption": caption})
            notify(f"{'✅' if ok else '❌'} {acc_username}: {'berhasil' if ok else 'gagal'}")
        except Exception as e:
            logger.error("Upload error for %s: %s", acc_username, e)
            results.append({"account": acc_username, "success": False, "caption": caption, "error": str(e)})
            notify(f"❌ {acc_username}: error — <code>{e}</code>")

    success_count = sum(1 for r in results if r["success"])
    notify(f"✅ <b>Selesai!</b> {success_count}/{len(accounts)} akun berhasil upload.")

    return {
        "success": success_count > 0,
        "campaign_id": campaign_id,
        "title": title,
        "cpm": cpm,
        "upload_results": results,
        "success_count": success_count,
        "total_accounts": len(accounts),
        "hashtags": hashtags,
        "brief": brief,
        "video_path": edited_path,
    }
