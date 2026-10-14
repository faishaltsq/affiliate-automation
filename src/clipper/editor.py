"""editor.py — Clip and render video into 9:16 vertical with hook & CTA overlay for Clippo."""

import os
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Union, List

try:
    import cv2
except ImportError:
    cv2 = None

from src.clipper.framing import determine_framing_mode, CropConfig
from src.clipper.subtitles import generate_clipper_subtitles, burn_subtitles_to_video

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


def is_valid_media_file(video_path: str) -> bool:
    """Check if file exists and contains a valid, readable video stream."""
    if not video_path or not os.path.exists(video_path):
        return False
    try:
        if os.path.getsize(video_path) < 10000:
            return False
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return res.returncode == 0 and "video" in res.stdout.lower()
    except Exception:
        return False


def has_audio_stream(video_path: str) -> bool:
    """Check if video file has an audio stream."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return res.returncode == 0 and "audio" in res.stdout.lower()
    except Exception:
        return False


def _get_framing_for_clip(video_path: str, start_sec: float = 0.0, dur_sec: float = None) -> "CropConfig":
    """
    Sample frames from a specific time range, detect faces, and return best CropConfig.
    Falls back to center crop if cv2 unavailable or no faces found in segment.
    """
    from src.clipper.framing import determine_framing_mode, detect_faces_in_image

    in_w, in_h = get_video_dimensions(video_path)

    if cv2 is None or not os.path.exists(video_path):
        return determine_framing_mode(in_w, in_h, face_boxes=None)

    file_dur = get_video_duration(video_path)
    end_sec = min(start_sec + dur_sec, file_dur) if dur_sec else file_dur
    span = end_sec - start_sec
    if span <= 0:
        return determine_framing_mode(in_w, in_h, face_boxes=None)

    # Sample 3 frames from within the actual segment window
    num_samples = 3
    sample_times = [start_sec + span * (i + 1) / (num_samples + 1) for i in range(num_samples)]
    all_faces = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, t in enumerate(sample_times):
            frame_path = os.path.join(tmp_dir, f"seg_frame_{i}.jpg")
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t:.2f}",
                "-i", video_path,
                "-vframes", "1", "-q:v", "3", frame_path,
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=10)
                if os.path.exists(frame_path):
                    img = cv2.imread(frame_path)
                    if img is not None:
                        boxes = detect_faces_in_image(img)
                        all_faces.extend(boxes)
            except Exception:
                continue

    cfg = determine_framing_mode(in_w, in_h, face_boxes=all_faces if all_faces else None)
    logger.debug(
        "Framing for %s [%.1f-%.1f]: mode=%s faces=%d reason=%s",
        os.path.basename(video_path), start_sec, end_sec, cfg.mode, len(all_faces), cfg.reason
    )
    return cfg


def _build_vf_segment(idx: int, path: str, dur_sec: float, crop_cfg, fill_mode: bool) -> str:
    """Build ffmpeg filter_complex fragment for one video segment with per-segment framing."""
    audio_filter = (
        f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{idx}]"
        if has_audio_stream(path)
        else f"anullsrc=r=48000:cl=stereo,atrim=end={dur_sec:.2f}[a{idx}]"
    )
    if fill_mode:
        return (
            f"[{idx}:v]scale=-2:1920:flags=lanczos,crop=1080:1920:(iw-1080)/2:0,"
            f"unsharp=5:5:1.0:5:5:0.5,setsar=1[v{idx}];"
            f"{audio_filter}"
        )
    elif crop_cfg.mode == "crop_4_3" and crop_cfg.crop_filter:
        x_off = crop_cfg.x_offset
        return (
            f"[{idx}:v]crop=1440:1080:{x_off}:0,scale=1080:810:flags=lanczos,unsharp=5:5:1.0:5:5:0.5[fg{idx}];"
            f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
            f"scale=270:480,boxblur=20:1,scale=1080:1920[bg{idx}];"
            f"[bg{idx}][fg{idx}]overlay=0:555,setsar=1[v{idx}];"
            f"{audio_filter}"
        )
    else:
        return (
            f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
            f"scale=270:480,boxblur=20:1,scale=1080:1920[bg{idx}];"
            f"[{idx}:v]scale=1080:-2:force_original_aspect_ratio=decrease:flags=lanczos,unsharp=5:5:1.0:5:5:0.5[fg{idx}];"
            f"[bg{idx}][fg{idx}]overlay=(W-w)/2:(H-h)/2,setsar=1[v{idx}];"
            f"{audio_filter}"
        )


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    """Return video width and height in pixels via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x",
        video_path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        parts = res.stdout.strip().split("x")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 1920, 1080


