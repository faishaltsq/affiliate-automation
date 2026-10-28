"""src/core/storage.py — SQLite connection, transactions, migration runner.

Design constraints from the master plan:
  * WAL mode when the filesystem allows it (OneDrive usually does).
  * busy_timeout so the legacy daemon and V2 never deadlock on the same file.
  * Additive, idempotent, reversible-where-feasible migrations.
  * Never DROP a user table. Tombstones instead of DELETE.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from src.core.clock import sqlite_utc, utc_now
from src.core.config import get_settings

_LOCAL = threading.local()

SCHEMA_VERSION = 2


def database_path() -> Path:
    return get_settings().database_path


def connect(path: Path | str | None = None, row_factory: bool = True) -> sqlite3.Connection:
    """Open a connection with the pragmas V2 relies on.

    check_same_thread=False is required because the scheduler and the Telegram
    listener may share a connection across threads; every write is serialised by
    SQLite itself plus the busy timeout.
    """
    target = Path(path) if path else database_path()
    conn = sqlite3.connect(
        str(target),
        timeout=30.0,
        isolation_level=None,  # explicit BEGIN/COMMIT via transaction()
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row if row_factory else None
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        pass  # network filesystems may refuse WAL; rollback journal still works
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def connection(path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. Rolls back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}


def index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def set_user_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(f"PRAGMA user_version = {int(version)}")


# ── Migration framework ─────────────────────────────────────────────────────
class Migration:
    """One additive, idempotent schema step."""

    version: int = 0
    name: str = ""

    def upgrade(self, conn: sqlite3.Connection) -> None:  # pragma: no cover
        raise NotImplementedError

    def downgrade(self, conn: sqlite3.Connection) -> None:
        """Default: no-op. Additive migrations are not reversed automatically."""


def _create_table(conn: sqlite3.Connection, ddl: str, name: str) -> None:
    if not table_exists(conn, name):
        conn.execute(ddl)


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in column_names(conn, table):
        conn.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {ddl}')


def _create_index(conn: sqlite3.Connection, name: str, ddl: str) -> None:
    if not index_exists(conn, name):
        conn.execute(ddl)


class Migration000Baseline(Migration):
    """Phase 0/1 — guarantee the legacy baseline tables exist.

    On the production database these tables already exist and every statement
    below is a no-op. On a fresh database (tests, new install) they are created
    with the *legacy* column set so the still-running daemon keeps working
    unchanged; Migration001 then adds the V2 lifecycle columns on top.
    """

    version = 0
    name = "baseline"

    def upgrade(self, conn: sqlite3.Connection) -> None:
        # The bookkeeping table must exist before the runner can record that
        # the baseline itself ran.
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checksum TEXT DEFAULT ''
            )
        """, "schema_migrations")
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS organic_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                pillar TEXT,
                topic TEXT,
                content TEXT,
                image_url TEXT,
                threads_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "organic_posts")
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "scheduler_state")
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS affiliate_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                affiliate_url TEXT NOT NULL,
                target_thread_url TEXT,
                product_name TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                fired_at TIMESTAMP,
                fired_post_id TEXT,
                fired_reply_id TEXT
            )
        """, "affiliate_queue")
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS affiliate_comment_history (
                post_id TEXT PRIMARY KEY,
                reply_id TEXT,
                affiliate_url TEXT,
                product_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "affiliate_comment_history")


class Migration001CoreSafety(Migration):
    """Phase 1 — tombstones, audit trail, operator sessions."""

    version = 1
    name = "core_safety"

    def upgrade(self, conn: sqlite3.Connection) -> None:
        # organic_posts gains a lifecycle instead of being DELETEd on non-200.
        _add_column(conn, "organic_posts", "status",
                    "TEXT NOT NULL DEFAULT 'published'")
        _add_column(conn, "organic_posts", "deleted_at", "TIMESTAMP")
        _add_column(conn, "organic_posts", "delete_reason", "TEXT DEFAULT ''")
        _add_column(conn, "organic_posts", "last_sync_status", "TEXT DEFAULT ''")
        _add_column(conn, "organic_posts", "last_sync_at", "TIMESTAMP")
        _add_column(conn, "organic_posts", "persona_version", "TEXT DEFAULT ''")
        _add_column(conn, "organic_posts", "pillar_v2", "TEXT DEFAULT ''")
        _create_index(conn, "idx_organic_posts_status",
                      "CREATE INDEX IF NOT EXISTS idx_organic_posts_status "
                      "ON organic_posts(status)")
        _create_index(conn, "idx_organic_posts_threads_id",
                      "CREATE INDEX IF NOT EXISTS idx_organic_posts_threads_id "
                      "ON organic_posts(threads_id)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                object_type TEXT NOT NULL DEFAULT '',
                object_id TEXT NOT NULL DEFAULT '',
                before_json TEXT DEFAULT '',
                after_json TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "audit_events")
        _create_index(conn, "idx_audit_events_object",
                      "CREATE INDEX IF NOT EXISTS idx_audit_events_object "
                      "ON audit_events(object_type, object_id)")
        _create_index(conn, "idx_audit_events_created",
                      "CREATE INDEX IF NOT EXISTS idx_audit_events_created "
                      "ON audit_events(created_at)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                checksum TEXT DEFAULT ''
            )
        """, "schema_migrations")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS telegram_callback_tokens (
                token TEXT PRIMARY KEY,
                draft_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                consumed_at TIMESTAMP,
                expires_at TIMESTAMP NOT NULL
            )
        """, "telegram_callback_tokens")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS operator_sessions (
                actor_id TEXT PRIMARY KEY,
                username TEXT DEFAULT '',
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_command TEXT DEFAULT '',
                denied_count INTEGER DEFAULT 0
            )
        """, "operator_sessions")


