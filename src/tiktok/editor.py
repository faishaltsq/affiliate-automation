"""Video editor: trim, professional 9:16 portrait formatting (blurred pillarbox), optional text overlay via ffmpeg."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import textwrap

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

    if in_w >= in_h:
        if fill_mode:
            # Center crop: scale up to fill 1080x1920 then crop center.
            # Scale so height = 1920, then width will be > 1080 (for 16:9 source).
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
                    f":x=(w-text_w)/2:y=80"
                    ":box=1:boxcolor=black@0.7:boxborderw=18[v]"
                )
                fc_parts.append(drawtext)
                map_label = "[v]"
            else:
                map_label = "[fg]"
            filter_complex = ";".join(fc_parts)
        else:
            # Blurred pillarbox background + centered sharp 1080p
            fc_parts = [
                f"[0:v]scale={crop_width}:{crop_height}:force_original_aspect_ratio=increase,crop={crop_width}:{crop_height},boxblur=25:5[bg]",
                f"[0:v]scale={crop_width}:-2:flags=lanczos,unsharp=5:5:0.8:5:5:0.4[fg]",
                f"[bg][fg]overlay=0:({crop_height}-h)/2[merged]",
            ]
            if overlay_text:
                safe_text = format_title_overlay(overlay_text)
                fg_h = int(crop_width * in_h / in_w)
                top_space = (crop_height - fg_h) // 2
                # Center title vertically in the top blurred space, guaranteed above video content
                title_y = max(240, (top_space - 100) // 2)
                drawtext = (
                    f"[merged]drawtext=font='Arial'"
                    f":text='{safe_text}'"
                    ":fontcolor=white:fontsize=46:line_spacing=14"
                    f":x=(w-text_w)/2:y={title_y}"
                    ":box=1:boxcolor=black@0.7:boxborderw=18[v]"
                )
                fc_parts.append(drawtext)
                map_label = "[v]"
            else:
                map_label = "[merged]"

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
        # Portrait (already vertical): Scale and pad directly
        filters = [
            f"scale={crop_width}:{crop_height}:force_original_aspect_ratio=decrease:flags=lanczos",
            "unsharp=5:5:0.8:5:5:0.4",
            f"pad={crop_width}:{crop_height}:({crop_width}-iw)/2:({crop_height}-ih)/2:black",
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
            logger.warning("ffmpeg with overlay failed, retrying without overlay: %s", result.stderr)
            return process_video(
                input_path,
                output_path,
                max_duration=max_duration,
                crop_width=crop_width,
                crop_height=crop_height,
                overlay_text=None,
                start_time=start_time,
            )
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr}")

    return output_path
