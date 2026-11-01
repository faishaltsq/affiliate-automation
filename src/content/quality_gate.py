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

import hashlib
import json
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

# ── Editorial quality & naturalness reason codes / vetoes ─────────────────────
V_UNNATURAL_INDONESIAN = "unnatural_indonesian"
V_FORCED_METAPHOR = "forced_metaphor"
V_ARTIFICIAL_THREAD_PADDING = "artificial_thread_padding"
V_TRIVIAL_OBSERVATION = "trivial_observation"
V_NO_CLEAR_POINT = "no_clear_point"
V_REDUNDANT_PARTS = "redundant_parts"
V_GENERIC_AI_VOICE = "generic_ai_voice"
V_UNSAFE_PRACTICAL_ADVICE = "unsafe_practical_advice"
V_SPELLING_TYPO = "spelling_typo"
V_FORMAT_MISMATCH = "format_mismatch"
V_AWKWARD_PHRASING = "awkward_phrasing"
V_FORMAL_LANGUAGE = "formal_language"
V_FORCED_HUMOR = "forced_humor"
V_REPETITIVE_STRUCTURE = "repetitive_structure"
V_ABSOLUTE_CLAIM = "absolute_claim"
V_READ_ALOUD_UNNATURAL = "read_aloud_unnatural"
V_POINT_OBSCURE = "point_obscure"
V_BLAND_WRITING = "bland_writing"
V_NO_INTERESTING_THOUGHT = "no_interesting_thought"
V_REMOVABLE_FILLER = "removable_filler"
V_AI_WRITING_ASSIGNMENT = "ai_writing_assignment"
V_AI_MOTIVATIONAL_CONCLUSION = "ai_motivational_conclusion"
V_FORCED_CASUALNESS = "forced_casualness"
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
    r"semangat\s+(?:menjalani\s+)?aktivitas",
    r"gunakan\s+teknologi\s+dengan\s+bijak",
    r"jadikan\s+hari(?:mu)?\s+(?:lebih\s+)?produktif",
    r"semoga\s+bermanfaat",
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

_FORCED_METAPHOR_RE = re.compile(
    r"(?:"
    r"(?:kabel|benda|barang|alat|hp|charger|laptop|mouse|baterai|power\s*bank)[\s\S]{0,40}?(?:nakal|ngambek|marah|capek|lelah|punya\s+perasaan|minta\s+dimengerti)"
    r"|bukan\s+karena\s+(?:kabel|benda|barang|alat|hp|charger|mouse|baterai)(?:nya)?\s+nakal"
    r"|tandanya\s+dia\s+butuh\s+(?:sedikit\s+)?perhatian"
    r"|(?:kabel|charger|baterai|hp|power\s*bank)(?:nya)?\s+(?:lagi\s+)?(?:ngambek|marah|galau|lelah)"
    r"|punya\s+perasaan"
    r")",
    re.I,
)

_UNSAFE_ADVICE_RE = re.compile(
    r"(?:"
    r"(?:benang\s+tembaga|kawat\s+tembaga|serabut\s+tembaga|tembaga\s+keliatan|kabel\s+(?:terkelupas|robek|kebuka)|ujung\s+kabel\s+rusak)[\s\S]{0,100}?(?:butuh\s+(?:sedikit\s+)?perhatian|cukup\s+lakban|masih\s+bisa\s+dipakai|aman\s+aja|nggak\s+bahaya|hemat)"
    r"|(?:cukup\s+lakban|lakban\s+aja)[\s\S]{0,80}?(?:tembaga|kabel\s+terkelupas|kawat)"
    r"|benang\s+tembaga[\s\S]{0,60}?tandanya\s+dia\s+butuh"
    r"|(?:baterai\s+(?:kembung|hamil|bengkak))[\s\S]{0,80}?(?:masih\s+bisa|aman|pakai\s+aja|tusuk)"
    r")",
    re.I,
)

