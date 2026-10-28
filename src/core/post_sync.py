"""src/core/post_sync.py — safe Threads post-status reconciliation.

Replaces `src/organic/db.py::sync_with_threads_status`, which DELETEd rows on
any non-200 response. A 429, a 5xx, an expired token, or a socket timeout all
produced the same outcome: permanent loss of content history.

New contract:
  * 200                      → status='published', last_sync_status='ok'
  * 404 (authoritative)      → status='deleted', deleted_at set, row KEPT
  * 429 / 5xx / timeout /    → status unchanged, last_sync_status records why
    transport / auth error     (retryable); the row is never touched
  * all-zero insights        → status unchanged, metrics NOT overwritten

Nothing is ever deleted. Confirmed deletion is a tombstone.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Optional

from src.core.clock import sqlite_utc
from src.core.storage import connection, transaction

# Sync outcomes, ordered by how much we trust them.
OK = "ok"
NOT_FOUND = "not_found"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
AUTH_ERROR = "auth_error"
PERMISSION_ERROR = "permission_error"
TRANSPORT_ERROR = "transport_error"
UNKNOWN = "unknown"


def classify_status(code: Optional[int], error: Optional[str] = None) -> str:
    """Map an HTTP result (or transport exception) onto a sync outcome."""
    if code is None:
        text = (error or "").lower()
        if "timeout" in text or "timed out" in text:
            return TRANSPORT_ERROR
        if "connection" in text:
            return TRANSPORT_ERROR
        return UNKNOWN
    if code == 200:
        return OK
    if code == 404:
        return NOT_FOUND
    if code == 429:
        return RATE_LIMITED
    if code in (401, 403):
        return AUTH_ERROR
    if 500 <= code < 600:
        return SERVER_ERROR
    if code in (400, 422):
        # A malformed request is our bug, not a permission problem — but it is
        # still not evidence the post is gone.
        return PERMISSION_ERROR
    if 400 <= code < 500:
        return PERMISSION_ERROR
    return UNKNOWN


@dataclass
class SyncOutcome:
    threads_id: str
    result: str
    http_code: Optional[int] = None
    detail: str = ""
    tombstoned: bool = False


def _probe(threads_id: str, prober: Callable[[str], tuple[Optional[int], str]]) -> tuple[Optional[int], str]:
    try:
        return prober(threads_id)
    except Exception as exc:  # noqa: BLE001 — transport failure is data, not a crash
        return None, str(exc)


def reconcile_post(
    conn: sqlite3.Connection,
    post_id: int,
    threads_id: str,
    prober: Callable[[str], tuple[Optional[int], str]],
) -> SyncOutcome:
    """Reconcile one row. `prober` returns (http_code, detail).

    The caller supplies the prober so this module never performs network I/O
    itself — tests pass a fake, production passes the Threads API call.
    """
    code, detail = _probe(threads_id, prober)
    result = classify_status(code, detail)
    now = sqlite_utc()

    if result == OK:
        with transaction(conn):
            conn.execute(
                "UPDATE organic_posts SET status = 'published', deleted_at = NULL, "
                "delete_reason = '', last_sync_status = ?, last_sync_at = ? "
                "WHERE id = ?",
                (result, now, post_id),
            )
        return SyncOutcome(threads_id, result, code, detail)

    if result == NOT_FOUND:
        # Authoritative deletion. Keep the row, mark it, record why.
        with transaction(conn):
            conn.execute(
                "UPDATE organic_posts SET status = 'deleted', deleted_at = ?, "
                "delete_reason = ?, last_sync_status = ?, last_sync_at = ? "
                "WHERE id = ?",
                (now, "confirmed 404 from Threads API", result, now, post_id),
            )
        return SyncOutcome(threads_id, result, code, detail, tombstoned=True)

    # Everything else is transient or ambiguous: record the observation,
    # change nothing about the post's lifecycle.
    with transaction(conn):
        conn.execute(
            "UPDATE organic_posts SET last_sync_status = ?, last_sync_at = ? "
            "WHERE id = ?",
            (result, now, post_id),
        )
    return SyncOutcome(threads_id, result, code, detail)


def reconcile_all(
    prober: Callable[[str], tuple[Optional[int], str]],
    path=None,
    limit: int = 200,
) -> dict:
    """Reconcile every non-tombstoned post. Returns a counted summary."""
    summary = {
        "checked": 0, "ok": 0, "tombstoned": 0,
        "rate_limited": 0, "server_error": 0, "auth_error": 0,
        "permission_error": 0, "transport_error": 0, "unknown": 0,
    }
    with connection(path) as conn:
        rows = conn.execute(
            "SELECT id, threads_id FROM organic_posts "
            "WHERE threads_id IS NOT NULL AND threads_id != '' "
            "AND (status IS NULL OR status != 'deleted') "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        for row in rows:
            outcome = reconcile_post(conn, row["id"], row["threads_id"], prober)
            summary["checked"] += 1
            key = outcome.result if outcome.result in summary else "unknown"
            summary[key] += 1
            if outcome.tombstoned:
                summary["tombstoned"] += 1
    return summary


def default_threads_prober(threads_id: str) -> tuple[Optional[int], str]:
    """Production prober: GET /{threads_id}?fields=id."""
    import os

    import requests

    from src.core.config import get_settings

    cfg = get_settings().threads
    if not cfg.configured:
        return None, "threads credentials not configured"
    try:
        r = requests.get(
            f"{cfg.base_url}/{threads_id}",
            params={"fields": "id", "access_token": cfg.access_token},
            timeout=10,
        )
        return r.status_code, r.text[:200]
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def row_count(path=None) -> int:
    with connection(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM organic_posts").fetchone()[0])


def status_counts(path=None) -> dict:
    with connection(path) as conn:
        rows = conn.execute(
            "SELECT COALESCE(status,'published') AS s, COUNT(*) AS n "
            "FROM organic_posts GROUP BY s"
        ).fetchall()
        return {r["s"]: r["n"] for r in rows}
