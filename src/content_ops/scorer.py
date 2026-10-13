"""
scorer.py — Content Candidate Evaluation Engine based on Faceless SOP.
Evaluates candidate videos on a 1-5 scale across 5 criteria (minimum 4.0/5.0 to pass).
"""

import logging
from typing import Optional

logger = logging.getLogger("content_ops.scorer")

# Keywords that indicate peak moments / strong visual engagement
PEAK_ACTION_KEYWORDS = {
    "mancing": [
        "strike", "catch", "monster", "huge", "big fish", "bass", "giant",
        "toman", "tenggiri", "casting", "hook", "lure", "tarikan", "babon",
        "got one", "reel in", "net", "rod bend", "underwater strike"
    ],
    "camping": [
        "fire", "bushcraft", "shelter", "rain", "steak", "cook", "wood stove",
        "tarp", "solo camping", "cabin", "survival", "axe", "campfire", "bbq",
        "primitive", "cozy", "asmr", "heavy rain", "snow camping"
    ],
    "outdoor_satisfying": [
        "satisfying", "oddly satisfying", "restoration", "waterfall", "clearing",
        "carving", "woodworking", "relaxing", "crafting", "natural spring"
    ]
}

# Negative keywords: low quality, talking head, podcasts, vlogs, compilation spam
NEGATIVE_KEYWORDS = [
    "podcast", "interview", "news", "talk show", "reaction", "prank",
    "review hp", "drama", "unboxing phone", "tutorial bicara"
]


def score_candidate(video: dict, target_niche: str = "mancing") -> dict:
    """
    Score candidate video against the 5 SOP criteria.
    Returns:
        {
            "passed": bool,
            "overall_score": float,  # 1.0 - 5.0
            "scores": {
                "retention": int,
                "peak_moment": int,
                "visual_quality": int,
                "niche_fit": int,
                "transformability": int,
            },
            "reasons": list[str]
        }
    """
    title = (video.get("title") or "").lower()
    channel = (video.get("channel") or "").lower()
    duration = int(video.get("duration") or 0)
    view_count = int(video.get("view_count") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    chapters = video.get("chapters") or []

    scores = {}
    reasons = []

    # 1. Retention proof (views, engagement signals)
    # If view_count not provided via search, infer from channel reputation or title virality
    if view_count > 100_000:
        retention_score = 5
        reasons.append("Tingkat views tinggi (>100k views)")
    elif view_count > 20_000:
        retention_score = 4
        reasons.append("Views solid (>20k views)")
    else:
        # Check title engagement cues
        if any(w in title for w in ["part 2", "gila", "record", "rare", "monster", "epic", "asmr", "viral"]):
            retention_score = 4
            reasons.append("Pola judul mengandung engagement magnet")
        else:
            retention_score = 3
    scores["retention"] = retention_score

    # 2. Peak moment in first 30s or clear chapters
    niche_peaks = PEAK_ACTION_KEYWORDS.get(target_niche, PEAK_ACTION_KEYWORDS["mancing"])
    has_peak = any(pk in title for pk in niche_peaks)
    has_action_chapter = False
    for ch in chapters:
        ch_title = (ch.get("title") or "").lower()
        if any(pk in ch_title for pk in niche_peaks):
            has_action_chapter = True
            break

    if has_action_chapter or (has_peak and duration <= 90):
        peak_score = 5
        reasons.append("Momen puncak sangat jelas terdeteksi (strike/action/chapter)")
    elif has_peak or duration <= 60:
        peak_score = 4
        reasons.append("Potensi aksi tinggi dalam durasi pendek")
    else:
        peak_score = 3
        reasons.append("Momen puncak perlu scrubbing manual")
    scores["peak_moment"] = peak_score

    # 3. Clean visual (resolution, no negative flags)
    is_hd = (height >= 1080 or width >= 1080)
    has_negative = any(neg in title for neg in NEGATIVE_KEYWORDS)
    if is_hd and not has_negative:
        visual_score = 5
        reasons.append("Resolusi FHD/4K bersih tanpa indikasi talking head")
    elif not has_negative:
        visual_score = 4
        reasons.append("Kualitas visual memadai untuk 9:16 reframe")
    else:
        visual_score = 2
        reasons.append("Terdeteksi format video yang tidak cocok (talking/vlog)")
    scores["visual_quality"] = visual_score

    # 4. Niche fit
    if target_niche == "mancing":
        mancing_cues = ["fish", "mancing", "strike", "angler", "river", "sea", "lake", "bass", "tuna", "toman", "umpan"]
        fit_count = sum(1 for c in mancing_cues if c in title or c in channel)
        if fit_count >= 2:
            niche_score = 5
            reasons.append("Sangat spesifik niche mancing")
        elif fit_count == 1:
            niche_score = 4
            reasons.append("Cocok niche mancing")
        else:
            niche_score = 2
            reasons.append("Di luar niche mancing")
    elif target_niche == "camping":
        camping_cues = ["camp", "bushcraft", "survival", "rain", "shelter", "forest", "wood", "stove", "tent", "tarp"]
        fit_count = sum(1 for c in camping_cues if c in title or c in channel)
        if fit_count >= 2:
            niche_score = 5
            reasons.append("Sangat spesifik niche camping/bushcraft")
        elif fit_count == 1:
            niche_score = 4
            reasons.append("Cocok niche camping")
        else:
            niche_score = 2
            reasons.append("Di luar niche camping")
    else:
        niche_score = 4
        reasons.append("Cocok kategori outdoor satisfying")
    scores["niche_fit"] = niche_score

    # 5. Transformability (Can we make a distinct, non-duplicate version?)
    # High score if footage allows clean center crop, color grading, custom text overlay, custom pacing
    if not has_negative and niche_score >= 4:
        transform_score = 5
        reasons.append("Sangat fleksibel ditransformasi (reframe, unsharp, color grade, custom hook)")
    elif not has_negative:
        transform_score = 4
        reasons.append("Bisa ditransformasi dengan aman")
    else:
        transform_score = 2
        reasons.append("Beresiko duplikasi atau sulit di-reframe")
    scores["transformability"] = transform_score

    overall = sum(scores.values()) / len(scores)
    passed = overall >= 4.0 and all(s >= 3 for s in scores.values())

    return {
        "passed": passed,
        "overall_score": round(overall, 2),
        "scores": scores,
        "reasons": reasons,
    }
