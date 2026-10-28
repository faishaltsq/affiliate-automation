"""src/core/models.py — typed DTOs shared across V2 packages.

These are plain dataclasses (no pydantic dependency) with explicit
to_row()/from_row() helpers so the DB layer stays boring and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ── Persona ─────────────────────────────────────────────────────────────────
@dataclass
class Persona:
    version: str
    name: str
    handle: str
    archetype: str
    bio: str
    tone: str
    voice_rules: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    audience: str = ""
    account_promise: str = ""
    variation_dimensions: list[str] = field(default_factory=list)
    active: bool = True

    def to_json_dict(self) -> dict:
        return asdict(self)


# ── Content lifecycle ───────────────────────────────────────────────────────
IDEA = "IDEA"
DRAFT = "DRAFT"
EVALUATED = "EVALUATED"
APPROVED = "APPROVED"
NEEDS_REVIEW = "NEEDS_REVIEW"
REJECTED = "REJECTED"
QUEUED = "QUEUED"
PUBLISHING = "PUBLISHING"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"
UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"

VALID_STATES = {
    IDEA, DRAFT, EVALUATED, APPROVED, NEEDS_REVIEW, REJECTED,
    QUEUED, PUBLISHING, PUBLISHED, FAILED, UNKNOWN_OUTCOME,
}


@dataclass
class ContentIdea:
    pillar: str
    angle: str = ""
    audience_problem: str = ""
    source_ids: list[int] = field(default_factory=list)
    planned_date: Optional[str] = None
    rank_score: float = 0.0
    state: str = IDEA
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class ContentDraft:
    idea_id: Optional[int]
    content: str
    structure: str = "single"
    persona_version: str = "teman_tongkrongan_v1"
    draft_version: int = 1
    status: str = DRAFT
    parts: list[str] = field(default_factory=list)
    image_query: str = ""
    disclosure: str = ""
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class Evaluation:
    draft_id: int
    scores: dict[str, float]
    vetoes: list[str]
    verdict: str
    total_score: float = 0.0
    evaluator_version: str = "v1"
    notes: str = ""
    id: Optional[int] = None
    created_at: Optional[str] = None

    @property
    def passed_hard_gates(self) -> bool:
        return not self.vetoes


@dataclass
class PublicationJob:
    draft_id: int
    operation_id: str
    state: str = QUEUED
    claimed_at: Optional[str] = None
    lease_expires_at: Optional[str] = None
    attempts: int = 0
    published_id: Optional[str] = None
    last_error: str = ""
    id: Optional[int] = None
    created_at: Optional[str] = None


# ── Affiliate ───────────────────────────────────────────────────────────────
UNVERIFIED = "UNVERIFIED"
VERIFIED = "VERIFIED"
EXPIRED = "EXPIRED"
REVOKED = "REVOKED"

TRACKING_STATES = {UNVERIFIED, VERIFIED, EXPIRED, REVOKED}


@dataclass
class Product:
    name: str
    category: str = ""
    source_platform: str = ""
    source_url: str = ""
    original_affiliate_url: str = ""
    verified_affiliate_url: str = ""
    tracking_status: str = UNVERIFIED
    evidence_url: str = ""
    price_if_verified: Optional[float] = None
    checked_at: Optional[str] = None
    expires_at: Optional[str] = None
    restrictions: str = ""
    reviewer_status: str = "pending"
    id: Optional[int] = None

    @property
    def is_commission_ready(self) -> bool:
        """Only an explicitly VERIFIED link with a non-empty verified URL counts."""
        return (
            self.tracking_status == VERIFIED
            and bool(self.verified_affiliate_url.strip())
            and self.reviewer_status == "approved"
        )


@dataclass
class Promotion:
    product_id: int
    post_id: str
    evidence: str = ""
    disclosed: bool = False
    status: str = "planned"
    id: Optional[int] = None
    created_at: Optional[str] = None


# ── Feedback & analytics ────────────────────────────────────────────────────
@dataclass
class PerformanceSnapshot:
    threads_id: str
    views: int = 0
    likes: int = 0
    replies: int = 0
    source_status: str = "ok"
    fetched_at: Optional[str] = None
    id: Optional[int] = None


@dataclass
class FeedbackEvent:
    draft_id: Optional[int]
    actor_id: str
    category: str
    reason: str = ""
    id: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class ExperimentAssignment:
    draft_id: int
    hypothesis: str
    variant: str
    attribution_window: str = "7d"
    id: Optional[int] = None


@dataclass
class AuditEvent:
    actor_type: str
    action: str
    object_type: str = ""
    object_id: str = ""
    actor_id: str = ""
    before_json: str = ""
    after_json: str = ""
    reason: str = ""
    created_at: Optional[str] = None
    id: Optional[int] = None
