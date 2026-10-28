"""src/strategy/calendar.py — the 30-day rolling content calendar.

V1 had a planner that reached for breaking news and weighted football heavily
in several time slots. V2 has a calendar of SLOTS, not of post bodies: each
slot declares a pillar, an audience problem, an angle, a format, and whether
commercial content is allowed. The actual copy is generated later, from the
slot.

Pillar distribution over a 30-day window (master plan §3):
  relatable       10
  practical_tips   8
  opinions         6
  discoveries      3
  affiliate        3   (conditional — downgrades to organic when unverified)

Slot kinds:
  main     — the one principal post of the day
  extra    — optional 2-3 per week, only after the main slot is healthy
"""
from __future__ import annotations

import random
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

from src.core.clock import to_wib, wib_date_key, utc_now, WIB
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("strategy_calendar")

PILLARS = ("relatable", "practical_tips", "opinions", "discoveries", "affiliate")

# Target counts per 30-day window.
PILLAR_TARGETS_30 = {
    "relatable": 10,
    "practical_tips": 8,
    "opinions": 6,
    "discoveries": 3,
    "affiliate": 3,
}

# Extra posts per week, spread across the week rather than clustered.
EXTRAS_PER_WEEK = 3
EXTRA_WEEKDAYS = (1, 3, 5)  # Tue, Thu, Sat (Mon=0)

# Slots that may carry a commercial recommendation.
COMMERCIAL_PILLARS = {"affiliate", "discoveries"}


@dataclass
class CalendarSlot:
    slot_date: str          # YYYY-MM-DD in WIB
    pillar: str
    slot_kind: str          # main | extra
    audience_problem: str = ""
    target_angle: str = ""
    format: str = "text"
    source_constraints: str = ""
    commercial_allowed: bool = False
    id: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _ensure_table(conn) -> None:
    conn.execute("""
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
    """)


def _pillar_sequence(days: int, rng: random.Random) -> list[str]:
    """Distribute pillars across `days` main slots, honouring the 30-day mix.

    For a 30-day window this reproduces the target counts exactly. For other
    lengths it scales proportionally and then interleaves so no pillar clusters
    into a run of consecutive days.
    """
    scale = days / 30.0
    counts = {
        pillar: max(1, round(PILLAR_TARGETS_30[pillar] * scale))
        for pillar in PILLARS
    }
    # Trim or pad to exactly `days`.
    total = sum(counts.values())
    while total > days:
        # Take from the pillar with the largest surplus over its share.
        pillar = max(counts, key=lambda p: counts[p] - PILLAR_TARGETS_30[p] * scale)
        if counts[pillar] <= 1:
            pillar = max(counts, key=counts.get)
        counts[pillar] -= 1
        total -= 1
    while total < days:
        pillar = min(counts, key=lambda p: counts[p] / max(1, PILLAR_TARGETS_30[p]))
        counts[pillar] += 1
        total += 1

    pool: list[str] = []
    for pillar, n in counts.items():
        pool.extend([pillar] * n)

    # Interleave: repeatedly take from the pillar with the most remaining,
    # skipping one that was just placed, to avoid runs.
    rng.shuffle(pool)
    pool.sort(key=lambda p: -counts[p])
    ordered: list[str] = []
    buckets = {p: [p] * counts[p] for p in PILLARS}
    last = None
    while any(buckets.values()):
        candidates = [p for p in PILLARS if buckets[p] and p != last]
        if not candidates:
            candidates = [p for p in PILLARS if buckets[p]]
        pick = max(candidates, key=lambda p: len(buckets[p]))
        ordered.append(pick)
        buckets[pick].pop()
        last = pick
    return ordered[:days]


