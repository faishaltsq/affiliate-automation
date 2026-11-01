"""src/persona/voice.py — variation inside one identity.

V1 switched between six unrelated personalities, so a reader following the
account met a different person every post. V2 keeps ONE character and varies
only the surface: format, opening, length, mood, goal.

The anti-repetition window is what stops the same variation combo from
recurring: a draft that repeats the last N combinations is pushed toward an
unused one instead of being rejected outright.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Optional

SINGLE_FORMATS = [
    "single_snappy",       # one or two punchy sentences
    "short_paragraph",     # one tight paragraph, one idea
    "multi_paragraph",     # 2-3 short paragraphs when the idea earns it
    "comparison",          # A vs B trade-off
]
THREAD_FORMATS = [
    "mini_thread",         # 2-4 connected parts, only when justified
]
FORMATS = SINGLE_FORMATS + THREAD_FORMATS

OPENINGS = [
    "observasi",           # "kok ya tiap…"
    "pernyataan_lugas",    # state the thing
    "pertanyaan_jujur",    # a real question, not engagement bait
    "anekdot_singkat",     # a tiny scene
    "kontras",             # expectation vs reality
    "langsung_ke_inti",    # no opening at all
]

LENGTHS = ["sangat_pendek", "pendek", "sedang"]

MOODS = ["ringan", "reflektif", "kritis", "antusias", "netral"]

GOALS = [
    "edukasi",         # teach one checkable thing
    "validasi",        # "you're not the only one"
    "peringatan",      # flag a real trap
    "rekomendasi",     # suggest something genuinely useful
    "diskusi",         # invite a real trade-off discussion
]

ALL_DIMENSIONS = ("format", "opening", "length", "mood", "goal")


@dataclass
class VoiceVariation:
    format: str = "single_snappy"
    opening: str = "observasi"
    length: str = "pendek"
    mood: str = "ringan"
    goal: str = "edukasi"

    def key(self) -> tuple:
        return (self.format, self.opening, self.length, self.mood, self.goal)

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "opening": self.opening,
            "length": self.length,
            "mood": self.mood,
            "goal": self.goal,
        }


def random_variation(
    rng: Optional[random.Random] = None,
    allow_threads: bool = False,
) -> VoiceVariation:
    r = rng or random
    fmt_pool = FORMATS if allow_threads else SINGLE_FORMATS
    return VoiceVariation(
        format=r.choice(fmt_pool),
        opening=r.choice(OPENINGS),
        length=r.choice(LENGTHS),
        mood=r.choice(MOODS),
        goal=r.choice(GOALS),
    )


def recent_keys(keys: Iterable[tuple], window: int = 6) -> set[tuple]:
    """The last `window` variation keys, as a set for O(1) membership."""
    return set(list(keys)[-window:])


def varied_variation(
    history: Iterable[tuple],
    rng: Optional[random.Random] = None,
    attempts: int = 12,
    allow_threads: bool = False,
) -> VoiceVariation:
    """Pick a variation that has not been used recently.

    Single post by default. Threads are only considered if allow_threads=True.
    Falls back to a random one only when the recent window covers every
    combination.
    """
    r = rng or random
    seen = recent_keys(history)
    for _ in range(attempts):
        candidate = random_variation(r, allow_threads=allow_threads)
        if candidate.key() not in seen:
            return candidate
    return random_variation(r, allow_threads=allow_threads)


def should_allow_thread(
    idea: Optional[object] = None,
    evidence: str = "",
    pillar: str = "",
) -> bool:
    """Determine whether an idea has verified information density justifying a multi-part thread.

    Strict rules:
    - Never allow threads for relatable or opinions pillars (mundane observations/feelings must never become threads).
    - Only practical_tips or discoveries with >= 2 distinct, substantive points can earn a thread.
    """
    import re

    p = (pillar or getattr(idea, "pillar", "") or "").lower()
    if p in ("relatable", "opinions", "opinion"):
        return False

    text_blocks = []
    if idea:
        for attr in ("premise", "angle", "title", "description", "why_it_matters", "hook"):
            val = getattr(idea, attr, None)
            if val and isinstance(val, str):
                text_blocks.append(val)
    if evidence:
        text_blocks.append(evidence)

    combined = " ".join(text_blocks).lower()
    if not combined:
        return False

    # Mundane observations / simple daily annoyances must never become threads
    trivial_keywords = ["kabel", "tas", "baterai", "ngambek", "kusut", "nyelip", "curhat", "perasaan"]
    if any(k in combined for k in trivial_keywords) and not ("langkah" in combined or "perbandingan" in combined):
        return False

    # Count distinct substantive steps or structured comparisons
    step_matches = re.findall(
        r"(?:^|\s)(?:\d+[\.\)]|langkah\s+\d+|tahap\s+\d+|pertama|kedua|ketiga|opsi\s+[ab12])",
        combined,
    )
    if len(step_matches) >= 2:
        return True

    bullets = re.findall(r"(?:^|\n)\s*[-*•]\s+([^\n]+)", combined)
    if len(bullets) >= 2:
        return True

    return False


def decide_format(
    idea: Optional[object] = None,
    evidence: str = "",
    pillar: str = "",
    history: Optional[Iterable[tuple]] = None,
    rng: Optional[random.Random] = None,
) -> VoiceVariation:
    """Contextual format selection: single post by default.

    Multi-part thread is only chosen when verified information density
    justifies sequential reading (>= 2 substantial takeaways).
    """
    allow_thread = should_allow_thread(idea, evidence=evidence, pillar=pillar)
    if allow_thread and history:
        recent = list(history)[-3:]
        if any(len(h) > 0 and h[0] == "mini_thread" for h in recent):
            allow_thread = False

    return varied_variation(history or [], rng=rng, allow_threads=allow_thread)

def word_budget(length: str) -> tuple[int, int]:
    """(min, max) words for a length label."""
    return {
        "sangat_pendek": (8, 25),
        "pendek": (20, 45),
        "sedang": (40, 110),
    }.get(length, (20, 45))


def structure_for_format(fmt: str) -> str:
    return {
        "single_snappy": "single",
        "short_paragraph": "single",
        "multi_paragraph": "single",
        "comparison": "single",
        "mini_thread": "thread",
    }.get(fmt, "single")


def opening_guide(opening: str) -> str:
    return {
        "observasi": "Buka dengan observasi kebiasaan yang orang kenali.",
        "pernyataan_lugas": "Buka dengan pernyataan lugas, langsung ke inti.",
        "pertanyaan_jujur": "Buka dengan satu pertanyaan yang benar-benar ingin dijawab.",
        "anekdot_singkat": "Buka dengan adegan singkat yang konkret.",
        "kontras": "Buka dengan harapan vs kenyataan.",
        "langsung_ke_inti": "Tanpa pembuka sama sekali — kalimat pertama adalah isinya.",
    }.get(opening, "Buka natural, tanpa klise.")


def mood_guide(mood: str) -> str:
    return {
        "ringan": "Ringan, ada humor kecil, tidak dipaksakan.",
        "reflektif": "Reflektif dan tenang.",
        "kritis": "Kritis terhadap hype, tapi tetap adil dan tidak merendahkan.",
        "antusias": "Antusias atas hal yang memang bagus, tanpa berlebihan.",
        "netral": "Netral dan informatif.",
    }.get(mood, "Netral dan informatif.")


def goal_guide(goal: str) -> str:
    return {
        "edukasi": "Selesaikan dengan satu hal yang bisa dibaca/dicek pembaca.",
        "validasi": "Buat pembaca merasa 'oh, bukan cuma gue'.",
        "peringatan": "Tandai satu jebakan yang nyata.",
        "rekomendasi": "Sarankan satu hal yang benar-benar berguna.",
        "diskusi": "Ajak satu pertukaran trade-off yang konkret.",
    }.get(goal, "Sampaikan satu ide yang berguna.")


def length_guide(length: str) -> str:
    lo, hi = word_budget(length)
    return (
        f"Panjang mengikuti substansi (orientasi {lo}-{hi} kata). "
        "Prinsip utama: jika observasi sudah utuh dan kuat dalam 15-20 kata, jangan dipanjangkan. "
        "Jika butuh membedah trade-off nyata atau langkah teknis, 60-75 kata diperbolehkan. "
        "Setiap kalimat harus membawa substansi; hilangkan kalimat pengisi."
    )


def variation_prompt(v: VoiceVariation) -> str:
    return "\n".join([
        f"- Format: {v.format}",
        f"- Pembuka: {opening_guide(v.opening)}",
        f"- Panjang: {length_guide(v.length)}",
        f"- Mood: {mood_guide(v.mood)}",
        f"- Tujuan: {goal_guide(v.goal)}",
    ])
