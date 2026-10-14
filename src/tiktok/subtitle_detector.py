"""subtitle_detector.py — Detect burned-in (hardcoded) subtitles in video frames.

Uses edge-density analysis in the subtitle zone (bottom 20-35% of frame)
across multiple sample frames. No OCR dependency — OpenCV only.

Returns True if the video likely has hardcoded subtitles.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("tiktok.subtitle_detector")

# Subtitle zone: bottom 20% to 35% of frame (where most burned-in subs sit)
_ZONE_TOP_RATIO = 0.65   # start at 65% from top
_ZONE_BOT_RATIO = 0.92   # end at 92% from top (exclude very bottom UI bar)

# Edge density threshold: if avg edge density across sampled frames exceeds this,
# consider subtitles present. Tuned empirically on TikTok/Reels content.
_EDGE_DENSITY_THRESHOLD = 0.035  # 3.5% of zone pixels are edges
_MIN_POSITIVE_FRAMES = 2         # at least 2 out of N frames must show text


def _extract_frames(video_path: str, timestamps: list[float], output_dir: str) -> list[str]:
    """Extract specific frames from video using ffmpeg."""
    frame_paths = []
    for i, ts in enumerate(timestamps):
        out = os.path.join(output_dir, f"frame_{i}.png")
        cmd = [
            "ffmpeg", "-y", "-ss", str(ts), "-i", video_path,
            "-frames:v", "1", "-q:v", "2", out,
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=15)
            if os.path.exists(out) and os.path.getsize(out) > 0:
                frame_paths.append(out)
        except Exception:
            continue
    return frame_paths


def _compute_edge_density(frame_path: str) -> float:
    """Compute edge density in the subtitle zone of a frame.
    
    Returns ratio of edge pixels to total pixels in the zone (0.0 - 1.0).
    """
    img = cv2.imread(frame_path)
    if img is None:
        return 0.0

    h, w = img.shape[:2]
    y_top = int(h * _ZONE_TOP_RATIO)
    y_bot = int(h * _ZONE_BOT_RATIO)
    zone = img[y_top:y_bot, :]

    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)

    # Apply bilateral filter to reduce noise while preserving text edges
    filtered = cv2.bilateralFilter(gray, 9, 75, 75)

    # Canny edge detection
    edges = cv2.Canny(filtered, 50, 150)

    total_pixels = edges.shape[0] * edges.shape[1]
    if total_pixels == 0:
        return 0.0

    edge_pixels = np.count_nonzero(edges)
    density = edge_pixels / total_pixels

    return density


def _check_text_contrast(frame_path: str) -> bool:
    """Secondary check: look for high-contrast horizontal bands typical of subtitle text.
    
    Burned-in subs usually have bright text on dark/semi-transparent background,
    creating sharp luminance transitions in the subtitle zone.
    """
    img = cv2.imread(frame_path)
    if img is None:
        return False

    h, w = img.shape[:2]
    y_top = int(h * _ZONE_TOP_RATIO)
    y_bot = int(h * _ZONE_BOT_RATIO)
    zone = img[y_top:y_bot, :]

    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)

    # Compute row-wise standard deviation of luminance
    row_stds = np.std(gray.astype(float), axis=1)

    # Text rows have high std (bright text on dark bg or vice versa)
    high_contrast_rows = np.sum(row_stds > 50)
    ratio = high_contrast_rows / len(row_stds) if len(row_stds) > 0 else 0

    return bool(ratio > 0.15)  # >15% of rows have high contrast


def has_hardcoded_subtitles(
    video_path: str,
    sample_timestamps: Optional[list[float]] = None,
) -> bool:
    """Detect whether a video has burned-in (hardcoded) subtitles.
    
    Samples multiple frames and analyzes the subtitle zone for text-like
    edge patterns. Returns True if hardcoded subtitles are likely present.
    
    Args:
        video_path: Path to video file.
        sample_timestamps: Specific seconds to sample. Default: [3, 5, 10, 15].
    
    Returns:
        True if burned-in subtitles detected.
    """
    if not os.path.exists(video_path):
        logger.warning("subtitle_detector: video not found: %s", video_path)
        return False

    if sample_timestamps is None:
        # Get video duration to pick smart sample points
        try:
            cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", video_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            import json
            duration = float(json.loads(res.stdout).get("format", {}).get("duration", 60))
        except Exception:
            duration = 60.0

        # Sample at 15%, 30%, 50%, 70% of duration (avoid first/last seconds)
        sample_timestamps = [
            max(1.0, duration * 0.15),
            max(2.0, duration * 0.30),
            max(3.0, duration * 0.50),
            max(4.0, duration * 0.70),
        ]

    tmpdir = tempfile.mkdtemp(prefix="subdetect_")
    try:
        frames = _extract_frames(video_path, sample_timestamps, tmpdir)
        if not frames:
            logger.warning("subtitle_detector: no frames extracted from %s", video_path)
            return False

        positive_edge = 0
        positive_contrast = 0

        for fp in frames:
            density = _compute_edge_density(fp)
            contrast = _check_text_contrast(fp)

            if density > _EDGE_DENSITY_THRESHOLD:
                positive_edge += 1
            if contrast:
                positive_contrast += 1

            logger.debug(
                "subtitle_detector frame %s: edge_density=%.4f, contrast=%s",
                fp, density, contrast,
            )

        # Require both edge density AND contrast evidence in multiple frames
        has_subs = (
            positive_edge >= _MIN_POSITIVE_FRAMES
            and positive_contrast >= _MIN_POSITIVE_FRAMES
        )

        logger.info(
            "subtitle_detector: %s → edge_positive=%d/%d, contrast_positive=%d/%d → %s",
            os.path.basename(video_path),
            positive_edge, len(frames),
            positive_contrast, len(frames),
            "HARDCODED SUBS DETECTED" if has_subs else "no hardcoded subs",
        )

        return has_subs

    finally:
        # Cleanup temp frames
        for f in Path(tmpdir).glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


def has_soft_subtitles(video_path: str) -> bool:
    """Check if video has embedded soft subtitle streams (SRT/ASS/etc)."""
    try:
        import json
        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_streams", video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(res.stdout)
        sub_streams = [
            s for s in data.get("streams", [])
            if s.get("codec_type") == "subtitle"
        ]
        return len(sub_streams) > 0
    except Exception:
        return False


def has_existing_subtitles(video_path: str) -> bool:
    """Combined check: returns True if video has either soft or hardcoded subtitles."""
    if has_soft_subtitles(video_path):
        logger.info("subtitle_detector: soft subtitle stream found in %s", video_path)
        return True
    return has_hardcoded_subtitles(video_path)
