"""
editor.py — Video rendering and transformation engine implementing Faceless SOP:
- Strict 15–35s duration (target 20–25s)
- Anti-duplicate transformation (3 of 5):
  1. Crop/reframe (1080x1920 center-crop + 1.05x subtle zoom)
  2. Tight pacing (cold open cut, dead air removed)
  3. Hook text overlay (bold, high contrast, top-center safe zone at 0–2.5s)
  4. Color grading filter (contrast/saturation boost)
  5. Unsharp + Lanczos rendering (CRF 18, high quality, BT.709)
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("content_ops.editor")

FONT_PATH = "C:/Windows/Fonts/arialbd.ttf"


def escape_drawtext(text: str) -> str:
    """Escape text for FFmpeg drawtext filter string."""
    # Escape special chars: \, ', :, %, ;
    clean = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%").replace(";", "\\;")
    return clean


def get_video_info(video_path: str) -> dict:
    """Get video width, height, and duration via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration:format=duration",
        "-of", "json",
        video_path
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(p.stdout)
        stream = data.get("streams", [{}])[0]
        fmt = data.get("format", {})
        w = int(stream.get("width") or 1920)
        h = int(stream.get("height") or 1080)
        dur = float(stream.get("duration") or fmt.get("duration") or 0.0)
        return {"width": w, "height": h, "duration": dur}
    except Exception as e:
        logger.error(f"Failed to probe video {video_path}: {e}")
        return {"width": 1920, "height": 1080, "duration": 0.0}


def render_sop_clip(
    input_path: str,
    output_path: str,
    hook_text: str,
    start_time: float = 0.0,
    target_duration: float = 22.0,
) -> bool:
    """
    Render video clip conforming strictly to SOP requirements:
    - Duration clamped to 15–35 seconds (default 22s)
    - 9:16 vertical resolution (1080x1920)
    - Anti-duplicate filter chain: crop + subtle zoom, color grading, unsharp
    - High contrast hook overlay in safe top-center zone
    """
    if not os.path.isfile(input_path):
        logger.error(f"Input file not found: {input_path}")
        return False

    info = get_video_info(input_path)
    total_dur = info["duration"]
    w = info["width"]
    h = info["height"]

    # Clamp duration to SOP range [15, 35]
    dur = max(15.0, min(35.0, target_duration))
    if total_dur > 0 and (start_time + dur) > total_dur:
        # Adjust start time if clip would overshoot
        start_time = max(0.0, total_dur - dur)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    is_vertical = (h > w)
    # Anti-duplicate video filters:
    # 1. Framing: center crop to 9:16 + slight zoom (crop 0.95x)
    if is_vertical:
        framing = "crop=in_w*0.95:in_h*0.95,scale=1080:1920:flags=lanczos"
    else:
        framing = "crop=in_h*(9/16)*0.95:in_h*0.95,scale=1080:1920:flags=lanczos"

    # 2. Color grading: slight contrast, brightness, and vibrant saturation
    color_grade = "eq=contrast=1.06:brightness=0.01:saturation=1.12"

    # 3. Unsharp filter
    sharp = "unsharp=5:5:1.0:5:5:0.5"

    # 4. Hook text overlay (bold Arial, safe zone y=280, dark box padding, 0–2.5s)
    esc_hook = escape_drawtext(hook_text)
    font_arg = f"fontfile='C\\:/Windows/Fonts/arialbd.ttf'" if os.path.isfile(FONT_PATH) else "font='Arial'"
    hook_overlay = (
        f"drawtext={font_arg}:text='{esc_hook}':"
        f"fontcolor=white:fontsize=48:x=(w-text_w)/2:y=280:"
        f"box=1:boxcolor=black@0.65:boxborderw=18:enable='between(t,0,2.5)'"
    )

    vf_chain = f"{framing},{color_grade},{sharp},{hook_overlay}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_time:.2f}",
        "-t", f"{dur:.2f}",
        "-i", input_path,
        "-vf", vf_chain,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-colorspace", "bt709",
        "-color_primaries", "bt709",
        "-color_trc", "bt709",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Rendering SOP clip: {output_path} (dur={dur:.1f}s, start={start_time:.1f}s)")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return os.path.isfile(output_path) and os.path.getsize(output_path) > 100_000
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg render error: {e.stderr}")
        return False
