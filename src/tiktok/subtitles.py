"""subtitles.py — Auto speech-to-text subtitle generator for TikTok clips."""

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_WHISPER_MODELS: dict = {}


def _get_whisper_model(size: str = "tiny"):
    """Lazy load faster-whisper on CPU with int8. Cached per model size.

    `size` matters: 'tiny' is fast but mangles non-English speech; 'base' is
    the practical minimum for an accurate transcript of an English source.
    """
    if size in _WHISPER_MODELS:
        return _WHISPER_MODELS[size]
    try:
        import faster_whisper
        logger.info("Loading faster-whisper '%s' model on CPU...", size)
        model = faster_whisper.WhisperModel(size, device="cpu", compute_type="int8")
        _WHISPER_MODELS[size] = model
        return model
    except Exception as e:
        logger.error("Failed to load faster-whisper '%s': %s", size, e)
        return None


SILENCE_GRACE_SEC = 5.0


def _word_ends(seg) -> list:
    """Word end-times inside a segment, [] when word timestamps are unavailable."""
    words = getattr(seg, "words", None)
    if not isinstance(words, list):
        return []
    return [float(w.end) for w in words if getattr(w, "end", None) is not None]


def cap_end_to_speech(start: float, end: float, word_ends, grace: float = SILENCE_GRACE_SEC) -> float:
    """A subtitle line may outlive its last spoken word by at most `grace` seconds.

    Whisper stretches a segment's `end` into the silence that follows; burned
    as-is, the line sits on screen while nobody is talking.
    """
    if not word_ends:
        return float(end)
    return max(float(start) + 0.3, min(float(end), max(word_ends) + grace))


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp string: H:MM:SS.cs"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass(
    raw_segments,
    output_ass_path: str,
    font_size: int = 36,
    margin_v: int = 580,
) -> Optional[str]:
    """Render segments to an ASS file. Shared by both the injected and Whisper paths."""
    dialogues = []
    for seg in raw_segments:
        text = (seg["text"] if isinstance(seg, dict) else seg.text).strip()
        if not text or len(text) < 2:
            continue
        clean_text = re.sub(r"\[.*?\]|\(.*?\)", "", text).strip()
        if not clean_text:
            continue

        words = clean_text.split()
        if len(words) > 7:
            mid = len(words) // 2
            clean_text = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])

        start_val = seg["start"] if isinstance(seg, dict) else seg.start
        end_val = seg["end"] if isinstance(seg, dict) else seg.end
        dialogues.append(
            f"Dialogue: 0,{format_ass_timestamp(start_val)},"
            f"{format_ass_timestamp(end_val)},Default,,0,0,0,,{clean_text}"
        )

    if not dialogues:
        logger.info("No speech segments to write, skipping subtitles.")
        return None

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


def generate_subtitles_ass(
    video_path: str,
    start_time: float = 0.0,
    duration: float = 60.0,
    output_ass_path: Optional[str] = None,
    margin_v: int = 580,
    font_size: int = 36,
    language: Optional[str] = None,
    model_size: str = "tiny",
    segments: Optional[list] = None,
) -> Optional[str]:
    """
    Build an ASS subtitle file for the video segment.

    If `segments` is supplied (list of {"start","end","text"} already rebased to
    clip-relative time), it is used verbatim and Whisper is NOT called. This is
    how the hook text and the subtitles are guaranteed to come from one
    transcription.

    Otherwise the audio is transcribed here (legacy path).
    """
    if not os.path.exists(video_path):
        return None

    temp_wav = None
    try:
        if segments is not None:
            raw_segments = list(segments)
            # Subtitle lines are already clip-relative; the audio slice is not needed.
            if not output_ass_path:
                output_ass_path = str(Path(video_path).with_suffix("")) + "_injected.ass"
            return _write_ass(raw_segments, output_ass_path, font_size, margin_v)

        model = _get_whisper_model(model_size)
        if model is None:
            return None

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

        raw_segments, info = model.transcribe(
            temp_wav,
            language=language,
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        raw_segments = [
            {
                "start": s.start,
                "end": cap_end_to_speech(s.start, s.end, _word_ends(s)),
                "text": s.text,
            }
            for s in raw_segments
        ]

        if not output_ass_path:
            output_ass_path = temp_wav.replace(".wav", ".ass")

        return _write_ass(raw_segments, output_ass_path, font_size, margin_v)
    except Exception as e:
        logger.warning("Subtitle generation failed (continuing without subs): %s", e)
        return None
    finally:
        if temp_wav and os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass
