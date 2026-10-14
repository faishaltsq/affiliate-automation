"""Video editor: trim, professional 9:16 portrait formatting (blurred pillarbox), optional text overlay via ffmpeg."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap
from pathlib import Path

from src.tiktok.subtitles import generate_subtitles_ass
from src.tiktok.subtitle_detector import has_existing_subtitles

logger = logging.getLogger(__name__)


def get_video_duration(input_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_video_dimensions(input_path: str) -> tuple[int, int]:
    """Return (width, height) of input video via ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        input_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"No video streams found in {input_path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def format_title_overlay(text: str, max_chars_per_line: int = 22) -> str:
    """Clean and wrap title text for safe mobile display."""
    import re
    cleaned = re.sub(r"#\S+", "", text)
    cleaned = re.sub(r"\[.*?\]|\(.*?\)", "", cleaned)
    parts = [p.strip() for p in re.split(r"\s*[|•—–]\s*", cleaned) if p.strip()]
    chosen = parts[0] if parts else text
    if (len(chosen) < 10 or chosen.lower().startswith("part")) and len(parts) > 1:
        chosen = parts[1]
    chosen = re.sub(r"[^\w\s\-,!?']", "", chosen)
    chosen = re.sub(r"\s+", " ", chosen).strip()
    clean = (chosen or text).replace("'", "\u2019").replace('"', "").replace(":", "\\:").strip()
    raw_lines = textwrap.wrap(clean, width=max_chars_per_line)[:2]
    lines = [l.strip().rstrip("-–—,") for l in raw_lines]
    return "\n".join(lines)


