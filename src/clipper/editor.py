"""editor.py — Clip and render video into 9:16 vertical with hook & CTA overlay for Clippo."""

import os
import logging
import subprocess
from pathlib import Path
from typing import Optional, Union, List

logger = logging.getLogger("clipper.editor")

FONT_PATH = "C\\:/Windows/Fonts/arialbd.ttf"


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
    input_path: Union[str, List[str]],
    output_dir: str = "data/clippo/output",
    start_sec: float = 0.0,
    duration_sec: float = 60.0,
    hook_text: Optional[str] = None,
    cta_text: Optional[str] = None,
    fill_mode: bool = False,
) -> Optional[str]:
    """
    Render video into 1080x1920 9:16 format with high quality H.264 BT.709.
    If input_path is a list of footage paths, combines slices from ALL provided footages
    so that every footage file is represented in the final clip.
    Optionally overlays hook text banner at top-center safe zone.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Multi-footage stitching mode
    if isinstance(input_path, list) and len(input_path) > 1:
        return _render_clipper_multi_video(
            video_paths=input_path,
            output_dir=output_dir,
            target_duration=duration_sec,
            hook_text=hook_text,
            fill_mode=fill_mode,
        )

    # Single video mode
    single_path = input_path[0] if isinstance(input_path, list) else input_path
    out_name = f"clip_{Path(single_path).stem}_{int(start_sec)}_{int(duration_sec)}.mp4"
    out_path = os.path.join(output_dir, out_name)

    total_dur = get_video_duration(single_path)
    if start_sec >= total_dur:
        start_sec = 0.0
    actual_dur = min(duration_sec, max(15.0, total_dur - start_sec))

    filter_complex = []
    if fill_mode:
        filter_complex.append(
            "[0:v]scale=-2:1920:flags=lanczos,crop=1080:1920:(iw-1080)/2:0,unsharp=5:5:1.0:5:5:0.5[basev]"
        )
    else:
        filter_complex.append(
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
            "scale=270:480,boxblur=20:1,scale=1080:1920[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease:flags=lanczos,unsharp=5:5:1.0:5:5:0.5[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[basev]"
        )

    last_v = "basev"
    if hook_text:
        esc_hook = hook_text.replace("'", "'\\''").replace(":", "\\:").upper()
        filter_complex.append(
            f"[{last_v}]drawtext=fontfile='{FONT_PATH}':text='{esc_hook}':"
            f"fontcolor=white:fontsize=46:x=(w-text_w)/2:y=280:box=1:boxcolor=black@0.65:boxborderw=18:"
            f"enable='between(t,0,3.5)'[outv]"
        )
        last_v = "outv"

    fc_str = ";".join(filter_complex)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_sec),
        "-t", str(actual_dur),
        "-i", single_path,
        "-filter_complex", fc_str,
        "-map", f"[{last_v}]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        "-profile:v", "high",
        "-level", "4.2",
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
        logger.info("Rendering clipper video: %s (%.1fs - %.1fs)", single_path, start_sec, actual_dur)
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if res.returncode == 0 and os.path.exists(out_path):
            logger.info("Rendered successfully: %s", out_path)
            return out_path
        else:
            logger.error("FFmpeg render failed: %s", res.stderr[-300:] if res.stderr else "Unknown")
    except Exception as e:
        logger.error("Render exception: %s", e)

    return None


def _render_clipper_multi_video(
    video_paths: List[str],
    output_dir: str,
    target_duration: float = 75.0,
    hook_text: Optional[str] = None,
    fill_mode: bool = False,
) -> Optional[str]:
    """
    Stitch slices from EVERY provided footage into a single cohesive 9:16 vertical video.
    Guarantees 100% of the provided footages are included in the clip.
    """
    n_clips = len(video_paths)
    out_name = f"clip_multi_{n_clips}_{int(target_duration)}s.mp4"
    out_path = os.path.join(output_dir, out_name)

    # Allocate duration per clip so total stays balanced
    seg_dur = max(4.0, target_duration / n_clips)

    inputs = []
    filter_parts = []
    concat_inputs = []

    for i, path in enumerate(video_paths):
        file_dur = get_video_duration(path)
        # Choose start offset (skip opening silence/dead time if video long enough)
        start_offset = min(4.0, max(0.0, file_dur - seg_dur - 1.0))
        actual_seg = min(seg_dur, max(3.0, file_dur - start_offset))

        inputs.extend(["-ss", f"{start_offset:.2f}", "-t", f"{actual_seg:.2f}", "-i", path])

        if fill_mode:
            vf = (
                f"[{i}:v]scale=-2:1920:flags=lanczos,crop=1080:1920:(iw-1080)/2:0,"
                f"unsharp=5:5:1.0:5:5:0.5,setsar=1[v{i}];"
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
        else:
            vf = (
                f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
                f"scale=270:480,boxblur=20:1,scale=1080:1920[bg{i}];"
                f"[{i}:v]scale=1080:-2:force_original_aspect_ratio=decrease:flags=lanczos,unsharp=5:5:1.0:5:5:0.5[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{i}];"
                f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{i}]"
            )
        filter_parts.append(vf)
        concat_inputs.append(f"[v{i}][a{i}]")

    concat_chain = "".join(concat_inputs) + f"concat=n={n_clips}:v=1:a=1[basev][outa]"
    filter_parts.append(concat_chain)

    last_v = "basev"
    if hook_text:
        esc_hook = hook_text.replace("'", "'\\''").replace(":", "\\:").upper()
        filter_parts.append(
            f"[{last_v}]drawtext=fontfile='{FONT_PATH}':text='{esc_hook}':"
            f"fontcolor=white:fontsize=46:x=(w-text_w)/2:y=280:box=1:boxcolor=black@0.65:boxborderw=18:"
            f"enable='between(t,0,3.5)'[outv]"
        )
        last_v = "outv"

    fc_str = ";".join(filter_parts)

    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex", fc_str,
        "-map", f"[{last_v}]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "veryfast",
        "-profile:v", "high",
        "-level", "4.2",
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
        logger.info(
            "Rendering multi-footage clipper video from %d files (target: %.1fs): %s",
            n_clips, target_duration, out_path
        )
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
        if res.returncode == 0 and os.path.exists(out_path):
            logger.info("Rendered multi-footage successfully: %s", out_path)
            return out_path
        else:
            logger.error("FFmpeg multi-render failed: %s", res.stderr[-400:] if res.stderr else "Unknown")
    except Exception as e:
        logger.error("Multi-render exception: %s", e)

    return None
