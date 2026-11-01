"""src/persona/editorial_memory.py — bounded structured feedback retrieval.

V1 had no feedback memory at all: an operator rejection was a Telegram message
that scrolled away, and the next draft was generated from the same prompt.

V2 persists every rejection and revision reason, then feeds the relevant ones
back into the next selection context. The memory is bounded (a rolling window,
not an ever-growing table) and structured (category + reason), so the writer
receives "the last 5 things the operator rejected and why" rather than a dump.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from typing import Optional

from src.core.storage import connect, table_exists, transaction
from src.logger import get_logger

log = get_logger("editorial_memory")

# Feedback categories. Anything else is stored as 'other' so nothing is lost.
CATEGORIES = (
    "voice_off", "too_long", "too_short", "no_substance", "unsourced_claim",
    "wrong_pillar", "repetitive", "clickbait", "engagement_bait",
    "off_brand", "forced_metaphor", "generic_ai_voice",
    "artificial_thread_padding", "unsafe_practical_advice", "redundant_parts",
    "other",
)
# How many recent events feed the next selection context.
DEFAULT_WINDOW = 12


def record_feedback(
    draft_id: Optional[int],
    actor_id: str,
    category: str,
    reason: str = "",
    path=None,
) -> int:
    """Persist one operator feedback event. History is never erased."""
    if category not in CATEGORIES:
        category = "other"
    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            cur = conn.execute(
                """
                INSERT INTO feedback_events (draft_id, actor_id, category, reason)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, str(actor_id), category, reason),
            )
            return int(cur.lastrowid)


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id INTEGER,
            actor_id TEXT NOT NULL,
            category TEXT NOT NULL,
            reason TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def recent_feedback(limit: int = DEFAULT_WINDOW, path=None) -> list[dict]:
    with connect(path) as conn:
        if not table_exists(conn, "feedback_events"):
            return []
        rows = conn.execute(
            "SELECT * FROM feedback_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def feedback_for_draft(draft_id: int, path=None) -> list[dict]:
    with connect(path) as conn:
        if not table_exists(conn, "feedback_events"):
            return []
        rows = conn.execute(
            "SELECT * FROM feedback_events WHERE draft_id = ? ORDER BY id ASC",
            (draft_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def category_counts(limit: int = 100, path=None) -> dict[str, int]:
    events = recent_feedback(limit=limit, path=path)
    return dict(Counter(e["category"] for e in events))


def revision_guidance(limit: int = DEFAULT_WINDOW, path=None) -> str:
    """The block injected into the next writer prompt.

    Bounded, deduplicated, and phrased as instructions rather than as a log
    dump. Empty when there is no feedback yet, so a fresh install is not
    handed a paragraph about nothing.
    """
    events = recent_feedback(limit=limit, path=path)
    if not events:
        return ""

    counts = Counter(e["category"] for e in events)
    reasons = [e["reason"].strip() for e in events if e["reason"].strip()]

    lines = ["UMPAN BALIK OPERATOR TERAKHIR (wajib diperhatikan):"]
    for category, n in counts.most_common():
        guidance = _CATEGORY_GUIDANCE.get(category)
        if guidance:
            lines.append(f"- {guidance} ({n}x)")
        else:
            lines.append(f"- hindari pola yang sebelumnya ditolak: {category} ({n}x)")

    if reasons:
        lines.append("Alasan spesifik yang pernah diberikan:")
        for reason in reasons[:5]:
            lines.append(f"  • {reason[:160]}")

    return "\n".join(lines)


_CATEGORY_GUIDANCE = {
    "voice_off": "Nada harus tetap Teman Tongkrongan: santai, hangat, observasional.",
    "too_long": "Buat lebih pendek — satu ide, kalimat padat.",
    "too_short": "Tambahkan satu detail konkret yang berguna.",
    "no_substance": "Harus ada isi yang bisa dicek atau dipakai pembaca.",
    "unsourced_claim": "Jangan pakai angka, harga, atau spesifikasi tanpa bukti.",
    "wrong_pillar": "Tetap pada pilar yang dijadwalkan.",
    "repetitive": "Ganti pembuka dan struktur; jangan ulang template yang sama.",
    "clickbait": "Hapus hook misteri dan janji palsu.",
    "engagement_bait": "Jangan tutup dengan pertanyaan klise.",
    "off_brand": "Jangan promosi berlebihan; jaga janji akun.",
    "forced_metaphor": "DILARANG personifikasi benda mati ('nakal', 'ngambek', 'lelah'). Benda mati tidak punya niat.",
    "generic_ai_voice": "Hindari klise AI ('niatnya... kenyataannya...', 'di era serba digital', 'saksi bisu').",
    "artificial_thread_padding": "Gunakan single post, jangan paksakan observasi sederhana menjadi thread.",
    "unsafe_practical_advice": "DILARANG saran berbahaya; kabel terbuka harus diganti/diisolasi, bukan dibiarkan.",
    "redundant_parts": "Tiap bagian thread harus memiliki poin/langkah baru yang berbeda.",
    "other": "Perhatikan penolakan terakhir operator.",
}


def get_recent_editorial_metadata(limit: int = 5, path=None) -> list[dict]:
    """Retrieve recent drafts' editorial metadata (opening, format, pillar, premise, payoff)."""
    with connect(path) as conn:
        if not table_exists(conn, "content_drafts"):
            return []
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(content_drafts)").fetchall()]
        has_meta = "editorial_metadata_json" in cols
        sql = (
            "SELECT id, editorial_metadata_json, content, parts_json, status, created_at "
            "FROM content_drafts ORDER BY id DESC LIMIT ?"
            if has_meta else
            "SELECT id, content, parts_json, status, created_at "
            "FROM content_drafts ORDER BY id DESC LIMIT ?"
        )
        rows = conn.execute(sql, (limit,)).fetchall()

    results = []
    for r in rows:
        meta_raw = r["editorial_metadata_json"] if has_meta and r["editorial_metadata_json"] else "{}"
        try:
            meta = json.loads(meta_raw)
        except Exception:
            meta = {}
        meta["draft_id"] = r["id"]
        meta["content"] = r["content"]
        results.append(meta)
    return results


def record_draft_editorial_metadata(draft_id: int, metadata: dict, path=None) -> None:
    """Store editorial metadata on a draft."""
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_drafts SET editorial_metadata_json = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), draft_id),
            )