_GENERIC_AI_VOICE_RE = re.compile(
    r"(?:"
    r"^\s*di\s+era\s+digital\s+(?:saat\s+ini|sekarang)"
    r"|tidak\s+dapat\s+dipungkiri\s+bahwa"
    r"|penting\s+bagi\s+kita\s+untuk\s+(?:selalu\s+)?(?:menjaga|memahami|mengetahui)"
    r"|dalam\s+kehidupan\s+sehari-hari\s+yang\s+serba\s+cepat"
    r"|mari\s+kita\s+(?:bahas|simak|telusuri)"
    r"|seperti\s+yang\s+kita\s+ketahui\s+bersama"
    r")",
    re.I,
)

_HASHTAG_RE = re.compile(r"(?<!\w)#\w+")

# Maximum age of a draft before it is considered stale.
STALE_DRAFT_HOURS = 72


def compute_content_hash(draft_text: str, parts: Optional[list[str]] = None) -> str:
    """Deterministic SHA-256 hash of content and parts for publish validation."""
    parts_json = json.dumps(parts or [], ensure_ascii=False) if parts else ""
    payload = f"{(draft_text or '').strip()}::{parts_json}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
    content_hash: str = ""
    findings: list[dict] = field(default_factory=list)
    critic_response: Optional[object] = None

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


def _check_clickbait(text: str, pillar: Optional[str] = None) -> list[str]:
    for pat in _CLICKBAIT_PATTERNS:
        if pat == r"plot twist" and pillar in ("affiliate", "deal"):
            continue
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


def _check_forced_metaphor(text: str) -> list[str]:
    return [V_FORCED_METAPHOR] if _FORCED_METAPHOR_RE.search(text) else []


def _check_unsafe_practical_advice(text: str) -> list[str]:
    return [V_UNSAFE_PRACTICAL_ADVICE] if _UNSAFE_ADVICE_RE.search(text) else []


def _check_generic_ai_voice(text: str) -> list[str]:
    return [V_GENERIC_AI_VOICE] if _GENERIC_AI_VOICE_RE.search(text) else []


