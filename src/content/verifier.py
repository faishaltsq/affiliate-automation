"""src/content/verifier.py — claim provenance and evidence policy.

A draft may only state a fact it can point at. This module is the mechanical
half of that rule: it extracts every checkable claim from a draft and asks
whether the supplied evidence actually supports it.

V1's generator invented percentages whenever the source summary was thin, then
tried to strip them afterwards. V2 refuses to write them in the first place
and vetoes any that slip through.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ── Claim extraction ────────────────────────────────────────────────────────
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*"
    r"(?:%|persen|ribu|juta|miliar|triliun|rb|jt|ml|wr|watt|\bw\b|gb|tb|mb|mah|"
    r"jam|menit|hari|bulan|tahun|cm|mm|inci|kg|gram|detik)?",
    re.I,
)

_PRICE_RE = re.compile(r"(?:rp\.?|idr)\s*[\d.,]+", re.I)

_URL_RE = re.compile(r"https?://\S+", re.I)

# Words that turn a number into a claim about the world.
_CLAIM_VERBS = (
    "naik", "turun", "tembus", "capai", "mencatat", "terjual", "hemat",
    "lebih cepat", "lebih lambat", "lebih awet", "sampai", "hanya",
    "rata-rata", "terbukti", "awet", "bertahan", "tahan", "cukup",
    "menurut data", "menurut riset", "studi", "penelitian", "survei",
    "uji", "teruji", "tercatat", "menghemat", "mengurangi", "meningkatkan",
)

_FIRSTHAND_RE = re.compile(
    r"\b(?:gue|aku|saya)\s+(?:sudah\s+)?(?:pernah\s+)?"
    r"(?:beli|pakai|menggunakan|memakai|coba|ngetes|punya|mengalami|liat|lihat)\b",
    re.I,
)

_DISCOUNT_RE = re.compile(
    r"\b(?:diskon|promo|potongan|sale|murah)\s*(?:nya|-nya)?\s*(?:sampai|hingga|hingga\s+with)?\s*"
    r"(?:\d{1,3}(?:[.,]\d{3})+|\d+)\s*(?:%|persen)",
    re.I,
)


@dataclass
class Claim:
    kind: str          # number | price | url | firsthand | discount | stat_reference
    text: str
    needs_evidence: bool = True


@dataclass
class VerificationResult:
    ok: bool
    claims: list[Claim] = field(default_factory=list)
    unsupported: list[Claim] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def extract_claims(text: str) -> list[Claim]:
    """Every checkable assertion found in the draft."""
    claims: list[Claim] = []
    if not text:
        return claims

    for m in _PRICE_RE.finditer(text):
        claims.append(Claim("price", m.group(0)))
    for m in _URL_RE.finditer(text):
        claims.append(Claim("url", m.group(0)))
    for m in _DISCOUNT_RE.finditer(text):
        claims.append(Claim("discount", m.group(0)))
    for m in _FIRSTHAND_RE.finditer(text):
        claims.append(Claim("firsthand", m.group(0)))

    lowered = text.lower()
    has_claim_verb = any(v in lowered for v in _CLAIM_VERBS)
    if has_claim_verb:
        for m in _NUMBER_RE.finditer(text):
            token = m.group(0).strip()
            # Skip bare years and tiny counts that carry no factual weight.
            if re.fullmatch(r"(19|20)\d{2}", token):
                continue
            if re.fullmatch(r"\d{1,2}", token):
                continue
            claims.append(Claim("number", token))

    return claims


def _normalise(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for matching."""
    text = text.lower()
    text = re.sub(r"[^\w\s%.,]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _numeric_tokens(evidence: str) -> set[str]:
    """Every number appearing in the evidence, in comparable form."""
    out: set[str] = set()
    if not evidence:
        return out
    for m in re.finditer(r"\d+(?:[.,]\d+)*", evidence):
        raw = m.group(0)
        out.add(raw)
        out.add(raw.replace(".", "").replace(",", ""))
        out.add(raw.replace(",", "."))
    return out


def _claim_supported(claim: Claim, evidence_norm: str, evidence_numbers: set[str]) -> bool:
    if claim.kind in ("url",):
        return claim.text.rstrip(".,)").lower() in evidence_norm
    if claim.kind == "firsthand":
        # Firsthand experience is never supported by an external source.
        return False
    if claim.kind in ("price", "discount"):
        for tok in re.findall(r"\d+(?:[.,]\d+)*", claim.text):
            if tok in evidence_numbers or tok.replace(".", "").replace(",", "") in evidence_numbers:
                return True
        return False
    # number / stat_reference
    for tok in re.findall(r"\d+(?:[.,]\d+)*", claim.text):
        if tok in evidence_numbers:
            return True
        if tok.replace(".", "").replace(",", "") in evidence_numbers:
            return True
    return False


def verify(
    draft_text: str,
    evidence: str = "",
    allow_firsthand: bool = False,
    allow_urls: bool = True,
) -> VerificationResult:
    """Check every claim in `draft_text` against `evidence`.

    `evidence` is the concatenation of every verified source the draft is
    allowed to draw on (audience signal provenance, operator-supplied product
    data, a fetched article summary). An empty evidence string means the draft
    may contain no checkable claim at all.
    """
    claims = extract_claims(draft_text)
    result = VerificationResult(ok=True, claims=claims)
    if not claims:
        return result

    evidence_norm = _normalise(evidence or "")
    evidence_numbers = _numeric_tokens(evidence or "")

    for claim in claims:
        if claim.kind == "firsthand" and allow_firsthand:
            continue
        if claim.kind == "url" and allow_urls:
            continue
        if _claim_supported(claim, evidence_norm, evidence_numbers):
            continue
        result.unsupported.append(claim)
        if claim.kind == "firsthand":
            result.reasons.append(
                "firsthand_experience_claim: "
                f"'{claim.text}' — no verified evidence of personal use"
            )
        elif claim.kind == "price":
            result.reasons.append(
                f"unsupported_price: '{claim.text}' not present in evidence"
            )
        elif claim.kind == "discount":
            result.reasons.append(
                f"unsupported_discount: '{claim.text}' not present in evidence"
            )
        else:
            result.reasons.append(
                f"unsupported_number: '{claim.text}' not present in evidence"
            )

    result.ok = not result.unsupported
    return result


def has_fabricated_firsthand(text: str) -> bool:
    """True when the draft claims the author bought/used/tested something."""
    return bool(_FIRSTHAND_RE.search(text or ""))


def has_unsourced_statistic(text: str, evidence: str = "") -> bool:
    return not verify(text, evidence).ok


def strip_claim_verbs(text: str) -> str:
    """Remove the verbs that upgrade a number into a factual claim."""
    out = text
    for verb in _CLAIM_VERBS:
        out = re.sub(rf"\b{re.escape(verb)}\b", "", out, flags=re.I)
    return re.sub(r"\s{2,}", " ", out).strip()