def render_clipper_video(
    input_path: Union[str, List[str]],
    output_dir: str = "data/clippo/output",
    start_sec: float = 0.0,
    duration_sec: float = 60.0,
    hook_text: Optional[str] = None,
    cta_text: Optional[str] = None,
    fill_mode: bool = False,
    climax_info: Optional[dict] = None,
    auto_subtitles: bool = True,
    story_beats: Optional[List[dict]] = None,
) -> Optional[str]:
    """
    Render video into 1080x1920 9:16 format with high quality H.264 BT.709.
    If input_path is a list of footage paths, combines slices from provided footages.
    If story_beats is provided, stitches dialogue beats preserving sentence integrity.
    Optionally prepends a 3.5s punchline climax teaser as cold open and overlays hook text.
    Burns synchronized running subtitles via faster-whisper.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Multi-footage stitching mode
    if isinstance(input_path, list) and (len(input_path) > 1 or story_beats):
        return _render_clipper_multi_video(
            video_paths=input_path,
            output_dir=output_dir,
            target_duration=duration_sec,
            hook_text=hook_text,
            fill_mode=fill_mode,
            climax_info=climax_info,
            auto_subtitles=auto_subtitles,
            story_beats=story_beats,
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
    climax_info: Optional[dict] = None,
    auto_subtitles: bool = True,
    story_beats: Optional[List[dict]] = None,
) -> Optional[str]:
    """
    Stitch slices from provided footages into a single cohesive 9:16 vertical video.
    If story_beats is provided, assembles dialogue beats preserving sentence integrity.
    If climax_info is present, prepends the climax punchline moment (3.5s) as the opening teaser hook.
    Applies adaptive 4:3 framing panned towards subject angle, with automatic 16:9 ambient fallback.
    Burns synchronized running subtitles via faster-whisper.
    """
    # Strict validation: keep only playable video files
    valid_paths = [p for p in video_paths if is_valid_media_file(p)]
    if not valid_paths:
        logger.error("No valid video files found in multi-footage input: %s", video_paths)
        return None

    if len(valid_paths) == 1 and not climax_info and not story_beats:
        return render_clipper_video(
            valid_paths[0],
            output_dir=output_dir,
            start_sec=0.0,
            duration_sec=target_duration,
            hook_text=hook_text,
            fill_mode=fill_mode,
            climax_info=climax_info,
            auto_subtitles=auto_subtitles,
        )

    video_paths = valid_paths
    n_clips = len(video_paths)
    out_name = f"clip_multi_{n_clips}_{int(target_duration)}s.mp4"
    out_path = os.path.join(output_dir, out_name)

    # Determine global framing (used for hook banner y position logic only)
    in_w, in_h = get_video_dimensions(video_paths[0])
    crop_cfg_global = determine_framing_mode(in_w, in_h)
    logger.info("Global framing reference: %s (reason: %s)", crop_cfg_global.mode, crop_cfg_global.reason)

    inputs = []
    filter_parts = []
    concat_inputs = []
    idx = 0
    effective_hook = hook_text

    # 1. Cold open teaser: if climax_info provided, inject as segment #0
    if climax_info and climax_info.get("target_file") and is_valid_media_file(climax_info["target_file"]):
        teaser_file = climax_info["target_file"]
        teaser_start = float(climax_info.get("climax_start", 0.0))
        teaser_dur = float(climax_info.get("climax_duration", 3.5))
        if climax_info.get("punchline_hook"):
            effective_hook = climax_info["punchline_hook"]

        # Per-segment face-aware framing for cold open
        teaser_cfg = _get_framing_for_clip(teaser_file, teaser_start, teaser_dur)
        logger.info("Teaser framing: %s (reason: %s)", teaser_cfg.mode, teaser_cfg.reason)

        inputs.extend(["-ss", f"{teaser_start:.2f}", "-t", f"{teaser_dur:.2f}", "-i", teaser_file])
        filter_parts.append(_build_vf_segment(idx, teaser_file, teaser_dur, teaser_cfg, fill_mode))
        concat_inputs.append(f"[v{idx}][a{idx}]")
        idx += 1

    # 2. Sequential story progression
    if story_beats and len(story_beats) > 0:
        for beat in story_beats:
            b_path = beat["file"]
            b_start = float(beat.get("start", 0.0))
            b_dur = float(beat.get("duration", beat.get("end", b_start + 10.0) - b_start))
            if b_dur <= 0.5:
                continue
            inputs.extend(["-ss", f"{b_start:.2f}", "-t", f"{b_dur:.2f}", "-i", b_path])

            # Per-beat face-aware framing
            beat_cfg = _get_framing_for_clip(b_path, b_start, b_dur)
            filter_parts.append(_build_vf_segment(idx, b_path, b_dur, beat_cfg, fill_mode))
            concat_inputs.append(f"[v{idx}][a{idx}]")
            idx += 1
    else:
        if climax_info and idx > 0:
            rem_time = max(20.0, target_duration - float(climax_info.get("climax_duration", 3.5)))
        else:
            rem_time = target_duration

        seg_dur = max(3.0, rem_time / n_clips)

        for path in video_paths:
            file_dur = get_video_duration(path)
            start_offset = min(4.0, max(0.0, file_dur - seg_dur - 1.0))
            actual_seg = min(seg_dur, max(2.5, file_dur - start_offset))

            inputs.extend(["-ss", f"{start_offset:.2f}", "-t", f"{actual_seg:.2f}", "-i", path])

            # Per-clip face-aware framing
            clip_cfg = _get_framing_for_clip(path, start_offset, actual_seg)
            filter_parts.append(_build_vf_segment(idx, path, actual_seg, clip_cfg, fill_mode))
            concat_inputs.append(f"[v{idx}][a{idx}]")
            idx += 1

    total_inputs_count = idx
    concat_chain = "".join(concat_inputs) + f"concat=n={total_inputs_count}:v=1:a=1[basev][outa]"
    filter_parts.append(concat_chain)

    last_v = "basev"
    if effective_hook:
        esc_hook = effective_hook.replace("'", "'\\''").replace(":", "\\:").upper()
        banner_y = 440 if crop_cfg_global.mode == "crop_4_3" else 520
        filter_parts.append(
            f"[{last_v}]drawtext=fontfile='{FONT_PATH}':text='{esc_hook}':"
            f"fontcolor=white:fontsize=46:x=(w-text_w)/2:y={banner_y}:box=1:boxcolor=black@0.65:boxborderw=18:"
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

            # Auto speech subtitles burning
            if auto_subtitles:
                try:
                    ass_path = generate_clipper_subtitles(out_path, margin_v=crop_cfg_global.margin_v)
                    if ass_path and os.path.exists(ass_path):
                        burned_out = out_path.replace(".mp4", "_subbed.mp4")
                        if burn_subtitles_to_video(out_path, ass_path, burned_out):
                            os.replace(burned_out, out_path)
                            logger.info("Subtitles burned successfully onto: %s", out_path)
                        if os.path.exists(ass_path):
                            os.remove(ass_path)
                except Exception as sub_err:
                    logger.warning("Subtitle burning error (continuing with base render): %s", sub_err)

            return out_path
        else:
            logger.error("FFmpeg multi-render failed: %s", res.stderr[-400:] if res.stderr else "Unknown")
    except Exception as e:
        logger.error("Multi-render exception: %s", e)

    return None
