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
    """How many V2 posts today already claimed a slot (WIB calendar day).

    Counts QUEUED and PUBLISHED. Counting only PUBLISHED would let shadow mode
    bypass the day cap entirely — nothing is ever published there, so the
    counter would stay at zero and the loop would enqueue without bound.

    `content_drafts.created_at` is stored in UTC by contract, but the day
    boundary we care about is the WIB one — a post drafted at 20:00 WIB is
    13:00 UTC and belongs to the same WIB day. Comparing the raw UTC date
    against the WIB date would mis-count anything drafted after 17:00 WIB.
    """
    from src.orchestration.publisher import PUBLISHED, QUEUED

    today_wib = wib_date_key()
    with connection(path) as conn:
        rows = conn.execute(
            """
            SELECT cd.created_at AS created_at
            FROM content_drafts cd
            JOIN publication_jobs pj ON pj.draft_id = cd.id
            WHERE pj.state IN (?, ?)
            """,
            (PUBLISHED, QUEUED),
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
    Threads API.

    `dry_run` controls PUBLISHING, not generation. Shadow mode
    (`dry_run=True`) still writes drafts and fills the queue - that is
    the whole point: an operator reviews what *would* have gone out.
    Only the publish step is suppressed. Skipping generation in dry-run
    mode left the queue permanently empty and made shadow mode useless.
    """
    decision = should_post_now(path=path)
    result = {"decision": decision.describe(), "ran": decision.should_run,
              "dry_run": dry_run}

    if not decision.should_run:
        result["reason"] = decision.reason
        return result


    draft = writer_fn(decision.pillar, decision.slot_kind)
    if draft is None:
        result["ran"] = False
        result["reason"] = "no acceptable draft produced; slot skipped safely"
        return result

    draft_id = getattr(draft, "draft_id", None) or getattr(draft, "id", None)
    job = enqueue_fn(draft_id, draft.content)
    record_post(decision.slot_kind, path=path)
    result["reason"] = (
        f"draft {draft_id} enqueued as job {job[0]}"
        + (" (shadow - not published)" if dry_run else "")
    )
    result["draft_id"] = draft_id
    result["job_id"] = job[0]
    return result


# ── The long-running loop ───────────────────────────────────────────────────
_running = True


def request_shutdown() -> None:
    """Ask the loop to exit after the current pass. Safe from a signal handler."""
    global _running
    _running = False
    log.info("Scheduler shutdown requested.")


def is_running() -> bool:
    return _running


def default_writer(pillar: str, slot_kind: str, path=None, client=None):
    """The production writer: audience problem -> draft -> quality gate.

    Returns a DraftResult or None. None is a normal outcome meaning the slot is
    skipped; the writer never emits filler.
    """
    from src.content.writer import write_draft

    return write_draft(pillar=pillar, persist=True, path=path, client=client)


def default_enqueue(draft_id: int, content: str, path=None):
    from src.orchestration.publisher import enqueue

    return enqueue(draft_id, content, path=path)


def run_loop(
    path=None,
    dry_run: bool = True,
    client=None,
    sleeper=None,
    max_passes: Optional[int] = None,
    on_pass=None,
) -> dict:
    """Run the scheduler until shutdown is requested.

    `dry_run=True` (the default) generates drafts and enqueues them but never
    publishes — that is shadow mode. `dry_run=False` additionally runs the
    publisher, which requires AUTOPUBLISH_ENABLED and DRY_RUN=false.

    `sleeper` is injected so tests do not actually sleep. `max_passes` bounds
    the loop for tests and one-shot runs.
    """
    import time as _time

    sleep = sleeper or _time.sleep
    passes = 0
    summary = {"passes": 0, "enqueued": 0, "skipped": 0, "errors": 0,
               "published": 0}

    log.info(
        "Scheduler loop starting (dry_run=%s, main/day=%s, extras/week=%s).",
        dry_run,
        get_settings().scheduler.main_posts_per_day,
        get_settings().scheduler.max_extra_posts_per_week,
    )

    while _running:
        if max_passes is not None and passes >= max_passes:
            break
        passes += 1
        try:
            result = run_once(
                writer_fn=lambda pillar, kind: default_writer(
                    pillar, kind, path=path, client=client
                ),
                enqueue_fn=lambda did, content: default_enqueue(
                    did, content, path=path
                ),
                path=path,
                dry_run=dry_run,
            )
            summary["passes"] = passes
            if result.get("job_id"):
                summary["enqueued"] += 1
            elif not result.get("ran", True):
                summary["skipped"] += 1
            if on_pass:
                on_pass(result)

            if not dry_run and result.get("job_id"):
                published = _drain_queue(path=path)
                summary["published"] += published

        except Exception as exc:  # noqa: BLE001 — the loop must survive
            summary["errors"] += 1
            log.error("Scheduler pass failed: %s", exc, exc_info=True)

        if max_passes is not None and passes >= max_passes:
            break
        if not _running:
            break

        delay = next_run_delay(path=path)
        # Sleep in short slices so a shutdown request is honoured promptly.
        waited = 0.0
        while waited < delay and _running:
            step = min(30.0, delay - waited)
            sleep(step)
            waited += step

    log.info("Scheduler loop stopped after %d pass(es): %s", passes, summary)
    return summary


def _drain_queue(path=None) -> int:
    """Claim and publish queued jobs. Returns how many were published.

    Only runs when live publishing is allowed; otherwise it is a no-op so a
    misconfigured flag cannot publish by accident.
    """
    from src.adapters.threads import get_threads_adapter
    from src.content.writer import get_draft
    from src.orchestration.publisher import (
        AdapterThreadsPublisher, claim_next_job, publish_job,
    )

    flags = get_settings().flags
    if not flags.live_publishing_allowed:
        return 0

    adapter = get_threads_adapter()
    bridge = AdapterThreadsPublisher(adapter)
    published = 0
    while True:
        job = claim_next_job(path=path)
        if job is None:
            break
        draft = get_draft(job.draft_id, path=path) or {}
        content = draft.get("content") or ""
        parts = draft.get("parts") or []
        outcome = publish_job(
            job.id, bridge, content=content, parts=parts, path=path
        )
        if outcome.succeeded:
            published += 1
        if outcome.state == "UNKNOWN_OUTCOME":
            break  # never continue past an ambiguous outcome
    return published
