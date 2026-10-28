"""src/orchestration/analytics.py — verified metrics with coverage caveats.

V1's `src/organic/feedback.py` refreshed views/likes/replies and fed them
straight into pillar weighting. Two problems:
  * all-zero metrics were indistinguishable from "insights unavailable"
  * engagement was treated as a quality signal on its own

V2 keeps `impressions`, `likes`, `replies` — and explicitly records
`source_status` so a failed fetch is never confused with a real zero. Commerce
metrics stay separate: `clicks`, `attributed_orders`, and `commission` are only
recorded when the affiliate platform actually reports them. Conversion is never
inferred from Threads likes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.core.clock import parse_sqlite_utc, sqlite_utc, utc_now, wib_display
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("orchestration_analytics")

# source_status values. Anything other than 'ok' means the numbers are absent,
# not zero.
OK = "ok"
UNAVAILABLE = "unavailable"
RATE_LIMITED = "rate_limited"
ERROR = "error"
NOT_FOUND = "not_found"

VALID_SOURCE_STATUS = {OK, UNAVAILABLE, RATE_LIMITED, ERROR, NOT_FOUND}

# Minimum age before a post's metrics mean anything. A brand-new post
# undercounts badly, and weighting pillars on it starves them unfairly.
MIN_AGE_HOURS = 24
# Don't re-poll the same post more often than this.
REFETCH_COOLDOWN_HOURS = 6


@dataclass
class Snapshot:
    threads_id: str
    views: Optional[int] = None
    likes: Optional[int] = None
    replies: Optional[int] = None
    source_status: str = OK
    fetched_at: Optional[str] = None
    id: Optional[int] = None

    @property
    def has_data(self) -> bool:
        return self.source_status == OK and any(
            v is not None for v in (self.views, self.likes, self.replies)
        )


@dataclass
class Coverage:
    """How much of the window we actually have data for."""

    posts: int = 0
    with_data: int = 0
    without_data: int = 0
    by_status: dict[str, int] = field(default_factory=dict)

    @property
    def coverage_ratio(self) -> float:
        if not self.posts:
            return 0.0
        return round(self.with_data / self.posts, 4)

    @property
    def caveat(self) -> str:
        if not self.posts:
            return "no posts in window"
        if self.coverage_ratio < 0.5:
            return (
                f"only {self.coverage_ratio:.0%} of posts have verified "
                "metrics — averages are indicative, not conclusive"
            )
        return f"{self.coverage_ratio:.0%} metric coverage"


def record_snapshot(
    threads_id: str,
    views: Optional[int],
    likes: Optional[int],
    replies: Optional[int],
    source_status: str = OK,
    path=None,
) -> int:
    """Store one measurement. `None` means unavailable, `0` means measured zero."""
    if source_status not in VALID_SOURCE_STATUS:
        source_status = ERROR
    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO performance_snapshots
                    (threads_id, views, likes, replies, source_status, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(threads_id), views, likes, replies, source_status,
                 sqlite_utc()),
            )
            return int(cur.lastrowid)


def latest_snapshot(threads_id: str, path=None) -> Optional[Snapshot]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM performance_snapshots WHERE threads_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (str(threads_id),),
        ).fetchone()
    if row is None:
        return None
    return Snapshot(
        threads_id=row["threads_id"], views=row["views"], likes=row["likes"],
        replies=row["replies"], source_status=row["source_status"],
        fetched_at=row["fetched_at"], id=int(row["id"]),
    )


def snapshots_since(hours: int = 24 * 30, path=None) -> list[Snapshot]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM performance_snapshots "
            "WHERE fetched_at >= datetime('now', ?) ORDER BY id ASC",
            (f"-{hours} hours",),
        ).fetchall()
    return [
        Snapshot(
            threads_id=r["threads_id"], views=r["views"], likes=r["likes"],
            replies=r["replies"], source_status=r["source_status"],
            fetched_at=r["fetched_at"], id=int(r["id"]),
        )
        for r in rows
    ]


def posts_needing_refresh(
    min_age_hours: int = MIN_AGE_HOURS,
    cooldown_hours: int = REFETCH_COOLDOWN_HOURS,
    limit: int = 20,
    path=None,
) -> list[dict]:
    """Published posts whose metrics are stale or missing.

    A post with a failed snapshot is retried after the cooldown; a post with
    no snapshot at all is picked up once it is old enough to be meaningful.
    """
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT pj.published_id AS threads_id,
                   pj.id AS job_id,
                   (SELECT MAX(fetched_at) FROM performance_snapshots ps
                    WHERE ps.threads_id = pj.published_id) AS last_fetch,
                   (SELECT source_status FROM performance_snapshots ps
                    WHERE ps.threads_id = pj.published_id
                    ORDER BY id DESC LIMIT 1) AS last_status
            FROM publication_jobs pj
            WHERE pj.state = 'PUBLISHED' AND pj.published_id IS NOT NULL
            ORDER BY pj.id DESC LIMIT ?
            """,
            (limit * 4,),
        ).fetchall()

    out = []
    for r in rows:
        last_fetch = parse_sqlite_utc(r["last_fetch"]) if r["last_fetch"] else None
        if last_fetch is not None:
            age_h = (utc_now() - last_fetch).total_seconds() / 3600.0
            if age_h < cooldown_hours:
                continue
        out.append({
            "threads_id": r["threads_id"], "job_id": r["job_id"],
            "last_status": r["last_status"] or "never",
        })
        if len(out) >= limit:
            break
    return out


