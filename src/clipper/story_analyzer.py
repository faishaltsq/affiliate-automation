import re
import os
from pathlib import Path
from typing import List, Dict

def natural_sort_paths(paths: List[str]) -> List[str]:
    """Sort paths numerically so that part 2 comes before part 10."""
    def _key(p: str):
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(p).name)]
    return sorted(paths, key=_key)

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
