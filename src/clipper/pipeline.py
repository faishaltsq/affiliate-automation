"""pipeline.py — End-to-end autonomous clipping pipeline for Clippo campaigns."""

import os
import re
import logging
import requests
from typing import Optional, Callable
from src.clipper.client import ClippoClient
from src.clipper.motionklip_client import MotionklipClient
from src.clipper.brief_analyzer import (
    analyze_campaign_brief, LLM_URL, LLM_API_KEY, MODEL,
    MORTERA_URL, MORTERA_MODEL, MORTERA_API_KEY,
)
from src.clipper.downloader import download_asset_videos, download_asset_video
from src.clipper.editor import render_clipper_video
from src.clipper.account_manager import list_accounts, get_account
from src.tiktok.uploader import TikTokUploader

logger = logging.getLogger("clipper.pipeline")


def _generate_variant_caption(brief: dict, account_style: str, account_index: int, custom_hook: Optional[str] = None) -> str:
    """Generate unique caption variant per account to avoid duplicate content flags."""
    title = brief.get("title", "")
    hashtags = brief.get("hashtags", [])
    hooks = brief.get("winning_hooks", [])
    if custom_hook:
        base_hook = custom_hook
    else:
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
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0.75,
        }
        r = None
        # 1. Primary: 9router
        r = requests.post(LLM_URL, json=payload, headers=headers, timeout=12)
        if not r.ok:
            logger.warning("Variant caption LLM (%s) HTTP %s, trying Mortera fallback...", MODEL, r.status_code)
            # 2. Fallback: Mortera Cloud direct (Muse Spark)
            if MORTERA_API_KEY:
                payload["model"] = MORTERA_MODEL
                m_headers = {"Authorization": f"Bearer {MORTERA_API_KEY}"}
                r = requests.post(MORTERA_URL, json=payload, headers=m_headers, timeout=30)
            else:
                logger.warning("Mortera fallback skipped: MORTERA_API_KEY missing")
        if r is not None and r.ok:
            caption = r.json()["choices"][0]["message"]["content"].strip()
            return re.sub(r'^["\']|["\']$', '', caption)
    except Exception as e:
        logger.warning("Variant caption LLM failed: %s", e)

    # Fallback
    fallback_hook = base_hook.strip('"\'')
    return f"{fallback_hook} {' '.join(hashtags)}"


def _ensure_hashtags(caption: str, hashtags: list[str], tags: list[str] = None) -> str:
    """Append any missing required hashtags and account tags to caption."""
    clean = caption.strip()
    if tags:
        for t in tags:
            tag_name = t.strip("@")
            if f"@{tag_name.lower()}" not in clean.lower():
                clean += f" @{tag_name}"
    for tag in hashtags:
        if tag.lower() not in clean.lower():
            clean += f" {tag}"
    return clean


def _upload_fingerprint(account: str, video_path: str, source_paths: Optional[list] = None) -> str:
    """Stable identity of one (account, source footage, rendered clip) upload.

    Keyed on content, NOT campaign_id: two different campaigns that resolve to
    the same footage render the same video, and posting it twice to the same
    account is exactly the duplicate this guards against. Render is
    byte-deterministic for identical inputs, and the output stem derives from
    the footage filename, so (account, source names, stem) is a stable key
    across re-runs while staying distinct for genuinely different footage.
    """
    import hashlib
    srcs = ",".join(
        sorted(
            os.path.splitext(os.path.basename(p))[0]
            for p in (source_paths or [])
        )
    )
    stem = os.path.splitext(os.path.basename(video_path or ""))[0]
    raw = f"{account}|{srcs}|{stem}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def get_campaign_provider(campaign_id: str):
    """Return campaign client provider based on campaign ID prefix."""
    if campaign_id.startswith("mk-"):
        from src.clipper.motionklip_client import MotionklipClient as _MK
        cls = globals().get("MotionklipClient", _MK)
        return cls()
    from src.clipper.client import ClippoClient as _CC
    cls = globals().get("ClippoClient", _CC)
    return cls()


