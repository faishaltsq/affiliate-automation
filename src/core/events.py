"""src/core/events.py — structured audit event helpers.

Every state transition that matters writes one row to audit_events. The log
line is for humans; the row is for forensics after a crash.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

from src.core.config import redact_secrets
from src.core.storage import connection, record_audit, transaction
from src.core.clock import utc_iso


def emit(
    action: str,
    object_type: str = "",
    object_id: str = "",
    actor_type: str = "system",
    actor_id: str = "",
    before: Any = None,
    after: Any = None,
    reason: str = "",
    path=None,
) -> int:
    """Append one audit event in its own transaction."""
    with connection(path) as conn:
        with transaction(conn):
            return record_audit(
                conn,
                actor_type=actor_type,
                action=redact_secrets(action),
                object_type=object_type,
                object_id=str(object_id),
                actor_id=str(actor_id),
                before=before,
                after=after,
                reason=redact_secrets(reason),
            )


def recent(limit: int = 50, action: Optional[str] = None, path=None) -> list[dict]:
    sql = "SELECT * FROM audit_events"
    params: list[Any] = []
    if action:
        sql += " WHERE action = ?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connection(path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def for_object(object_type: str, object_id: str, path=None) -> list[dict]:
    with connection(path) as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM audit_events WHERE object_type = ? AND object_id = ? "
                "ORDER BY id ASC",
                (object_type, str(object_id)),
            ).fetchall()
        ]


def count_since(hours: int = 24, path=None) -> int:
    with connection(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE created_at >= datetime('now', ?)",
            (f"-{hours} hours",),
        ).fetchone()
        return int(row[0])
