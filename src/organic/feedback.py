"""
Engagement feedback loop: pulls Threads insights for published organic posts
and stores them so the planner can weight pillars by measured performance.
"""

from src.organic.db import DB_PATH, update_post_metrics
from src.logger import get_logger

log = get_logger("organic_feedback")

# Threads insights for a brand-new post undercount badly; only score posts
# that have had a day to accumulate reads.
DEFAULT_MIN_AGE_HOURS = 24
# Don't re-poll a post more often than this.
REFETCH_COOLDOWN_HOURS = 6


def refresh_post_metrics(min_age_hours: int = DEFAULT_MIN_AGE_HOURS, max_fetches: int = 20) -> int:
    """Fetches views/likes/replies for published posts and stores them.

    Returns the number of posts successfully updated. One failed insight call
    never aborts the batch (warnings logged, remaining posts still processed).
    Reuses comment_engine.get_post_metrics — the /insights parsing already
    lives there; importing it fresh here would be a second implementation.
    """
    import sqlite3
    from src.affiliate.comment_engine import get_post_metrics

    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            """
            SELECT id, threads_id FROM organic_posts
            WHERE threads_id != ''
              AND (metrics_updated_at IS NULL
                   OR metrics_updated_at < datetime('now', ?))
              AND created_at <= datetime('now', ?)
            ORDER BY id DESC LIMIT ?
            """,
            (f"-{REFETCH_COOLDOWN_HOURS} hours", f"-{min_age_hours} hours", max_fetches),
        ).fetchall()
    finally:
        conn.close()

    updated = 0
    for _id, threads_id in rows:
        try:
            m = get_post_metrics(threads_id)
            update_post_metrics(threads_id, int(m.get("views", 0)), int(m.get("likes", 0)), int(m.get("replies", 0)))
            updated += 1
        except Exception as e:
            log.warning(f"Metrics fetch failed for post {threads_id}: {e}")
    if rows:
        log.info(f"Post metrics refreshed: {updated}/{len(rows)} posts")
    return updated
