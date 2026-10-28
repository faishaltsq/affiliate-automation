"""src/core/clock.py — UTC storage, WIB presentation.

Every timestamp written to SQLite is UTC. Every timestamp shown to a human is
WIB (UTC+7). Mixing the two is how a scheduler ends up posting at 03:00 local.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def wib_now() -> datetime:
    return datetime.now(WIB)


def to_wib(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(WIB)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive datetimes coming out of SQLite are UTC by contract.
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_iso(dt: datetime | None = None) -> str:
    """Storage format: '2026-10-28T11:37:18+00:00'."""
    return (dt or utc_now()).astimezone(UTC).isoformat()


def sqlite_utc(dt: datetime | None = None) -> str:
    """Storage format matching SQLite CURRENT_TIMESTAMP: '2026-10-28 11:37:18'."""
    return (dt or utc_now()).astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def parse_sqlite_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return to_utc(dt)


def wib_display(dt: datetime | None = None, with_seconds: bool = False) -> str:
    """Human format: 'Selasa, 28 Okt 2026 18:37'."""
    local = to_wib(dt or utc_now())
    days = ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu")
    months = (
        "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
        "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
    )
    base = (
        f"{days[local.weekday()]}, {local.day:02d} {months[local.month - 1]} "
        f"{local.year} {local.hour:02d}:{local.minute:02d}"
    )
    if with_seconds:
        base += f":{local.second:02d}"
    return f"{base} WIB"


def wib_date_key(dt: datetime | None = None) -> str:
    """Calendar day in WIB — the scheduler's unit of accounting."""
    return to_wib(dt or utc_now()).strftime("%Y-%m-%d")


def wib_hour(dt: datetime | None = None) -> int:
    return to_wib(dt or utc_now()).hour


def is_quiet_hour(
    hour: int, quiet_start: int, quiet_end: int
) -> bool:
    """True when `hour` falls inside the quiet window.

    Handles windows that wrap midnight (23 → 6) as well as same-day windows.
    """
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= hour < quiet_end
    return hour >= quiet_start or hour < quiet_end


def seconds_until_quiet_end(quiet_end_hour: int, now: datetime | None = None) -> float:
    local = to_wib(now or utc_now())
    wake = local.replace(hour=quiet_end_hour, minute=0, second=0, microsecond=0)
    if wake <= local:
        wake += timedelta(days=1)
    return (wake - local).total_seconds()
