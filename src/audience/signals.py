"""src/audience/signals.py — structured audience pain points with provenance.

The news-first system asked "what happened today?". The persona-first system
asks "what does this audience actually struggle with?". Signals are the second
question, stored with a source URL and a confidence so a writer can never
present a guess as a fact.

Seed signals ship with the code so the system produces useful content with
every trend/news source disabled — that is acceptance criterion #2.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("audience_signals")

# Categories map onto the V2 content pillars.
CATEGORIES = [
    "relatable",
    "practical_tips",
    "opinions",
    "discoveries",
    "affiliate",
]


@dataclass
class AudienceSignal:
    category: str
    problem: str
    source_url: str = ""
    confidence: float = 0.6
    status: str = "active"
    verified_at: Optional[str] = None
    id: Optional[int] = None


# ── Seed catalog ────────────────────────────────────────────────────────────
# These are observational, widely-recognised everyday problems. They carry no
# statistics, no prices, and no product claims, so they need no external
# verification to be written about honestly.
SEED_SIGNALS: list[AudienceSignal] = [
    AudienceSignal("relatable", "Kabel charger yang selalu kusut di tas dan putus di ujung",
                   confidence=0.9),
    AudienceSignal("relatable", "Meja kerja penuh kabel sampai mouse nggak muat gerak",
                   confidence=0.85),
    AudienceSignal("relatable", "Niat bangun pagi buat rapikan kamar, akhirnya scroll HP di kasur",
                   confidence=0.8),
    AudienceSignal("relatable", "Beli gadget baru karena diskon, dipakai dua minggu lalu disimpan",
                   confidence=0.85),
    AudienceSignal("relatable", "Tab browser kebuka puluhan, laptop makin lempar",
                   confidence=0.75),
    AudienceSignal("relatable", "Power bank dibawa 'buat jaga-jaga' tapi jarganya sendiri yang habis",
                   confidence=0.8),
    AudienceSignal("relatable", "Niat cari produk murah, malah habis dua jam bandingin spesifikasi",
                   confidence=0.85),
    AudienceSignal("relatable", "Notifikasi grup bikin fokus buyar tiap lima menit",
                   confidence=0.8),
    AudienceSignal("relatable", "Kulkas penuh tapi akhirnya pesan antar",
                   confidence=0.7),
    AudienceSignal("relatable", "File penting cuma ada di HP yang memorinya selalu penuh",
                   confidence=0.75),

    AudienceSignal("practical_tips", "Nggak tahu cara cek apakah charger benar-benar support fast charging",
                   confidence=0.9),
    AudienceSignal("practical_tips", "Bingung pilih power bank: capacity nyata vs angka di kemasan",
                   confidence=0.9),
    AudienceSignal("practical_tips", "Kabel USB-C murah sering nggak bisa transfer data, cuma charging",
                   confidence=0.85),
    AudienceSignal("practical_tips", "Nggak sadar aplikasi background yang bikin baterai boros",
                   confidence=0.8),
    AudienceSignal("practical_tips", "Susah bedain mana harga wajar dan mana harga yang kena markup",
                   confidence=0.85),
    AudienceSignal("practical_tips", "Nggak tahu cara backup otomatis sebelum HP rusak",
                   confidence=0.9),
    AudienceSignal("practical_tips", "Nggak paham bedanya adaptor 30W, 65W, dan 100W untuk kebutuhan sendiri",
                   confidence=0.85),
    AudienceSignal("practical_tips", "Bingung cara cek garansi resmi sebelum beli gadget bekas",
                   confidence=0.85),

    AudienceSignal("opinions", "Spesifikasi tinggi di atas kertas sering nggak terasa di pemakaian harian",
                   confidence=0.85),
    AudienceSignal("opinions", "Diskon besar kadang cuma mengembalikan harga normal",
                   confidence=0.9),
    AudienceSignal("opinions", "Review yang terlalu rapi dan positif itu tanda bahaya, bukan jaminan",
                   confidence=0.8),
    AudienceSignal("opinions", "Nggak semua orang butuh flagship; banyak yang butuh baterai awet",
                   confidence=0.85),
    AudienceSignal("opinions", "Iklan 'harga spesial 3 hari' lebih sering trik tekanan waktu",
                   confidence=0.8),
    AudienceSignal("opinions", "Gadget murah yang awet sering lebih masuk akal daripada mahal yang sekali drop rusak",
                   confidence=0.8),

    AudienceSignal("discoveries", "Kategori kecil yang sering terlupakan: organizer kabel, stand HP, lampu meja",
                   confidence=0.75),
    AudienceSignal("discoveries", "Fitur HP yang jarang dipakai padahal berguna: mode fokus, batas pengisian",
                   confidence=0.8),
    AudienceSignal("discoveries", "Aksesori yang bikin setup rapi tanpa beli meja baru",
                   confidence=0.8),

    AudienceSignal("affiliate", "Butuh rekomendasi power bank yang benar-benar sesuai kebutuhan harian",
                   confidence=0.7),
    AudienceSignal("affiliate", "Butuh panduan memilih kabel yang awet, bukan cuma murah",
                   confidence=0.7),
    AudienceSignal("affiliate", "Butuh saran adaptor untuk laptop dan HP sekaligus",
                   confidence=0.7),
]


def _ensure_table(conn) -> None:
    conn.execute("""
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
    """)


def seed_if_empty(path=None) -> int:
    """Insert the seed catalog on a fresh database. Returns rows added."""
    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            existing = conn.execute(
                "SELECT COUNT(*) AS n FROM audience_signals"
            ).fetchone()["n"]
            if existing:
                return 0
            for s in SEED_SIGNALS:
                conn.execute(
                    "INSERT INTO audience_signals "
                    "(category, problem, source_url, confidence, status) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (s.category, s.problem, s.source_url, s.confidence, s.status),
                )
            return len(SEED_SIGNALS)


def add_signal(signal: AudienceSignal, path=None) -> int:
    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            cur = conn.execute(
                "INSERT INTO audience_signals "
                "(category, problem, source_url, confidence, status, verified_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (signal.category, signal.problem, signal.source_url,
                 signal.confidence, signal.status, signal.verified_at),
            )
            return int(cur.lastrowid)


def get_signals(
    category: Optional[str] = None,
    status: str = "active",
    limit: int = 100,
    path=None,
) -> list[AudienceSignal]:
    sql = "SELECT * FROM audience_signals WHERE status = ?"
    params: list = [status]
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY confidence DESC, id ASC LIMIT ?"
    params.append(limit)
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [
        AudienceSignal(
            category=r["category"], problem=r["problem"],
            source_url=r["source_url"], confidence=r["confidence"],
            status=r["status"], verified_at=r["verified_at"], id=r["id"],
        )
        for r in rows
    ]


def count_by_category(path=None) -> dict[str, int]:
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM audience_signals "
            "WHERE status = 'active' GROUP BY category"
        ).fetchall()
        return {r["category"]: r["n"] for r in rows}


def retire_signal(signal_id: int, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE audience_signals SET status = 'retired' WHERE id = ?",
                (signal_id,),
            )


def export_seed_json(path=None) -> str:
    return json.dumps(
        [s.__dict__ for s in SEED_SIGNALS], indent=2, ensure_ascii=False
    )
