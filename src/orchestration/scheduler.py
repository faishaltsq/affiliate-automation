"""src/orchestration/scheduler.py — durable, pausable, quiet-hours-aware.

V1's daemon hardcoded DAILY_TARGET = 12 and slept 80-110 minutes in a loop
with no durable state, no pause command, and no way to see what was coming.

V2's scheduler:
  * reads slots from the 30-day calendar, not a counter
  * 1 main post/day, optional extras, configurable quiet hours
  * pause/resume persisted in the database, surviving restarts
  * day cap enforced per WIB calendar day
  * every decision is logged with a reason code
  * nothing is published directly — it enqueues, and the publisher runs the job
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from src.core.clock import (
    to_wib, utc_now, wib_date_key, wib_display, wib_hour,
    is_quiet_hour, seconds_until_quiet_end, parse_sqlite_utc,
)
from src.core.config import get_settings
from src.core.storage import connect, connection, transaction
from src.logger import get_logger

log = get_logger("orchestration_scheduler")

PAUSED = "paused"
RUNNING = "running"

_STATE_KEY = "v2_scheduler_state"
_LAST_MAIN_KEY = "v2_last_main_post_date"
_LAST_EXTRA_KEY = "v2_last_extra_post_date"
_EXTRA_COUNT_KEY = "v2_extra_posts_this_week"


@dataclass
class SchedulerDecision:
    should_run: bool
    reason: str
    slot_kind: Optional[str] = None
    pillar: Optional[str] = None
    wait_seconds: float = 0.0
    quiet: bool = False

    def describe(self) -> str:
        return (
            f"{'RUN' if self.should_run else 'SKIP'} "
            f"[{self.slot_kind or '-'}/{self.pillar or '-'}] {self.reason}"
        )


def _get_state(path=None) -> str:
    with connection(path) as conn:
        row = conn.execute(
            "SELECT value FROM scheduler_state WHERE key = ?", (_STATE_KEY,)
        ).fetchone()
    if row is None:
        return RUNNING
    return row["value"] or RUNNING


def set_paused(paused: bool, path=None) -> str:
    """Persist the pause state. Survives restarts and process death."""
    new = PAUSED if paused else RUNNING
    with connection(path) as conn:
        with transaction(conn):
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (_STATE_KEY, new),
            )
    log.info("Scheduler state set to %s", new)
    return new


def is_paused(path=None) -> bool:
    return _get_state(path) == PAUSED


def _kv_get(key: str, path=None) -> Optional[str]:
    with connection(path) as conn:
        row = conn.execute(
            "SELECT value FROM scheduler_state WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def _kv_set(key: str, value: str, path=None) -> None:
    with connection(path) as conn:
        with transaction(conn):
            conn.execute(
                "INSERT OR REPLACE INTO scheduler_state (key, value, updated_at) "
                "VALUES (?, ?, CURRENT_TIMESTAMP)",
                (key, value),
            )


def posts_today(path=None) -> int:
    """How many V2 posts already went out today (WIB calendar day).

    `content_drafts.created_at` is stored in UTC by contract, but the day
    boundary we care about is the WIB one — a post drafted at 20:00 WIB is
    13:00 UTC and belongs to the same WIB day. Comparing the raw UTC date
    against the WIB date would mis-count anything drafted after 17:00 WIB.
    """
    from src.orchestration.publisher import PUBLISHED

    today_wib = wib_date_key()
    with connection(path) as conn:
        rows = conn.execute(
            """
            SELECT cd.created_at AS created_at
            FROM content_drafts cd
            JOIN publication_jobs pj ON pj.draft_id = cd.id
            WHERE pj.state = ?
            """,
            (PUBLISHED,),
        ).fetchall()

    count = 0
    for r in rows:
        dt = parse_sqlite_utc(r["created_at"])
        if dt is None:
            continue
        if to_wib(dt).strftime("%Y-%m-%d") == today_wib:
            count += 1
    return count


def extras_this_week(path=None) -> int:
    local = to_wib(utc_now())
    monday = (local - timedelta(days=local.weekday())).strftime("%Y-%m-%d")
    stored = _kv_get(_EXTRA_COUNT_KEY, path=path)
    if stored is None:
        return 0
    try:
        value, week = stored.split("|", 1)
    except ValueError:
        return 0
    return int(value) if week == monday else 0


def _record_extra(path=None) -> None:
    local = to_wib(utc_now())
    monday = (local - timedelta(days=local.weekday())).strftime("%Y-%m-%d")
    current = extras_this_week(path=path)
    _kv_set(_EXTRA_COUNT_KEY, f"{current + 1}|{monday}", path=path)


def should_post_now(
    now: Optional[datetime] = None,
    path=None,
    extra_due: bool = False,
) -> SchedulerDecision:
    """The single scheduling decision. Pure given its inputs, so it is testable."""
    cfg = get_settings().scheduler
    local = to_wib(now or utc_now())
    hour = local.hour
    today = local.strftime("%Y-%m-%d")

    if is_paused(path=path):
        return SchedulerDecision(False, "scheduler paused (/resume to continue)")

    if is_quiet_hour(hour, cfg.quiet_start_hour, cfg.quiet_end_hour):
        wait = seconds_until_quiet_end(cfg.quiet_end_hour, now or utc_now())
        return SchedulerDecision(
            False, f"quiet hours ({cfg.quiet_start_hour}:00-{cfg.quiet_end_hour}:00 WIB)",
            wait_seconds=wait, quiet=True,
        )

    today_count = posts_today(path=path)

    # Main slot: at most one per day.
    if today_count < cfg.main_posts_per_day:
        from src.strategy.calendar import get_slot

        slot = get_slot(today, "main", path=path)
        if slot is None:
            # The calendar may legitimately start tomorrow (it is built after
            # the evening cut-off). Rather than silently skipping the day, use
            # the earliest upcoming main slot so the account keeps its rhythm.
            from src.strategy.calendar import upcoming_slots

            upcoming = [
                s for s in upcoming_slots(days=7, path=path)
                if s.slot_kind == "main"
            ]
            if not upcoming:
                return SchedulerDecision(
                    False, "no calendar slot available in the next 7 days"
                )
            slot = upcoming[0]
        return SchedulerDecision(
            True, f"main slot for {slot.slot_date}",
            slot_kind="main", pillar=slot.pillar,
        )

    # Extra slots: capped per week, only when explicitly due.
    if extra_due:
        if extras_this_week(path=path) >= cfg.max_extra_posts_per_week:
            return SchedulerDecision(
                False,
                f"weekly extra cap reached "
                f"({cfg.max_extra_posts_per_week}/week)",
            )
        from src.strategy.calendar import get_slot

        slot = get_slot(today, "extra", path=path)
        if slot is None:
            from src.strategy.calendar import upcoming_slots

            upcoming = [
                s for s in upcoming_slots(days=7, path=path)
                if s.slot_kind == "extra"
            ]
            if not upcoming:
                return SchedulerDecision(False, "no extra slot available")
            slot = upcoming[0]
        return SchedulerDecision(
            True, "extra slot", slot_kind="extra", pillar=slot.pillar,
        )

    return SchedulerDecision(
        False, f"daily main post already published ({today_count}/{cfg.main_posts_per_day})",
    )


def record_post(slot_kind: str, path=None) -> None:
    """Called after a successful publish so the caps stay accurate."""
    today = wib_date_key()
    if slot_kind == "main":
        _kv_set(_LAST_MAIN_KEY, today, path=path)
    else:
        _kv_set(_LAST_EXTRA_KEY, today, path=path)
        _record_extra(path=path)


def next_run_delay(now: Optional[datetime] = None, path=None) -> float:
    """Seconds until the scheduler should look again."""
    cfg = get_settings().scheduler
    local = to_wib(now or utc_now())
    if is_paused(path=path):
        return float(cfg.poll_interval_seconds)
    if is_quiet_hour(local.hour, cfg.quiet_start_hour, cfg.quiet_end_hour):
        return seconds_until_quiet_end(cfg.quiet_end_hour, now or utc_now())
    decision = should_post_now(now=now, path=path)
    if decision.should_run:
        return 0.0
    # Nothing due: check again in a while, but never less than a minute.
    return max(60.0, min(float(cfg.poll_interval_seconds), 3600.0))


def status(path=None) -> dict:
    cfg = get_settings().scheduler
    local = to_wib(utc_now())
    from src.orchestration.publisher import queue_depth, oldest_pending_age_seconds

    return {
        "state": _get_state(path=path),
        "paused": is_paused(path=path),
        "wib_now": wib_display(),
        "quiet_hours": f"{cfg.quiet_start_hour}:00-{cfg.quiet_end_hour}:00 WIB",
        "in_quiet_hours": is_quiet_hour(
            local.hour, cfg.quiet_start_hour, cfg.quiet_end_hour
        ),
        "main_posts_per_day": cfg.main_posts_per_day,
        "max_extra_posts_per_week": cfg.max_extra_posts_per_week,
        "posts_today": posts_today(path=path),
        "extras_this_week": extras_this_week(path=path),
        "last_main_post_date": _kv_get(_LAST_MAIN_KEY, path=path),
        "queue": queue_depth(path=path),
        "oldest_pending_age_seconds": oldest_pending_age_seconds(path=path),
        "next_check_seconds": next_run_delay(path=path),
    }


def run_once(
    writer_fn: Callable[[str, Optional[str]], object],
    enqueue_fn: Callable[[int, str], object],
    path=None,
    dry_run: bool = True,
) -> dict:
    """One scheduling pass. Returns what it decided and why.

    `writer_fn(pillar, slot_kind)` produces a draft (or None).
    `enqueue_fn(draft_id, content)` puts it on the publication queue.
    Both are injected so the scheduler itself never touches the LLM or the
    Threads API — and so a dry run has no side effects at all.
    """
    decision = should_post_now(path=path)
    result = {"decision": decision.describe(), "ran": decision.should_run}

    if not decision.should_run:
        result["reason"] = decision.reason
        return result

    if dry_run:
        result["reason"] = "dry_run — draft not generated, nothing enqueued"
        result["dry_run"] = True
        return result

    draft = writer_fn(decision.pillar, decision.slot_kind)
    if draft is None:
        result["ran"] = False
        result["reason"] = "no acceptable draft produced; slot skipped safely"
        return result

    job = enqueue_fn(draft.id, draft.content)
    record_post(decision.slot_kind, path=path)
    result["reason"] = f"draft {draft.id} enqueued as job {job[0]}"
    result["draft_id"] = getattr(draft, "id", None)
    result["job_id"] = job[0]
    return result
