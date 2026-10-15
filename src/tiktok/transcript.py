"""transcript.py — The single transcription for one long source video.

Deliberately does NOT use YouTube captions: the transcript built here is the
same data that gets burned as subtitles, so the on-screen hook and the
subtitles can never disagree.
"""

import logging
import os
import subprocess
import tempfile
from typing import Dict, List, Optional

from src.tiktok.subtitles import _get_whisper_model

logger = logging.getLogger("tiktok.transcript")

MIN_SEGMENT_CHARS = 2


def transcribe_source(
    video_path: str,
    language: Optional[str] = None,
    model_size: str = "base",
) -> Dict:
    """Transcribe an ENTIRE video once. Returns {"segments", "language", "duration"}.

    `language=None` means auto-detect — an English video stays English.
    There is intentionally no duration cap: hook detection needs the whole source.
    """
    if not video_path or not os.path.exists(video_path):
        logger.error("Source video missing: %s", video_path)
        return {"segments": [], "language": None, "duration": 0.0}

    model = _get_whisper_model(model_size)
    if model is None:
        logger.error("Whisper model '%s' unavailable", model_size)
        return {"segments": [], "language": None, "duration": 0.0}

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, "full.wav")
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            audio_path,
        ]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception as e:
            logger.error("Audio extraction failed for %s: %s", video_path, e)
            return {"segments": [], "language": None, "duration": 0.0}

        if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
            logger.warning("Extracted audio too small for %s", video_path)
            return {"segments": [], "language": None, "duration": 0.0}

        try:
            raw, info = model.transcribe(
                audio_path,
                language=language,
                beam_size=1,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )
        except Exception as e:
            logger.error("Transcription failed for %s: %s", video_path, e)
            return {"segments": [], "language": None, "duration": 0.0}

        segments: List[Dict] = []
        for s in raw:
            text = (s.text or "").strip()
            if len(text) < MIN_SEGMENT_CHARS:
                continue
            segments.append({
                "start": round(float(s.start), 2),
                "end": round(float(s.end), 2),
                "text": text,
            })

        detected = getattr(info, "language", None) or language
        total = segments[-1]["end"] if segments else 0.0
        logger.info(
            "Transcribed %s: %d segments, language=%s, %.0fs",
            os.path.basename(video_path), len(segments), detected, total,
        )
        return {"segments": segments, "language": detected, "duration": total}


def slice_segments(
    segments: List[Dict],
    window_start: float,
    window_end: float,
) -> List[Dict]:
    """Cut the transcript to a hook window and REBASE timestamps to clip-relative.

    Subtitles are burned onto a clip that starts at 0, so a segment at 205s in a
    window starting at 200s must become 5s.
    """
    out: List[Dict] = []
    for s in segments or []:
        if s["end"] <= window_start or s["start"] >= window_end:
            continue
        start = max(0.0, s["start"] - window_start)
        end = min(float(window_end - window_start), s["end"] - window_start)
        if end - start <= 0.05:
            continue
        out.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": s["text"],
        })
    return out
