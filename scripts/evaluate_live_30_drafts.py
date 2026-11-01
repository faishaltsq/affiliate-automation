"""scripts/evaluate_live_30_drafts.py — Live LLM Evaluation without Mocking.

Generates 30 real drafts across 5 pillars using HttpLLMClient against fresh temporary DBs.
Runs full evaluation and quality gate on every draft.
Outputs comprehensive metrics and samples to docs/v2/evaluation_report_30_drafts.json and .md.
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.content.quality_gate import (
    APPROVED_AUTO, NEEDS_REVIEW, REJECTED, REVISE_ONCE,
    compute_content_hash, evaluate_draft, validate_draft_for_publishing,
)
from src.content.writer import write_draft
from src.core.llm import HttpLLMClient
from src.core.storage import migrate
from src.logger import get_logger

log = get_logger("evaluate_live_30")

PILLARS = ["relatable", "practical_tips", "opinions", "discoveries", "affiliate"]
DRAFTS_PER_PILLAR = 6  # 5 * 6 = 30 total drafts


def evaluate_pillar(pillar: str, base_dir: Path) -> list[dict]:
    db_path = base_dir / f"eval_live_30_{pillar}.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    migrate(db_path)
    llm = HttpLLMClient()
    pillar_results = []

    for i in range(DRAFTS_PER_PILLAR):
        idx = i + 1
        t0 = time.time()
        try:
            draft = write_draft(pillar=pillar, client=llm, path=db_path, persist=True)
            if draft is None:
                print(f"[{pillar}] Draft {idx}/6: SKIPPED (no draft)")
                pillar_results.append({
                    "id": None,
                    "pillar": pillar,
                    "status": "skipped",
                    "error": "write_draft returned None",
                })
                continue

            # Run quality gate with live LLM critic
            gate = evaluate_draft(
                draft.content,
                parts=draft.parts,
                pillar=draft.pillar,
                draft_id=draft.draft_id,
                client=llm,
                path=db_path,
                persist=True,
            )

            # Check publication validation with fail-closed semantics
            pub_val = validate_draft_for_publishing(
                draft.draft_id, review_required=True, path=db_path
            )

            elapsed = round(time.time() - t0, 2)
            print(f"[{pillar}] Draft {idx}/6 DONE ({elapsed}s) | Score: {gate.total_score:.1f} | Verdict: {gate.verdict}")

            expected_hash = compute_content_hash(draft.content, draft.parts)
            crit_data = {}
            if hasattr(gate, "critic_response") and gate.critic_response:
                cr = gate.critic_response
                crit_data = {
                    "persona_voice": getattr(cr, "persona_voice", 0),
                    "audience_relevance": getattr(cr, "audience_relevance", 0),
                    "natural_writing": getattr(cr, "natural_writing", 0),
                    "substance": getattr(cr, "substance", 0),
                    "safe_practical_advice": getattr(cr, "safe_practical_advice", 0),
                    "reasons": getattr(cr, "reasons", []),
                    "concerns": getattr(cr, "concerns", ""),
                }

            record = {
                "draft_id": draft.draft_id,
                "pillar": draft.pillar,
                "angle": draft.angle,
                "problem": draft.audience_problem,
                "structure": draft.structure,
                "format": draft.variation.get("format", ""),
                "opening": draft.variation.get("opening", ""),
                "parts_count": len(draft.parts) if draft.parts else 0,
                "content": draft.content,
                "parts": draft.parts,
                "content_hash": expected_hash,
                "verdict": gate.verdict,
                "total_score": gate.total_score,
                "vetoes": list(gate.vetoes),
                "critic": crit_data,
                "pub_validate_ok": pub_val.eligible,
                "pub_validate_reason": pub_val.reason,
                "elapsed_s": elapsed,
            }
            pillar_results.append(record)

        except Exception as exc:
            print(f"[{pillar}] Draft {idx}/6 FAILED: {exc}")
            pillar_results.append({
                "id": None,
                "pillar": pillar,
                "status": "error",
                "error": str(exc),
            })

    return pillar_results


def run_evaluation() -> dict:
    base_dir = REPO_ROOT / "data"
    base_dir.mkdir(parents=True, exist_ok=True)

    print("Starting generation and evaluation of 30 live drafts across 5 pillars (parallel)...")
    total_start = time.time()

    all_results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(evaluate_pillar, p, base_dir): p for p in PILLARS}
        for fut in as_completed(futures):
            p = futures[fut]
            try:
                res = fut.result()
                all_results.extend(res)
                print(f"--> Pillar {p} complete with {len(res)} drafts.")
            except Exception as exc:
                print(f"--> Pillar {p} execution error: {exc}")

    total_time = round(time.time() - total_start, 2)
    print(f"\nAll drafts finished in {total_time}s.")

    # Sort results by pillar and draft_id
    valid_results = [r for r in all_results if r.get("draft_id")]
    verdict_counts = {}
    structure_counts = {}
    format_counts = {}
    scores = []

    for r in valid_results:
        v = r["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        st = r["structure"]
        structure_counts[st] = structure_counts.get(st, 0) + 1
        fmt = r["format"]
        format_counts[fmt] = format_counts.get(fmt, 0) + 1
        scores.append(r["total_score"])

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    summary = {
        "total_attempted": len(all_results),
        "total_successful": len(valid_results),
        "total_time_seconds": total_time,
        "average_score": avg_score,
        "verdict_breakdown": verdict_counts,
        "structure_breakdown": structure_counts,
        "format_breakdown": format_counts,
        "results": all_results,
    }
    return summary


def main():
    summary = run_evaluation()

    # Write JSON report
    out_json = REPO_ROOT / "docs" / "v2" / "evaluation_report_30_drafts.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"Saved evaluation JSON to {out_json}")

    # Write Markdown report
    out_md = REPO_ROOT / "docs" / "v2" / "evaluation_report_30_drafts.md"
    lines = [
        "# Affiliate Automation V2 — 30-Draft Live Evaluation Report",
        "",
        f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"- **Model**: Production tig/step-5-unrestricted (No Mocking)",
        f"- **Total Drafts Evaluated**: {summary['total_successful']}/{summary['total_attempted']}",
        f"- **Average Quality Score**: {summary['average_score']:.1f}/100",
        f"- **Total Duration**: {summary['total_time_seconds']}s",
        "",
        "## Summary Metrics",
        "",
        "### Verdict Distribution",
        "| Verdict | Count | Percentage |",
        "| --- | --- | --- |",
    ]

    total_valid = max(1, summary["total_successful"])
    for verdict, count in sorted(summary["verdict_breakdown"].items()):
        pct = (count / total_valid) * 100
        lines.append(f"| `{verdict}` | {count} | {pct:.1f}% |")

    lines.extend([
        "",
        "### Format & Structure Distribution",
        "| Structure | Format | Count |",
        "| --- | --- | --- |",
    ])
    for fmt, count in sorted(summary["format_breakdown"].items()):
        lines.append(f"| Single Post | `{fmt}` | {count} |")
    for st, count in sorted(summary["structure_breakdown"].items()):
        if st == "thread":
            lines.append(f"| Thread | `mini_thread` | {count} |")

    lines.extend([
        "",
        "## Detailed Draft Records",
        "",
    ])

    for i, r in enumerate(summary["results"]):
        if not r.get("draft_id"):
            lines.append(f"### Draft #{i+1}: FAILED/SKIPPED ({r.get('pillar')})")
            lines.append(f"- Error: {r.get('error')}")
            lines.append("")
            continue

        lines.extend([
            f"### Draft #{r['draft_id']} — [{r['pillar'].upper()}] {r['format']} (Score: {r['total_score']:.1f} | `{r['verdict']}`)",
            f"- **Problem**: {r['problem']}",
            f"- **Angle**: {r['angle']}",
            f"- **Structure**: `{r['structure']}` (Parts: {r['parts_count']})",
            f"- **Opening Style**: `{r['opening']}`",
            f"- **Content Hash**: `{r['content_hash'][:16]}...`",
            f"- **Publication Pre-Validation**: `eligible={r['pub_validate_ok']}` ({r['pub_validate_reason']})",
        ])

        if r["vetoes"]:
            lines.append(f"- **Vetoes**: `{', '.join(r['vetoes'])}`")

        if r["critic"]:
            c = r["critic"]
            lines.append(f"- **Critic Scores**: Voice: {c['persona_voice']}, Relevance: {c['audience_relevance']}, Naturalness: {c['natural_writing']}, Substance: {c['substance']}, Safety: {c['safe_practical_advice']}")
            if c.get("reasons"):
                lines.append(f"- **Critic Reasons**: `{', '.join(c['reasons'])}`")
            if c.get("concerns"):
                lines.append(f"- **Critic Notes**: {c['concerns']}")

        lines.append("")
        if r["parts"]:
            lines.append("**Thread Content:**")
            for idx, p in enumerate(r["parts"], 1):
                lines.append(f"{idx}. {p}")
        else:
            lines.append("**Content:**")
            lines.append(f"> {r['content']}")

        lines.append("")
        lines.append("---")
        lines.append("")

    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Saved evaluation Markdown report to {out_md}")


if __name__ == "__main__":
    main()
