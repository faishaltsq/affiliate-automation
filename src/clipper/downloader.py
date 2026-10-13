"""downloader.py — Download video assets from YouTube, TikTok, Reels, or Drive for Clippo campaigns."""

import os
import re
import logging
import zipfile
import subprocess
import requests
from pathlib import Path
from typing import Optional, List
import urllib3

# Suppress insecure HTTPS request warnings when connecting to servers with untrusted certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("clipper.downloader")

YTDLP_COOKIES = "data/tiktok/youtube_cookies.txt"


def _natural_sort_key(s: str) -> list:
    """Sort strings containing numbers naturally (e.g. part 2 before part 10)."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', str(s))]


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


def _download_cloud_or_archive(url: str, output_dir: str) -> List[str]:
    """
    Download cloud shares (Nextcloud/ownCloud/direct zip) with SSL verify fallback,
    uncompress archives, and return all discovered video file paths.
    """
    clean_url = url.strip()
    download_url = clean_url

    # Nextcloud public share format: .../index.php/s/<token> -> .../index.php/s/<token>/download
    if "/index.php/s/" in clean_url and not clean_url.rstrip("/").endswith("/download"):
        download_url = clean_url.rstrip("/") + "/download"

    logger.info("Attempting direct cloud/archive download from: %s", download_url)

    try:
        with requests.get(download_url, stream=True, verify=False, timeout=300) as resp:
            if resp.status_code not in (200, 206):
                logger.warning("Direct download HTTP %d for %s", resp.status_code, download_url)
                return []

            cd = resp.headers.get("content-disposition", "")
            filename = None
            if "filename=" in cd:
                m = re.search(r'filename=["\']?([^"\';]+)["\']?', cd)
                if m:
                    filename = m.group(1).strip()

            content_type = resp.headers.get("content-type", "").lower()
            is_zip_hint = (
                "zip" in content_type or
                (filename and filename.lower().endswith(".zip")) or
                clean_url.lower().endswith(".zip")
            )

            chunk_iter = resp.iter_content(chunk_size=65536)
            first_chunk = next(chunk_iter, b"")
            if not first_chunk:
                return []

            is_zip = is_zip_hint or first_chunk.startswith(b"PK\x03\x04")

            token_id = re.sub(r'[^a-zA-Z0-9_-]', '_', Path(clean_url).name or "cloud_asset")[:40]

            if is_zip:
                zip_filename = filename or f"{token_id}.zip"
                zip_path = Path(output_dir) / zip_filename
                logger.info("Saving zip archive to: %s", zip_path)
                with open(zip_path, "wb") as f:
                    f.write(first_chunk)
                    for chunk in chunk_iter:
                        f.write(chunk)

                extract_folder = Path(output_dir) / f"{Path(zip_filename).stem}_extracted"
                extract_folder.mkdir(parents=True, exist_ok=True)
                logger.info("Extracting zip to: %s", extract_folder)
                try:
                    with zipfile.ZipFile(zip_path, "r") as zf:
                        zf.extractall(extract_folder)
                except Exception as ze:
                    logger.warning("Failed unzipping %s: %s", zip_path, ze)

                video_exts = {".mp4", ".mov", ".mkv", ".webm"}
                found_videos = [
                    str(p) for p in extract_folder.rglob("*")
                    if p.is_file() and p.suffix.lower() in video_exts
                ]
                found_videos.sort(key=_natural_sort_key)
                if found_videos:
                    logger.info("Extracted %d video files from archive", len(found_videos))
                    return found_videos
            else:
                video_filename = filename or f"{token_id}.mp4"
                video_path = Path(output_dir) / video_filename
                logger.info("Saving direct video to: %s", video_path)
                with open(video_path, "wb") as f:
                    f.write(first_chunk)
                    for chunk in chunk_iter:
                        f.write(chunk)

                if video_path.exists() and video_path.stat().st_size > 100000:
                    return [str(video_path)]
    except Exception as e:
        logger.warning("Cloud/archive download error for %s: %s", url, e)

    return []


def download_asset_videos(url: str, output_dir: str = "data/clippo/downloads") -> List[str]:
    """
    Download video asset(s) from YouTube, TikTok, Nextcloud, direct cloud share, or ZIP archives.
    Returns a list of all downloaded video file paths.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    if not url:
        return []

    clean_url = url.strip()

    # 1. Check if already extracted locally
    token_id = re.sub(r'[^a-zA-Z0-9_-]', '_', Path(clean_url).name or "asset")[:40]
    cached_folder = Path(output_dir) / f"{token_id}_extracted"
    if cached_folder.exists():
        video_exts = {".mp4", ".mov", ".mkv", ".webm"}
        cached_videos = [
            str(p) for p in cached_folder.rglob("*")
            if p.is_file() and p.suffix.lower() in video_exts
        ]
        if cached_videos:
            cached_videos.sort(key=_natural_sort_key)
            logger.info("Using cached footage (%d files) from %s", len(cached_videos), cached_folder)
            return cached_videos

    # Also check if manual extraction folder exists (like juan_emas)
    alt_folder = Path(output_dir) / "juan_emas" / "JUAL EMAS INDONESIA"
    if alt_folder.exists() and any(k in clean_url for k in ["8yiPFqpo5gnEbLF", "jualemas", "juan"]):
        cached_videos = [
            str(p) for p in alt_folder.glob("*.mp4") if p.is_file()
        ]
        if cached_videos:
            cached_videos.sort(key=_natural_sort_key)
            logger.info("Using existing extracted footage (%d files) from %s", len(cached_videos), alt_folder)
            return cached_videos

    # 2. Cloud shares, Nextcloud, direct zip or binary links
    if any(k in clean_url for k in ["index.php/s/", ".zip", "/download", "mybati.co.id"]):
        cloud_videos = _download_cloud_or_archive(clean_url, output_dir)
        if cloud_videos:
            return cloud_videos

    # 3. If TikTok link, try TikWM first
    if any(k in clean_url for k in ["tiktok.com", "vt.tiktok.com"]):
        twm_result = _download_tiktok_tikwm(clean_url, output_dir)
        if twm_result:
            return [twm_result]

    # 4. Use yt-dlp for YouTube, Instagram, etc.
    import sys
    node_path = _find_node()
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--force-ipv4",
        "--js-runtimes", f"node:{node_path}",
        "--remote-components", "ejs:github",
        "--no-playlist",
        "-f", "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=2160]+bestaudio/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", os.path.join(output_dir, "%(id)s.%(ext)s"),
        "--no-warnings",
        "--quiet",
    ]

    if any(k in clean_url for k in ["youtube.com", "youtu.be"]) and os.path.exists(YTDLP_COOKIES):
        cmd.extend([
            "--cookies", YTDLP_COOKIES,
        ])

    cmd.append(clean_url)

    try:
        logger.info("Downloading asset via yt-dlp: %s", clean_url)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode == 0:
            all_files = [
                f for f in Path(output_dir).iterdir()
                if f.is_file() and f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")
            ]
            if all_files:
                newest = max(all_files, key=lambda f: f.stat().st_mtime)
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
                return [str(newest)]
        else:
            logger.warning("yt-dlp failed (code %d): %s", res.returncode, res.stderr[:300])
    except Exception as e:
        logger.error("Download exception for %s: %s", clean_url, e)

    # 5. Fallback to direct cloud/archive download
    fallback_videos = _download_cloud_or_archive(clean_url, output_dir)
    if fallback_videos:
        return fallback_videos

    return []


def download_asset_video(url: str, output_dir: str = "data/clippo/downloads") -> Optional[str]:
    """Download single video or return first available file."""
    videos = download_asset_videos(url, output_dir)
    return videos[0] if videos else None
