"""downloader.py — Download video assets from YouTube, TikTok, Reels, or Drive for Clippo campaigns."""

import os
import re
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipper.downloader")

YTDLP_COOKIES = "data/tiktok/youtube_cookies.txt"


def _find_node() -> str:
    """Find node executable: Hermes bundled first, then PATH."""
    hermes_node = Path(os.environ.get("HERMES_HOME", str(Path.home() / "AppData/Local/hermes")))
    candidates = list(hermes_node.parent.glob("tools/node-*/node.exe")) + \
                 list(hermes_node.parent.glob("tools/node*/node.exe"))
    if candidates:
        return str(sorted(candidates)[-1])
    return "node"


def _download_tiktok_tikwm(url: str, output_dir: str) -> Optional[str]:
    """Fallback download for TikTok videos using TikWM API (bypasses bot wall & audio-only bug)."""
    try:
        import requests
        r = requests.post("https://www.tikwm.com/api/", data={"url": url}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if data.get("code") == 0:
                play_url = data["data"].get("play") or data["data"].get("hdplay")
                video_id = data["data"].get("id", "tiktok_video")
                if play_url:
                    out_path = Path(output_dir) / f"{video_id}.mp4"
                    logger.info("Downloading TikTok video via TikWM CDN: %s", video_id)
                    with requests.get(play_url, stream=True, timeout=60) as resp:
                        resp.raise_for_status()
                        with open(out_path, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=8192):
                                f.write(chunk)
                    if out_path.exists() and out_path.stat().st_size > 100000:
                        logger.info("Successfully downloaded via TikWM: %s (%d bytes)", out_path, out_path.stat().st_size)
                        return str(out_path)
    except Exception as e:
        logger.warning("TikWM fallback failed for %s: %s", url, e)
    return None


def download_asset_video(url: str, output_dir: str = "data/clippo/downloads") -> Optional[str]:
    """Download video from YouTube, TikTok, Instagram Reels, or direct URL via yt-dlp."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if not url:
        return None

    clean_url = url.strip()

    # If TikTok link, try TikWM first (more reliable for TikTok CDN & no watermark)
    if any(k in clean_url for k in ["tiktok.com", "vt.tiktok.com"]):
        twm_result = _download_tiktok_tikwm(clean_url, output_dir)
        if twm_result:
            return twm_result

    import sys
    # YouTube, TikTok, Instagram all supported by yt-dlp
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", os.path.join(output_dir, "%(id)s.%(ext)s"),
        "--no-warnings",
        "--quiet",
    ]

    # Inject YouTube cookies if YouTube URL
    if any(k in clean_url for k in ["youtube.com", "youtu.be"]) and os.path.exists(YTDLP_COOKIES):
        node_path = _find_node()
        cmd.extend([
            "--cookies", YTDLP_COOKIES,
            "--js-runtimes", f"node:{node_path}",
        ])

    cmd.append(clean_url)

    try:
        logger.info("Downloading asset: %s", clean_url)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode == 0:
            # Find the file that was actually just written (any video format)
            # yt-dlp uses %(id)s.%(ext)s — find newest file regardless of ext
            all_files = [
                f for f in Path(output_dir).iterdir()
                if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")
            ]
            if all_files:
                newest = max(all_files, key=lambda f: f.stat().st_mtime)
                # If not mp4, remux to mp4 via ffmpeg
                if newest.suffix.lower() != ".mp4":
                    mp4_path = newest.with_suffix(".mp4")
                    remux = subprocess.run(
                        ["ffmpeg", "-y", "-i", str(newest), "-c", "copy", str(mp4_path)],
                        capture_output=True, timeout=120
                    )
                    if remux.returncode == 0:
                        newest.unlink(missing_ok=True)
                        newest = mp4_path
                logger.info("Successfully downloaded: %s", newest)
                return str(newest)
            else:
                logger.warning("yt-dlp exited 0 but no video file found in %s", output_dir)
        else:
            logger.warning("yt-dlp failed (code %d): %s", res.returncode, res.stderr[:300])
    except Exception as e:
        logger.error("Download exception for %s: %s", clean_url, e)

    return None
