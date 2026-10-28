"""src/orchestration/experiments.py — one variable at a time, with sample floors.

V1 had no experimentation. V2 rotates approved formats and hypotheses, but
refuses to declare a winner on a small sample. The guard is a hard minimum
sample size: below it, the report says "insufficient data" instead of naming
a champion.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Optional

from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("orchestration_experiments")

# A winner is never declared below this many measured posts per variant.
MIN_SAMPLES_PER_VARIANT = 5
# And never below this total across the experiment.
MIN_TOTAL_SAMPLES = 12

# The variables worth testing, one at a time.
HYPOTHESES = {
    "hook_type": ["observasi", "pernyataan_lugas", "pertanyaan_jujur",
                  "anekdot_singkat", "kontras"],
    "length": ["sangat_pendek", "pendek", "sedang"],
    "humor_intensity": ["ringan", "netral", "reflektif"],
    "content_format": ["single_snappy", "short_paragraph", "multi_paragraph",
                       "comparison", "mini_thread"],
    "problem_category": ["relatable", "practical_tips", "opinions",
                         "discoveries"],
}


@dataclass
class Experiment:
    hypothesis: str
    variant: str
    attribution_window: str = "7d"
    draft_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class ExperimentReport:
    hypothesis: str
    variants: dict[str, dict] = field(default_factory=dict)
    total_samples: int = 0
    winner: Optional[str] = None
    confidence: str = "insufficient_data"
    caveat: str = ""

    def to_dict(self) -> dict:
        return {
            "hypothesis": self.hypothesis,
            "variants": self.variants,
            "total_samples": self.total_samples,
            "winner": self.winner,
            "confidence": self.confidence,
            "caveat": self.caveat,
        }


def assign_variant(
    draft_id: int,
    hypothesis: str,
    variant: Optional[str] = None,
    rng: Optional[random.Random] = None,
    attribution_window: str = "7d",
    path=None,
) -> Experiment:
    """Assign a draft to one arm of an experiment. Random, not round-robin."""
    if hypothesis not in HYPOTHESES:
        raise ValueError(f"unknown hypothesis '{hypothesis}'")
    r = rng or random.Random()
    chosen = variant or r.choice(HYPOTHESES[hypothesis])

    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO experiment_assignments
                    (draft_id, hypothesis, variant, attribution_window)
                VALUES (?, ?, ?, ?)
                """,
                (draft_id, hypothesis, chosen, attribution_window),
            )
            exp_id = int(cur.lastrowid)

    return Experiment(hypothesis=hypothesis, variant=chosen,
                      attribution_window=attribution_window,
                      draft_id=draft_id, id=exp_id)


def assignments(hypothesis: Optional[str] = None, path=None) -> list[dict]:
    sql = "SELECT * FROM experiment_assignments"
    params: list = []
    if hypothesis:
        sql += " WHERE hypothesis = ?"
        params.append(hypothesis)
    sql += " ORDER BY id ASC"
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def variant_for_draft(draft_id: int, hypothesis: str, path=None) -> Optional[str]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT variant FROM experiment_assignments "
            "WHERE draft_id = ? AND hypothesis = ? ORDER BY id DESC LIMIT 1",
            (draft_id, hypothesis),
        ).fetchone()
    return row["variant"] if row else None


def evaluate_experiment(
    hypothesis: str,
    metric: str = "avg_views",
    path=None,
) -> ExperimentReport:
    """Compare variants. Refuses to name a winner on a small sample."""
    from src.orchestration.analytics import latest_snapshot

    rows = assignments(hypothesis, path=path)
    if not rows:
        return ExperimentReport(
            hypothesis=hypothesis,
            confidence="no_data",
            caveat="no drafts assigned to this experiment yet",
        )

    # Map each assignment to its published post and its latest good snapshot.
    with connect(path) as conn:
        job_rows = conn.execute(
            """
            SELECT ea.variant AS variant, pj.published_id AS threads_id
            FROM experiment_assignments ea
            JOIN publication_jobs pj ON pj.draft_id = ea.draft_id
            WHERE ea.hypothesis = ? AND pj.state = 'PUBLISHED'
              AND pj.published_id IS NOT NULL
            """,
            (hypothesis,),
        ).fetchall()

    values: dict[str, list[float]] = {}
    for r in job_rows:
        snap = latest_snapshot(r["threads_id"], path=path)
        if snap is None or snap.source_status != "ok":
            continue
        raw = {
            "avg_views": snap.views, "avg_likes": snap.likes,
            "avg_replies": snap.replies,
        }.get(metric)
        if raw is None:
            continue
        values.setdefault(r["variant"], []).append(float(raw))

    report = ExperimentReport(hypothesis=hypothesis)
    total = 0
    for variant, samples in values.items():
        total += len(samples)
        report.variants[variant] = {
            "samples": len(samples),
            "mean": round(sum(samples) / len(samples), 2),
            "min": min(samples),
            "max": max(samples),
        }
    report.total_samples = total

    if total < MIN_TOTAL_SAMPLES:
        report.confidence = "insufficient_data"
        report.caveat = (
            f"only {total} measured post(s); need at least {MIN_TOTAL_SAMPLES} "
            "before comparing variants"
        )
        return report

    thin = [v for v, d in report.variants.items()
            if d["samples"] < MIN_SAMPLES_PER_VARIANT]
    if thin:
        report.confidence = "insufficient_data"
        report.caveat = (
            f"variants with fewer than {MIN_SAMPLES_PER_VARIANT} samples: "
            f"{', '.join(sorted(thin))}"
        )
        return report

    best = max(report.variants, key=lambda v: report.variants[v]["mean"])
    report.winner = best
    report.confidence = "directional"
    report.caveat = (
        "directional only — Threads reach is noisy and one viral post can "
        "swing a mean. Do not lock a format on this alone."
    )
    return report


def daily_report(path=None) -> dict:
    """Short daily summary with sample sizes and caveats."""
    from src.orchestration.analytics import coverage, pillar_performance
    from src.orchestration.scheduler import posts_today, status

    cov = coverage(path=path)
    return {
        "generated_for": "today",
        "posts_today": posts_today(path=path),
        "scheduler": status(path=path)["state"],
        "metric_coverage": {
            "posts": cov.posts, "with_data": cov.with_data,
            "ratio": cov.coverage_ratio, "caveat": cov.caveat,
        },
        "pillar_performance": pillar_performance(path=path),
    }


def weekly_report(path=None) -> dict:
    """Weekly retrospective with sample sizes and explicit uncertainty."""
    from src.orchestration.analytics import coverage, pillar_performance
    from src.persona.editorial_memory import category_counts, recent_feedback

    cov = coverage(days=7, path=path)
    feedback = recent_feedback(limit=50, path=path)
    experiments = {}
    for hypothesis in HYPOTHESES:
        rep = evaluate_experiment(hypothesis, path=path)
        if rep.total_samples:
            experiments[hypothesis] = rep.to_dict()

    return {
        "window": "7 days",
        "metric_coverage": {
            "posts": cov.posts, "with_data": cov.with_data,
            "ratio": cov.coverage_ratio, "caveat": cov.caveat,
        },
        "pillar_performance": pillar_performance(days=7, path=path),
        "operator_feedback": {
            "count": len(feedback),
            "by_category": category_counts(path=path),
        },
        "experiments": experiments,
        "uncertainty_note": (
            "No conclusion here is statistically established. Sample sizes are "
            "reported so a reader can judge for themselves; Threads reach is "
            "noisy and a single viral post can dominate a mean."
        ),
    }
