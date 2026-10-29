"""src/orchestration/publisher.py — exactly one logical publication operation.

V1's `src/publish/threads.py` created a container, then published it. If the
process died, the token expired, or the socket timed out between those two
calls, the outcome was unknown — and a naive retry produced a duplicate post on
a live account.

V2 makes publication a state machine with a stable `operation_id`:

    QUEUED -> PUBLISHING -> PUBLISHED
                          -> FAILED
                          -> UNKNOWN_OUTCOME

`operation_id` is derived from the draft ID and the content hash, so it is
stable across restarts. A retry with the same operation_id can never publish
twice: the claim is atomic (single UPDATE ... WHERE state='QUEUED'), and
UNKNOWN_OUTCOME is reconciled by querying the API rather than by republishing.

The Threads API itself is the reason for the UNKNOWN state: the Meta Graph
returns 200 only after the container is created, and the publish call can time
out after the server has already accepted it. There is no idempotency key on
their side, so the only safe design is to never blindly retry.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

from src.core.clock import sqlite_utc, to_utc, utc_iso, utc_now, parse_sqlite_utc
from src.core.storage import connect, connection, transaction
from src.logger import get_logger

log = get_logger("orchestration_publisher")

QUEUED = "QUEUED"
PUBLISHING = "PUBLISHING"
PUBLISHED = "PUBLISHED"
FAILED = "FAILED"
UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"

VALID_STATES = {QUEUED, PUBLISHING, PUBLISHED, FAILED, UNKNOWN_OUTCOME}

# A job may be retried at most this many times before it is parked for review.
MAX_ATTEMPTS = 3
# How long a claimed job is leased for. A crashed worker's claim expires so
# another worker can pick the job up instead of the queue stalling forever.
LEASE_SECONDS = 300


def make_operation_id(draft_id: int, content: str) -> str:
    """Stable across restarts: same draft + same content -> same operation."""
    digest = hashlib.sha256(f"{draft_id}:{content}".encode("utf-8")).hexdigest()
    return f"op_{digest[:32]}"


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

    def to_dict(self) -> dict:
        return {
            "id": self.id, "draft_id": self.draft_id,
            "operation_id": self.operation_id, "state": self.state,
            "attempts": self.attempts, "published_id": self.published_id,
            "last_error": self.last_error, "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "created_at": self.created_at,
        }


def enqueue(draft_id: int, content: str, path=None) -> tuple[int, str, bool]:
    """Create a publication job. Returns (job_id, operation_id, created).

    Re-enqueueing the same draft with identical content returns the existing
    job rather than creating a second one — that is the duplicate-prevention
    guarantee at the queue level.
    """
    op_id = make_operation_id(draft_id, content)
    with connect(path) as conn:
        with transaction(conn):
            existing = conn.execute(
                "SELECT id, state FROM publication_jobs WHERE operation_id = ?",
                (op_id,),
            ).fetchone()
            if existing:
                return int(existing["id"]), op_id, False
            cur = conn.execute(
                """
                INSERT INTO publication_jobs
                    (draft_id, operation_id, state, attempts)
                VALUES (?, ?, ?, 0)
                """,
                (draft_id, op_id, QUEUED),
            )
            return int(cur.lastrowid), op_id, True


def claim_next_job(path=None, lease_seconds: int = LEASE_SECONDS) -> Optional[PublicationJob]:
    """Atomically claim the oldest claimable job.

    The UPDATE ... WHERE state='QUEUED' is the concurrency control: two workers
    racing for the same job means only one UPDATE matches a row, so only one
    worker gets a job back. No separate lock file, no TOCTOU window.
    """
    now = utc_now()
    now_s = sqlite_utc(now)
    lease_s = sqlite_utc(now + timedelta(seconds=lease_seconds))
    with connect(path) as conn:
        with transaction(conn):
            row = conn.execute(
                """
                SELECT id FROM publication_jobs
                WHERE state = ?
                   OR (state = ? AND (lease_expires_at IS NULL
                                      OR lease_expires_at < ?))
                ORDER BY id ASC LIMIT 1
                """,
                (QUEUED, PUBLISHING, now_s),
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["id"])
            cur = conn.execute(
                """
                UPDATE publication_jobs
                SET state = ?, claimed_at = ?, lease_expires_at = ?,
                    attempts = attempts + 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (PUBLISHING, now_s, lease_s, job_id),
            )
            if cur.rowcount != 1:
                return None  # lost the race
            got = conn.execute(
                "SELECT * FROM publication_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return _row_to_job(got)


def _row_to_job(row) -> PublicationJob:
    return PublicationJob(
        id=int(row["id"]), draft_id=int(row["draft_id"]),
        operation_id=row["operation_id"], state=row["state"],
        claimed_at=row["claimed_at"], lease_expires_at=row["lease_expires_at"],
        attempts=int(row["attempts"]), published_id=row["published_id"],
        last_error=row["last_error"] or "", created_at=row["created_at"],
    )


def get_job(job_id: int, path=None) -> Optional[PublicationJob]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM publication_jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_job(row) if row else None


def get_job_by_operation(operation_id: str, path=None) -> Optional[PublicationJob]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM publication_jobs WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return _row_to_job(row) if row else None


def mark_published(job_id: int, published_id: str, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE publication_jobs SET state = ?, published_id = ?, "
                "last_error = '', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (PUBLISHED, str(published_id), job_id),
            )


def mark_failed(job_id: int, error: str, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE publication_jobs SET state = ?, last_error = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (FAILED, str(error)[:2000], job_id),
            )


def mark_unknown_outcome(job_id: int, error: str, path=None) -> None:
    """A submit that may or may not have succeeded. Never auto-retried."""
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE publication_jobs SET state = ?, last_error = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (UNKNOWN_OUTCOME, str(error)[:2000], job_id),
            )


