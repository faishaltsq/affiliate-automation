"""hook_finder.py — Pick N non-overlapping high-retention windows from ONE long source.

Reads the SAME segments that will be burned as subtitles, so the hook text is
always supported by what is actually said on screen.
"""

import json
import logging
from typing import Dict, List, Optional

import requests

from src.clipper.punchline import LLM_API_KEY, LLM_ENDPOINT, LLM_MODEL, LLM_FALLBACK_MODEL, _llm_post, _parse_json_loose

logger = logging.getLogger("tiktok.hook_finder")

MIN_CLIP_SEC = 45.0
MAX_CLIP_SEC = 90.0
OVERLAP_PAD_SEC = 2.0
MAX_TRANSCRIPT_CHARS = 60000


def _overlaps(a: Dict, b: Dict, pad: float = OVERLAP_PAD_SEC) -> bool:
    return not (a["end"] + pad <= b["start"] or b["end"] + pad <= a["start"])


def _sanitize_windows(raw: list, total_duration: float, count: int) -> List[Dict]:
    """Clamp, de-overlap, sort chronologically and cap LLM-proposed windows. Pure function."""
    clean: List[Dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            continue

        start = max(0.0, start)
        end = min(float(total_duration), end)
        if end - start <= 1.0:
            continue

        if end - start < MIN_CLIP_SEC:
            end = min(float(total_duration), start + MIN_CLIP_SEC)
        if end - start > MAX_CLIP_SEC:
            end = start + MAX_CLIP_SEC
        duration = end - start
        if duration < 10.0:
            continue

        cand = {
            "start": round(start, 2),
            "end": round(end, 2),
            "duration": round(duration, 2),
            "hook_title": (item.get("hook_title") or "HIGHLIGHT").strip().upper()[:60],
            "reason": (item.get("reason") or "").strip()[:200],
        }
        if any(_overlaps(cand, c) for c in clean):
            continue
        clean.append(cand)
        if len(clean) >= count:
            break

    clean.sort(key=lambda w: w["start"])
    return clean


def find_hook_windows(
    segments: List[Dict],
    total_duration: float,
    count: int = 3,
    source_title: str = "",
    source_language: Optional[str] = None,
) -> List[Dict]:
    """Ask the LLM for `count` distinct hook windows from an already-built transcript."""
    count = max(1, min(int(count), 8))
    if not segments:
        logger.warning("No transcript segments supplied to hook finder")
        return []

    transcript_text = "\n".join(
        f"({s['start']}s - {s['end']}s): \"{s['text']}\"" for s in segments
    )[:MAX_TRANSCRIPT_CHARS]

    lang_note = source_language or "the transcript's own language"
    prompt = f"""You are a short-form video editor. Below is the FULL transcript of one long video.

Source title: "{source_title}"
Total duration: {total_duration:.0f} seconds.

Pick exactly {count} DIFFERENT moments that would each work as a standalone vertical short clip.

Rules:
1. The windows must NOT overlap each other.
2. Each window must be {MIN_CLIP_SEC:.0f}-{MAX_CLIP_SEC:.0f} seconds long.
3. Prefer moments with a clear payoff: a reveal, a build, a catch, a satisfying result, a strong statement.
4. Spread the windows across the whole video. Do not cluster them near the start.
5. `hook_title` = short curiosity hook, maximum 6 words.
   Write it in {lang_note} — do NOT translate it.
   It must be FACTUALLY supported by the transcript text of that window.
   Never invent facts, names or numbers that are not in the transcript.
6. Output ONLY raw JSON, no markdown fences, no commentary:
[
  {{"start": 123.4, "end": 198.4, "hook_title": "SHORT HOOK", "reason": "why this works"}}
]

Transcript:
{transcript_text}"""

    try:
        body = _llm_post(prompt, temperature=0.4, timeout=90)
        if body is None:
            return []

        content = body["choices"][0]["message"]["content"].strip()
        raw = _parse_json_loose(content)
        if isinstance(raw, dict):
            raw = raw.get("clips", []) or raw.get("windows", [])
        return _sanitize_windows(raw, total_duration, count)
    except Exception as e:
        logger.error("Hook window selection failed: %s", e)
        return []
