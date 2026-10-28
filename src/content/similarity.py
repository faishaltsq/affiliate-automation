"""src/content/similarity.py — near-duplicate and repetitive-template guard.

Two failure modes this prevents:
  1. Exact copies — the same post going out twice.
  2. Near-template duplicates — "kok ya tiap X selalu Y" reworded 30 times.

V1 had an anti-repetition window on topics only, so the *shape* of a post could
repeat indefinitely. This guard compares both the words and the skeleton.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, Optional

_STOPWORDS = {
    "yang", "dan", "atau", "tapi", "kalau", "kalo", "jadi", "buat", "untuk",
    "dengan", "sudah", "udah", "dah", "lagi", "aja", "saja", "itu", "ini",
    "saya", "aku", "gue", "kamu", "kalian", "loe", "lo", "dia", "mereka",
    "ada", "tidak", "nggak", "ga", "gak", "bisa", "harus", "perlu", "mau",
    "the", "a", "an", "is", "are", "of", "to", "in", "on", "for",
}


def normalise(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def content_hash(text: str) -> str:
    """Stable hash of the normalised text — the exact-copy check."""
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def tokens(text: str) -> list[str]:
    return [t for t in normalise(text).split() if t not in _STOPWORDS and len(t) > 2]


def skeleton(text: str) -> str:
    """The shape of a post: sentence count plus per-sentence length bands.

    Catches "kok ya tiap pagi selalu macet" vs "kok ya tiap malam selalu lempar"
    — different words, identical template. Lengths are banded rather than exact
    so a one-word difference does not hide a repeated shape.
    """
    text = text or ""
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    sig = []
    for part in parts:
        words = part.split()
        if not words:
            continue
        n = len(words)
        band = "s" if n <= 6 else ("m" if n <= 14 else "l")
        sig.append(band)
    return "-".join(sig)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def similarity(a: str, b: str) -> float:
    """0..1 blend of token overlap and sequence ratio."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    tok = jaccard(tokens(a), tokens(b))
    seq = SequenceMatcher(None, na, nb).ratio()
    return round(0.6 * tok + 0.4 * seq, 4)


@dataclass
class SimilarityVerdict:
    ok: bool
    max_similarity: float = 0.0
    matched_hash: Optional[str] = None
    matched_excerpt: str = ""
    reason: str = ""


DEFAULT_EXACT_THRESHOLD = 1.0      # identical normalised text
DEFAULT_NEAR_THRESHOLD = 0.72      # near-duplicate
DEFAULT_SKELETON_WINDOW = 8        # last N posts compared by template shape


def check_against_history(
    draft_text: str,
    history: Iterable[str],
    hashes: Optional[Iterable[str]] = None,
    near_threshold: float = DEFAULT_NEAR_THRESHOLD,
    skeleton_window: int = DEFAULT_SKELETON_WINDOW,
) -> SimilarityVerdict:
    """Compare a draft against previously published content.

    `history` is the recent post corpus, newest last. `hashes` are the
    content_hash values of the same corpus; when omitted they are computed.
    """
    history = list(history)
    if not history:
        return SimilarityVerdict(ok=True, reason="no history to compare")

    draft_hash = content_hash(draft_text)
    hash_list = list(hashes) if hashes is not None else [content_hash(h) for h in history]

    # 1. Exact copy — fastest, and the one that must never happen.
    if draft_hash in set(hash_list):
        idx = hash_list.index(draft_hash)
        return SimilarityVerdict(
            ok=False,
            max_similarity=1.0,
            matched_hash=draft_hash,
            matched_excerpt=history[idx][:120],
            reason="exact_duplicate",
        )

    # 2. Near-duplicate over the whole window.
    best = 0.0
    best_excerpt = ""
    for text in history:
        score = similarity(draft_text, text)
        if score > best:
            best = score
            best_excerpt = text[:120]
    if best >= near_threshold:
        return SimilarityVerdict(
            ok=False,
            max_similarity=best,
            matched_excerpt=best_excerpt,
            reason=f"near_duplicate (>{near_threshold})",
        )

    # 3. Template repetition: same sentence-length skeleton too often.
    draft_skel = skeleton(draft_text)
    if draft_skel:
        recent = history[-skeleton_window:]
        repeats = sum(1 for h in recent if skeleton(h) == draft_skel)
        if repeats >= max(2, skeleton_window // 2):
            return SimilarityVerdict(
                ok=False,
                max_similarity=best,
                matched_excerpt=best_excerpt,
                reason=f"repetitive_template ({repeats}/{len(recent)} share this shape)",
            )

    return SimilarityVerdict(ok=True, max_similarity=best, reason="unique")


def opening_similarity(draft_text: str, history: Iterable[str]) -> float:
    """How much the first clause resembles recent openings.

    "kok ya tiap…" twenty times in a row is the single most recognisable
    content-farm tell, so openings get their own check.
    """
    history = list(history)
    if not history:
        return 0.0
    first = _opening(draft_text)
    if not first:
        return 0.0
    best = 0.0
    for text in history:
        other = _opening(text)
        if not other:
            continue
        best = max(best, similarity(first, other))
    return best


def _opening(text: str, words: int = 4) -> str:
    parts = re.split(r"(?<=[,.!?])\s+", (text or "").strip())
    head = parts[0] if parts else ""
    return " ".join(head.split()[:words])
