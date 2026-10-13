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
    Extract substantial, continuous dialogue beats from transcribed speech segments,
    skipping opening pleasantries ('basa-basi') and maintaining conversational sentence boundaries.
    Never cuts off mid-explanation: completes the current spoken sentence/thought before breaking.
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

    for s in filtered[1:]:
        pause = s["start"] - current_beat_end
        current_dur = current_beat_end - current_beat_start
        projected_dur = s["end"] - current_beat_start

        # Keep merging if pause is short (< 1.6s) AND projected beat does not severely overshoot target
        if pause < 1.6 and (projected_dur <= target_duration * 1.15 or current_dur < min_beat_duration):
            current_beat_end = s["end"]
        else:
            # Beat boundary at natural pause
            dur = current_beat_end - current_beat_start
            if dur >= min_beat_duration:
                beats.append({"start": round(current_beat_start, 2), "end": round(current_beat_end, 2), "duration": round(dur, 2)})
                accumulated_duration += dur
                if accumulated_duration >= target_duration:
                    break
            # Start next beat
            current_beat_start = s["start"]
            current_beat_end = s["end"]

    # Append trailing beat only if accumulated duration is still well below target
    dur = current_beat_end - current_beat_start
    if dur >= 3.0 and accumulated_duration < target_duration * 0.85:
        beats.append({"start": round(current_beat_start, 2), "end": round(current_beat_end, 2), "duration": round(dur, 2)})

    return beats
