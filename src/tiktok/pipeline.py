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

    keywords = [keyword_override] if keyword_override else config.get("keywords", [])
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
    for kw in keywords:
        results = search_videos([kw], max_results=max_results, max_duration=max_duration)
        for v in results:
            if v["id"] not in seen_ids and not db.is_uploaded(v["id"]):
                seen_ids.add(v["id"])
                videos.append(v)
        if len(videos) >= max_results:
            break

    videos = videos[:max_results]
    if not videos:
        notify("⚠️ Tidak ditemukan kandidat video YouTube baru.")
        db.close()
        return {"success": False, "uploaded": 0, "found": 0, "videos": [], "errors": ["No candidate videos found"]}

    notify(f"🔍 Ditemukan {len(videos)} kandidat video HD. Memulai proses download & edit...")

    uploaded_videos = []
    errors = []

    for video in videos:
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
        notify(f"✂️ Merender 9:16 Lanczos + Unsharp (Durasi: {clip_duration}s)...")
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