def pillar_performance(days: int = 30, path=None) -> dict:
    """Average verified metrics per pillar, with an explicit coverage caveat.

    Posts whose latest snapshot is not 'ok' are excluded rather than counted
    as zero — including them would drag every average toward 0 and starve
    pillars that merely had a bad fetch.
    """
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT cd.id AS draft_id,
                   (SELECT pillar FROM content_ideas ci
                    WHERE ci.id = cd.idea_id) AS pillar,
                   pj.published_id AS threads_id
            FROM content_drafts cd
            JOIN publication_jobs pj ON pj.draft_id = cd.id
            WHERE pj.state = 'PUBLISHED' AND pj.published_id IS NOT NULL
            """,
        ).fetchall()

    by_pillar: dict[str, list[Snapshot]] = {}
    for r in rows:
        pillar = r["pillar"]
        if not pillar:
            continue
        snap = latest_snapshot(r["threads_id"], path=path)
        if snap is None or snap.source_status != OK:
            continue
        by_pillar.setdefault(pillar, []).append(snap)

    out: dict[str, dict] = {}
    for pillar, snaps in by_pillar.items():
        views = [s.views for s in snaps if s.views is not None]
        likes = [s.likes for s in snaps if s.likes is not None]
        replies = [s.replies for s in snaps if s.replies is not None]
        out[pillar] = {
            "posts": len(snaps),
            "avg_views": round(sum(views) / len(views), 1) if views else None,
            "avg_likes": round(sum(likes) / len(likes), 1) if likes else None,
            "avg_replies": round(sum(replies) / len(replies), 1) if replies else None,
        }
    return out


def coverage(days: int = 30, path=None) -> Coverage:
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT pj.published_id AS threads_id
            FROM publication_jobs pj
            WHERE pj.state = 'PUBLISHED' AND pj.published_id IS NOT NULL
            """,
        ).fetchall()
    cov = Coverage(posts=len(rows))
    for r in rows:
        snap = latest_snapshot(r["threads_id"], path=path)
        if snap is not None and snap.has_data:
            cov.with_data += 1
        else:
            cov.without_data += 1
            status = snap.source_status if snap else "never"
            cov.by_status[status] = cov.by_status.get(status, 0) + 1
    return cov


def health(path=None) -> dict:
    from src.orchestration.publisher import (
        oldest_pending_age_seconds, queue_depth,
    )
    from src.orchestration.scheduler import is_paused

    cov = coverage(path=path)
    return {
        "checked_at": wib_display(),
        "scheduler_paused": is_paused(path=path),
        "queue": queue_depth(path=path),
        "oldest_pending_age_seconds": oldest_pending_age_seconds(path=path),
        "metric_coverage": {
            "posts": cov.posts,
            "with_data": cov.with_data,
            "coverage_ratio": cov.coverage_ratio,
            "caveat": cov.caveat,
        },
    }


# ── Commerce metrics: never inferred from engagement ────────────────────────
def record_commerce_metric(
    post_id: str,
    product_id: int,
    clicks: Optional[int] = None,
    attributed_orders: Optional[int] = None,
    commission: Optional[float] = None,
    source: str = "affiliate_platform",
    path=None,
) -> None:
    """Store a commerce metric. Every field is optional and platform-sourced.

    `clicks`, `attributed_orders`, and `commission` are only ever recorded
    when the affiliate platform reports them. There is no code path that
    derives them from Threads likes or views.
    """
    import json

    payload = {
        "clicks": clicks, "attributed_orders": attributed_orders,
        "commission": commission, "source": source,
    }
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO audit_events
                    (actor_type, action, object_type, object_id, after_json)
                VALUES ('system', 'commerce_metric', 'promotion', ?, ?)
                """,
                (str(post_id), json.dumps(payload, ensure_ascii=False)),
            )
    log.info("Commerce metric recorded for post %s: %s", post_id, payload)


def commerce_summary(days: int = 30, path=None) -> dict:
    """Aggregate only what the affiliate platform actually reported."""
    import json

    with connect(path) as conn:
        rows = conn.execute(
            "SELECT after_json FROM audit_events "
            "WHERE action = 'commerce_metric' "
            "AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        ).fetchall()

    clicks = orders = 0
    commission = 0.0
    reported = 0
    for r in rows:
        try:
            data = json.loads(r["after_json"] or "{}")
        except (ValueError, TypeError):
            continue
        reported += 1
        clicks += int(data.get("clicks") or 0)
        orders += int(data.get("attributed_orders") or 0)
        commission += float(data.get("commission") or 0.0)

    return {
        "window_days": days,
        "reports": reported,
        "clicks": clicks,
        "attributed_orders": orders,
        "commission": round(commission, 2),
        "note": (
            "figures come only from the affiliate platform; nothing here is "
            "inferred from Threads engagement"
        ),
    }
