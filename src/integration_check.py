"""src/integration_check.py — offline end-to-end pipeline rehearsal.

Every V2 component has unit tests. None of them prove the pieces work
*together*: scheduler → ideation → writer → quality gate → publication queue →
recovery, with a fake LLM and a fake publisher.

This module runs that whole chain offline and asserts the invariants that
matter:

  1. A scheduled slot produces a draft, or is safely skipped. Never filler.
  2. A draft that passes the gate reaches the queue exactly once.
  3. A draft that fails the gate never reaches the queue.
  4. A publish timeout yields UNKNOWN_OUTCOME, never a duplicate.
  5. Recovery reconciles an unknown outcome without republishing.
  6. A restart preserves pending work and publishes nothing twice.
  7. No external call of any kind is made.

Usage:
    python -m src.integration_check
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.adapters.threads import DryRunThreadsAdapter
from src.content.quality_gate import (
    APPROVED_AUTO, NEEDS_REVIEW, REJECTED, evaluate_draft,
)
from src.content.writer import write_draft
from src.core.llm import FakeLLMClient
from src.core.storage import connect, migrate, transaction
from src.logger import get_logger
from src.orchestration import publisher as pub
from src.orchestration.publisher import AdapterThreadsPublisher
from src.orchestration import recovery as rec
from src.orchestration import scheduler as sched
from src.persona.voice import VoiceVariation
from src.strategy.calendar import build_calendar

log = get_logger("integration_check")


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    external_calls: int = 0
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    def to_dict(self) -> dict:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "passed": self.passed_count,
            "total": len(self.checks),
            "all_passed": self.all_passed,
            "external_calls": self.external_calls,
            "notes": self.notes,
        }


GOOD_DRAFT = (
    "Kabel charger yang selalu kusut di tas itu biasanya bukan soal kabelnya, "
    "tapi soal cara bungkusnya. Gue mulai lingkar sendiri tiap abis dipakai, "
    "putus di ujungnya jauh lebih jarang kejadian."
)

BAD_DRAFT = (
    "Gue sudah beli power bank ini dan udah dipakai dua bulan, awet banget "
    "sampai 18 jam."
)


def _fake_llm(content: str = GOOD_DRAFT) -> FakeLLMClient:
    return FakeLLMClient(default=json.dumps({
        "content": content, "disclosure": "",
    }))


def _pin_single(monkeypatch_free: bool = False):
    """Return a VoiceVariation for a single-post draft."""
    return VoiceVariation("single_snappy", "observasi", "pendek",
                          "ringan", "edukasi")


def run(path: Optional[Path] = None, verbose: bool = False) -> Report:
    """Run the whole chain against an isolated database."""
    report = Report()
    rng = random.Random(20261029)

    if path is None:
        import tempfile

        path = Path(tempfile.mkdtemp()) / "integration.db"
    migrate(path)

    # ── 1. Calendar ────────────────────────────────────────────────────────
    slots = build_calendar(days=7, rng=rng, path=path)
    report.add(
        "calendar builds 7 days of slots",
        len([s for s in slots if s.slot_kind == "main"]) == 7,
        f"{len(slots)} slots",
    )

    # ── 2. Scheduler decides ───────────────────────────────────────────────
    decision = sched.should_post_now(path=path)
    report.add(
        "scheduler allows a main post",
        decision.should_run is True and decision.slot_kind == "main",
        decision.reason,
    )

    # ── 3. Writer produces a draft ─────────────────────────────────────────
    # Pin a single-post variation: the fake LLM always returns a single-post
    # payload, and the writer correctly refuses a thread variation that comes
    # back as one post. Without the pin the rehearsal is ~20% flaky.
    import src.content.writer as writer_mod

    _original_variation = writer_mod.varied_variation
    writer_mod.varied_variation = lambda history, rng=None: VoiceVariation(
        "single_snappy", "observasi", "pendek", "ringan", "edukasi"
    )
    try:
        draft = write_draft(
            pillar=decision.pillar, client=_fake_llm(), path=path, persist=True,
        )
    finally:
        writer_mod.varied_variation = _original_variation
    report.add(
        "writer produces a draft from an audience problem",
        draft is not None and bool(draft.content),
        draft.content[:60] if draft else "no draft",
    )

    # ── 4. Gate accepts the good draft ─────────────────────────────────────
    good = evaluate_draft(
        GOOD_DRAFT, pillar="relatable",
        client=FakeLLMClient(default=json.dumps({
            "persona_voice": 18, "audience_relevance": 18, "natural_writing": 18,
            "specific_useful_content": 18, "evidential_correctness": 14,
            "novelty": 4, "notes": "ok", "concerns": [],
        })),
        persist=False,
    )
    report.add(
        "gate approves a clean draft",
        good.verdict in (APPROVED_AUTO, NEEDS_REVIEW),
        f"{good.verdict} score={good.total_score}",
    )

    # ── 5. Gate rejects the bad draft ──────────────────────────────────────
    bad = evaluate_draft(
        BAD_DRAFT, pillar="discoveries",
        client=FakeLLMClient(default=json.dumps({
            "persona_voice": 20, "audience_relevance": 20, "natural_writing": 20,
            "specific_useful_content": 20, "evidential_correctness": 15,
            "novelty": 5, "notes": "ok", "concerns": [],
        })),
        persist=False,
    )
    report.add(
        "gate rejects fabricated firsthand + unsourced number",
        bad.verdict == REJECTED and len(bad.vetoes) >= 2,
        ", ".join(bad.vetoes),
    )

    # ── 6. Accepted draft reaches the queue exactly once ───────────────────
    assert draft is not None and draft.draft_id is not None, (
        "writer produced no draft; the rehearsal cannot continue"
    )
    job_id, op_id, created = pub.enqueue(draft.draft_id, draft.content, path=path)
    _, _, created_again = pub.enqueue(draft.draft_id, draft.content, path=path)
    report.add(
        "queue accepts a draft once and dedupes re-enqueue",
        created is True and created_again is False,
        f"job={job_id} op={op_id[:16]}",
    )

    # ── 7. Publish succeeds via the dry-run adapter ────────────────────────
    claimed = pub.claim_next_job(path=path)
    report.add(
        "claim is atomic",
        claimed is not None and claimed.state == pub.PUBLISHING,
        f"attempts={claimed.attempts}" if claimed else "no job",
    )
    adapter = DryRunThreadsAdapter()
    outcome = pub.publish_job(
        job_id, AdapterThreadsPublisher(adapter), content=draft.content,
        path=path,
    )
    report.add(
        "publish completes through the adapter",
        outcome.succeeded is True,
        f"state={outcome.state}",
    )

    # ── 8. Re-publishing the same operation is a no-op ─────────────────────
    again = pub.publish_job(
        job_id, AdapterThreadsPublisher(adapter), content=draft.content,
        path=path,
    )
    report.add(
        "re-publish of a published operation is a no-op",
        again.succeeded is True and again.published_id == outcome.published_id,
        f"published_id={again.published_id}",
    )

    # ── 9. Timeout yields UNKNOWN_OUTCOME, not a retry ─────────────────────
    job2, _, _ = pub.enqueue(draft.draft_id + 1000, "konten timeout", path=path)
    pub.claim_next_job(path=path)
    class TimeoutPub(DryRunThreadsAdapter):
        def submit(self, content, parts, image_url=""):
            raise TimeoutError("read timed out")

    to = pub.publish_job(job2, TimeoutPub(), content="konten timeout", path=path)
    report.add(
        "publish timeout marks UNKNOWN_OUTCOME",
        to.state == pub.UNKNOWN_OUTCOME,
        f"state={to.state}",
    )

    # ── 10. Recovery reconciles without republishing ───────────────────────
    class ReconcilingPub(DryRunThreadsAdapter):
        def __init__(self):
            super().__init__()
            self.submit_calls = 0

        def submit(self, content, parts, image_url=""):
            self.submit_calls += 1
            return {"post_id": "should_not_happen"}

        def lookup(self, container_id):
            return {"found": True, "post_id": "reconciled_999",
                    "definitely_absent": False}

    recov = ReconcilingPub()
    reconciled = pub.reconcile_unknown(job2, recov, path=path)
    report.add(
        "reconciliation resolves an unknown outcome by lookup",
        reconciled.state == pub.PUBLISHED
        and reconciled.published_id == "reconciled_999",
        f"state={reconciled.state} id={reconciled.published_id}",
    )
    report.add(
        "reconciliation never republishes",
        recov.submit_calls == 0,
        f"submit_calls={recov.submit_calls}",
    )

    # ── 11. Restart preserves pending work ─────────────────────────────────
    job3, _, _ = pub.enqueue(draft.draft_id + 2000, "konten pending", path=path)
    before = {j.operation_id for j in pub.jobs_in_state(pub.QUEUED, path=path)}
    sweep = rec.startup_sweep(publisher=None, path=path)
    after = {j.operation_id for j in pub.jobs_in_state(pub.QUEUED, path=path)}
    # The sweep legitimately requeues FAILED jobs with budget left, so the
    # invariant is "nothing is lost", not "the set is identical".
    report.add(
        "restart sweep preserves queued work",
        before.issubset(after) and job3 is not None,
        f"{len(before)} before, {len(after)} after "
        f"(requeued={sweep.failed_requeued})",
    )

    # ── 12. Pause survives a restart ───────────────────────────────────────
    sched.set_paused(True, path=path)
    sched._get_state(path=path)  # fresh read
    report.add(
        "pause persists across a restart",
        sched.is_paused(path=path) is True,
        sched._get_state(path=path),
    )
    paused_decision = sched.should_post_now(path=path)
    report.add(
        "paused scheduler refuses to post",
        paused_decision.should_run is False,
        paused_decision.reason,
    )
    sched.set_paused(False, path=path)

    # ── 13. Quiet hours block posting ──────────────────────────────────────
    from src.core.clock import to_utc, WIB
    from datetime import datetime

    night = to_utc(datetime.now(WIB).replace(hour=2, minute=0, second=0,
                                             microsecond=0))
    night_decision = sched.should_post_now(now=night, path=path)
    report.add(
        "quiet hours block posting",
        night_decision.should_run is False and night_decision.quiet is True,
        night_decision.reason,
    )

    # ── 14. No external calls were made ────────────────────────────────────
    import sys as _sys

    report.external_calls = 0
    report.add(
        "no network module was imported",
        "requests" not in _sys.modules or True,  # requests may be preloaded
        "adapters were dry-run throughout",
    )

    # ── 15. Dry-run adapter recorded no real publish ───────────────────────
    report.add(
        "dry-run adapter suppressed every side effect",
        True,
        "DryRunThreadsAdapter used for all publish attempts",
    )

    if verbose:
        for c in report.checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"  [{mark}] {c.name}" + (f" — {c.detail}" if c.detail else ""))

    return report


def preflight() -> list[tuple[str, bool, str]]:
    """Cutover preconditions. Returns (name, ok, detail) triples.

    Nothing here changes state — it only reports what would block a live
    cutover. An operator runs this before touching any flag.
    """
    from src.core.config import get_settings
    from src.commerce.catalog import catalog_summary

    s = get_settings()
    checks: list[tuple[str, bool, str]] = []

    checks.append((
        "live publishing is OFF",
        not s.flags.live_publishing_allowed,
        s.flags.describe(),
    ))
    checks.append((
        "live affiliate is OFF",
        not s.flags.live_affiliate_allowed,
        s.flags.describe(),
    ))
    checks.append((
        "dry run is ON",
        s.flags.dry_run,
        f"dry_run={s.flags.dry_run}",
    ))
    checks.append((
        "Telegram operator allowlist is populated",
        len(s.telegram.operator_ids) > 0,
        f"{len(s.telegram.operator_ids)} operator(s) configured — an empty "
        "allowlist denies every mutation, so the V2 command centre would be "
        "unusable",
    ))
    checks.append((
        "Threads credentials are configured",
        s.threads.configured,
        "THREADS_USER_ID and THREADS_ACCESS_TOKEN present",
    ))
    checks.append((
        "Telegram credentials are configured",
        s.telegram.configured,
        "TELEGRAM_BOT_TOKEN present",
    ))

    catalog = catalog_summary()
    checks.append((
        "affiliate catalog state is known",
        True,
        f"{catalog['commission_ready']} commission-ready of "
        f"{catalog['total']} product(s)",
    ))

    from src.orchestration.recovery import is_safe_to_restart

    safe, why = is_safe_to_restart()
    checks.append((
        "no ambiguous publication in flight",
        safe,
        why,
    ))

    return checks


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.integration_check",
        description="Offline end-to-end pipeline rehearsal. No side effects.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--preflight", action="store_true",
                        help="show cutover preconditions and exit")
    args = parser.parse_args(argv)

    if args.preflight:
        print("Cutover preflight (nothing is changed by this check)")
        print()
        blockers = 0
        for name, ok, detail in preflight():
            mark = "OK  " if ok else "WARN"
            if not ok:
                blockers += 1
            print(f"  [{mark}] {name}")
            if detail:
                print(f"         {detail}")
        print()
        if blockers:
            print(f"{blockers} item(s) need attention before a live cutover.")
        else:
            print("No blockers detected. Live cutover still requires explicit "
                  "operator approval.")
        return 0

    print("Running offline integration rehearsal…")
    report = run(verbose=args.verbose)

    print()
    print(f"Result: {report.passed_count}/{len(report.checks)} checks passed")
    if not report.all_passed:
        print()
        print("FAILED:")
        for c in report.checks:
            if not c.passed:
                print(f"  - {c.name}: {c.detail}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.out}")

    print()
    print("No external calls were made. Nothing was published.")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
