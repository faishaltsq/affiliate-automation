import os
import logging
import tempfile
import subprocess
from typing import Optional

logger = logging.getLogger("clipper.subtitles")

_WHISPER_MODEL = None


def _get_whisper_model():
    """Singleton getter for Whisper model."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        logger.info("Loading faster-whisper (tiny) on CPU...")
        _WHISPER_MODEL = WhisperModel("tiny", device="cpu", compute_type="int8")
    return _WHISPER_MODEL


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp format: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    """Escape characters for ASS subtitle lines."""
    clean = text.replace("{", "\\{").replace("}", "\\}")
    return " ".join(clean.split())


def generate_clipper_subtitles(
    video_path: str,
    out_ass: Optional[str] = None,
    margin_v: int = 480,
) -> Optional[str]:
    """
    Extract audio and generate ASS subtitle file with layout-aware vertical margin.
    """
    if not os.path.exists(video_path):
        logger.error(f"Video file not found: {video_path}")
        return None

    if not out_ass:
        out_ass = os.path.splitext(video_path)[0] + ".ass"

    try:
        model = _get_whisper_model()
    except Exception as e:
        logger.warning(f"Could not load faster-whisper: {e}")
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, "audio.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            audio_path
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception as e:
            logger.error(f"FFmpeg audio extraction failed: {e}")
            return None

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            logger.warning("Audio extracted is empty or missing.")
            return None

        try:
            segments, _ = model.transcribe(audio_path, language="id", beam_size=1)
            segments = list(segments)
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None

    if not segments:
        logger.info("No speech detected in video.")
        return None

    # Construct ASS file with modern styling
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ClipperDefault,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for seg in segments:
        text = _escape_ass_text(seg.text)
        if not text:
            continue
        start_ts = format_ass_timestamp(seg.start)
        end_ts = format_ass_timestamp(seg.end)
        events.append(f"Dialogue: 0,{start_ts},{end_ts},ClipperDefault,,0,0,0,,{text}")

    if not events:
        return None

    with open(out_ass, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

    return out_ass


def burn_subtitles_to_video(in_video: str, ass_path: str, out_video: str) -> bool:
    """Burn ASS subtitles into the video using FFmpeg."""
    if not os.path.exists(in_video) or not os.path.exists(ass_path):
        return False

    # Normalize ASS path for Windows FFmpeg filter syntax
    safe_ass_path = os.path.abspath(ass_path).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-i", in_video,
        "-vf", f"subtitles='{safe_ass_path}'",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "copy",
        out_video
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(out_video) and os.path.getsize(out_video) > 1000
    except Exception as e:
        logger.error(f"Failed to burn subtitles to video: {e}")
        return False
