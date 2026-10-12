"""
Affiliate Queue Manager
Stores pending affiliate links + their target context.
Checked by daemon every cycle; fires when threshold reached.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from src.config import DATA_DIR
from src.logger import get_logger

log = get_logger("aff_queue")

DB_PATH = DATA_DIR / "posts.db"


def init_affiliate_queue():
    """Create affiliate_queue table if not exists."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
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
    """)
    conn.commit()
    conn.close()
    log.info("Affiliate queue table verified.")


def enqueue(affiliate_url: str, target_thread_url: str = "", product_name: str = "") -> int:
    """Add an affiliate link to the queue. Returns row ID."""
    init_affiliate_queue()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO affiliate_queue (affiliate_url, target_thread_url, product_name) VALUES (?, ?, ?)",
        (affiliate_url, target_thread_url, product_name),
    )
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    log.info(f"Queued affiliate link #{row_id}: {affiliate_url[:60]}")
    return row_id


def get_pending() -> list[dict]:
    """Get all pending affiliate tasks."""
    init_affiliate_queue()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM affiliate_queue WHERE status = 'pending' ORDER BY created_at")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_fired(row_id: int, post_id: str, reply_id: str):
    """Mark a queue entry as fired."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE affiliate_queue SET status='fired', fired_at=?, fired_post_id=?, fired_reply_id=? WHERE id=?",
        (datetime.utcnow().isoformat(), post_id, reply_id, row_id),
    )
    conn.commit()
    conn.close()
    log.info(f"Queue #{row_id} marked as fired.")


def mark_direct(row_id: int, reply_id: str):
    """Mark a direct (specific URL) queue entry as done."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE affiliate_queue SET status='fired', fired_at=?, fired_reply_id=? WHERE id=?",
        (datetime.utcnow().isoformat(), reply_id, row_id),
    )
    conn.commit()
    conn.close()
