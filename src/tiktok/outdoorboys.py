"""outdoorboys.py — Ingest Outdoor Boys long-form videos and slice into high-retention multi-part vertical clips."""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional

from src.tiktok.editor import process_video
from src.tiktok.state import VideoStateDB
from src.tiktok.youtube import _ytdlp_base, _find_node

logger = logging.getLogger("tiktok.outdoorboys")

OUTDOOR_BOYS_CHANNEL = "https://www.youtube.com/@OutdoorBoys/videos"

INTRO_SKIP_KEYWORDS = [
    "intro", "introduction", "arrival", "arrive", "leave", "leaving",
    "conclusion", "overview", "prep", "packing", "registration", "trailer",
    "sponsor", "flight", "airport", "ferry", "drive", "road trip",
]

ACTION_PRIORITY_KEYWORDS = [
    "shelter", "camp", "build", "forge", "hunt", "walrus", "bear",
    "fish", "cooking", "snow", "cave", "fire", "skull", "dugout",
    "log", "survival", "hazard", "tusk", "meat", "blizzard", "storm",
    "catch", "ice", "wood", "river", "tent", "cabin", "axe", "saw",
    "steak", "bread", "winter", "subzero", "night", "trap",
]


def fetch_channel_videos(channel_url: str = OUTDOOR_BOYS_CHANNEL, limit: int = 8) -> list[dict]:
    """Fetch the latest full videos from Outdoor Boys YouTube channel."""
    cmd = [
        *_ytdlp_base(),
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        "--quiet",
        channel_url,
        "--playlist-items",
        f"1:{limit}",
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            logger.error("Failed to fetch Outdoor Boys channel: %s", res.stderr)
            return []
    except Exception as exc:
        logger.error("Timeout/error fetching Outdoor Boys channel: %s", exc)
        return []

    videos = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            duration = d.get("duration") or 0
            # Outdoor Boys full videos are typically >= 15 minutes (900 seconds)
            # Shorts (< 180s) are ignored in favor of long episodic content
            if duration >= 600 or not duration:
                videos.append({
                    "id": d.get("id"),
                    "title": d.get("title", ""),
                    "duration": duration,
                    "url": d.get("url") or f"https://www.youtube.com/watch?v={d.get('id')}",
                    "channel": "Outdoor Boys",
                })
        except Exception:
            continue

    return videos


def fetch_video_metadata(url_or_id: str) -> dict:
    """Fetch full metadata including chapters for an Outdoor Boys video."""
    url = url_or_id if url_or_id.startswith("http") else f"https://www.youtube.com/watch?v={url_or_id}"
    cmd = [
        *_ytdlp_base(),
        "--dump-json",
        "--no-warnings",
        "--quiet",
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout.strip().splitlines()[0])
    except Exception as exc:
        logger.error("Error fetching metadata for %s: %s", url_or_id, exc)
    return {}


def select_action_chapters(
    chapters: list[dict],
    total_duration: float,
    max_clips: int = 4,
    clip_duration: float = 75.0,
) -> list[dict]:
    """Select the best non-intro action chapters from YouTube metadata."""
    valid = []
    for ch in chapters:
        title = ch.get("title", "").strip()
        start = float(ch.get("start_time", 0))
        end = float(ch.get("end_time", total_duration))
        dur = max(0.0, end - start)
        t_lower = title.lower()

        # Skip intro / travel / conclusion
        if any(w in t_lower for w in INTRO_SKIP_KEYWORDS):
            continue

        # Score priority
        score = sum(2 for w in ACTION_PRIORITY_KEYWORDS if w in t_lower)
        if dur >= 30.0:
            score += 1

        valid.append({
            "title": title,
            "start": start,
            "end": end,
            "duration": dur,
            "score": score,
        })

    # Sort by priority score descending, then chronologically
    valid.sort(key=lambda x: (-x["score"], x["start"]))

    chosen = valid[:max_clips]
    # Re-sort chosen chapters chronologically so Part 1, 2, 3 follow the real story
    chosen.sort(key=lambda x: x["start"])

    # Fallback if no valid chapters exist
    if not chosen and total_duration > 180:
        step = (total_duration * 0.7) / max_clips
        for i in range(max_clips):
            s = (total_duration * 0.15) + (i * step)
            chosen.append({
                "title": f"Alaska Wilderness Action {i+1}",
                "start": s,
                "end": s + clip_duration,
                "duration": clip_duration,
                "score": 0,
            })

    return chosen


def clean_chapter_title(title: str, max_chars: int = 24) -> str:
    """Clean chapter title for clear TikTok top-banner display."""
    clean = re.sub(r"[^\w\s\-,!?']", "", title)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].rsplit(" ", 1)[0]
    return clean.title()


def download_full_720p(url: str, download_dir: str) -> Optional[str]:
    """Download single 720p copy for fast local slicing (shared implementation)."""
    from src.tiktok.youtube import download_video_720p
    return download_video_720p(url, download_dir)


