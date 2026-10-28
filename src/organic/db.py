"""
SQLite Storage & Deduplication for Organic Netizen Posts
Tracks post history, timestamps, pillars, and Threads IDs.
Enforces 7-day anti-repetition filter on topics.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from src.config import DATA_DIR
from src.logger import get_logger

log = get_logger("organic_db")

DB_PATH = DATA_DIR / "posts.db"


def init_organic_db():
    """Initializes tables for organic posting and scheduler state."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
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
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduler_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Additive migration: rows written before chain support have no structure.
    cols = [row[1] for row in c.execute("PRAGMA table_info(organic_posts)")]
    if "post_structure" not in cols:
        c.execute("ALTER TABLE organic_posts ADD COLUMN post_structure TEXT DEFAULT 'single'")
    # Source context + engagement metrics (additive, idempotent).
    for col, decl in [
        ("source_summary", "TEXT DEFAULT ''"),
        ("views", "INTEGER DEFAULT 0"),
        ("likes", "INTEGER DEFAULT 0"),
        ("replies", "INTEGER DEFAULT 0"),
        ("metrics_updated_at", "TIMESTAMP"),
    ]:
        if col not in cols:
            c.execute(f"ALTER TABLE organic_posts ADD COLUMN {col} {decl}")
    # Article context survives daemon restarts (the in-process summary cache
    # does not), so the generator can still ground number use after a reboot.
    c.execute("""
        CREATE TABLE IF NOT EXISTS article_context (
            topic TEXT PRIMARY KEY,
            summary TEXT NOT NULL,
            source_names TEXT DEFAULT '',
            article_links TEXT DEFAULT '',
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    log.info("Organic DB tables verified.")


def is_topic_recent(topic: str, days: int = 7) -> bool:
    """Checks if a similar topic was posted in the last N days."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Check exact match or substring containment
    clean = topic.strip().lower()
    c.execute("""
        SELECT topic FROM organic_posts
        WHERE created_at >= datetime('now', ?)
    """, (f"-{days} days",))
    rows = c.fetchall()
    conn.close()

    for (past_topic,) in rows:
        past_clean = past_topic.strip().lower()
        if clean in past_clean or past_clean in clean:
            return True
    return False


def get_today_post_count() -> int:
    """Returns number of organic posts published today (UTC/local date)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM organic_posts
        WHERE date(created_at) = date('now')
    """)
    count = c.fetchone()[0]
    conn.close()
    return count


def get_last_post_time() -> str:
    """Returns ISO timestamp of the most recent organic post."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT created_at FROM organic_posts
        ORDER BY id DESC LIMIT 1
    """)
    row = c.fetchone()
    conn.close()
    return row[0] if row else ""


def log_organic_post(source: str, pillar: str, topic: str,
                     content: str, image_url: str, threads_id: str,
                     post_structure: str = "single", source_summary: str = ""):
    """Records an organic post into the database."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO organic_posts (source, pillar, topic, content, image_url, threads_id, post_structure, source_summary)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (source, pillar, topic, content, image_url, threads_id, post_structure, source_summary))
    conn.commit()
    conn.close()
    log.info(f"Logged organic post #{threads_id} on topic '{topic[:30]}' ({post_structure})")


def get_today_chain_count() -> int:
    """How many chain posts already went out today — the planner's daily quota."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM organic_posts
        WHERE date(created_at) = date('now') AND post_structure = 'chain'
    """)
    count = c.fetchone()[0]
    conn.close()
    return count


def save_article_context(topic: str, summary: str, source_names: str = "", article_links: str = "") -> None:
    """Persists the feed description + provenance for a headline across restarts."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO article_context (topic, summary, source_names, article_links, fetched_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (topic.strip().lower(), summary, source_names, article_links))
    conn.commit()
    conn.close()

def get_article_context(topic: str) -> dict | None:
    """Returns {'summary','source_names','article_links','fetched_at'} or None."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT summary, source_names, article_links, fetched_at FROM article_context WHERE topic = ?",
              (topic.strip().lower(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def update_post_metrics(threads_id: str, views: int, likes: int, replies: int) -> None:
    """Stores latest Threads engagement numbers for a logged post."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE organic_posts
        SET views = ?, likes = ?, replies = ?, metrics_updated_at = CURRENT_TIMESTAMP
        WHERE threads_id = ?
    """, (views, likes, replies, threads_id))
    conn.commit()
    conn.close()

def get_pillar_performance(days: int = 7, min_age_hours: int = 24) -> dict:
    """Average engagement per pillar over the window, from published posts only.

    Rows with all-zero metrics were never fetched (or the fetch failed), so
    including them would drag averages toward 0 and starve pillars unfairly —
    they are excluded. Empty dict when there is no fetched post yet; callers
    must fall back to static weights.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"""
        SELECT pillar,
               COUNT(*) AS posts,
               AVG(views) AS avg_views,
               AVG(likes) AS avg_likes,
               AVG(replies) AS avg_replies
        FROM organic_posts
        WHERE threads_id != ''
          AND metrics_updated_at IS NOT NULL
          AND created_at <= datetime('now', ?)
          AND created_at >= datetime('now', ?)
          AND (views > 0 OR likes > 0 OR replies > 0)
        GROUP BY pillar
    """, (f"-{min_age_hours} hours", f"-{days} days"))
    perf = {
        pillar: {"posts": posts, "avg_views": avg_views, "avg_likes": avg_likes, "avg_replies": avg_replies}
        for pillar, posts, avg_views, avg_likes, avg_replies in c.fetchall()
    }
    conn.close()
    return perf

def get_recent_posts(limit: int = 5) -> list[dict]:
    """Returns recent organic posts for status check."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT id, pillar, topic, content, threads_id, post_structure, created_at
        FROM organic_posts ORDER BY id DESC LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def sync_with_threads_status() -> dict:
    """Check each logged post with Threads API; remove deleted posts from DB."""
    import requests
    from src.publish.threads import ACCESS_TOKEN

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, threads_id, topic FROM organic_posts")
    rows = c.fetchall()

    deleted_ids = []
    alive_ids = []
    for pid, tid, topic in rows:
        if not tid:
            deleted_ids.append(pid)
            continue
        try:
            url = f"https://graph.threads.net/v1.0/{tid}"
            r = requests.get(url, params={"fields": "id", "access_token": ACCESS_TOKEN}, timeout=4)
            if r.status_code == 200:
                alive_ids.append(pid)
            else:
                deleted_ids.append(pid)
        except Exception:
            pass

    if deleted_ids:
        placeholders = ",".join("?" for _ in deleted_ids)
        c.execute(f"DELETE FROM organic_posts WHERE id IN ({placeholders})", deleted_ids)
        conn.commit()
    conn.close()
    return {"alive": len(alive_ids), "deleted": len(deleted_ids)}


if __name__ == "__main__":
    init_organic_db()
    print("Today's count:", get_today_post_count())
    print("Last post time:", get_last_post_time())