def _check_thread_padding(
    parts: Optional[list[str]],
    text: str = "",
    pillar: str = "",
) -> list[str]:
    if not parts or len(parts) < 2:
        return []
    out = []
    # Artificial stretching: 3+ parts with very few words per part
    total_words = sum(len(p.split()) for p in parts)
    if len(parts) >= 3 and total_words < 45:
        out.append(V_ARTIFICIAL_THREAD_PADDING)

    # Lexical redundancy between parts
    words_by_part = [set(re.findall(r"\w+", p.lower())) for p in parts]
    for i in range(len(words_by_part) - 1):
        s1 = words_by_part[i]
        s2 = words_by_part[i + 1]
        if s1 and s2:
            overlap = len(s1 & s2) / max(1, min(len(s1), len(s2)))
            if overlap > 0.6:
                out.append(V_REDUNDANT_PARTS)
                break

    # Mundane observation stretched across multi-part thread
    lower_text = text.lower()
    mundane_triggers = [
        "kabel charger di tas",
        "kabel kusut di tas",
        "mousepad di meja",
        "tab browser kebuka",
    ]
    if any(m in lower_text for m in mundane_triggers) and len(parts) >= 3:
        if V_ARTIFICIAL_THREAD_PADDING not in out:
            out.append(V_ARTIFICIAL_THREAD_PADDING)

    return out

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
    parts: Optional[list[str]] = None,
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
    vetoes.extend(_check_clickbait(text, pillar=pillar))
    vetoes.extend(_check_engagement_bait(text))
    vetoes.extend(_check_hashtag(text))
    vetoes.extend(_check_unsafe(text))
    vetoes.extend(_check_forced_metaphor(text))
    vetoes.extend(_check_unsafe_practical_advice(text))
    vetoes.extend(_check_generic_ai_voice(text))
    vetoes.extend(_check_thread_padding(parts, text, pillar))
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
    parts: Optional[list[str]] = None,
    format_name: str = "",
    persona=None,
) -> GateDecision:
    """The gate. Returns a reason-coded decision.

    `require_critic=True` means an unavailable critic is itself a veto: we
    cannot claim a draft is good when nobody checked it.
    """
    content_hash = compute_content_hash(draft_text, parts)
    vetoes = hard_vetoes(
        draft_text, evidence, pillar, history, hashes,
        is_affiliate, affiliate_verified, has_disclosure,
        draft_age_hours, image_rights_known, operator_authorized,
        parts=parts,
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
            content_hash=content_hash,
        )
        if persist:
            _persist(decision, draft_id, path)
        return decision

    verdict_obj: CriticVerdict = critique(
        draft_text, evidence, pillar, client=client, parts=parts, format_name=format_name,
        persona=persona, path=path,
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
                content_hash=content_hash,
                critic_response=verdict_obj,
            )
            if persist:
                _persist(decision, draft_id, path)
            return decision
        # Caller explicitly opted out of the critic (offline smoke tests).
        verdict_obj = CriticVerdict(scores={}, total_score=0.0, ok=True,
                                   notes="critic skipped by caller")

    # Extract structured findings
    raw_findings = getattr(verdict_obj, "findings", []) or []
    findings_dicts: list[dict] = []
    for f in raw_findings:
        if hasattr(f, "to_dict"):
            findings_dicts.append(f.to_dict())
        elif isinstance(f, dict):
            findings_dicts.append(f)

    critic_reasons = list(getattr(verdict_obj, "reason_codes", []) or [])

    # Check for BLOCKING findings or severe safety/policy failures
    blocking_findings = [f for f in findings_dicts if f.get("severity") == "BLOCKING"]
    if pillar in ("affiliate", "deal"):
        blocking_findings = [f for f in findings_dicts if f.get("code") != V_GENERIC_AI_VOICE]
    for code in critic_reasons:
        if code in (
            V_UNSAFE_PRACTICAL_ADVICE,
            V_FORCED_METAPHOR,
            V_ARTIFICIAL_THREAD_PADDING,
            V_GENERIC_AI_VOICE,
        ):
            if code == V_GENERIC_AI_VOICE and pillar in ("affiliate", "deal"):
                continue
            if not any(f.get("code") == code for f in blocking_findings):
                bf = {
                    "code": code,
                    "severity": "BLOCKING",
                    "evidence": "Detected by critic reason codes",
                    "suggested_fix": "Fix critical safety, metaphor, or padding failure",
                }
                blocking_findings.append(bf)
                findings_dicts.append(bf)
        elif code == V_REDUNDANT_PARTS and parts and len(parts) >= 2:
            if not any(f.get("code") == code for f in blocking_findings):
                bf = {
                    "code": code,
                    "severity": "BLOCKING",
                    "evidence": "Redundant parts in multi-part thread",
                    "suggested_fix": "Eliminate redundant parts",
                }
                blocking_findings.append(bf)
                findings_dicts.append(bf)
    if blocking_findings:
        blocking_codes = [f["code"] for f in blocking_findings]
        decision = GateDecision(
            verdict=REJECTED,
            total_score=0.0,
            scores=verdict_obj.scores,
            vetoes=blocking_codes,
            notes=f"Critic identified BLOCKING issues: {', '.join(blocking_codes)}. {verdict_obj.notes}",
            concerns=verdict_obj.concerns,
            similarity=similarity_score,
            reason_codes=blocking_codes + critic_reasons,
            content_hash=content_hash,
            findings=findings_dicts,
            critic_response=verdict_obj,
        )
        if persist:
            _persist(decision, draft_id, path)
        return decision

    # Check for MAJOR findings or serious defects (language, typo, format mismatch)
    major_findings = [f for f in findings_dicts if f.get("severity") == "MAJOR"]

    # Requirement 8: Ensure concerns are enforced by quality_gate.py.
    # Inspect concerns for defect keywords
    concerns_text = " ".join(verdict_obj.concerns or []).lower()
    for kw, code in [
        ("typo", V_SPELLING_TYPO),
        ("salah ketik", V_SPELLING_TYPO),
        ("sebagnya", V_SPELLING_TYPO),
        ("format", V_FORMAT_MISMATCH),
        ("paragraf", V_FORMAT_MISMATCH),
        ("kaku", V_UNNATURAL_INDONESIAN),
        ("berbelit", V_AWKWARD_PHRASING),
        ("tidak natural", V_UNNATURAL_INDONESIAN),
        ("read aloud", V_READ_ALOUD_UNNATURAL),
        ("baca bersuara", V_READ_ALOUD_UNNATURAL),
        ("kabur", V_POINT_OBSCURE),
        ("hambar", V_BLAND_WRITING),
        ("basi", V_NO_INTERESTING_THOUGHT),
        ("filler", V_REMOVABLE_FILLER),
        ("mubazir", V_REMOVABLE_FILLER),
        ("garing", V_FORCED_HUMOR),
        ("motivasi", V_AI_MOTIVATIONAL_CONCLUSION),
        ("esai", V_AI_WRITING_ASSIGNMENT),
    ]:
        if kw in concerns_text and not any(f.get("code") == code for f in major_findings):
            major_finding = {
                "code": code,
                "severity": "MAJOR",
                "evidence": f"Identified in critic concerns: '{kw}'",
                "suggested_fix": f"Address {code}",
            }
            major_findings.append(major_finding)
            findings_dicts.append(major_finding)
            if code not in critic_reasons:
                critic_reasons.append(code)

    raw_score = verdict_obj.total_score

    # Severity-aware gating:
    # Major defects MUST prevent APPROVED_AUTO, forcing REVISE_ONCE.
    # Score 88 must NOT override major findings!
    if major_findings:
        major_codes = [f["code"] for f in major_findings]
        effective_score = min(raw_score, 68.0)
        verdict = REVISE_ONCE
        all_reasons = [REVISE_ONCE] + [c for c in major_codes + critic_reasons if c != REVISE_ONCE]
        decision = GateDecision(
            verdict=verdict,
            total_score=effective_score,
            scores=verdict_obj.scores,
            vetoes=[],
            notes=f"Critic identified MAJOR defects ({len(major_findings)}): {', '.join(major_codes)}. {verdict_obj.notes}",
            concerns=verdict_obj.concerns,
            similarity=similarity_score,
            revision_allowed=True,
            reason_codes=all_reasons,
            content_hash=content_hash,
            findings=findings_dicts,
            critic_response=verdict_obj,
        )
        if persist:
            _persist(decision, draft_id, path)
        return decision

    # No blocking or major defects: score thresholds apply cleanly
    if raw_score >= AUTO_THRESHOLD:
        verdict = APPROVED_AUTO
    elif raw_score >= REVIEW_THRESHOLD:
        verdict = NEEDS_REVIEW
    else:
        verdict = REVISE_ONCE

    all_reasons = [verdict] + [r for r in critic_reasons if r != verdict]
    decision = GateDecision(
        verdict=verdict,
        total_score=raw_score,
        scores=verdict_obj.scores,
        vetoes=[],
        notes=verdict_obj.notes,
        concerns=verdict_obj.concerns,
        similarity=similarity_score,
        revision_allowed=(verdict == REVISE_ONCE),
        reason_codes=all_reasons,
        content_hash=content_hash,
        findings=findings_dicts,
        critic_response=verdict_obj,
    )
    if persist:
        _persist(decision, draft_id, path)
    return decision


