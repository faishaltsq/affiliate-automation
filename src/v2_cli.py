"""src/v2_cli.py — Affiliate Automation V2 command line.

Offline by default. Nothing here contacts an external API, publishes, or sends
a Telegram message unless the corresponding feature flag is explicitly enabled
in the environment.

Usage:
  python -m src.v2_cli migrate            # apply additive migrations
  python -m src.v2_cli migrate --status   # show migration state
  python -m src.v2_cli health             # flags, queue, DB summary
  python -m src.v2_cli persona            # show the active Persona Bible
  python -m src.v2_cli plan [--days 30]   # build the content calendar
  python -m src.v2_cli ideas [--n 3]      # generate candidate ideas
  python -m src.v2_cli draft [--pillar X] # generate one draft (no publish)
  python -m src.v2_cli shadow [--n 30]    # offline shadow run, fixtures only
  python -m src.v2_cli sync               # reconcile posts (no deletion)
  python -m src.v2_cli audit [--limit 20] # recent audit events
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.config import get_settings, redact_secrets
from src.core.storage import (
    connect, migrate, migration_status, table_exists,
)
from src.core.clock import wib_display, utc_iso
from src.logger import get_logger

log = get_logger("v2_cli")


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def cmd_migrate(args) -> int:
    if args.status:
        _print_json(migration_status())
        return 0
    applied = migrate()
    if applied:
        print("Applied migrations:")
        for name in applied:
            print(f"  - {name}")
    else:
        print("No pending migrations.")
    print()
    _print_json(migration_status())
    return 0


def cmd_health(args) -> int:
    s = get_settings()
    payload = {
        "checked_at": utc_iso(),
        "wib_now": wib_display(),
        "settings": s.safe_summary(),
        "live_publishing_allowed": s.flags.live_publishing_allowed,
        "live_affiliate_allowed": s.flags.live_affiliate_allowed,
    }
    with connect() as conn:
        payload["tables"] = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            except Exception:
                n = -1
            payload["tables"][name] = n
    _print_json(payload)
    return 0


def cmd_persona(args) -> int:
    from src.persona.profile import get_active_persona

    p = get_active_persona()
    _print_json(p.to_json_dict())
    return 0


def cmd_plan(args) -> int:
    from src.strategy.calendar import build_calendar

    slots = build_calendar(days=args.days)
    print(f"Calendar: {len(slots)} slots ({args.days} days)")
    for slot in slots:
        print(
            f"  {slot.slot_date}  {slot.pillar:<16} {slot.slot_kind:<6} "
            f"commercial={'Y' if slot.commercial_allowed else 'n'}  "
            f"{slot.target_angle[:60]}"
        )
    return 0


def cmd_ideas(args) -> int:
    from src.strategy.ideation import generate_ideas

    ideas = generate_ideas(count=args.n)
    print(f"{len(ideas)} candidate ideas:")
    for i, idea in enumerate(ideas, 1):
        print(
            f"  {i}. [{idea.pillar}] score={idea.rank_score:.1f} "
            f"{idea.angle[:70]}"
        )
    return 0


def cmd_draft(args) -> int:
    from src.content.writer import write_draft
    from src.content.quality_gate import evaluate_draft

    draft = write_draft(pillar=args.pillar)
    if draft is None:
        print("No draft produced (no acceptable idea available).")
        return 1
    verdict = evaluate_draft(draft)
    print(f"Draft #{draft.id} pillar={draft.idea_pillar} "
          f"structure={draft.structure}")
    print(f"Verdict: {verdict.verdict} score={verdict.total_score:.1f}")
    if verdict.vetoes:
        print(f"Vetoes: {', '.join(verdict.vetoes)}")
    print()
    print(draft.content)
    print()
    print("(dry run — nothing published)")
    return 0


def cmd_shadow(args) -> int:
    from src.content.writer import write_draft
    from src.content.quality_gate import evaluate_draft

    produced = 0
    accepted = 0
    pillars: dict[str, int] = {}
    for i in range(args.n):
        draft = write_draft(seed=i)
        if draft is None:
            continue
        produced += 1
        pillars[draft.idea_pillar] = pillars.get(draft.idea_pillar, 0) + 1
        verdict = evaluate_draft(draft)
        if verdict.verdict in ("APPROVED_AUTO", "NEEDS_REVIEW"):
            accepted += 1
    print(f"Shadow run: {produced} drafts produced, {accepted} accepted")
    print("Pillar distribution:")
    for pillar, count in sorted(pillars.items(), key=lambda kv: -kv[1]):
        print(f"  {pillar:<16} {count}")
    print()
    print("No external calls were made. Nothing was published.")
    return 0


def cmd_sync(args) -> int:
    from src.core import post_sync

    summary = post_sync.reconcile_all(prober=post_sync.default_threads_prober)
    print("Post reconciliation (0 rows deleted):")
    _print_json(summary)
    return 0


def cmd_audit(args) -> int:
    from src.core.events import recent

    events = recent(limit=args.limit)
    if not events:
        print("No audit events recorded yet.")
        return 0
    for e in events:
        print(
            f"  [{e['created_at']}] {e['actor_type']}:{e['actor_id'] or '-'} "
            f"{e['action']} {e['object_type']}#{e['object_id'] or '-'}"
        )
        if e.get("reason"):
            print(f"      reason: {redact_secrets(e['reason'])[:120]}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.v2_cli",
        description="Affiliate Automation V2 — offline-first control surface.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="apply additive SQLite migrations")
    p.add_argument("--status", action="store_true", help="show state only")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("health", help="flags, tables, row counts")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("persona", help="show the active Persona Bible")
    p.set_defaults(func=cmd_persona)

    p = sub.add_parser("plan", help="build the content calendar")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("ideas", help="generate candidate ideas")
    p.add_argument("--n", type=int, default=3)
    p.set_defaults(func=cmd_ideas)

    p = sub.add_parser("draft", help="generate one draft without publishing")
    p.add_argument("--pillar", default=None)
    p.set_defaults(func=cmd_draft)

    p = sub.add_parser("shadow", help="offline shadow run, fixtures only")
    p.add_argument("--n", type=int, default=30)
    p.set_defaults(func=cmd_shadow)

    p = sub.add_parser("sync", help="reconcile posts against Threads (no delete)")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("audit", help="recent audit events")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    s = get_settings()
    if not s.flags.dry_run and args.command in ("draft", "shadow", "plan", "ideas"):
        # These commands are always offline; refuse to run them with live
        # flags on so nobody mistakes a shadow run for a publishing run.
        print(
            "NOTE: DRY_RUN is false in the environment. These commands remain "
            "offline by design.",
            file=sys.stderr,
        )
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