def generate_outdoorboys_clips(
    video_dict: dict,
    db: VideoStateDB,
    queue_dir: str = "data/tiktok/queue",
    download_dir: str = "data/tiktok/downloads",
    max_clips: int = 4,
    clip_duration: float = 75.0,
    fill_mode: bool = True,
    notify_fn: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """Download 1 Outdoor Boys video, cut multiple vertical highlight clips, and enqueue them."""
    def notify(msg: str):
        if notify_fn:
            notify_fn(msg)
        logger.info(msg)

    vid = video_dict.get("id")
    title = video_dict.get("title", "Outdoor Boys Alaska Survival")
    url = video_dict.get("url") or f"https://www.youtube.com/watch?v={vid}"

    notify(f"🎣 <b>Outdoor Boys Pipeline:</b> Mengambil metadata video...\n<i>{title}</i>")
    meta = fetch_video_metadata(url)
    if not meta:
        notify("❌ Gagal mengambil metadata video Outdoor Boys.")
        return []

    chapters = meta.get("chapters") or []
    total_dur = float(meta.get("duration") or video_dict.get("duration") or 1800)
    selected_chapters = select_action_chapters(chapters, total_dur, max_clips=max_clips, clip_duration=clip_duration)

    if not selected_chapters:
        notify("⚠️ Tidak ditemukan babak aksi yang cocok untuk dipotong.")
        return []

    notify(f"📥 Mengunduh video master 720p (Ditemukan {len(selected_chapters)} babak aksi)...")
    raw_path = download_full_720p(url, download_dir)
    if not raw_path or not os.path.exists(raw_path):
        notify("❌ Gagal mengunduh video master Outdoor Boys.")
        return []

    os.makedirs(queue_dir, exist_ok=True)
    generated = []
    total_parts = len(selected_chapters)

    for i, ch in enumerate(selected_chapters, start=1):
        ch_title = clean_chapter_title(ch["title"])
        clip_id = f"{vid}_part{i}"
        out_filename = f"{vid}_part{i}.mp4"
        out_path = os.path.join(queue_dir, out_filename)

        start_sec = max(0.0, ch["start"])
        # Slight trim offset if chapter is long enough
        if ch["duration"] > clip_duration + 10:
            start_sec += 5.0

        overlay_header = f"OUTDOOR BOYS\n{ch_title} [Part {i}/{total_parts}]"
        notify(f"✂️ Merender <b>Part {i}/{total_parts}</b>: <i>{ch_title}</i> ({clip_duration:.0f}s)...")

        try:
            process_video(
                input_path=raw_path,
                output_path=out_path,
                max_duration=int(clip_duration),
                crop_width=1080,
                crop_height=1920,
                overlay_text=overlay_header,
                start_time=start_sec,
                fill_mode=fill_mode,
                auto_subtitles=True,
            )

            # Enqueue into DB
            full_clip_title = f"Outdoor Boys: {ch_title} (Part {i}/{total_parts})"
            db.enqueue_clip(
                clip_id=clip_id,
                video_id=vid,
                part_num=i,
                total_parts=total_parts,
                title=full_clip_title,
                chapter_title=ch_title,
                file_path=out_path,
            )
            generated.append({
                "clip_id": clip_id,
                "video_id": vid,
                "part_num": i,
                "total_parts": total_parts,
                "title": full_clip_title,
                "file_path": out_path,
            })
        except Exception as exc:
            logger.error("Failed to render part %d: %s", i, exc)
            notify(f"⚠️ Gagal render Part {i}: {exc}")

    # Remove the large raw master file to preserve disk space
    try:
        if os.path.exists(raw_path):
            os.remove(raw_path)
    except Exception:
        pass

    notify(f"🎉 <b>Batch Selesai:</b> Berhasil merender {len(generated)}/{total_parts} cuplikan Outdoor Boys ke dalam antrean!")
    return generated


def replenish_outdoorboys_queue(
    db: VideoStateDB,
    config: dict,
    notify_fn: Optional[Callable[[str], None]] = None,
) -> int:
    """Ensure queue has pending Outdoor Boys clips. Generates new batch if empty."""
    pending_count = db.get_queued_count()
    if pending_count > 0:
        return pending_count

    ob_cfg = config.get("outdoorboys", {})
    channel_url = ob_cfg.get("channel_url", OUTDOOR_BOYS_CHANNEL)
    clips_per_vid = ob_cfg.get("clips_per_video", 4)
    clip_dur = ob_cfg.get("clip_duration_seconds", 75.0)
    fill_mode = ob_cfg.get("fill_mode", True)
    queue_dir = config.get("queue_dir", "data/tiktok/queue")
    download_dir = config.get("download_dir", "data/tiktok/downloads")

    if notify_fn:
        notify_fn("🔍 Antrean cuplikan Outdoor Boys kosong. Mencari video baru dari channel...")

    videos = fetch_channel_videos(channel_url, limit=10)
    if not videos:
        logger.warning("No Outdoor Boys videos retrieved from channel.")
        return 0

    target_video = None
    for v in videos:
        vid = v.get("id")
        if vid and not db.is_video_processed(vid):
            target_video = v
            break

    if not target_video:
        logger.info("All latest Outdoor Boys videos already processed, choosing oldest candidate.")
        target_video = videos[0]

    generated = generate_outdoorboys_clips(
        video_dict=target_video,
        db=db,
        queue_dir=queue_dir,
        download_dir=download_dir,
        max_clips=clips_per_vid,
        clip_duration=clip_dur,
        fill_mode=fill_mode,
        notify_fn=notify_fn,
    )

    return len(generated)
