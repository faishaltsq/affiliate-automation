"""youtube.py — YouTube search and download via yt-dlp."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_COOKIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "tiktok" / "youtube_cookies.txt"

_YTDLP = [sys.executable, "-m", "yt_dlp"]

def _find_node() -> str:
    """Find node executable: Hermes bundled first, then PATH."""
    hermes_node = Path(os.environ.get("HERMES_HOME", str(Path.home() / "AppData/Local/hermes")))
    candidates = list(hermes_node.parent.glob("tools/node-*/node.exe")) + \
                 list(hermes_node.parent.glob("tools/node*/node.exe"))
    if candidates:
        return str(sorted(candidates)[-1])  # latest version
    return "node"  # fallback to PATH


def _ytdlp_base() -> list[str]:
    """Build base yt-dlp command with cookies and JS runtime if available."""
    cmd = list(_YTDLP)
    node_path = _find_node()
    cmd.extend(["--js-runtimes", f"node:{node_path}"])
    if _COOKIES_PATH.exists():
        cmd.extend(["--cookies", str(_COOKIES_PATH)])
    return cmd


ACTION_KEYWORDS = [
    "fish", "catch", "hunt", "build", "craft", "restor", "make", "cook",
    "survival", "shelter", "wood", "camp", "asmr", "satisfying", "cut",
    "knife", "brick", "wall", "forag", "lake", "river", "sea", "boat",
    "joinery", "lathe", "repair", "tasty", "harvest",
]

INTRO_KEYWORDS = [
    "intro", "begin", "start", "adventure begin", "ferry", "travel",
    "driv", "head", "pack", "prep", "arriv", "prologue", "overview",
    "plan", "sponsor", "road", "flight", "fly", "airport",
]


def get_action_start_time(
    chapters: list | None, total_duration: float, clip_duration: float = 360
) -> tuple[float, str | None]:
    """Find the optimal start time to skip intros and jump straight to core action.

    Returns (start_time_seconds, chapter_title_or_None).
    """
    if chapters:
        # 1. Search for matching action chapters
        for ch in chapters:
            title = ch.get("title", "").lower()
            if any(ik in title for ik in INTRO_KEYWORDS):
                continue
            if any(ak in title for ak in ACTION_KEYWORDS):
                start = float(ch.get("start_time", 0))
                if start + 30 <= total_duration:
                    return start, ch.get("title")

        # 2. If no explicit action keyword, pick first non-intro chapter
        for ch in chapters:
            title = ch.get("title", "").lower()
            if not any(ik in title for ik in INTRO_KEYWORDS):
                start = float(ch.get("start_time", 0))
                if start > 0 and start + 30 <= total_duration:
                    return start, ch.get("title")

    # Fallback when no chapters: skip typical intro
    if total_duration > 300:
        return min(120.0, total_duration * 0.15), None
    elif total_duration > 120:
        return min(45.0, total_duration * 0.10), None
    return 0.0, None


def search_videos(
    keywords: list[str],
    max_results: int = 5,
    max_duration: int = 1800,
    prefer_vertical: bool = True,
) -> list[dict]:
    """Search YouTube for videos matching keywords.

    Returns a deduplicated list of dicts:
        {"id": str, "title": str, "channel": str, "duration": int, "webpage_url": str}

    When prefer_vertical=True (default), Shorts-style queries are used and
    videos with portrait aspect ratio are prioritised. Landscape videos are
    deprioritised (moved to end of list) so the pipeline almost always gets
    ready-to-use vertical footage without heavy FFmpeg cropping.
    Videos longer than max_duration seconds are excluded.
    """
    query = " ".join(keywords)
    # Force Shorts-style search: YouTube returns vertical Shorts for these queries
    if prefer_vertical and "#shorts" not in query.lower():
        query = query + " #shorts"
    search_url = f"ytsearch{max_results * 5}:{query}"  # over-fetch to survive duration filter

    cmd = [
        *_ytdlp_base(),
        "--dump-json",
        "--flat-playlist",
        "--no-warnings",
        "--quiet",
        search_url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp search timed out for query: %s", query)
        return []
    except Exception as exc:
        logger.error("yt-dlp search failed: %s", exc)
        return []

    seen_ids: set[str] = set()
    videos: list[dict] = []

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            info = json.loads(line)
        except json.JSONDecodeError:
            continue

        video_id = info.get("id") or info.get("url", "")
        if not video_id or video_id in seen_ids:
            continue

        duration = info.get("duration") or 0
        if duration and duration > max_duration:
            continue

        seen_ids.add(video_id)
        w = info.get("width") or 0
        h = info.get("height") or 0
        is_vert = (h > w) if (w > 0 and h > 0) else False

        videos.append(
            {
                "id": video_id,
                "title": info.get("title", ""),
                "channel": info.get("channel") or info.get("uploader") or "",
                "duration": int(duration) if duration else 0,
                "chapters": info.get("chapters") or [],
                "is_vertical": is_vert,
                "webpage_url": info.get("webpage_url")
                or info.get("url")
                or f"https://www.youtube.com/watch?v={video_id}",
            }
        )

        if len(videos) >= max_results * 3:
            break

    # Prioritise vertical (portrait) videos over landscape
    if prefer_vertical:
        videos.sort(key=lambda v: (not v.get("is_vertical", False),))

    return videos


def download_video(url: str, output_dir: str) -> str | None:
    """Download a YouTube video to output_dir.

    Caps stream to 1080p for ultra-fast download and efficient FFmpeg rendering.
    Returns the absolute path of the downloaded file, or None on failure.
    """
    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    outtmpl = str(out_dir / "%(id)s.%(ext)s")

    cmd = [
        *_ytdlp_base(),
        "--format", "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[height>=1080]/best",
        "--merge-output-format", "mp4",
        "--output", outtmpl,
        "--no-warnings",
        "--quiet",
        url,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp download timed out for URL: %s", url)
        return None
    except Exception as exc:
        logger.error("yt-dlp download failed: %s", exc)
        return None

    if result.returncode != 0:
        logger.error("yt-dlp exited %d for %s:\n%s", result.returncode, url, result.stderr)
        return None

    video_id = _extract_video_id(url)
    if video_id:
        candidate = out_dir / f"{video_id}.mp4"
        if candidate.exists():
            return str(candidate)

    mp4_files = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp4_files:
        return str(mp4_files[0])

    logger.error("Download appeared to succeed but no mp4 found in %s", out_dir)
    return None


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from a URL."""
    import re
    match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})(?:[&?]|$)", url)
    return match.group(1) if match else None
