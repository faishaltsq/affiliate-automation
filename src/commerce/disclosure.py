"""src/commerce/disclosure.py — transparent affiliate labelling.

Every commercial post and every commercial reply carries a disclosure. There
is no configuration that turns this off, because an undisclosed affiliate link
is both a platform-policy violation and a breach of the account promise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The canonical disclosure text. Indonesian, plain, no legalese.
DISCLOSURE_TEXT = "Post ini mengandung link afiliasi. Kalau kalian beli lewat link itu, gue dapat komisi kecil — tanpa biaya tambahan buat kalian."

# Shorter form for replies where space is tight.
DISCLOSURE_SHORT = "Link afiliasi — gue dapat komisi kecil kalau kalian beli, tanpa biaya tambahan."

_DISCLOSURE_MARKERS = (
    "link afiliasi",
    "mengandung link afiliasi",
    "affiliate link",
    "komisi kecil",
    "paid link",
    "sponsored",
    "iklan",
)

_HASHTAG_DISCLOSURE_RE = re.compile(
    r"#(?:afiliasi|affiliate|ad|iklan|sponsored|paidpartnership)", re.I
)


@dataclass
class DisclosureVerdict:
    present: bool
    text: str = ""
    reason: str = ""


def has_disclosure(text: str) -> bool:
    """True when the text already carries an affiliate disclosure."""
    if not text:
        return False
    low = text.lower()
    if any(marker in low for marker in _DISCLOSURE_MARKERS):
        return True
    return bool(_HASHTAG_DISCLOSURE_RE.search(text))


def ensure_disclosure(text: str, short: bool = False) -> str:
    """Append the disclosure if it is missing. Idempotent."""
    if has_disclosure(text):
        return text
    label = DISCLOSURE_SHORT if short else DISCLOSURE_TEXT
    body = (text or "").rstrip()
    if not body:
        return label
    return f"{body}\n\n{label}"


def check_disclosure(text: str, is_commercial: bool) -> DisclosureVerdict:
    """The gate's disclosure check.

    A non-commercial post needs nothing. A commercial post must carry the
    label — `present=False` is a hard veto.
    """
    if not is_commercial:
        return DisclosureVerdict(present=True, reason="not a commercial post")
    if has_disclosure(text):
        return DisclosureVerdict(present=True, text=text,
                                 reason="disclosure already present")
    return DisclosureVerdict(
        present=False,
        reason="commercial content without an affiliate disclosure",
    )


def strip_disclosure(text: str) -> str:
    """Remove a disclosure label — used when re-drafting so it is not doubled."""
    out = text or ""
    for marker in _DISCLOSURE_MARKERS:
        # Remove the sentence containing the marker.
        out = re.sub(
            rf"[^.\n]*{re.escape(marker)}[^.\n]*\.?",
            "",
            out,
            flags=re.I,
        )
    out = _HASHTAG_DISCLOSURE_RE.sub("", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def disclosure_for_reply(reply_text: str) -> str:
    """A commercial reply always carries the short form."""
    return ensure_disclosure(reply_text, short=True)
