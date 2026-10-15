"""source_clipper.py — One long source video -> N vertical hook clips -> direct upload.

Flow: metadata -> 720p download -> ONE Whisper pass -> LLM hook windows ->
      per-window render (hook title 10s + subtitles sliced from that same
      transcript) -> immediate upload to TikTok & YouTube Shorts.
"""

import logging
import os
from typing import Callable, Dict, List, Optional

from src.tiktok.editor import process_video
from src.tiktok.hook_finder import find_hook_windows
from src.tiktok.state import VideoStateDB
from src.tiktok.transcript import slice_segments, transcribe_source
from src.tiktok.youtube import download_video_720p, fetch_source_metadata

logger = logging.getLogger("tiktok.source_clipper")

DEFAULT_CLIP_COUNT = 3
DEFAULT_CLIP_SECONDS = 75


def _noop(_msg: str) -> None:
    return None


def _default_tiktok_upload(path: str, caption: str) -> bool:
    from src.tiktok.config import load_config
    from src.tiktok.uploader import TikTokUploader
    cfg = load_config()
    uploader = TikTokUploader(cfg.get("tiktok_cookies_path", "data/tiktok/cookies.txt"))
    return bool(uploader.upload_video(path, caption))


def _default_youtube_upload(path: str, title: str, description: str, tags: List[str]) -> Dict:
    from src.tiktok.youtube_uploader import upload_shorts
    return upload_shorts(video_path=path, title=title, description=description, tags=tags)


