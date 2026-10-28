"""src/orchestration/recovery.py — restart recovery and reconciliation.

Answers the question V1 could not: after a crash, a restart, or a timeout,
what is the true state of the world?

Three recovery duties:
  1. Expired leases are reclaimed so a crashed worker does not stall the queue.
  2. UNKNOWN_OUTCOME jobs are reconciled against the API, never republished.
  3. A startup sweep reports anything that needs a human, without touching it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.core.clock import sqlite_utc, utc_now, utc_iso
from src.core.storage import connect, connection, transaction
from src.logger import get_logger

from src.orchestration.publisher import (
    FAILED, MAX_ATTEMPTS, PUBLISHED, PUBLISHING, QUEUED, UNKNOWN_OUTCOME,
    PublicationJob, get_job, jobs_in_state, reconcile_unknown, requeue,
)

log = get_logger("orchestration_recovery")


@dataclass
class RecoveryReport:
    started_at: str = field(default_factory=utc_iso)
    expired_leases_reclaimed: int = 0
    unknown_reconciled_published: int = 0
    unknown_reconciled_absent: int = 0
    unknown_still_pending: int = 0
    failed_requeued: int = 0
    failed_parked: int = 0
    needs_human: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "expired_leases_reclaimed": self.expired_leases_reclaimed,
            "unknown_reconciled_published": self.unknown_reconciled_published,
            "unknown_reconciled_absent": self.unknown_reconciled_absent,
            "unknown_still_pending": self.unknown_still_pending,
            "failed_requeued": self.failed_requeued,
            "failed_parked": self.failed_parked,
            "needs_human": self.needs_human,
            "actions": self.actions,
        }


def reclaim_expired_leases(path=None) -> int:
    """Return jobs whose lease lapsed back to QUEUED.

    A worker that claimed a job and then died leaves it in PUBLISHING with a
    lease that eventually expires. Without this, the queue stalls forever.
    """
    now_s = sqlite_utc(utc_now())
    with connection(path) as conn:
        with transaction(conn):
            rows = conn.execute(
                "SELECT id FROM publication_jobs "
                "WHERE state = ? AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at < ?",
                (PUBLISHING, now_s),
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE publication_jobs SET state = ?, claimed_at = NULL, "
                    "lease_expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ?",
                    (QUEUED, int(row["id"])),
                )
    n = len(rows)
    if n:
        log.info("Reclaimed %d expired lease(s).", n)
    return n


def reconcile_unknown_jobs(publisher, path=None) -> tuple[int, int, int]:
    """Reconcile every UNKNOWN_OUTCOME job. Returns (published, absent, pending)."""
    published = absent = pending = 0
    for job in jobs_in_state(UNKNOWN_OUTCOME, path=path):
        outcome = reconcile_unknown(job.id, publisher, path=path)
        if outcome.state == PUBLISHED:
            published += 1
        elif outcome.state == QUEUED:
            absent += 1
        else:
            pending += 1
    return published, absent, pending


def retry_failed_jobs(path=None) -> tuple[int, int]:
    """Requeue FAILED jobs that still have budget. Returns (requeued, parked)."""
    requeued = parked = 0
    for job in jobs_in_state(FAILED, path=path):
        if job.attempts >= MAX_ATTEMPTS:
            parked += 1
            continue
        if requeue(job.id, path=path):
            requeued += 1
        else:
            parked += 1
    return requeued, parked


def startup_sweep(publisher=None, path=None) -> RecoveryReport:
    """The full restart recovery pass. Safe to run on every boot."""
    report = RecoveryReport()

    report.expired_leases_reclaimed = reclaim_expired_leases(path=path)
    if report.expired_leases_reclaimed:
        report.actions.append(
            f"reclaimed {report.expired_leases_reclaimed} expired lease(s)"
        )

    if publisher is not None:
        pub, absent, pending = reconcile_unknown_jobs(publisher, path=path)
        report.unknown_reconciled_published = pub
        report.unknown_reconciled_absent = absent
        report.unknown_still_pending = pending
        if pub:
            report.actions.append(f"reconciled {pub} unknown job(s) as PUBLISHED")
        if absent:
            report.actions.append(f"reconciled {absent} unknown job(s) as absent, requeued")
        if pending:
            report.needs_human.append(
                f"{pending} publication job(s) still UNKNOWN_OUTCOME — "
                "check the Threads account manually before retrying"
            )

    requeued, parked = retry_failed_jobs(path=path)
    report.failed_requeued = requeued
    report.failed_parked = parked
    if requeued:
        report.actions.append(f"requeued {requeued} failed job(s)")
    if parked:
        report.needs_human.append(
            f"{parked} failed job(s) exhausted their retry budget and need review"
        )

    log.info(
        "Recovery sweep: leases=%d unknown(pub=%d absent=%d pending=%d) "
        "failed(requeued=%d parked=%d)",
        report.expired_leases_reclaimed, pub if publisher else 0,
        absent if publisher else 0, pending if publisher else 0,
        requeued, parked,
    )
    return report


def pending_human_review(path=None) -> list[dict]:
    """Everything an operator should look at, without changing anything."""
    out: list[dict] = []
    for job in jobs_in_state(UNKNOWN_OUTCOME, path=path):
        out.append({
            "job_id": job.id, "operation_id": job.operation_id,
            "state": job.state, "attempts": job.attempts,
            "last_error": job.last_error,
            "action": "verify on Threads, then reconcile or requeue",
        })
    for job in jobs_in_state(FAILED, path=path):
        if job.attempts >= MAX_ATTEMPTS:
            out.append({
                "job_id": job.id, "operation_id": job.operation_id,
                "state": job.state, "attempts": job.attempts,
                "last_error": job.last_error,
                "action": "retry budget exhausted; inspect and requeue manually",
            })
    return out


def is_safe_to_restart(path=None) -> tuple[bool, str]:
    """Pre-restart sanity check for the runbook."""
    with connection(path) as conn:
        publishing = conn.execute(
            "SELECT COUNT(*) AS n FROM publication_jobs WHERE state = ?",
            (PUBLISHING,),
        ).fetchone()["n"]
        unknown = conn.execute(
            "SELECT COUNT(*) AS n FROM publication_jobs WHERE state = ?",
            (UNKNOWN_OUTCOME,),
        ).fetchone()["n"]
    if unknown:
        return False, (
            f"{unknown} job(s) in UNKNOWN_OUTCOME. Reconcile them before "
            "restarting so a duplicate cannot be published."
        )
    if publishing:
        return True, (
            f"{publishing} job(s) mid-publish; their leases will expire and be "
            "reclaimed on the next sweep."
        )
    return True, "no in-flight publications"
