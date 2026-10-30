"""CLI launcher for Affiliate Automation V2.

The account is persona-first now. This launcher exposes exactly two long-lived
processes and the offline control surface:

  python main.py bot      Telegram listener — routes the V2 command set
  python main.py v2       content runtime — schedule, generate, gate, queue
  python main.py v2 --once        one scheduling pass, then exit
  python main.py v2 --status      report state without scheduling
  python main.py v2 --dry-run     force dry-run for this process

Everything else the V1 launcher offered (organic daemon, TikTok pipeline,
Clipper batch, affiliate link pipeline, trend research, worker, monitor) is
disabled. The modules still exist and their data is untouched; they are simply
no longer reachable from the command line. Re-enabling one is a deliberate
edit here, not a flag flip.

Offline control surface lives in `python -m src.v2_cli` and
`python -m src.v2_runtime`.
"""
import sys

DISABLED = {
    "organic": "Legacy news-first Threads daemon. Replaced by `main.py v2`.",
    "tiktok": "TikTok/Shorts pipeline. Disabled during the V2 cutover.",
    "ops": "Content-ops batch production. Disabled during the V2 cutover.",
    "link": "Affiliate link-to-content pipeline. Affiliate is locked off.",
    "trends": "Trend research job. V2 generates from audience signals, not news.",
    "worker": "Legacy per-topic worker. Disabled during the V2 cutover.",
    "full": "Trends + worker. Disabled during the V2 cutover.",
    "single": "Single-topic legacy pipeline. Disabled during the V2 cutover.",
    "monitor": "Legacy metrics snapshot. Use `/analytics` in Telegram.",
}

USAGE = """Affiliate Automation V2 — CLI

  python main.py bot                 Telegram listener (V2 command set)
  python main.py v2                  content runtime (long-running)
  python main.py v2 --once           one scheduling pass
  python main.py v2 --status         report state, schedule nothing
  python main.py v2 --dry-run        force dry-run for this process

Offline tools:
  python -m src.v2_cli health        flags, tables, row counts
  python -m src.v2_cli plan          build the content calendar
  python -m src.v2_cli draft         generate one draft (no publish)
  python -m src.v2_cli shadow        gate + queue, no publish
  python -m src.v2_cli run           scheduler passes, respects flags
  python -m src.integration_check    offline end-to-end rehearsal
"""


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "bot":
        from src.bot.telegram_listener import run_telegram_listener

        run_telegram_listener()
        return 0

    if cmd == "v2":
        from src.v2_runtime import main as v2_main

        return v2_main(sys.argv[2:])

    if cmd in DISABLED:
        print(f"'{cmd}' is disabled: {DISABLED[cmd]}")
        print()
        print(USAGE)
        return 2

    print(USAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