class Migration002PersonaStrategy(Migration):
    """Phase 2 — persona versions, audience signals, calendar, ideas."""

    version = 2
    name = "persona_strategy"

    def upgrade(self, conn: sqlite3.Connection) -> None:
        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS persona_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                content_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 0
            )
        """, "persona_versions")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS audience_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                problem TEXT NOT NULL,
                source_url TEXT DEFAULT '',
                verified_at TIMESTAMP,
                confidence REAL NOT NULL DEFAULT 0.5,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "audience_signals")
        _create_index(conn, "idx_audience_signals_category",
                      "CREATE INDEX IF NOT EXISTS idx_audience_signals_category "
                      "ON audience_signals(category, status)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS content_ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pillar TEXT NOT NULL,
                angle TEXT NOT NULL DEFAULT '',
                source_ids_json TEXT DEFAULT '[]',
                idea_state TEXT NOT NULL DEFAULT 'IDEA',
                planned_date TEXT,
                rank_score REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "content_ideas")
        _create_index(conn, "idx_content_ideas_state",
                      "CREATE INDEX IF NOT EXISTS idx_content_ideas_state "
                      "ON content_ideas(idea_state, planned_date)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS calendar_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_date TEXT NOT NULL,
                pillar TEXT NOT NULL,
                audience_problem TEXT DEFAULT '',
                target_angle TEXT DEFAULT '',
                format TEXT DEFAULT 'text',
                source_constraints TEXT DEFAULT '',
                commercial_allowed INTEGER NOT NULL DEFAULT 0,
                slot_kind TEXT NOT NULL DEFAULT 'main',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(slot_date, slot_kind)
            )
        """, "calendar_slots")
        _create_index(conn, "idx_calendar_slots_date",
                      "CREATE INDEX IF NOT EXISTS idx_calendar_slots_date "
                      "ON calendar_slots(slot_date)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS content_drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                idea_id INTEGER,
                content TEXT NOT NULL,
                structure TEXT NOT NULL DEFAULT 'single',
                persona_version TEXT NOT NULL DEFAULT '',
                draft_version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                parts_json TEXT DEFAULT '[]',
                image_query TEXT DEFAULT '',
                disclosure TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "content_drafts")
        _create_index(conn, "idx_content_drafts_status",
                      "CREATE INDEX IF NOT EXISTS idx_content_drafts_status "
                      "ON content_drafts(status)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS content_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                scores_json TEXT NOT NULL DEFAULT '{}',
                vetoes_json TEXT NOT NULL DEFAULT '[]',
                verdict TEXT NOT NULL,
                total_score REAL NOT NULL DEFAULT 0,
                evaluator_version TEXT NOT NULL DEFAULT 'v1',
                notes TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "content_evaluations")
        _create_index(conn, "idx_content_evaluations_draft",
                      "CREATE INDEX IF NOT EXISTS idx_content_evaluations_draft "
                      "ON content_evaluations(draft_id)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS publication_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                operation_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL DEFAULT 'QUEUED',
                claimed_at TIMESTAMP,
                lease_expires_at TIMESTAMP,
                attempts INTEGER NOT NULL DEFAULT 0,
                published_id TEXT,
                last_error TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "publication_jobs")
        _create_index(conn, "idx_publication_jobs_state",
                      "CREATE INDEX IF NOT EXISTS idx_publication_jobs_state "
                      "ON publication_jobs(state, lease_expires_at)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS performance_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                threads_id TEXT NOT NULL,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                views INTEGER NOT NULL DEFAULT 0,
                likes INTEGER NOT NULL DEFAULT 0,
                replies INTEGER NOT NULL DEFAULT 0,
                source_status TEXT NOT NULL DEFAULT 'ok'
            )
        """, "performance_snapshots")
        _create_index(conn, "idx_performance_snapshots_threads",
                      "CREATE INDEX IF NOT EXISTS idx_performance_snapshots_threads "
                      "ON performance_snapshots(threads_id, fetched_at)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS feedback_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER,
                actor_id TEXT NOT NULL,
                category TEXT NOT NULL,
                reason TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "feedback_events")
        _create_index(conn, "idx_feedback_events_draft",
                      "CREATE INDEX IF NOT EXISTS idx_feedback_events_draft "
                      "ON feedback_events(draft_id)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                source_platform TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                original_affiliate_url TEXT DEFAULT '',
                verified_url TEXT DEFAULT '',
                affiliate_url TEXT DEFAULT '',
                tracking_state TEXT NOT NULL DEFAULT 'UNVERIFIED',
                checked_at TIMESTAMP,
                expires_at TIMESTAMP,
                price_if_verified REAL,
                restrictions TEXT DEFAULT '',
                reviewer_status TEXT NOT NULL DEFAULT 'pending',
                evidence_url TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "products")
        _create_index(conn, "idx_products_tracking",
                      "CREATE INDEX IF NOT EXISTS idx_products_tracking "
                      "ON products(tracking_state, reviewer_status)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS promotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                post_id TEXT NOT NULL,
                evidence TEXT DEFAULT '',
                disclosed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'planned',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "promotions")
        _create_index(conn, "idx_promotions_product",
                      "CREATE INDEX IF NOT EXISTS idx_promotions_product "
                      "ON promotions(product_id)")

        _create_table(conn, """
            CREATE TABLE IF NOT EXISTS experiment_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                hypothesis TEXT NOT NULL,
                variant TEXT NOT NULL,
                attribution_window TEXT NOT NULL DEFAULT '7d',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """, "experiment_assignments")


MIGRATIONS: list[Migration] = [
    Migration000Baseline(),
    Migration001CoreSafety(),
    Migration002PersonaStrategy(),
]


def pending_migrations(conn: sqlite3.Connection) -> list[Migration]:
    current = user_version(conn)
    return [m for m in MIGRATIONS if m.version > current]


def migrate(path: Path | str | None = None, target: int | None = None) -> list[str]:
    """Apply every pending migration. Returns the names applied.

    Safe to call repeatedly: each migration checks for existing columns/tables
    before creating them, and schema_migrations records what has been applied.

    The baseline migration (version 0) runs on a virgin database where
    user_version is still 0 and no schema_migrations row exists yet. It is
    skipped afterwards because its row is recorded.
    """
    applied: list[str] = []
    with connection(path) as conn:
        with transaction(conn):
            # Bookkeeping must exist before the runner can consult it.
            _create_table(conn, """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    checksum TEXT DEFAULT ''
                )
            """, "schema_migrations")
            current = user_version(conn)
            for migration in MIGRATIONS:
                if migration.version == 0:
                    already = conn.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = 0"
                    ).fetchone()
                    if already:
                        continue
                    migration.upgrade(conn)
                    conn.execute(
                        "INSERT OR REPLACE INTO schema_migrations (version, name) "
                        "VALUES (?, ?)",
                        (0, migration.name),
                    )
                    applied.append("v0:baseline")
                    continue
                if migration.version <= current:
                    continue
                if target is not None and migration.version > target:
                    break
                migration.upgrade(conn)
                set_user_version(conn, migration.version)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, name) "
                    "VALUES (?, ?)",
                    (migration.version, migration.name),
                )
                applied.append(f"v{migration.version}:{migration.name}")
    return applied


def migration_status(path: Path | str | None = None) -> dict:
    with connection(path) as conn:
        current = user_version(conn)
        if not table_exists(conn, "schema_migrations"):
            rows = []
        else:
            rows = conn.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        return {
            "current_version": current,
            "latest_version": MIGRATIONS[-1].version,
            "pending": [m.name for m in pending_migrations(conn)],
            "applied": [dict(r) for r in rows],
        }


# ── Audit trail ─────────────────────────────────────────────────────────────
def record_audit(
    conn: sqlite3.Connection,
    actor_type: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    actor_id: str = "",
    before: Any = None,
    after: Any = None,
    reason: str = "",
) -> int:
    import json

    conn.execute(
        """
        INSERT INTO audit_events
            (actor_type, actor_id, action, object_type, object_id,
             before_json, after_json, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_type,
            str(actor_id or ""),
            action,
            object_type,
            str(object_id or ""),
            json.dumps(before, ensure_ascii=False) if before is not None else "",
            json.dumps(after, ensure_ascii=False) if after is not None else "",
            reason,
            sqlite_utc(),
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def audit_log(
    conn: sqlite3.Connection,
    actor_type: str,
    action: str,
    object_type: str = "",
    object_id: str = "",
    actor_id: str = "",
    before: Any = None,
    after: Any = None,
    reason: str = "",
) -> int:
    """record_audit inside its own transaction — for callers without one."""
    with connection() as own:
        with transaction(own):
            return record_audit(
                own, actor_type, action, object_type, object_id,
                actor_id, before, after, reason,
            )


def retry_on_locked(fn, attempts: int = 5, base_delay: float = 0.05):
    """Run fn, retrying on SQLITE_BUSY/LOCKED with exponential backoff + jitter."""
    import random

    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            last = exc
            time.sleep(base_delay * (2 ** i) + random.uniform(0, base_delay))
    assert last is not None
    raise last


def now_utc_iso() -> str:
    return utc_now().isoformat()


def vacuum(path: Path | str | None = None) -> None:
    with connection(path) as conn:
        conn.execute("VACUUM")
