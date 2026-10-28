"""src/commerce/relevance.py — does this product actually solve this post's problem?

V1's `comment_engine.py` gated affiliate replies on engagement: enough views,
enough replies, old enough. A post about a flood could receive a link to a
knit sweater because the numbers lined up. Engagement is not relevance.

V2's relevance engine asks two questions and both must be yes:
  1. Does the product directly solve the problem the post states?
  2. Does the audience context support a recommendation right now?

If either is no, the affiliate slot is blocked — regardless of views, likes,
or replies. Acceptance criterion #8.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.commerce.catalog import Product
from src.core.models import Product as ProductModel

log = None


def _logger():
    global log
    if log is None:
        from src.logger import get_logger
        log = get_logger("commerce_relevance")
    return log


# Product category -> the audience problems it can legitimately address.
# Kept deliberately narrow: a category only maps to problems it truly solves.
CATEGORY_PROBLEM_MAP = {
    "power_bank": [
        "baterai", "cas", "charging", "daya", "listrik", "habis", "boros",
        "power bank", "adaptor", "kabel",
    ],
    "charger": [
        "cas", "charging", "adaptor", "kabel", "daya", "listrik", "port",
        "fast charging",
    ],
    "cable": [
        "kabel", "cas", "charging", "kusut", "putus", "port", "usb",
    ],
    "phone_stand": [
        "meja", "stand", "pegangan", "posisi", "leher", "sempit", "setup",
    ],
    "earphone": [
        "earphone", "headphone", "audio", "musik", "volume", "kabel",
        "bluetooth",
    ],
    "desk_accessory": [
        "meja", "setup", "rapi", "organizer", "kabel", "ruang", "sempit",
    ],
    "storage": [
        "memori", "penyimpanan", "file", "backup", "penuh", "ssd", "flash",
    ],
    "laptop_accessory": [
        "laptop", "port", "usb", "hub", "dongle", "adaptor", "meja",
    ],
}

# Problems a product must never be attached to, whatever the category.
SENSITIVE_PROBLEM_RE = re.compile(
    r"\b(?:bencana|banjir|gempa|tsunami|longsor|erupsi|kebakaran|kecelakaan|"
    r"tewas|meninggal|korban|duka|wabah|krisis kesehatan|kekerasan|"
    r"pelecehan|pembunuhan|kelaparan)\b",
    re.I,
)

# A product must match at least this many distinct problem terms to count as
# relevant, even when the ratio is low because the sentence is long.
MIN_MATCHED_TERMS = 2

# Words that indicate the post is about buying/choosing rather than using.
BUYING_INTENT_RE = re.compile(
    r"\b(?:beli|membeli|cari|pilih|memilih|bandingin|membandingkan|rekomendasi|"
    r"saran|worth it|layak|perlu|butuh|cariin|cari yang)\b",
    re.I,
)


@dataclass
class RelevanceVerdict:
    relevant: bool
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.relevant


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return {t for t in text.split() if len(t) > 2}


def _category_terms(product: Product) -> set[str]:
    """Every term that could connect this product to a problem."""
    terms = set(_tokens(product.category))
    terms |= set(_tokens(product.name))
    # Explicit category mapping wins over name tokens.
    key = (product.category or "").lower().replace(" ", "_")
    for cat, words in CATEGORY_PROBLEM_MAP.items():
        if cat in key or key in cat:
            terms |= set(words)
    return terms


def check_relevance(
    product: Product,
    post_text: str,
    audience_problem: str = "",
    pillar: str = "",
    min_score: float = 0.15,
) -> RelevanceVerdict:
    """Decide whether this product may be attached to this post.

    Blocking conditions (any one blocks):
      * the post or its problem is about a disaster or other sensitive event
      * the pillar is not one that permits a recommendation
      * the product's terms do not overlap the post/problem at all
      * the post shows no buying or choosing intent
    """
    verdict = RelevanceVerdict(relevant=False)
    combined = f"{post_text} {audience_problem}".strip()

    if SENSITIVE_PROBLEM_RE.search(combined):
        verdict.blocked_by.append("sensitive_topic")
        verdict.reasons.append(
            "post or problem concerns a disaster/sensitive event; "
            "no product may be attached"
        )
        return verdict

    if pillar and pillar not in ("affiliate", "discoveries", "practical_tips"):
        verdict.blocked_by.append("pillar_not_commercial")
        verdict.reasons.append(
            f"pillar '{pillar}' does not permit a product recommendation"
        )
        return verdict

    if not combined.strip():
        verdict.blocked_by.append("no_context")
        verdict.reasons.append("no post text or audience problem to match against")
        return verdict

    product_terms = _category_terms(product)
    context_tokens = _tokens(combined)
    matched = product_terms & context_tokens

    if not matched:
        verdict.blocked_by.append("no_term_overlap")
        verdict.reasons.append(
            "product terms do not appear in the post or its stated problem"
        )
        return verdict

    # Score against the CONTEXT, not the product's vocabulary. A category map
    # legitimately lists many synonyms, so dividing by the product's term count
    # punishes exactly the products that are well described. What matters is
    # how much of the reader's problem the product actually addresses.
    #
    # A pure ratio is still too brittle: "Gue lagi cari power bank yang
    # kapasitasnya nggak bohong" is a long sentence whose subject is only two
    # words. So relevance requires BOTH a minimum ratio and a minimum number
    # of distinct matched terms.
    score = len(matched) / max(1, len(context_tokens))
    verdict.score = round(min(1.0, score), 4)
    verdict.matched_terms = sorted(matched)

    if verdict.score < min_score and len(matched) < MIN_MATCHED_TERMS:
        verdict.blocked_by.append("weak_overlap")
        verdict.reasons.append(
            f"overlap {verdict.score:.2f} below threshold {min_score} and only "
            f"{len(matched)} matched term(s)"
        )
        return verdict

    # Buying intent: a recommendation needs a decision to inform.
    if not BUYING_INTENT_RE.search(combined):
        verdict.blocked_by.append("no_buying_intent")
        verdict.reasons.append(
            "post shows no buying or choosing intent; a link would be "
            "inserted rather than helpful"
        )
        return verdict

    verdict.relevant = True
    verdict.reasons.append(
        f"product matches {len(matched)} term(s) of the stated problem"
    )
    return verdict


def relevance_is_independent_of_engagement() -> bool:
    """Documentation anchor: engagement never enters this decision.

    The signature above takes no views, likes, or replies — by design. A post
    with 10,000 views about a flood still gets no product link.
    """
    return True