def _is_valid_footage_source(url: str, label: str) -> bool:
    """Check if a URL is an actual downloadable footage asset, not a guideline, watermark, or creator profile."""
    u = url.lower().strip()
    lbl = label.lower().strip()

    # Explicit non-footage documents, rules, tutorials, and watermark templates
    non_footage_words = ["guideline", "panduan", "brief", "aturan", "syarat", "doc", "watermark", "template", "penempatan", "contoh"]
    if any(w in lbl for w in non_footage_words):
        return False
    if any(w in u for w in ["watermark", "fgqxqwoxgjm", "thbidhbt0m0"]):
        return False

    # Social media profile links (creator account profiles, not individual videos)
    if "instagram.com" in u and not any(p in u for p in ["/p/", "/reel/", "/tv/"]):
        return False
    if "tiktok.com" in u and not any(p in u for p in ["/video/", "vt.tiktok.com"]):
        return False
    if "youtube.com" in u and any(p in u for p in ["/@", "/channel/", "/c/", "/user/"]):
        return False

    return True


def run_clipper_pipeline(
    campaign_id: str,
    source_url: Optional[str] = None,
    dry_run: bool = False,
    fill_mode: bool = False,
    target_accounts: Optional[list[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    count: int = 1,
    distribution: str = "distribute",
    force: bool = False,
) -> dict:
    """
    Execute full clipper pipeline for a Clippo or Motionklip campaign.
    Uploads to all active TikTok accounts with unique caption per account.
    target_accounts: list of account folder names; None = all active accounts.
    force: re-upload clips that were already posted to these accounts.
    """
    def notify(msg: str):
        logger.info(msg)
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    platform_name = "Motionklip" if campaign_id.startswith("mk-") else "Clippo"
    notify(f"✂️ <b>Memulai Clipper Pipeline</b> untuk Campaign <code>{campaign_id}</code> ({platform_name})...")

    # 1. Fetch Campaign Details
    client = get_campaign_provider(campaign_id)
    detail = client.get_campaign_detail(campaign_id)
    if not detail or not detail.get("title"):
        err = f"Campaign {campaign_id} tidak ditemukan di {platform_name}."
        notify(f"⚠️ {err}")
        return {"success": False, "error": err}

    title = detail.get("title")
    cpm = detail.get("cpm", 0)
    hashtags = detail.get("hashtags", [])
    cta = detail.get("cta", "")

    # Extract required brand/account tags from campaign
    raw_data = detail.get("raw", {})
    campaign_tags = []
    for ct in raw_data.get("campaignTags", []):
        tname = ct.get("tag", {}).get("name")
        if tname:
            campaign_tags.append(tname)
    # Also check if Do's specifically mentions tagging an account (e.g. @jualemasidn)
    for do_item in detail.get("dos", []):
        for mention in re.findall(r'@([a-zA-Z0-9._]+)', do_item):
            if mention not in campaign_tags:
                campaign_tags.append(mention)

    notify(f"📋 <b>Campaign Ditemukan:</b> {title} (CPM: Rp {cpm:,})")

    # Resolve watermark for campaign (Motionklip requires mandatory template watermark)
    is_motionklip = str(campaign_id).startswith("mk-") or detail.get("source") == "motionklip"
    watermark_path = None
    if is_motionklip:
        candidate_wms = [
            os.path.abspath("assets/watermarks/motionklip.png"),
            os.path.expanduser(r"~\OneDrive\Gambar\Watermark New MotionKlip.png"),
        ]
        for c_wm in candidate_wms:
            if os.path.exists(c_wm):
                watermark_path = c_wm
                break
        if watermark_path:
            notify("🏷️ <b>Watermark MotionKlip:</b> Template resmi wajib aktif & akan ditempel otomatis.")

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

    # 4. Determine Video Sources (collect all available assets)
    if source_url:
        target_urls = [source_url]
    else:
        assets = detail.get("assets", [])
        footage_assets = []
        for a in assets:
            if isinstance(a, dict):
                u = a.get("url", "")
                dname = a.get("display_name", "")
            elif isinstance(a, str):
                u = a
                dname = ""
            else:
                continue

            if not u:
                continue
            if _is_valid_footage_source(u, dname):
                footage_assets.append(u)

        target_urls = footage_assets

    if not target_urls:
        # Check if campaign provided talent profiles instead of footage files
        assets = detail.get("assets", [])
        social_profiles = [
            (a.get("url") if isinstance(a, dict) else a) for a in assets
            if any(p in (a.get("url") if isinstance(a, dict) else a).lower() for p in ["youtube.com/@", "tiktok.com/@", "instagram.com"])
        ]
        if social_profiles:
            sp_str = ", ".join(f"<code>{sp}</code>" for sp in social_profiles[:3])
            err = (
                f"Campaign ini tidak menyediakan link video/Google Drive rekaman langsung di brief (hanya profil channel: {sp_str}). "
                f"Video panduan penempatan watermark tidak akan diklip.\n\n"
                f"👉 <b>Silakan kirim ulang dengan menyertakan URL video YouTube yang ingin diklip:</b>\n"
                f"<code>/clip {campaign_id} {count} https://www.youtube.com/watch?v=VIDEO_ID</code>"
            )
        else:
            err = "Tidak ada bahan footage video yang dapat diunduh dari campaign ini."
        notify(f"⚠️ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 5. Download Footage (guaranteeing all provided footages are acquired)
    notify(f"📥 Mengunduh bahan footage campaign...")
    if dry_run:
        notify(f"🧪 Dry-run aktif: skip download, render ({count} video), dan upload.")
        return {
            "success": True,
            "dry_run": True,
            "campaign_id": campaign_id,
            "platform": platform_name,
            "title": title,
            "cpm": cpm,
            "count": count,
            "distribution": distribution,
            "accounts": [a["username"] for a in accounts],
            "brief": brief,
            "source_url": target_urls[0],
        }

    import time as _time
    t0 = _time.time()
    all_footages = []
    for u in target_urls:
        notify(f"📥 Mengunduh footage dari: <code>{u[:60]}</code>")
        vids = download_asset_videos(u)
        all_footages.extend(vids)

    # Deduplicate while preserving sequence order, ensuring strictly valid video media files
    from src.clipper.editor import is_valid_media_file
    seen_paths = set()
    unique_footages = []
    for p in all_footages:
        if p not in seen_paths and os.path.exists(p) and is_valid_media_file(p):
            seen_paths.add(p)
            unique_footages.append(p)

    dl_sec = _time.time() - t0
    if not unique_footages:
        err = f"Gagal mengunduh footage dari {target_urls}"
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}

    notify(f"📥 Download selesai ({dl_sec:.0f}s): {len(unique_footages)} bahan footage siap disatukan.")

    # 6. Render base video with natural dialogue continuity and adaptive framing
    min_dur = float(detail.get("min_duration", 15))
    max_dur = float(detail.get("max_duration", 180))

    from src.clipper.story_analyzer import (
        natural_sort_paths,
        build_story_narrative_beats,
        build_multi_story_packages,
    )
    from src.clipper.punchline import (
        transcribe_footage_clips,
        select_climax_punchline,
        select_multiple_climax_punchlines,
    )

    unique_footages = natural_sort_paths(unique_footages)
    rendered_videos = []

    # Find potential outro/closing clip from last footage or closing keywords
    outro_file = None
    for f in unique_footages:
        f_name = os.path.basename(f).lower()
        if "outro" in f_name or "closing" in f_name or f == unique_footages[-1]:
            outro_file = f
            break

    if count > 1:
        notify(f"🎙️ Mentranskripsi dialog footage untuk mencari {count} sudut pandang/hook berbeda...")
        transcripts = []
        try:
            transcripts = transcribe_footage_clips(unique_footages[:12])
        except Exception as p_err:
            logger.warning("Footage transcription failed: %s", p_err)

        punchlines = select_multiple_climax_punchlines(title, transcripts, count=count, brief=brief)
        notify(f"🎯 Ditemukan {len(punchlines)} variasi hook cerita unik:")
        for idx, p in enumerate(punchlines):
            notify(f"   • Video {idx + 1}: <i>\"{p.get('punchline_hook')}\"</i> ({os.path.basename(p.get('target_file', ''))})")

        packages = build_multi_story_packages(
            footages=unique_footages,
            punchlines=punchlines,
            outro_file=outro_file,
            target_duration=max(min_dur, min(max_dur, 90.0)),
        )

        for p_idx, pkg in enumerate(packages):
            v_num = p_idx + 1
            v_hook = pkg["punchline_hook"]
            v_dur = max(min_dur, min(max_dur, pkg["estimated_duration"]))
            v_beats = pkg["story_beats"]
            v_input = [b["file"] for b in v_beats] if v_beats else unique_footages

            notify(f"🎬 Merender Video {v_num}/{len(packages)} ({v_dur:.0f}s, hook: <i>\"{v_hook}\"</i>)...")
            v_t0 = _time.time()
            v_path = render_clipper_video(
                input_path=v_input,
                duration_sec=v_dur,
                hook_text=v_hook,
                cta_text=cta,
                fill_mode=fill_mode,
                climax_info=pkg["punchline_info"],
                auto_subtitles=True,
                story_beats=v_beats,
                watermark_path=watermark_path,
            )
            v_sec = _time.time() - v_t0
            if v_path and os.path.exists(v_path):
                notify(f"✅ Video {v_num}/{len(packages)} selesai dirender ({v_sec:.0f}s).")
                rendered_videos.append({
                    "video_path": v_path,
                    "hook": v_hook,
                    "duration": v_dur,
                    "climax_info": pkg["punchline_info"],
                })
            else:
                notify(f"⚠️ Video {v_num} gagal dirender.")

    elif len(unique_footages) > 1:
        # Multi-footage single video: transcribe and extract climax punchline matching campaign title
        notify("🎙️ Mentranskripsi dialog footage untuk mencari momen punchline/klimaks...")
        climax_info = None
        story_beats = None
        try:
            transcripts = transcribe_footage_clips(unique_footages[:12])
            if transcripts:
                notify(f"🔍 Ditemukan {len(transcripts)} penggalan dialog; menganalisis punchline terbaik...")
                climax_info = select_climax_punchline(title, transcripts, brief=brief)
                if climax_info and climax_info.get("punchline_hook"):
                    notify(f"🎯 <b>Punchline Hook:</b> <i>\"{climax_info.get('punchline_hook')}\"</i> "
                           f"({os.path.basename(climax_info.get('target_file', ''))} @ {climax_info.get('climax_start')}s)")
        except Exception as p_err:
            logger.warning("Punchline detection failed, proceeding with normal hook: %s", p_err)

        target_dur = max(min_dur, min(max_dur, 90.0))
        hero_file = climax_info.get("target_file") if climax_info else None
        single_outro = None
        for f in unique_footages:
            f_name = os.path.basename(f).lower()
            if f != hero_file and ("outro" in f_name or "closing" in f_name or f == unique_footages[-1]):
                single_outro = f

        if hero_file and os.path.exists(hero_file):
            notify("🎬 Mengekstrak alur percakapan natural (skip basa-basi & menjaga keutuhan kalimat)...")
            story_beats = build_story_narrative_beats(hero_file, outro_file=single_outro, target_duration=target_dur)
            if story_beats:
                beats_sum = sum(b.get("duration", b.get("end", 0.0) - b.get("start", 0.0)) for b in story_beats)
                teaser_pad = float(climax_info.get("climax_duration", 3.5)) if climax_info else 0.0
                target_dur = max(min_dur, min(max_dur, beats_sum + teaser_pad))

        if story_beats and len(story_beats) > 0:
            notify(f"🎬 Merender klip 9:16 HD ({target_dur:.0f}s) dengan dialog natural & subtitle sinkron...")
            input_for_render = [b["file"] for b in story_beats]
        else:
            notify(f"🎬 Merender klip 9:16 HD dari {len(unique_footages)} footage ({target_dur:.0f}s)...")
            input_for_render = unique_footages

        hook_to_use = climax_info.get("punchline_hook") if (climax_info and climax_info.get("punchline_hook")) else hook
        t1 = _time.time()
        edited_path = render_clipper_video(
            input_path=input_for_render,
            duration_sec=target_dur,
            hook_text=hook_to_use,
            cta_text=cta,
            fill_mode=fill_mode,
            climax_info=climax_info,
            auto_subtitles=True,
            story_beats=story_beats,
            watermark_path=watermark_path,
        )
        render_sec = _time.time() - t1
        if edited_path and os.path.exists(edited_path):
            rendered_videos.append({
                "video_path": edited_path,
                "hook": hook_to_use,
                "duration": target_dur,
                "climax_info": climax_info,
            })
    else:
        # Single footage — run full punchline + story beat pipeline (same as multi-footage)
        target_dur = max(min_dur, min(max_dur, 65.0))
        solo_file = unique_footages[0]
        notify("🎙️ Mentranskripsi footage untuk mencari momen punchline/klimaks terbaik...")
        climax_info = None
        story_beats = None
        try:
            transcripts = transcribe_footage_clips([solo_file])
            if transcripts:
                notify(f"🔍 Ditemukan {len(transcripts)} penggalan dialog; menganalisis punchline terbaik...")
                climax_info = select_climax_punchline(title, transcripts, brief=brief)
                if climax_info and climax_info.get("punchline_hook"):
                    notify(
                        f"🎯 <b>Punchline Hook:</b> <i>\"{climax_info.get('punchline_hook')}\"</i> "
                        f"(@ {climax_info.get('climax_start')}s)"
                    )
        except Exception as p_err:
            logger.warning("Punchline detection failed on single footage, proceeding with normal hook: %s", p_err)

        # Build story beats to avoid cold intro
        try:
            story_beats = build_story_narrative_beats(solo_file, outro_file=None, target_duration=target_dur)
            if story_beats:
                beats_sum = sum(b.get("duration", b.get("end", 0.0) - b.get("start", 0.0)) for b in story_beats)
                teaser_pad = float(climax_info.get("climax_duration", 3.5)) if climax_info else 0.0
                target_dur = max(min_dur, min(max_dur, beats_sum + teaser_pad))
        except Exception as b_err:
            logger.warning("Story beats build failed for single footage: %s", b_err)

        hook_to_use = climax_info.get("punchline_hook") if (climax_info and climax_info.get("punchline_hook")) else hook
        notify(f"🎬 Merender klip 9:16 HD ({target_dur:.0f}s, hook: <i>\"{hook_to_use[:50]}\"</i>)...")

        input_for_render = [b["file"] for b in story_beats] if story_beats else [solo_file]
        t1 = _time.time()
        edited_path = render_clipper_video(
            input_path=input_for_render,
            duration_sec=target_dur,
            hook_text=hook_to_use,
            cta_text=cta,
            fill_mode=fill_mode,
            climax_info=climax_info,
            auto_subtitles=True,
            story_beats=story_beats,
            watermark_path=watermark_path,
        )
        render_sec = _time.time() - t1
        if edited_path and os.path.exists(edited_path):
            rendered_videos.append({
                "video_path": edited_path,
                "hook": hook,
                "duration": target_dur,
                "climax_info": None,
            })

    if not rendered_videos:
        err = "Gagal merender video klip."
        notify(f"❌ {err}")
        return {"success": False, "error": err, "brief": brief}

    # 7. Upload ke masing-masing akun
    # Upload ledger (same SQLite state file as the TikTok queue) = anti-duplikat:
    # re-running a campaign must not re-post identical clips to the same account.
    from src.tiktok.config import load_config as _load_cfg
    from src.tiktok.state import VideoStateDB as _StateDB
    try:
        _cfg = _load_cfg()
        upload_db = _StateDB(_cfg.get("db_path", "data/tiktok/state.db"))
    except Exception as _db_err:
        logger.warning("Upload ledger unavailable, duplicate guard disabled: %s", _db_err)
        upload_db = None

    results = []
    if distribution == "distribute" and len(rendered_videos) > 1:
        # Distribute distinct video per account
        for idx, account in enumerate(accounts):
            acc_username = account["username"]
            acc_style = account.get("style", "default")
            cookies = account["cookies_path"]

            v_item = rendered_videos[idx % len(rendered_videos)]
            v_path = v_item["video_path"]
            v_hook = v_item["hook"]

            notify(f"🚀 <b>Upload ke {acc_username}</b> ({idx + 1}/{len(accounts)}) dengan video: \"{v_hook}\"...")
            caption = _generate_variant_caption(brief, acc_style, idx, custom_hook=v_hook)
            caption = _ensure_hashtags(caption, hashtags, tags=campaign_tags)

            fp = _upload_fingerprint(acc_username, v_path, unique_footages)
            if upload_db and not force and upload_db.is_clipper_uploaded(fp):
                notify(f"⏭️ {acc_username}: klip ini sudah pernah diupload, dilewati (anti-duplikat).")
                results.append({
                    "account": acc_username,
                    "video_path": v_path,
                    "hook": v_hook,
                    "success": True,
                    "skipped_duplicate": True,
                    "caption": caption,
                })
                continue

            try:
                uploader = TikTokUploader(cookies)
                ok = uploader.upload_video(v_path, caption)
                if ok and upload_db:
                    upload_db.mark_clipper_uploaded(fp, campaign_id, acc_username, v_path)
                results.append({
                    "account": acc_username,
                    "video_path": v_path,
                    "hook": v_hook,
                    "success": ok,
                    "caption": caption
                })
                notify(f"{'✅' if ok else '❌'} {acc_username}: {'berhasil' if ok else 'gagal'}")
            except Exception as e:
                logger.error("Upload error for %s: %s", acc_username, e)
                results.append({"account": acc_username, "video_path": v_path, "hook": v_hook, "success": False, "caption": caption, "error": str(e)})
                notify(f"❌ {acc_username}: error — <code>{e}</code>")
    else:
        # Single video or all videos to all accounts
        for v_item in rendered_videos:
            v_path = v_item["video_path"]
            v_hook = v_item["hook"]
            for idx, account in enumerate(accounts):
                acc_username = account["username"]
                acc_style = account.get("style", "default")
                cookies = account["cookies_path"]

                notify(f"🚀 <b>Upload ke {acc_username}</b> (video: \"{v_hook}\")...")
                if idx == 0 and len(rendered_videos) == 1:
                    caption = _ensure_hashtags(caption_rec, hashtags, tags=campaign_tags)
                else:
                    caption = _generate_variant_caption(brief, acc_style, idx, custom_hook=v_hook)
                    caption = _ensure_hashtags(caption, hashtags, tags=campaign_tags)

                try:
                    uploader = TikTokUploader(cookies)
                    fp = _upload_fingerprint(acc_username, v_path, unique_footages)
                    if upload_db and not force and upload_db.is_clipper_uploaded(fp):
                        notify(f"⏭️ {acc_username}: klip ini sudah pernah diupload, dilewati (anti-duplikat).")
                        results.append({
                            "account": acc_username,
                            "video_path": v_path,
                            "hook": v_hook,
                            "success": True,
                            "skipped_duplicate": True,
                            "caption": caption,
                        })
                        continue
                    ok = uploader.upload_video(v_path, caption)
                    if ok and upload_db:
                        upload_db.mark_clipper_uploaded(fp, campaign_id, acc_username, v_path)
                    results.append({"account": acc_username, "video_path": v_path, "hook": v_hook, "success": ok, "caption": caption})
                    notify(f"{'✅' if ok else '❌'} {acc_username}: {'berhasil' if ok else 'gagal'}")
                except Exception as e:
                    logger.error("Upload error for %s: %s", acc_username, e)
                    results.append({"account": acc_username, "video_path": v_path, "hook": v_hook, "success": False, "caption": caption, "error": str(e)})
                    notify(f"❌ {acc_username}: error — <code>{e}</code>")

    success_count = sum(1 for r in results if r["success"])
    skipped_dupes = sum(1 for r in results if r.get("skipped_duplicate"))
    if upload_db:
        upload_db.close()
    if skipped_dupes:
        notify(f"⏭️ <b>{skipped_dupes} unggahan dilewati</b> karena sudah pernah diupload sebelumnya.")
    notify(f"✅ <b>Selesai!</b> {success_count}/{len(results)} unggahan berhasil.")

    primary_path = rendered_videos[0]["video_path"] if rendered_videos else None
    return {
        "success": success_count > 0,
        "campaign_id": campaign_id,
        "platform": platform_name,
        "title": title,
        "cpm": cpm,
        "count": len(rendered_videos),
        "distribution": distribution,
        "videos": rendered_videos,
        "upload_results": results,
        "success_count": success_count,
        "total_accounts": len(accounts),
        "hashtags": hashtags,
        "brief": brief,
        "video_path": primary_path,
    }
