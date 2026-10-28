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
from typing import Optional

from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("editorial_memory")

# Feedback categories. Anything else is stored as 'other' so nothing is lost.
CATEGORIES = (
    "voice_off", "too_long", "too_short", "no_substance", "unsourced_claim",
    "wrong_pillar", "repetitive", "clickbait", "engagement_bait",
    "off_brand", "other",
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
            cur = conn.execute(
                """
                INSERT INTO feedback_events (draft_id, actor_id, category, reason)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, str(actor_id), category, reason),
            )
            return int(cur.lastrowid)


def recent_feedback(limit: int = DEFAULT_WINDOW, path=None) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM feedback_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def feedback_for_draft(draft_id: int, path=None) -> list[dict]:
    with connect(path) as conn:
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
    "other": "Perhatikan penolakan terakhir operator.",
}


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
        row = conn.execute("SELECT COUNT(*) AS n FROM feedback_events").fetchone()
    return int(row["n"]) > 0
