"""src/content/quality_gate.py — publish / review / revise / reject.

The single decision point between a draft and the publication queue. Every
publishing path goes through it: scheduled slots, Telegram manual approval,
and the affiliate flow. V1's publisher called the generator and published in
the same breath; there was no gate at all.

Decisions:
  APPROVED_AUTO  — score >= 85 and zero hard vetoes
  NEEDS_REVIEW   — score 70-84, or auto-eligible but the operator wants eyes
  REVISE_ONCE    — score < 70, one bounded revision allowed
  REJECTED       — any hard veto, or a revision that still fails

Hard vetoes (master plan §5). Any one of these rejects outright regardless of
score: unverified numeric claim, fabricated firsthand usage, unknown source on
time-sensitive facts, incompatible product, uncertain affiliate tracking, no
disclosure, near-duplicate, excessive repetition, unsafe/sensitive framing,
unknown image rights, stale/invalid draft, unauthorized operator action.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from src.content import similarity as sim
from src.content.critic import CriticVerdict, critique
from src.content.verifier import VerificationResult, verify
from src.core.llm import LLMClient
from src.core.models import Evaluation, EVALUATED
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("quality_gate")

APPROVED_AUTO = "APPROVED_AUTO"
NEEDS_REVIEW = "NEEDS_REVIEW"
REVISE_ONCE = "REVISE_ONCE"
REJECTED = "REJECTED"

AUTO_THRESHOLD = 85.0
REVIEW_THRESHOLD = 70.0

# ── Hard veto codes ─────────────────────────────────────────────────────────
V_UNVERIFIED_NUMBER = "unverified_numeric_claim"
V_FABRICATED_FIRSTHAND = "fabricated_firsthand_usage"
V_UNKNOWN_SOURCE = "unknown_source_time_sensitive"
V_INCOMPATIBLE_PRODUCT = "incompatible_product"
V_UNCERTAIN_TRACKING = "uncertain_affiliate_tracking"
V_NO_DISCLOSURE = "no_disclosure"
V_NEAR_DUPLICATE = "near_duplicate"
V_EXCESSIVE_REPETITION = "excessive_repetition"
V_UNSAFE_FRAMING = "unsafe_sensitive_framing"
V_UNKNOWN_IMAGE_RIGHTS = "unknown_image_rights"
V_STALE_DRAFT = "stale_invalid_draft"
V_UNAUTHORIZED = "unauthorized_operator_action"
V_CRITIC_UNAVAILABLE = "critic_unavailable"
V_GENERIC_FILLER = "generic_filler"
V_EMPTY = "empty_draft"
V_CLICKBAIT = "clickbait_pattern"
V_ENGAGEMENT_BAIT = "engagement_bait_closer"
V_HASHTAG = "hashtag_present"
V_AFFILIATE_LINK_UNVERIFIED = "affiliate_link_unverified"

# Patterns that indicate content-farm filler — the exact failure mode that
# produced "Isu X ini punya implikasi nyata…" on the live account.
_FILLER_PATTERNS = [
    r"punya implikasi nyata",
    r"dampaknya ke depan bakal signifikan",
    r"perlu dicermati secara objektif",
    r"menarik untuk disimak",
    r"worth it untuk dicoba",
    r"game changer",
    r"belum tentu benar|belum tentu salah",
]

_CLICKBAIT_PATTERNS = [
    r"jangan percaya",
    r"ternyata.*?(mengejutkan|mengguncang)",
    r"rahasia yang.*?(disembunyikan|jarang diketahui)",
    r"kamu pasti.*?kaget",
    r"nggak nyangka.*?fakta",
    r"plot twist",
]

# Engagement-bait CLOSERS. Matched against the last 200 characters so a post
# that legitimately asks a question mid-body is not penalised — only a bait
# closer is.
_ENGAGEMENT_BAIT_RE = re.compile(
    r"(?:"
    r"menurut\s+(?:kalian|kamu|anda)"
    r"|gimana\s+menurut"
    r"|kalian\s+tim\s+(?:mana|apa)"
    r"|tim\s+(?:mana|apa)\s*(?:nih|sih|ya|dong)?"
    r"|ada\s+yang\s+(?:ngerasain|merasain|pernah)"
    r"|setuju\s+nggak"
    r"|siapa\s+yang\s+(?:sama|pernah)"
    r")[\s?,.!]*(?:[a-zA-Z]+[\s?,.!]*){0,2}$",
    re.I,
)

# Engagement-bait OPENERS. A question is not automatically bait — the persona
# guide explicitly allows `pertanyaan_jujur`, an honest question. What is bait
# is a question that addresses the audience as a bloc and asks them to
# self-identify: "Kalian sering nunggu diskon gede ya?" is a poll, not a
# question. So this only fires on second-person-plural + a tag question, which
# is the shape that farms replies rather than starting a thought.
_BAIT_OPENER_RE = re.compile(
    r"^\s*(?:"
    # Direct bloc address: "Kalian tim mana", "Kamu nih"
    r"(?:kalian|kamu|anda|loe|lu)\s+(?:tim\s+(?:mana|apa)|nih|sih|semua)\b"
    # Self-identification poll aimed at the reader
    r"|ada\s+yang\s+(?:ngerasain|merasain|pernah|sama)\b"
    # "Kamu/Kalian pernah ngerasain/merasain ..." — a poll dressed as a
    # question. The self-identification verb is the tell, wherever the tag sits.
    r"|(?:kamu|kalian|anda|loe|lu)\s+\S{0,20}\s*pernah\s+\S{0,20}\s*"
    r"(?:ngerasain|merasain|rasain)\b"
    # Second-person-plural sentence ending in a tag question
    r"|(?:kalian|kamu|anda|loe|lu)\s+[^.?!]{5,120}?"
    r"(?:nggak\s+siH|\bsih\b|\bdong\b|\bdeh\b|\bya\b|\bkan\b)\s*[?.!]"
    r")",
    re.I,
)

_UNSAFE_RE = re.compile(
    r"\b(?:bunuh diri|self harm|kekerasan seksual|pelecehan|"
    r"anjuran medis|diagnosis|obat|dosis|terapi)\b",
    re.I,
)

_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")

# Maximum age of a draft before it is considered stale.
STALE_DRAFT_HOURS = 72


@dataclass
class GateDecision:
    verdict: str
    total_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)
    vetoes: list[str] = field(default_factory=list)
    notes: str = ""
    concerns: list[str] = field(default_factory=list)
    similarity: float = 0.0
    revision_allowed: bool = False
    reason_codes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.verdict in (APPROVED_AUTO, NEEDS_REVIEW)


def _check_filler(text: str) -> list[str]:
    out = []
    for pat in _FILLER_PATTERNS:
        if re.search(pat, text, re.I):
            out.append(V_GENERIC_FILLER)
            break
    return out


def _check_clickbait(text: str) -> list[str]:
    for pat in _CLICKBAIT_PATTERNS:
        if re.search(pat, text, re.I):
            return [V_CLICKBAIT]
    return []


def _check_engagement_bait(text: str) -> list[str]:
    """Bait closers and bait openers. Honest questions are allowed."""
    body = text.strip()
    if _ENGAGEMENT_BAIT_RE.search(body[-200:]):
        return [V_ENGAGEMENT_BAIT]
    if _BAIT_OPENER_RE.search(body[:200]):
        return [V_ENGAGEMENT_BAIT]
    return []


def _check_hashtag(text: str) -> list[str]:
    return [V_HASHTAG] if _HASHTAG_RE.search(text) else []


def _check_unsafe(text: str) -> list[str]:
    return [V_UNSAFE_FRAMING] if _UNSAFE_RE.search(text) else []


def hard_vetoes(
    draft_text: str,
    evidence: str = "",
    pillar: str = "",
    history: Optional[list[str]] = None,
    hashes: Optional[list[str]] = None,
    is_affiliate: bool = False,
    affiliate_verified: bool = False,
    has_disclosure: bool = False,
    draft_age_hours: Optional[float] = None,
    image_rights_known: bool = True,
    operator_authorized: bool = True,
) -> list[str]:
    """Every mechanical hard veto. Returns the codes that fired.

    These run BEFORE the critic so a draft that fails a hard rule never costs
    an LLM call and can never be rescued by a high score.
    """
    vetoes: list[str] = []
    text = draft_text or ""

    if not text.strip():
        return [V_EMPTY]

    # Evidence policy.
    vres: VerificationResult = verify(text, evidence)
    for reason in vres.reasons:
        if reason.startswith("firsthand"):
            vetoes.append(V_FABRICATED_FIRSTHAND)
        elif reason.startswith("unsupported_price") or reason.startswith("unsupported_discount"):
            vetoes.append(V_UNVERIFIED_NUMBER)
        else:
            vetoes.append(V_UNVERIFIED_NUMBER)

    # Style / persona hard rules.
    vetoes.extend(_check_filler(text))
    vetoes.extend(_check_clickbait(text))
    vetoes.extend(_check_engagement_bait(text))
    vetoes.extend(_check_hashtag(text))
    vetoes.extend(_check_unsafe(text))

    # Duplication.
    if history:
        verdict = sim.check_against_history(text, history, hashes)
        if not verdict.ok:
            if verdict.reason.startswith("exact"):
                vetoes.append(V_NEAR_DUPLICATE)
            elif verdict.reason.startswith("near"):
                vetoes.append(V_NEAR_DUPLICATE)
            else:
                vetoes.append(V_EXCESSIVE_REPETITION)

    # Commercial rules.
    if is_affiliate:
        if not affiliate_verified:
            vetoes.append(V_UNCERTAIN_TRACKING)
        if not has_disclosure:
            vetoes.append(V_NO_DISCLOSURE)

    # Freshness and rights.
    if draft_age_hours is not None and draft_age_hours > STALE_DRAFT_HOURS:
        vetoes.append(V_STALE_DRAFT)
    if not image_rights_known:
        vetoes.append(V_UNKNOWN_IMAGE_RIGHTS)
    if not operator_authorized:
        vetoes.append(V_UNAUTHORIZED)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for v in vetoes:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def evaluate_draft(
    draft_text: str,
    evidence: str = "",
    pillar: str = "",
    history: Optional[list[str]] = None,
    hashes: Optional[list[str]] = None,
    is_affiliate: bool = False,
    affiliate_verified: bool = False,
    has_disclosure: bool = False,
    draft_age_hours: Optional[float] = None,
    image_rights_known: bool = True,
    operator_authorized: bool = True,
    client: Optional[LLMClient] = None,
    require_critic: bool = True,
    draft_id: Optional[int] = None,
    path=None,
    persist: bool = True,
) -> GateDecision:
    """The gate. Returns a reason-coded decision.

    `require_critic=True` means an unavailable critic is itself a veto: we
    cannot claim a draft is good when nobody checked it.
    """
    vetoes = hard_vetoes(
        draft_text, evidence, pillar, history, hashes,
        is_affiliate, affiliate_verified, has_disclosure,
        draft_age_hours, image_rights_known, operator_authorized,
    )

    similarity_score = 0.0
    if history:
        sv = sim.check_against_history(draft_text, history, hashes)
        similarity_score = sv.max_similarity

    if vetoes:
        decision = GateDecision(
            verdict=REJECTED,
            total_score=0.0,
            scores={},
            vetoes=vetoes,
            notes="hard veto fired before scoring",
            similarity=similarity_score,
            reason_codes=list(vetoes),
        )
        if persist:
            _persist(decision, draft_id, path)
        return decision

    verdict_obj: CriticVerdict = critique(
        draft_text, evidence, pillar, client=client
    )
    if not verdict_obj.ok:
        if require_critic:
            decision = GateDecision(
                verdict=REJECTED,
                total_score=0.0,
                scores={},
                vetoes=[V_CRITIC_UNAVAILABLE],
                notes=verdict_obj.error,
                similarity=similarity_score,
                reason_codes=[V_CRITIC_UNAVAILABLE],
            )
            if persist:
                _persist(decision, draft_id, path)
            return decision
        # Caller explicitly opted out of the critic (offline smoke tests).
        verdict_obj = CriticVerdict(scores={}, total_score=0.0, ok=True,
                                   notes="critic skipped by caller")

    score = verdict_obj.total_score
    if score >= AUTO_THRESHOLD:
        verdict = APPROVED_AUTO
    elif score >= REVIEW_THRESHOLD:
        verdict = NEEDS_REVIEW
    else:
        verdict = REVISE_ONCE

    decision = GateDecision(
        verdict=verdict,
        total_score=score,
        scores=verdict_obj.scores,
        vetoes=[],
        notes=verdict_obj.notes,
        concerns=verdict_obj.concerns,
        similarity=similarity_score,
        revision_allowed=(verdict == REVISE_ONCE),
        reason_codes=[verdict],
    )
    if persist:
        _persist(decision, draft_id, path)
    return decision


def _persist(decision: GateDecision, draft_id: Optional[int], path) -> None:
    if draft_id is None:
        return
    import json

    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO content_evaluations
                    (draft_id, scores_json, vetoes_json, verdict, total_score,
                     evaluator_version, notes)
                VALUES (?, ?, ?, ?, ?, 'v1', ?)
                """,
                (
                    draft_id,
                    json.dumps(decision.scores, ensure_ascii=False),
                    json.dumps(decision.vetoes, ensure_ascii=False),
                    decision.verdict,
                    decision.total_score,
                    decision.notes,
                ),
            )


def get_evaluations(draft_id: int, path=None) -> list[dict]:
    import json

    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM content_evaluations WHERE draft_id = ? ORDER BY id ASC",
            (draft_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scores"] = json.loads(d.get("scores_json") or "{}")
        d["vetoes"] = json.loads(d.get("vetoes_json") or "[]")
        out.append(d)
    return out
