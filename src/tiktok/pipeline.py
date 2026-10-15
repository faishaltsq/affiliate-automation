"""pipeline.py — Main TikTok automation pipeline orchestrator.

Search → Download → Skip Intro / Seek Action → Edit (9:16 Lanczos) → Upload → Track in SQLite.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from src.tiktok.config import load_config
from src.tiktok.state import VideoStateDB
from src.tiktok.uploader import TikTokUploader
from src.tiktok.youtube import search_videos, download_video, get_action_start_time
from src.tiktok.editor import process_video
from src.tiktok.youtube_uploader import upload_shorts
from src.tiktok.outdoorboys import replenish_outdoorboys_queue

logger = logging.getLogger("tiktok.pipeline")


def setup_logging(log_dir: str) -> None:
    """Ensure daily rotating log file for TikTok runs."""
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"tiktok-{today}.log"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_file.resolve())
        for h in root.handlers
    ):
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s"))
        root.addHandler(fh)


def run_pipeline(
    dry_run: bool = False,
    count: Optional[int] = None,
    keyword_override: Optional[str] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    fill_mode: bool = True,
    upload_youtube: bool = True,
) -> dict:
    """
    Run the TikTok pipeline.
    Returns summary dict:
    {"success": bool, "uploaded": int, "found": int, "videos": list[dict], "errors": list[str]}
    """
    config = load_config()
    setup_logging(config.get("log_dir", "logs/tiktok"))

    def notify(msg: str):
        logger.info(msg)
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    notify("🎬 <b>Memulai TikTok Pipeline...</b>")

    db = VideoStateDB(config.get("db_path", "data/tiktok/state.db"))
    uploader = TikTokUploader(config.get("tiktok_cookies_path", "data/tiktok/cookies.txt"))

    # Outdoor Boys Multi-Part Queue Mode
    source_mode = config.get("source_mode", "shorts")
    is_outdoorboys = (source_mode == "outdoorboys") or (
        keyword_override and any(w in keyword_override.lower() for w in ["outdoorboys", "outdoor boys", "luke nichols"])
    )

    if is_outdoorboys:
        notify("🌲 <b>Mode Outdoor Boys Aktif:</b> Mengambil cuplikan dari antrean...")
        replenish_outdoorboys_queue(db, config, notify_fn=notify)

        uploaded_videos = []
        errors = []
        max_uploads = count if count is not None else 1

        for _ in range(max_uploads):
            clip = db.get_next_queued_clip()
            if not clip:
                notify("⚠️ Tidak ada cuplikan Outdoor Boys yang tersedia di antrean.")
                break

            clip_id = clip["clip_id"]
            title = clip["title"]
            file_path = clip["file_path"]
            part_num = clip["part_num"]
            total_parts = clip["total_parts"]

            if not os.path.exists(file_path):
                notify(f"⚠️ File cuplikan {file_path} tidak ditemukan di disk, melewati...")
                db.mark_clip_uploaded(clip_id)
                continue

            if dry_run:
                notify(f"🧪 [DRY RUN] Cuplikan siap di: <code>{file_path}</code> (Upload dilewati).")
                uploaded_videos.append({"id": clip_id, "title": title, "channel": "Outdoor Boys", "path": file_path})
                continue

            notify(f"🚀 Mengunggah ke TikTok: <b>{title}</b>...")
            caption = f"{title} #outdoorboys #bushcraft #survival #camping #alaska #fyp"
            success = uploader.upload_video(file_path, caption)
            if success:
                db.mark_clip_uploaded(clip_id)
                db.mark_uploaded(clip_id, title, "Outdoor Boys")
                uploaded_videos.append({"id": clip_id, "title": title, "channel": "Outdoor Boys", "path": file_path})

                remaining = db.get_queued_count()
                notify(
                    f"✅ <b>Sukses terunggah ke TikTok:</b> <i>{title}</i>\n"
                    f"📦 <i>Sisa antrean: {remaining} part cuplikan.</i>"
                )

                if upload_youtube:
                    notify("📺 Mengunggah ke YouTube Shorts...")
                    yt_desc = f"{title}\n\nOriginal by Outdoor Boys\n\n#Shorts #OutdoorBoys #Bushcraft #Survival"
                    yt_result = upload_shorts(
                        video_path=file_path,
                        title=title,
                        description=yt_desc,
                        tags=["Shorts", "OutdoorBoys", "Bushcraft", "Survival", "Alaska"],
                    )
                    if yt_result.get("success"):
                        yt_url = yt_result.get("url", "")
                        notify(f"✅ <b>Sukses terunggah ke YouTube Shorts:</b> <a href='{yt_url}'>{yt_result.get('title', title)}</a>")
                        uploaded_videos[-1]["youtube_url"] = yt_url
                    else:
                        yt_err = yt_result.get("error", "Unknown error")
                        notify(f"⚠️ YouTube Shorts upload gagal: {yt_err}")
            else:
                err = f"Upload TikTok gagal untuk {clip_id}"
                errors.append(err)

        db.close()
        return {
            "success": len(uploaded_videos) > 0,
            "uploaded": len(uploaded_videos),
            "found": len(uploaded_videos) + len(errors),
            "videos": uploaded_videos,
            "errors": errors,
        }

    if keyword_override:
        kw_clean = keyword_override.strip()
        # Clean formatting/noise words so they don't pollute YouTube search
        import re
        for noise in ["fullscreen", "full screen", "status", "whatsapp", "wa", "hd", "4k", "shorts", "#shorts"]:
            kw_clean = re.sub(rf"\b{re.escape(noise)}\b", "", kw_clean, flags=re.IGNORECASE).strip()
        kw_clean = re.sub(r"\s+", " ", kw_clean).strip()

        kw_lower = kw_clean.lower()
        search_candidates = []

        # Disambiguation & targeted search
        if "katana" in kw_lower and "sword" not in kw_lower and "pedang" not in kw_lower:
            search_candidates.append(f"{kw_clean} sword #shorts")
            search_candidates.append(f"{kw_clean} samurai #shorts")
            search_candidates.append(f"{kw_clean} sword")
        elif any(w in kw_lower for w in ["mobil", "car", "otomotif", "auto", "vehicle"]):
            search_candidates.extend([
                "WD Detailing abandoned car restoration barn find 4K",
                "classic car restoration timelapse 4K",
                f"{kw_clean} car restoration 4K",
                f"{kw_clean} 4K",
            ])
        elif any(w in kw_lower for w in ["pisau", "knife", "pedang", "blade", "golok", "cleaver"]):
            search_candidates.extend([
                "TysyTube rusty knife restoration satisfying 4K",
                "rusty knife restoration ASMR 4K",
                f"{kw_clean} restoration 4K",
                f"{kw_clean} 4K",
            ])
        elif any(w in kw_lower for w in ["pabrik", "factory", "industrial", "manufaktur", "produksi", "assembly"]):
            search_candidates.extend([
                "satisfying factory production line 4K",
                "satisfying industrial manufacturing process 4K",
                "satisfying automated factory machines 4K",
                "satisfying factory assembly line compilation 4K",
                f"{kw_clean} 4K satisfying",
            ])
        elif any(w in kw_lower for w in ["satisfying", "bersih", "cuci", "karpet", "rug", "wash", "asmr"]):
            search_candidates.extend([
                "satisfying factory production line 4K",
                "satisfying ASMR rug cleaning restoration 4K",
                "satisfying pressure washing deep cleaning 4K",
                "satisfying automated factory machines 4K",
                "Odd Tinkering antique restoration satisfying 4K",
                f"{kw_clean} satisfying 4K",
                f"satisfying {kw_clean} 4K",
            ])
        elif any(w in kw_lower for w in ["mancing", "ikan", "fish", "fishing"]):
            search_candidates.extend([
                "Outdoor Boys camping fishing 4K",
                f"Outdoor Boys {kw_clean} 4K",
                f"{kw_clean} fishing 4k outdoor",
                f"{kw_clean} 4K",
            ])
        else:
            search_candidates.append(f"{kw_clean} #shorts")
            search_candidates.append(kw_clean)

        keywords = search_candidates
    else:
        import random
        all_keywords = config.get("keywords", [])
        # Shuffle to vary niches across scheduled runs, pick 3 random keywords
        if len(all_keywords) > 3:
            keywords = random.sample(all_keywords, 3)
        else:
            keywords = list(all_keywords)
            random.shuffle(keywords)
    max_results = count if count is not None else config.get("videos_per_run", 1)
    max_duration = config.get("max_duration_seconds", 1800)
    clip_duration = config.get("clip_duration_seconds", 360)
    download_dir = config.get("download_dir", "data/tiktok/downloads")
    output_dir = config.get("output_dir", "data/tiktok/output")
    crop_w = config.get("crop_width", 1080)
    crop_h = config.get("crop_height", 1920)
    caption_tpl = config.get("caption_template", "{title} #satisfying #relaxing #diy #outdoor #fyp")

    Path(download_dir).mkdir(parents=True, exist_ok=True)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    videos = []
    seen_ids = set()
    # Fetch 3x the required count as buffer against download failures
    search_buffer = max((max_results or 1) * 3, 5)
    for kw in keywords:
        results = search_videos([kw], max_results=search_buffer, max_duration=max_duration)
        for v in results:
            if v["id"] not in seen_ids and not db.is_uploaded(v["id"]):
                seen_ids.add(v["id"])
                videos.append(v)
        if len(videos) >= search_buffer:
            break

    # Keep more candidates than strictly needed, so failed downloads can be retried with the next
    videos = videos[:search_buffer]
    if not videos:
        notify("⚠️ Tidak ditemukan kandidat video YouTube baru.")
        db.close()
        return {"success": False, "uploaded": 0, "found": 0, "videos": [], "errors": ["No candidate videos found"]}

    notify(f"🔍 Ditemukan {len(videos)} kandidat video HD. Memulai proses download & edit...")

    uploaded_videos = []
    errors = []

    for video in videos:
        # Stop once we've hit the target number of successful uploads
        if len(uploaded_videos) >= max_results:
            break

        vid = video["id"]
        title = video.get("title", "")
        channel = video.get("channel", "")
        url = video.get("webpage_url", "")

        notify(f"📥 Mengunduh: <b>{title[:50]}</b> ({channel})...")
        raw_path = download_video(url, download_dir)
        if not raw_path:
            err = f"Download gagal untuk video {vid}"
            logger.error(err)
            errors.append(err)
            continue

        # Skip intro to core action
        start_time, action_ch = get_action_start_time(
            video.get("chapters"),
            float(video.get("duration", 0)),
            clip_duration=clip_duration,
        )
        if start_time > 0:
            notify(f"⏩ Melompati intro ke detik {start_time:.0f} (Bab: {action_ch or 'Aksi Utama'})...")

        # Edit to 9:16 portrait
        mode_label = "Center Crop (Full HP 9:16)" if fill_mode else "Blurred Pillarbox (9:16 Lanczos)"
        notify(f"✂️ Merender {mode_label} + Unsharp (Durasi: {clip_duration}s)...")
        edited_path = str(Path(output_dir) / f"{vid}_edited.mp4")
        try:
            process_video(
                raw_path,
                edited_path,
                max_duration=clip_duration,
                crop_width=crop_w,
                crop_height=crop_h,
                overlay_text=title,
                start_time=start_time,
                fill_mode=fill_mode,
            )
        except Exception as exc:
            err = f"Render FFmpeg gagal: {exc}"
            logger.error(err)
            errors.append(err)
            _safe_remove(raw_path)
            continue

        if dry_run:
            notify(f"🧪 [DRY RUN] Video siap dirender di: <code>{edited_path}</code> (Upload dilewati).")
            uploaded_videos.append({"id": vid, "title": title, "channel": channel, "path": edited_path})
            _safe_remove(raw_path)
            continue

        # Upload via isolated Playwright subprocess
        notify("🚀 Mengunggah ke TikTok via Playwright browser...")
        caption = caption_tpl.format(title=title)
        success = uploader.upload_video(edited_path, caption)
        if success:
            db.mark_uploaded(vid, title, channel)
            uploaded_videos.append({"id": vid, "title": title, "channel": channel, "path": edited_path})
            notify(f"✅ <b>Sukses terunggah ke TikTok:</b> <i>{title}</i>")

            # Upload to YouTube Shorts
            if upload_youtube:
                notify("📺 Mengunggah ke YouTube Shorts...")
                yt_desc = f"{title}\n\nOriginal by {channel}\n\n#Shorts #Viral #Satisfying"
                yt_tags = ["Shorts", "Viral", "Satisfying"]
                if channel:
                    yt_tags.append(channel)
                yt_result = upload_shorts(
                    video_path=edited_path,
                    title=title,
                    description=yt_desc,
                    tags=yt_tags,
                )
                if yt_result.get("success"):
                    yt_url = yt_result.get("url", "")
                    notify(f"✅ <b>Sukses terunggah ke YouTube Shorts:</b> <a href='{yt_url}'>{yt_result.get('title', title)}</a>")
                    uploaded_videos[-1]["youtube_url"] = yt_url
                    uploaded_videos[-1]["youtube_id"] = yt_result.get("video_id")
                else:
                    yt_err = yt_result.get("error", "Unknown error")
                    notify(f"⚠️ YouTube Shorts upload gagal: {yt_err}")
                    errors.append(f"YouTube upload gagal: {yt_err}")
        else:
            err = f"Upload TikTok gagal untuk {vid}"
            errors.append(err)

        _safe_remove(raw_path)

    db.log_run(
        found=len(videos),
        uploaded=len(uploaded_videos),
        skipped=len(videos) - len(uploaded_videos),
        errors="; ".join(errors),
    )
    db.close()

    return {
        "success": len(uploaded_videos) > 0,
        "uploaded": len(uploaded_videos),
        "found": len(videos),
        "videos": uploaded_videos,
        "errors": errors,
    }


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