def process_video(
    input_path: str,
    output_path: str,
    max_duration: int = 60,
    crop_width: int = 1080,
    crop_height: int = 1920,
    overlay_text: str | None = None,
    start_time: float = 0.0,
    fill_mode: bool = False,
    auto_subtitles: bool = True,
) -> str:
    """Format video to 9:16 portrait for mobile screens without chopping content.

    Landscape videos (16:9) use ambient blurred pillarbox backgrounds with
    full-width sharp video centered, keeping 100% of the original framing.
    If fill_mode=True, the video is center-cropped to fill the entire 9:16
    frame (no blur bars), maximizing screen coverage on mobile.
    Portrait videos are scaled/padded to fit 1080x1920.

    Returns *output_path* on success; raises ``RuntimeError`` on failure.
    """
    duration = get_video_duration(input_path)
    effective_duration = max(0.0, duration - start_time)
    trim_duration = min(effective_duration, float(max_duration))
    in_w, in_h = get_video_dimensions(input_path)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ss_args = ["-ss", str(start_time)] if start_time > 0 else []

    sub_ass = None
    try:
        # Auto-detect existing subtitles (soft or hardcoded/burned-in)
        if auto_subtitles and has_existing_subtitles(input_path):
            logger.info("Existing subtitles detected in %s — skipping auto subtitle generation", input_path)
            auto_subtitles = False

        if in_w >= in_h:
            fg_h = int(crop_width * in_h / in_w)
            top_space = (crop_height - fg_h) // 2

            if fill_mode:
                # Center crop: scale up to fill 1080x1920 then crop center.
                fc_parts = [
                    f"[0:v]scale=-2:{crop_height}:flags=lanczos,"
                    f"crop={crop_width}:{crop_height}:(iw-{crop_width})/2:0,"
                    f"unsharp=5:5:0.8:5:5:0.4[fg]",
                ]
                if overlay_text:
                    safe_text = format_title_overlay(overlay_text)
                    drawtext = (
                        f"[fg]drawtext=font='Arial'"
                        f":text='{safe_text}'"
                        ":fontcolor=white:fontsize=46:line_spacing=14"
                        f":x=(w-text_w)/2:y=280"
                        ":box=1:boxcolor=black@0.7:boxborderw=18[v_title]"
                    )
                    fc_parts.append(drawtext)
                    map_label = "[v_title]"
                else:
                    map_label = "[fg]"

                if auto_subtitles:
                    sub_ass = generate_subtitles_ass(
                        input_path,
                        start_time=start_time,
                        duration=trim_duration,
                        margin_v=360,
                    )
                    if sub_ass and os.path.exists(sub_ass):
                        escaped_sub = Path(sub_ass).resolve().as_posix().replace(":", r"\:")
                        fc_parts.append(f"{map_label}subtitles='{escaped_sub}'[v_sub]")
                        map_label = "[v_sub]"

                filter_complex = ";".join(fc_parts)
            else:
                # Blurred pillarbox background + centered sharp 1080p
                fc_parts = [
                    f"[0:v]scale={crop_width}:{crop_height}:force_original_aspect_ratio=increase:flags=lanczos,crop={crop_width}:{crop_height},scale=270:480,boxblur=20:1,scale={crop_width}:{crop_height}[bg]",
                    f"[0:v]scale={crop_width}:-2:flags=lanczos,unsharp=5:5:1.0:5:5:0.5[fg]",
                    f"[bg][fg]overlay=0:({crop_height}-h)/2[merged]",
                ]
                if overlay_text:
                    safe_text = format_title_overlay(overlay_text)
                    # Posisikan banner kalimat persis di atas video (agak ke bawah dari posisi lama)
                    num_lines = max(1, len(safe_text.split("\n")))
                    banner_h = num_lines * 52 + 36
                    title_y = max(240, top_space - banner_h - 20)
                    drawtext = (
                        f"[merged]drawtext=font='Arial'"
                        f":text='{safe_text}'"
                        ":fontcolor=white:fontsize=46:line_spacing=14"
                        f":x=(w-text_w)/2:y={title_y}"
                        ":box=1:boxcolor=black@0.7:boxborderw=18[v_title]"
                    )
                    fc_parts.append(drawtext)
                    map_label = "[v_title]"
                else:
                    map_label = "[merged]"

                if auto_subtitles:
                    # Subtitle muncul di area blur persis di bawah video
                    video_bottom = (crop_height + fg_h) // 2
                    sub_margin_v = max(260, crop_height - (video_bottom + 50))
                    sub_ass = generate_subtitles_ass(
                        input_path,
                        start_time=start_time,
                        duration=trim_duration,
                        margin_v=sub_margin_v,
                    )
                    if sub_ass and os.path.exists(sub_ass):
                        escaped_sub = Path(sub_ass).resolve().as_posix().replace(":", r"\:")
                        fc_parts.append(f"{map_label}subtitles='{escaped_sub}'[v_sub]")
                        map_label = "[v_sub]"

                filter_complex = ";".join(fc_parts)

            cmd = [
                "ffmpeg", "-y",
                *ss_args,
                "-i", input_path,
                "-t", str(trim_duration),
                "-filter_complex", filter_complex,
                "-map", map_label,
                "-map", "0:a?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
                "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ]
        else:
            # Portrait (already vertical): Scale and crop to exact 9:16 vertical fullscreen
            filters = [
                f"scale={crop_width}:{crop_height}:force_original_aspect_ratio=increase:flags=lanczos",
                f"crop={crop_width}:{crop_height}:(iw-{crop_width})/2:(ih-{crop_height})/2",
                "unsharp=5:5:0.8:5:5:0.4",
            ]
            if overlay_text:
                safe_text = format_title_overlay(overlay_text)
                drawtext = (
                    f"drawtext=font='Arial'"
                    f":text='{safe_text}'"
                    ":fontcolor=white:fontsize=46:line_spacing=14"
                    f":x=(w-text_w)/2:y=280"
                    ":box=1:boxcolor=black@0.7:boxborderw=18"
                )
                filters.append(drawtext)

            if auto_subtitles:
                sub_ass = generate_subtitles_ass(
                    input_path,
                    start_time=start_time,
                    duration=trim_duration,
                    margin_v=360,
                )
                if sub_ass and os.path.exists(sub_ass):
                    escaped_sub = Path(sub_ass).resolve().as_posix().replace(":", r"\:")
                    filters.append(f"subtitles='{escaped_sub}'")

            cmd = [
                "ffmpeg", "-y",
                *ss_args,
                "-i", input_path,
                "-t", str(trim_duration),
                "-vf", ",".join(filters),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
                "-colorspace", "bt709", "-color_trc", "bt709", "-color_primaries", "bt709",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ]

        logger.info("Running ffmpeg: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            if overlay_text:
                logger.warning("ffmpeg with overlay/subs failed, retrying without overlay: %s", result.stderr)
                return process_video(
                    input_path,
                    output_path,
                    max_duration=max_duration,
                    crop_width=crop_width,
                    crop_height=crop_height,
                    overlay_text=None,
                    start_time=start_time,
                    auto_subtitles=False,
                )
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr}")

        return output_path
    finally:
        if sub_ass and os.path.exists(sub_ass):
            try:
                os.remove(sub_ass)
            except OSError:
                pass