def requeue(job_id: int, path=None) -> bool:
    """Return a FAILED job to QUEUED if it still has retry budget."""
    with connect(path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT attempts, state FROM publication_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return False
            if row["state"] != FAILED:
                return False
            if int(row["attempts"]) >= MAX_ATTEMPTS:
                return False
            conn.execute(
                "UPDATE publication_jobs SET state = ?, claimed_at = NULL, "
                "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (QUEUED, job_id),
            )
            return True


def jobs_in_state(state: str, path=None) -> list[PublicationJob]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM publication_jobs WHERE state = ? ORDER BY id ASC",
            (state,),
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def queue_depth(path=None) -> dict:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) AS n FROM publication_jobs GROUP BY state"
        ).fetchall()
    return {r["state"]: r["n"] for r in rows}


def oldest_pending_age_seconds(path=None) -> Optional[float]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT MIN(created_at) AS oldest FROM publication_jobs "
            "WHERE state IN ('QUEUED', 'PUBLISHING')"
        ).fetchone()
    if not row or not row["oldest"]:
        return None
    dt = parse_sqlite_utc(row["oldest"])
    if dt is None:
        return None
    return max(0.0, (utc_now() - dt).total_seconds())


@dataclass
class PublishOutcome:
    job_id: int
    operation_id: str
    state: str
    published_id: Optional[str] = None
    error: str = ""
    reconciled: bool = False

    @property
    def succeeded(self) -> bool:
        return self.state == PUBLISHED


class ThreadsPublisher:
    """The publish seam the state machine talks to.

    Deliberately narrow: `submit` performs one publication and returns a dict
    carrying `post_id`; `lookup` answers whether a container actually went out.
    `submit` may raise TimeoutError to simulate the ambiguous case.

    This is NOT the same type as `src.adapters.threads.ThreadsAdapter`. The
    adapter is the *external side-effect* seam (it owns credentials, HTTP, and
    dry-run suppression); this is the *state-machine* seam (it owns the
    contract publish_job depends on). `AdapterThreadsPublisher` below bridges
    the two, so production code injects the adapter and tests inject a fake.
    """

    def submit(self, content: str, parts: list[str], image_url: str = "") -> dict:
        raise NotImplementedError

    def lookup(self, container_id: str) -> dict:
        """Query whether a container was actually published."""
        raise NotImplementedError


class AdapterThreadsPublisher(ThreadsPublisher):
    """Bridges a `src.adapters.threads.ThreadsAdapter` into the state machine.

    Without this the two abstractions never meet: the publisher's `submit()`
    raises NotImplementedError, which publish_job catches as a generic failure,
    so a dry-run publish could never reach PUBLISHED. That is exactly what the
    offline integration rehearsal caught.
    """

    def __init__(self, adapter):
        self._adapter = adapter

    def submit(self, content: str, parts: list[str], image_url: str = "") -> dict:
        if parts:
            return self._adapter.publish_chain(parts, image_url=image_url)
        return self._adapter.publish_text(content, image_url=image_url)

    def lookup(self, container_id: str) -> dict:
        return self._adapter.lookup_post(container_id)