def can_use_opening(opening: str, limit: int = 2, path=None) -> bool:
    """Check if the given opening style violates consecutive opening restrictions.

    Rule: Never allow the same opening style more than 2 times consecutively.
    """
    recent = get_recent_editorial_metadata(limit=limit, path=path)
    if len(recent) < limit:
        return True
    openings = [m.get("opening") for m in recent if m.get("opening")]
    if len(openings) >= limit and all(o == opening for o in openings[:limit]):
        return False
    return True


def recent_categories_5(path=None) -> list[str]:
    """Return the pillars/categories of the last 5 drafts."""
    recent = get_recent_editorial_metadata(limit=5, path=path)
    return [m.get("pillar") for m in recent if m.get("pillar")]


def can_use_category_in_window(category: str, max_in_window: int = 2, path=None) -> bool:
    """Check category rotation within the last 5 posts.

    Ensures no single pillar dominates the recent 5-post window (max 2 per 5 posts).
    """
    recent_cats = recent_categories_5(path=path)
    return recent_cats.count(category) < max_in_window

def prune(keep: int = 200, path=None) -> int:
    """Trim the oldest events beyond the rolling window. Never touches recent."""
    with connect(path) as conn:
        with transaction(conn):
            row = conn.execute(
                "SELECT MIN(id) AS cutoff FROM ("
                "  SELECT id FROM feedback_events ORDER BY id DESC LIMIT ?"
                ")", (keep,),
            ).fetchone()
            if not row or row["cutoff"] is None:
                return 0
            cur = conn.execute(
                "DELETE FROM feedback_events WHERE id < ?", (row["cutoff"],)
            )
            return int(cur.rowcount)


def has_feedback(path=None) -> bool:
    with connect(path) as conn:
        if not table_exists(conn, "feedback_events"):
            return False
        row = conn.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()
    return int(row["n"]) > 0
