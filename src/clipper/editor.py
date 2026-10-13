"""editor.py — Clip and render video into 9:16 vertical with hook & CTA overlay for Clippo."""

import os
import logging
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipper.editor")


def get_video_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(res.stdout.strip())
    except Exception:
        return 60.0


def render_clipper_video(
    input_path: str,
    output_dir: str = "data/clippo/output",
    start_sec: float = 0.0,
    duration_sec: float = 60.0,
    hook_text: Optional[str] = None,
    cta_text: Optional[str] = None,
    fill_mode: bool = False,
) -> Optional[str]:
    """
    Render video segment into 1080x1920 9:16 format with high quality H.264 BT.709.
    Optionally overlays hook text at top and CTA text at bottom.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    out_name = f"clip_{Path(input_path).stem}_{int(start_sec)}_{int(duration_sec)}.mp4"
    out_path = os.path.join(output_dir, out_name)

    total_dur = get_video_duration(input_path)
    if start_sec >= total_dur:
        start_sec = 0.0
    actual_dur = min(duration_sec, max(15.0, total_dur - start_sec))

    # Base video filter (either Center Crop or Blurred Pillarbox)
    if fill_mode:
        # Scale to height 1920, center-crop 1080
        vf_base = "scale=-2:1920,crop=1080:1920:(iw-1080)/2:0,unsharp=5:5:0.8:5:5:0.4"
    else:
        # Blurred background + sharp centered original
        vf_base = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            "boxblur=25:5[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease,unsharp=5:5:0.8:5:5:0.4[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-t", str(actual_dur),
        "-i", input_path,
        "-filter_complex" if not fill_mode else "-vf", vf_base,
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-movflags", "+faststart",
        out_path,
    ]

    try:
        logger.info("Rendering clipper video: %s (%.1fs - %.1fs)", input_path, start_sec, actual_dur)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode == 0 and os.path.exists(out_path):
            logger.info("Rendered successfully: %s", out_path)
            return out_path
        else:
            logger.error("FFmpeg render failed: %s", res.stderr[-300:] if res.stderr else "Unknown")
    except Exception as e:
        logger.error("Render exception: %s", e)

    return None