def run_source_pipeline(
    url: str,
    count: int = DEFAULT_CLIP_COUNT,
    db: Optional[VideoStateDB] = None,
    config: Optional[dict] = None,
    fill_mode: bool = True,
    hook_duration: float = 10.0,
    language: Optional[str] = None,
    upload_tiktok_fn: Optional[Callable[[str, str], bool]] = None,
    upload_youtube_fn: Optional[Callable[[str, str, str, List[str]], Dict]] = None,
    notify_fn: Optional[Callable[[str], None]] = None,
) -> dict:
    """Turn one long source URL into N vertical hook clips and upload them immediately."""
    config = config or {}
    notify = notify_fn or _noop
    upload_tiktok = upload_tiktok_fn or _default_tiktok_upload
    upload_youtube = upload_youtube_fn or _default_youtube_upload
    owns_db = db is None

    if db is None:
        db = VideoStateDB(config.get("db_path", "data/tiktok/state.db"))

    sc_cfg = config.get("source_clip", {})
    queue_dir = config.get("queue_dir", "data/tiktok/queue")
    download_dir = config.get("download_dir", "data/tiktok/downloads")
    clip_seconds = int(sc_cfg.get("clip_duration_seconds", DEFAULT_CLIP_SECONDS))
    model_size = sc_cfg.get("whisper_model", "base")

    try:
        # 1. Metadata
        notify("🔎 <i>Mengambil metadata video sumber...</i>")
        meta = fetch_source_metadata(url)
        if not meta or not meta.get("id"):
            return {"success": False, "error": "Gagal mengambil metadata video sumber.",
                    "clips_enqueued": 0, "clips_uploaded": 0}

        source_id = meta["id"]
        title = meta.get("title", "") or source_id
        duration = float(meta.get("duration") or 0)
        notify(f"📋 <b>Sumber:</b> <i>{title[:70]}</i> ({duration / 60:.0f} menit)")

        # 2. Idempotency
        if db.is_video_processed(source_id):
            return {"success": False,
                    "error": f"Sumber {source_id} sudah pernah dipotong sebelumnya.",
                    "clips_enqueued": 0, "clips_uploaded": 0, "source_id": source_id}

        # 3. Download
        notify("📥 <i>Mengunduh video sumber (720p untuk pemotongan cepat)...</i>")
        raw_path = download_video_720p(url, download_dir)
        if not raw_path or not os.path.exists(raw_path):
            return {"success": False, "error": "Download video sumber gagal.",
                    "clips_enqueued": 0, "clips_uploaded": 0}

        # 4. THE single transcription — feeds both hooks and subtitles
        notify("🎙️ <i>Mentranskripsi video (bahasa dideteksi otomatis)...</i>")
        tr = transcribe_source(raw_path, language=language, model_size=model_size)
        segments = tr.get("segments") or []
        if not segments:
            return {"success": False,
                    "error": "Transkrip gagal / tidak ada suara yang terdeteksi.",
                    "clips_enqueued": 0, "clips_uploaded": 0}

        used_lang = tr.get("language")
        if duration <= 0:
            duration = tr.get("duration") or 0
        notify(f"📝 <i>Transkrip siap: {len(segments)} segmen "
               f"(bahasa terdeteksi: <b>{used_lang or 'auto'}</b>)</i>")

        # 5. Hook detection over that same transcript
        notify("🎯 <i>Mencari momen hook terbaik dari transkrip...</i>")
        hooks = find_hook_windows(
            segments=segments,
            total_duration=duration,
            count=count,
            source_title=title,
            source_language=used_lang,
        )
        if not hooks:
            return {"success": False, "error": "Tidak ada hook yang bisa dipakai dari transkrip ini.",
                    "clips_enqueued": 0, "clips_uploaded": 0}

        notify(f"✂️ <i>Ditemukan {len(hooks)} hook. Merender lalu langsung upload...</i>")

        # 6. Render + upload each clip
        os.makedirs(queue_dir, exist_ok=True)
        total_parts = len(hooks)
        clips: List[Dict] = []
        uploaded_count = 0
        errors: List[str] = []

        for i, hook in enumerate(hooks, start=1):
            hook_title = hook["hook_title"]
            clip_id = f"{source_id}_part{i}"
            out_path = os.path.join(queue_dir, f"{clip_id}.mp4")
            overlay = f"{hook_title} [{i}/{total_parts}]"

            # Subtitles for THIS window, from the same transcript, rebased to clip time
            window_segments = slice_segments(segments, hook["start"], hook["end"])
            if not window_segments:
                logger.warning("No transcript segments inside window %s-%s", hook["start"], hook["end"])

            notify(f"✂️ Merender <b>Part {i}/{total_parts}</b>: <i>{hook_title}</i> ({hook['duration']:.0f}s)")

            try:
                process_video(
                    input_path=raw_path,
                    output_path=out_path,
                    max_duration=min(int(hook["duration"]), clip_seconds * 2),
                    crop_width=config.get("crop_width", 1080),
                    crop_height=config.get("crop_height", 1920),
                    overlay_text=overlay,
                    start_time=float(hook["start"]),
                    fill_mode=fill_mode,
                    auto_subtitles=True,
                    hook_duration=hook_duration,
                    subtitle_language=used_lang,
                    subtitle_model_size=model_size,
                    subtitle_segments=window_segments,
                )
            except Exception as exc:
                msg = f"Render Part {i} gagal: {exc}"
                logger.error(msg)
                errors.append(msg)
                notify(f"⚠️ {msg}")
                continue

            clip_title = f"{hook_title} (Part {i}/{total_parts})"
            db.enqueue_clip(
                clip_id=clip_id,
                video_id=source_id,
                part_num=i,
                total_parts=total_parts,
                title=clip_title,
                chapter_title=hook_title,
                file_path=out_path,
            )

            tt_ok = False
            yt_res: Dict = {}
            try:
                notify(f"🚀 Upload TikTok <b>Part {i}/{total_parts}</b>...")
                tt_ok = bool(upload_tiktok(out_path, f"{clip_title} #shorts #fyp #viral"))
            except Exception as exc:
                errors.append(f"TikTok Part {i}: {exc}")
                logger.error("TikTok upload failed for part %d: %s", i, exc)

            try:
                notify(f"📺 Upload YouTube Shorts <b>Part {i}/{total_parts}</b>...")
                yt_res = upload_youtube(
                    out_path,
                    clip_title,
                    f"{clip_title}\n\nOriginal by "
                    f"{meta.get('uploader') or meta.get('channel') or 'source'}\n\n#Shorts",
                    ["Shorts", "Viral", "FYP"],
                ) or {}
            except Exception as exc:
                errors.append(f"YouTube Part {i}: {exc}")
                logger.error("YouTube upload failed for part %d: %s", i, exc)

            yt_ok = bool(yt_res.get("success"))
            if not tt_ok:
                errors.append(f"TikTok Part {i} gagal")
            if not yt_ok:
                errors.append(f"YouTube Part {i} gagal: {yt_res.get('error', 'unknown')}")

            if tt_ok or yt_ok:
                uploaded_count += 1

            clips.append({
                "clip_id": clip_id,
                "source_id": source_id,
                "part_num": i,
                "total_parts": total_parts,
                "title": clip_title,
                "hook_title": hook_title,
                "start": hook["start"],
                "end": hook["end"],
                "duration": hook["duration"],
                "subtitle_lines": len(window_segments),
                "file_path": out_path,
                "tiktok_ok": tt_ok,
                "youtube_ok": yt_ok,
                "youtube_url": yt_res.get("url", ""),
            })

            # Direct-upload pipeline: a clip that actually went out is 'uploaded', so the
            # queue stops advertising it as ready. A clip that failed both platforms stays
            # 'pending' on purpose — the reminder then surfaces it for a /upload retry.
            if tt_ok or yt_ok:
                db.mark_clip_uploaded(clip_id)
            db.mark_uploaded(clip_id, clip_title, meta.get("uploader") or "source")

        # 7. Cleanup the master
        try:
            if raw_path and os.path.exists(raw_path):
                os.remove(raw_path)
        except OSError:
            pass

        return {
            "success": uploaded_count > 0,
            "source_id": source_id,
            "title": title,
            "language": used_lang,
            "transcript_segments": len(segments),
            "clips_enqueued": len(clips),
            "clips_uploaded": uploaded_count,
            "clips": clips,
            "errors": errors,
            "error": "" if uploaded_count else "Semua upload gagal.",
        }
    finally:
        if owns_db:
            db.close()
