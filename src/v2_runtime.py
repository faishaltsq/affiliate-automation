"""src/v2_runtime.py — the long-running V2 automation process.

This is the process that replaces `main.py organic --daemon`. It owns the
content loop for the Threads account and nothing else: no TikTok, no Clipper,
no affiliate replies.

Boot sequence, in order:

  1. `migrate()` — additive, idempotent; safe on every start.
  2. `startup_sweep()` — reclaim expired leases, reconcile any
     UNKNOWN_OUTCOME against the API, requeue FAILED jobs with budget left.
     This runs before any new draft is generated so a crash mid-publish is
     resolved before the queue grows.
  3. `run_loop()` — schedule, generate, gate, enqueue, and (when live
     publishing is allowed) publish.

Publishing is gated twice:

  * `AUTOPUBLISH_ENABLED=true` AND `DRY_RUN=false` are required before the
    adapter will do anything but log.
  * `V2_REVIEW_REQUIRED=true` (the default) additionally requires the draft's
    status to be APPROVED, which only an operator command sets.

So with default flags this process generates and queues drafts and publishes
nothing. That is the intended state for a fresh cutover.

Usage:
    python -m src.v2_runtime                # run until stopped
    python -m src.v2_runtime --once         # one pass, then exit
    python -m src.v2_runtime --status       # report state, run nothing
    python -m src.v2_runtime --dry-run      # force dry-run for this process
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from typing import Optional

from src.core.config import get_settings
from src.core.llm import get_llm_client
from src.core.storage import migrate, migration_status
from src.logger import get_logger
from src.orchestration import recovery as rec
from src.orchestration import scheduler as sched
from src.singleton import ensure_single_instance

log = get_logger("v2_runtime")


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def status() -> dict:
    """Everything an operator needs to decide whether to start the loop."""
    s = get_settings()
    safe, why = rec.is_safe_to_restart()
    payload = {
        "flags": s.flags.describe(),
        "live_publishing_allowed": s.flags.live_publishing_allowed,
        "review_required": s.flags.v2_review_required,
        "scheduler": sched.status(),
        "safe_to_start": safe,
        "safe_to_start_reason": why,
        "needs_human": rec.pending_human_review(),
        "migrations": migration_status(),
    }
    return payload


def run(once: bool = False, dry_run: Optional[bool] = None) -> dict:
    """Boot, recover, then run the content loop."""
    ensure_single_instance("v2_runtime")

    applied = migrate()
    if applied:
        log.info("Applied migrations: %s", ", ".join(applied))

    # Recovery first: resolve anything ambiguous before generating new work.
    sweep = rec.startup_sweep(publisher=None)
    if sweep.actions:
        log.info("Startup sweep: %s", "; ".join(sweep.actions))
    for item in sweep.needs_human:
        log.warning("Needs human review: %s", item)

    flags = get_settings().flags
    if dry_run is None:
        dry_run = not flags.live_publishing_allowed

    if dry_run and not flags.dry_run:
        log.warning(
            "Live flags are on but this process was started in dry-run mode; "
            "nothing will be published."
        )

    summary = sched.run_loop(
        dry_run=dry_run,
        client=get_llm_client(),
        max_passes=1 if once else None,
        review_required=flags.v2_review_required,
    )
    return {
        "dry_run": dry_run,
        "review_required": flags.v2_review_required,
        "recovery": sweep.to_dict(),
        "summary": summary,
    }


def _install_signal_handlers() -> None:
    def _stop(signum, frame):  # noqa: ANN001, ARG001
        log.info("Signal %s received; finishing the current pass.", signum)
        sched.request_shutdown()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.v2_runtime",
        description="Affiliate Automation V2 — persona-first Threads runtime.",
    )
    parser.add_argument("--once", action="store_true",
                        help="run a single scheduling pass and exit")
    parser.add_argument("--status", action="store_true",
                        help="print state and exit without scheduling")
    parser.add_argument("--dry-run", action="store_true",
                        help="force dry-run for this process regardless of flags")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output")
    args = parser.parse_args(argv)

    try:
        if args.status:
            payload = status()
            if args.json:
                _print_json(payload)
            else:
                print("Affiliate Automation V2 — status")
                print(f"  flags            : {payload['flags']}")
                print(f"  live publishing  : {payload['live_publishing_allowed']}")
                print(f"  review required  : {payload['review_required']}")
                print(f"  scheduler        : {payload['scheduler']['state']} "
                      f"({payload['scheduler']['wib_now']})")
                print(f"  queue            : {payload['scheduler']['queue']}")
                print(f"  safe to start    : {payload['safe_to_start']} "
                      f"— {payload['safe_to_start_reason']}")
                if payload["needs_human"]:
                    print(f"  needs human      : {len(payload['needs_human'])} item(s)")
            return 0

        _install_signal_handlers()
        result = run(once=args.once, dry_run=True if args.dry_run else None)
    finally:
        # Release the log file so a supervising process can rotate or delete it.
        # Without this the runtime holds logs/pipeline.log open for as long as
        # the process lives, which is exactly what a long-running daemon does.
        logging.shutdown()
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass

    if args.json:
        _print_json(result)
    else:
        print(f"Dry run         : {result['dry_run']}")
        print(f"Review required : {result['review_required']}")
        print(f"Summary         : {result['summary']}")
        if result["dry_run"]:
            print("Nothing was published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
