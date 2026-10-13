"""subtitles.py — Auto speech-to-text subtitle generator for TikTok clips."""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WHISPER_MODEL = None


def _get_whisper_model():
    """Lazy load faster-whisper model (tiny model on CPU with int8)."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        try:
            import faster_whisper
            logger.info("Loading faster-whisper 'tiny' model on CPU...")
            _WHISPER_MODEL = faster_whisper.WhisperModel("tiny", device="cpu", compute_type="int8")
        except Exception as e:
            logger.error("Failed to load faster-whisper: %s", e)
            return None
    return _WHISPER_MODEL


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp string: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def generate_subtitles_ass(
    video_path: str,
    start_time: float = 0.0,
    duration: float = 60.0,
    output_ass_path: Optional[str] = None,
    margin_v: int = 580,
    font_size: int = 36,
) -> Optional[str]:
    """
    Transcribe speech in the video segment and output an ASS subtitle file.
    Returns path to the generated ASS file, or None if no speech is detected.
    """
    if not os.path.exists(video_path):
        return None

    model = _get_whisper_model()
    if model is None:
        return None

    # Extract temporary WAV audio slice for transcription
    temp_wav = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            temp_wav = tf.name

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-t", str(duration),
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            temp_wav,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode != 0 or not os.path.exists(temp_wav) or os.path.getsize(temp_wav) < 1000:
            logger.debug("Failed to extract audio or video is silent: %s", res.stderr)
            return None

        # Transcribe with faster-whisper
        segments, info = model.transcribe(
            temp_wav,
            beam_size=1,
            word_timestamps=False,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        dialogues = []
        for seg in segments:
            text = seg.text.strip()
            # Skip empty or garbage hallucinations
            if not text or len(text) < 2:
                continue
            # Clean unwanted brackets/emojis
            clean_text = re.sub(r"\[.*?\]|\(.*?\)", "", text).strip()
            if not clean_text:
                continue

            # Break long lines if necessary
            words = clean_text.split()
            if len(words) > 7:
                mid = len(words) // 2
                clean_text = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])

            # ASS formatting
            start_ts = format_ass_timestamp(seg.start)
            end_ts = format_ass_timestamp(seg.end)
            dialogues.append(
                f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{clean_text}"
            )

        if not dialogues:
            logger.info("No speech detected in clip, skipping subtitles.")
            return None

        if not output_ass_path:
            output_ass_path = temp_wav.replace(".wav", ".ass")

        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.0,1.2,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            f.write("\n".join(dialogues) + "\n")

        logger.info("Generated %d subtitle dialogue lines: %s", len(dialogues), output_ass_path)
        return output_ass_path

    except Exception as e:
        logger.warning("Subtitle generation failed (continuing without subs): %s", e)
        return None
    finally:
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass
