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


def download_asset_video(url: str, output_dir: str = "data/clippo/downloads") -> Optional[str]:
    """Download video from YouTube, TikTok, Instagram Reels, or direct URL via yt-dlp."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if not url:
        return None

    clean_url = url.strip()

    import sys
    # YouTube, TikTok, Instagram all supported by yt-dlp
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
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
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            # Find newest mp4 in output_dir
            files = list(Path(output_dir).glob("*.mp4"))
            if files:
                newest = max(files, key=lambda f: f.stat().st_mtime)
                logger.info("Successfully downloaded: %s", newest)
                return str(newest)
        else:
            logger.warning("yt-dlp failed (code %d): %s", res.returncode, res.stderr[:200])
    except Exception as e:
        logger.error("Download exception for %s: %s", clean_url, e)

    return None
