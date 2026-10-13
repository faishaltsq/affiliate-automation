import logging
from typing import List, Dict

logger = logging.getLogger("clipper.trimmer")

BASA_BASI_PHRASES = [
    "selamat datang",
    "kenalan dulu",
    "senang kenal",
    "dengan siapa",
    "apa kabar",
    "terima kasih banyak",
    "halo bu",
    "halo pak",
    "salam kenal",
]

def is_basa_basi(text: str) -> bool:
    """Check if the text represents conversational pleasantries or formal greetings."""
    t = text.lower().strip()
    return any(p in t for p in BASA_BASI_PHRASES)

def extract_dialogue_beats(
    segments: List[Dict],
    target_duration: float = 60.0,
    min_beat_duration: float = 8.0,
) -> List[Dict]:
    """
    Extract substantial, continuous dialogue beats (10–25s) from transcribed speech segments,
    skipping opening pleasantries ('basa-basi') and maintaining conversational sentence boundaries.
    """
    if not segments:
        return []

    # 1. Skip opening pleasantries at the very beginning of the clip
    filtered = []
    skip_initial = True
    for s in segments:
        text = s.get("text", "")
        if skip_initial and is_basa_basi(text):
            continue
        skip_initial = False
        filtered.append(s)

    if not filtered:
        filtered = segments

    # 2. Cluster consecutive speech segments into continuous beats
    beats = []
    current_beat_start = filtered[0]["start"]
    current_beat_end = filtered[0]["end"]
    accumulated_duration = 0.0

    max_beat_duration = min(30.0, target_duration)
    for s in filtered[1:]:
        pause = s["start"] - current_beat_end
        current_dur = current_beat_end - current_beat_start
        # If pause between spoken sentences is natural (< 1.5s) AND current beat hasn't exceeded max duration
        if pause < 1.5 and current_dur < max_beat_duration:
            current_beat_end = s["end"]
        else:
            # Beat boundary
            dur = current_beat_end - current_beat_start
            if dur >= min_beat_duration:
                beats.append({"start": round(current_beat_start, 2), "end": round(current_beat_end, 2), "duration": round(dur, 2)})
                accumulated_duration += dur
                if accumulated_duration >= target_duration:
                    break
            # Start next beat
            current_beat_start = s["start"]
            current_beat_end = s["end"]

    # Append the last accumulated beat
    dur = current_beat_end - current_beat_start
    if dur >= 3.0 and accumulated_duration < target_duration:
        # Only append trailing beat if it doesn't severely overshoot target duration
        if accumulated_duration == 0 or (accumulated_duration + dur <= target_duration * 1.15):
            beats.append({"start": round(current_beat_start, 2), "end": round(current_beat_end, 2), "duration": round(dur, 2)})

    return beats
