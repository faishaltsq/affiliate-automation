import re
import os
from pathlib import Path
from typing import List, Dict

def natural_sort_paths(paths: List[str]) -> List[str]:
    """Sort paths numerically so that part 2 comes before part 10."""
    def _key(p: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).name)]
    return sorted(paths, key=_key)

import tempfile
import subprocess
from src.clipper.trimmer import extract_dialogue_beats

def build_story_narrative_beats(
    hero_file: str,
    outro_file: Optional[str] = None,
    target_duration: float = 65.0,
) -> List[Dict]:
    """
    Extract natural, continuous dialogue beats from the hero story video
    and append outro concluding beat if available.
    """
    beats = []
    outro_dur = 0.0
    if outro_file and os.path.exists(outro_file):
        try:
            from src.clipper.editor import get_video_duration
            full_odur = get_video_duration(outro_file)
            # Use full outro if <= 20s so explanation/CTA is never severed mid-sentence
            outro_dur = round(full_odur, 2) if full_odur <= 20.0 else 15.0
        except Exception:
            outro_dur = 12.0

    rem_hero_dur = max(50.0, target_duration - outro_dur)

    try:
        from src.clipper.subtitles import _get_whisper_model, DEFAULT_INITIAL_PROMPT
        model = _get_whisper_model("base")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_wav = tmp.name

        subprocess.run(
            ["ffmpeg", "-y", "-i", hero_file, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_wav],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        segs, _ = model.transcribe(
            tmp_wav,
            language="id",
            initial_prompt=DEFAULT_INITIAL_PROMPT,
            beam_size=1,
        )
        segments = [{"start": s.start, "end": s.end, "text": s.text} for s in segs]
        try:
            os.remove(tmp_wav)
        except Exception:
            pass

        raw_beats = extract_dialogue_beats(segments, target_duration=rem_hero_dur)
        for b in raw_beats:
            beats.append({
                "file": hero_file,
                "start": b["start"],
                "end": b["end"],
                "duration": b["duration"],
            })
    except Exception as e:
        logger.warning(f"Could not build automated narrative beats: {e}")
        # Fallback to single chunk
        beats.append({
            "file": hero_file,
            "start": 0.0,
            "end": rem_hero_dur,
            "duration": rem_hero_dur,
        })

    if outro_file and os.path.exists(outro_file):
        beats.append({
            "file": outro_file,
            "start": 0.0,
            "end": outro_dur,
            "duration": outro_dur,
        })

    return beats


def select_story_footages(campaign_title: str, transcripts_by_file: Dict[str, str]) -> List[str]:
    """
    Select the primary coherent story footage and matching outro/supporting clips,
    preventing arbitrary jumps across completely unrelated subjects or customers.
    """
    if not transcripts_by_file:
        return []

    # If only 1 or 2 footages exist, use them directly
    if len(transcripts_by_file) <= 2:
        return natural_sort_paths(list(transcripts_by_file.keys()))

    title_words = set(re.findall(r"\w+", campaign_title.lower()))
    # Stopwords in Indonesian
    stopwords = {"dan", "di", "ke", "dari", "yang", "untuk", "pada", "ini", "itu", "atau", "part", "juan"}
    clean_title_keywords = [w for w in title_words if len(w) > 2 and w not in stopwords]

    scored = []
    outro_files = []

    for f, text in transcripts_by_file.items():
        text_lower = text.lower()
        # Detect outro / CTA clips
        if any(w in text_lower for w in ["sekali lagi di", "terima emas apapun", "singgah aja", "terima kasih", "hubungi kami"]):
            outro_files.append(f)
            continue

        # Score matching with campaign title keywords
        score = sum(2 for kw in clean_title_keywords if kw in text_lower)
        # Bonus for high-intent story words
        for high_intent in ["palsu", "bermasalah", "surat", "curiga", "tangkap", "nangkep", "cek", "kadar"]:
            if high_intent in text_lower and high_intent in clean_title_keywords:
                score += 5
            elif high_intent in text_lower:
                score += 2

        scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)

    chosen = []
    # Take the top matched story clip
    if scored and scored[0][0] > 0:
        chosen.append(scored[0][1])
        # If the runner-up is also closely tied to the same narrative (score close to top), include it
        if len(scored) > 1 and scored[1][0] >= scored[0][0] * 0.7:
            chosen.append(scored[1][1])
    else:
        # Fallback to the first couple files
        chosen = [f for _, f in scored[:2]] if scored else list(transcripts_by_file.keys())[:2]

    # Include outro/conclusion file if found
    for out_f in outro_files:
        if out_f not in chosen:
            chosen.append(out_f)
            break

    return natural_sort_paths(chosen)