def build_calendar(
    days: int = 30,
    start: Optional[datetime] = None,
    rng: Optional[random.Random] = None,
    path=None,
) -> list[CalendarSlot]:
    """Create (or reuse) the rolling calendar. Idempotent per (date, kind)."""
    r = rng or random.Random()
    base = to_wib(start or utc_now())
    if base.hour >= 20:
        # Late evening: plan from tomorrow so today's slot isn't already gone.
        base = base + timedelta(days=1)
    start_date = base.replace(hour=0, minute=0, second=0, microsecond=0)

    pillars = _pillar_sequence(days, r)
    slots: list[CalendarSlot] = []

    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            for offset, pillar in enumerate(pillars):
                day = start_date + timedelta(days=offset)
                date_key = day.strftime("%Y-%m-%d")
                commercial = pillar in COMMERCIAL_PILLARS
                row = conn.execute(
                    "SELECT * FROM calendar_slots WHERE slot_date = ? AND slot_kind = 'main'",
                    (date_key,),
                ).fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO calendar_slots "
                        "(slot_date, pillar, slot_kind, commercial_allowed) "
                        "VALUES (?, ?, 'main', ?)",
                        (date_key, pillar, 1 if commercial else 0),
                    )
                    sid = int(cur.lastrowid)
                else:
                    sid = int(row["id"])
                slots.append(CalendarSlot(
                    slot_date=date_key, pillar=pillar, slot_kind="main",
                    commercial_allowed=commercial, id=sid,
                ))

            # Optional extras: 3 per week on fixed weekdays.
            week = 0
            while week * 7 < days:
                for weekday in EXTRA_WEEKDAYS:
                    offset = week * 7 + weekday
                    if offset >= days:
                        continue
                    day = start_date + timedelta(days=offset)
                    date_key = day.strftime("%Y-%m-%d")
                    existing = conn.execute(
                        "SELECT id FROM calendar_slots "
                        "WHERE slot_date = ? AND slot_kind = 'extra'",
                        (date_key,),
                    ).fetchone()
                    if existing:
                        continue
                    # Extras favour the light pillars; affiliate extras are
                    # never created automatically.
                    pillar = r.choices(
                        ["relatable", "practical_tips", "opinions", "discoveries"],
                        weights=[4, 4, 2, 1],
                    )[0]
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO calendar_slots "
                        "(slot_date, pillar, slot_kind, commercial_allowed) "
                        "VALUES (?, ?, 'extra', ?)",
                        (date_key, pillar,
                         1 if pillar in COMMERCIAL_PILLARS else 0),
                    )
                    if cur.lastrowid:
                        slots.append(CalendarSlot(
                            slot_date=date_key, pillar=pillar, slot_kind="extra",
                            commercial_allowed=pillar in COMMERCIAL_PILLARS,
                            id=int(cur.lastrowid),
                        ))
                week += 1

    slots.sort(key=lambda s: (s.slot_date, 0 if s.slot_kind == "main" else 1))
    return slots


def get_slot(date_key: str, slot_kind: str = "main", path=None) -> Optional[CalendarSlot]:
    with connect(path) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM calendar_slots WHERE slot_date = ? AND slot_kind = ?",
            (date_key, slot_kind),
        ).fetchone()
    if row is None:
        return None
    return CalendarSlot(
        slot_date=row["slot_date"], pillar=row["pillar"],
        slot_kind=row["slot_kind"],
        audience_problem=row["audience_problem"],
        target_angle=row["target_angle"], format=row["format"],
        source_constraints=row["source_constraints"],
        commercial_allowed=bool(row["commercial_allowed"]), id=row["id"],
    )


def upcoming_slots(days: int = 7, path=None) -> list[CalendarSlot]:
    today = wib_date_key()
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM calendar_slots WHERE slot_date >= ? "
            "ORDER BY slot_date ASC, slot_kind ASC LIMIT ?",
            (today, days * 2),
        ).fetchall()
    return [
        CalendarSlot(
            slot_date=r["slot_date"], pillar=r["pillar"], slot_kind=r["slot_kind"],
            audience_problem=r["audience_problem"], target_angle=r["target_angle"],
            format=r["format"], source_constraints=r["source_constraints"],
            commercial_allowed=bool(r["commercial_allowed"]), id=r["id"],
        )
        for r in rows
    ]


def assign_problem(slot_id: int, problem: str, angle: str = "", path=None) -> None:
    """Attach the chosen audience problem + angle to a slot."""
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE calendar_slots SET audience_problem = ?, target_angle = ? "
                "WHERE id = ?",
                (problem, angle, slot_id),
            )


def pillar_distribution(days: int = 30, path=None) -> dict[str, int]:
    today = wib_date_key()
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT pillar, COUNT(*) AS n FROM calendar_slots "
            "WHERE slot_kind = 'main' AND slot_date >= ? "
            "GROUP BY pillar",
            (today,),
        ).fetchall()
    return {r["pillar"]: r["n"] for r in rows}


def downgrade_to_organic(slot_id: int, reason: str, path=None) -> None:
    """An affiliate slot with no verified product becomes organic content."""
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE calendar_slots SET pillar = 'practical_tips', "
                "commercial_allowed = 0, source_constraints = ? WHERE id = ?",
                (f"downgraded_from_affiliate: {reason}", slot_id),
            )
    log.info("Calendar slot %s downgraded to organic: %s", slot_id, reason)