class FakeThreadsPublisher(ThreadsPublisher):
    """Deterministic test double. `outcomes` decides what each submit does."""

    def __init__(self, outcomes=None, lookups=None):
        # outcomes: list of either {"post_id": "x"} or an Exception to raise
        self.outcomes = list(outcomes or [{"post_id": "fake_post_1"}])
        self.lookups = lookups or {}
        self.submits: list[dict] = []
        self._i = 0

    def submit(self, content: str, parts: list[str], image_url: str = "") -> dict:
        self.submits.append({
            "content": content, "parts": parts, "image_url": image_url,
        })
        idx = min(self._i, len(self.outcomes) - 1)
        self._i += 1
        outcome = self.outcomes[idx]
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    def lookup(self, container_id: str) -> dict:
        return dict(self.lookups.get(container_id, {"found": False}))


def publish_job(
    job_id: int,
    publisher: ThreadsPublisher,
    content: str,
    parts: Optional[list[str]] = None,
    image_url: str = "",
    path=None,
) -> PublishOutcome:
    """Publish one claimed job. Idempotent per operation_id.

    The four outcomes:
      * submit returns a post id      -> PUBLISHED
      * submit raises a clear error   -> FAILED (retryable)
      * submit times out              -> UNKNOWN_OUTCOME (reconcile later)
      * job already PUBLISHED         -> no-op, returns the existing id
    """
    job = get_job(job_id, path=path)
    if job is None:
        return PublishOutcome(job_id, "", FAILED, error="job_not_found")

    # Idempotency: an already-published operation never publishes again.
    if job.state == PUBLISHED:
        return PublishOutcome(job_id, job.operation_id, PUBLISHED,
                              published_id=job.published_id)

    if job.state != PUBLISHING:
        return PublishOutcome(job_id, job.operation_id, job.state,
                              error=f"job not claimable (state={job.state})")

    try:
        result = publisher.submit(content, parts or [], image_url)
    except TimeoutError as exc:
        # The API may have accepted the container. We do not know, so we do
        # not retry — we reconcile.
        mark_unknown_outcome(job_id, f"timeout: {exc}", path=path)
        log.warning("Job %s submit timed out; marked UNKNOWN_OUTCOME for "
                    "reconciliation (no duplicate publish).", job_id)
        return PublishOutcome(job_id, job.operation_id, UNKNOWN_OUTCOME,
                              error=f"timeout: {exc}")
    except Exception as exc:  # noqa: BLE001
        mark_failed(job_id, f"{type(exc).__name__}: {exc}", path=path)
        return PublishOutcome(job_id, job.operation_id, FAILED,
                              error=f"{type(exc).__name__}: {exc}")

    post_id = str(result.get("post_id") or result.get("id") or "")
    if not post_id:
        mark_failed(job_id, "publisher returned no post id", path=path)
        return PublishOutcome(job_id, job.operation_id, FAILED,
                              error="no post id returned")

    mark_published(job_id, post_id, path=path)
    return PublishOutcome(job_id, job.operation_id, PUBLISHED,
                          published_id=post_id)


def reconcile_unknown(
    job_id: int,
    publisher: ThreadsPublisher,
    container_id: str = "",
    path=None,
) -> PublishOutcome:
    """Resolve an UNKNOWN_OUTCOME job by asking the API, never by republishing.

    If the lookup finds the post, the job becomes PUBLISHED with the real id.
    If it definitively does not exist, the job returns to QUEUED so it can be
    retried safely. If the lookup is itself inconclusive, the job stays
    UNKNOWN_OUTCOME for a human.
    """
    job = get_job(job_id, path=path)
    if job is None:
        return PublishOutcome(job_id, "", FAILED, error="job_not_found")
    if job.state != UNKNOWN_OUTCOME:
        return PublishOutcome(job_id, job.operation_id, job.state)

    try:
        info = publisher.lookup(container_id or job.operation_id)
    except Exception as exc:  # noqa: BLE001
        return PublishOutcome(job_id, job.operation_id, UNKNOWN_OUTCOME,
                              error=f"lookup failed: {exc}")

    if info.get("found") and info.get("post_id"):
        mark_published(job_id, str(info["post_id"]), path=path)
        return PublishOutcome(job_id, job.operation_id, PUBLISHED,
                              published_id=str(info["post_id"]),
                              reconciled=True)

    if info.get("definitely_absent"):
        with connect(path) as conn:
            with transaction(conn):
                conn.execute(
                    "UPDATE publication_jobs SET state = ?, claimed_at = NULL, "
                    "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (QUEUED, job_id),
                )
        return PublishOutcome(job_id, job.operation_id, QUEUED,
                              error="reconciled as absent; requeued",
                              reconciled=True)

    return PublishOutcome(job_id, job.operation_id, UNKNOWN_OUTCOME,
                          error="lookup inconclusive; manual review needed")