def _persist(decision: GateDecision, draft_id: Optional[int], path) -> None:
    if draft_id is None:
        return
    import json

    findings_json = json.dumps(decision.findings, ensure_ascii=False)

    with connect(path) as conn:
        with transaction(conn):
            try:
                conn.execute(
                    """
                    INSERT INTO content_evaluations
                        (draft_id, scores_json, vetoes_json, verdict, total_score,
                         evaluator_version, notes, content_hash, findings_json)
                    VALUES (?, ?, ?, ?, ?, 'v2', ?, ?, ?)
                    """,
                    (
                        draft_id,
                        json.dumps(decision.scores, ensure_ascii=False),
                        json.dumps(decision.vetoes, ensure_ascii=False),
                        decision.verdict,
                        decision.total_score,
                        decision.notes,
                        decision.content_hash,
                        findings_json,
                    ),
                )
            except Exception:
                try:
                    conn.execute(
                        """
                        INSERT INTO content_evaluations
                            (draft_id, scores_json, vetoes_json, verdict, total_score,
                             evaluator_version, notes, content_hash)
                        VALUES (?, ?, ?, ?, ?, 'v2', ?, ?)
                        """,
                        (
                            draft_id,
                            json.dumps(decision.scores, ensure_ascii=False),
                            json.dumps(decision.vetoes, ensure_ascii=False),
                            decision.verdict,
                            decision.total_score,
                            decision.notes,
                            decision.content_hash,
                        ),
                    )
                except Exception:
                    # Fallback if content_hash column has not been added yet
                    conn.execute(
                        """
                        INSERT INTO content_evaluations
                            (draft_id, scores_json, vetoes_json, verdict, total_score,
                             evaluator_version, notes)
                        VALUES (?, ?, ?, ?, ?, 'v2', ?)
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

@dataclass
class PublishValidation:
    eligible: bool
    reason: str
    decision: Optional[GateDecision] = None
    evaluation_id: Optional[int] = None
    content_hash: str = ""


def validate_draft_for_publishing(
    draft_id: int,
    review_required: bool = True,
    path=None,
) -> PublishValidation:
    """Fail-closed validation before any draft is published.

    Checks:
      1. Draft exists.
      2. Current evaluation exists.
      3. Content hash matches between draft and evaluation.
      4. Evaluation has zero vetoes.
      5. REJECTED and REVISE_ONCE drafts are NEVER eligible.
      6. NEEDS_REVIEW requires draft.status == 'APPROVED'.
      7. APPROVED_AUTO is eligible only when review_required is False, OR
         draft.status == 'APPROVED'.
    """
    from src.content.writer import get_draft

    draft = get_draft(draft_id, path=path)
    if not draft:
        return PublishValidation(False, "draft_not_found")

    content = draft.get("content") or ""
    parts = draft.get("parts") or []
    current_hash = compute_content_hash(content, parts)

    evals = get_evaluations(draft_id, path=path)
    if not evals:
        return PublishValidation(False, "missing_evaluation")

    latest = evals[-1]
    eval_hash = latest.get("content_hash") or ""
    if eval_hash and eval_hash != current_hash:
        return PublishValidation(False, "content_hash_mismatch")

    verdict = latest.get("verdict")
    vetoes = latest.get("vetoes") or []
    if vetoes:
        return PublishValidation(False, f"evaluation_has_vetoes: {', '.join(vetoes)}")

    if verdict == REJECTED:
        return PublishValidation(False, "verdict_rejected")

    if verdict == REVISE_ONCE:
        return PublishValidation(False, "verdict_revise_once")

    status = (draft.get("status") or "").upper()
    if verdict == NEEDS_REVIEW:
        if status != "APPROVED":
            return PublishValidation(False, "needs_operator_approval")

    if verdict == APPROVED_AUTO:
        if review_required and status != "APPROVED":
            return PublishValidation(False, "review_required_pending_approval")

    return PublishValidation(
        True,
        "eligible",
        evaluation_id=latest.get("id"),
        content_hash=current_hash,
    )
