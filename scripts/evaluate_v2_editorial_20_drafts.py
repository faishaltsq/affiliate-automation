"""scripts/evaluate_v2_editorial_20_drafts.py — 20 Live Drafts Evaluation.

Generates 20 real drafts across 5 pillars using the active LLM client:
- relatable (4)
- opinions (4)
- practical_tips (4)
- questions (4)
- curiosity_gimmick (4)

Records original draft, cleaned draft, rewritten draft (if REVISE_ONCE),
critic scores, structured findings, concerns, notes, and final decisions.
Saves results to docs/v2/editorial_overhaul_20_drafts.json and .md.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.content.editor import edit_and_clean_draft
from src.content.quality_gate import (
    APPROVED_AUTO,
    NEEDS_REVIEW,
    REJECTED,
    REVISE_ONCE,
    compute_content_hash,
    evaluate_draft,
    validate_draft_for_publishing,
)
from src.content.writer import write_draft
from src.core.config import LLMConfig, get_settings
from src.core.llm import HttpLLMClient
from src.core.storage import connect, migrate
from src.logger import get_logger
from src.strategy.ideation import IdeaCandidate

log = get_logger("editorial_20_eval")

DRAFT_SPECIFICATIONS = [
    # ── Relatable (4) ──
    {
        "pillar": "relatable",
        "audience_problem": "Kabel charger yang selalu kusut di tas dan leher konektornya gampang getas",
        "angle": "kebiasaan kecil yang bikin barang cepat rusak",
        "format": "short_paragraph",
    },
    {
        "pillar": "relatable",
        "audience_problem": "Meja kerja penuh kabel sampai mouse nggak muat gerak",
        "angle": "realita meja kerja vs foto estetik di medsos",
        "format": "short_paragraph",
    },
    {
        "pillar": "relatable",
        "audience_problem": "Beli gadget baru karena diskon, dipakai dua minggu lalu disimpan di laci",
        "angle": "impulsive buying yang sering disesali",
        "format": "one_liner",
    },
    {
        "pillar": "relatable",
        "audience_problem": "Notifikasi grup chat kantor/kuliah bikin fokus buyar tiap lima menit",
        "angle": "kelelahan digital yang dinormalisasi",
        "format": "short_paragraph",
    },
    # ── Opinions (4) ──
    {
        "pillar": "opinions",
        "audience_problem": "Spesifikasi RAM 16GB di HP murah sering cuma gimmick swap memory virtual",
        "angle": "klaim marketing vs realita performa harian",
        "format": "short_paragraph",
    },
    {
        "pillar": "opinions",
        "audience_problem": "Diskon besar 70% tanggal kembar sering cuma harga normal yang di-markup dulu",
        "angle": "trik harga coret yang manipulatif",
        "format": "short_paragraph",
    },
    {
        "pillar": "opinions",
        "audience_problem": "Review gadget bersponsor yang terlalu rapi tanpa kekurangan satu pun",
        "angle": "review jujur vs promosi berbayar",
        "format": "short_paragraph",
    },
    {
        "pillar": "opinions",
        "audience_problem": "Upgrade HP tiap tahun padahal perubahannya cuma modul kamera dan warna",
        "angle": "stagnasi inovasi teknologi smartphone",
        "format": "one_liner",
    },
    # ── Practical Tips (4) ──
    {
        "pillar": "practical_tips",
        "audience_problem": "Nggak tahu cara cek apakah charger dan kabel beneran fast charging",
        "angle": "indikator visual di layar yang sering diabaikan",
        "format": "short_paragraph",
    },
    {
        "pillar": "practical_tips",
        "audience_problem": "Kapasitas mAh di kotak power bank bukan daya nyata yang masuk ke HP",
        "angle": "rumus efisiensi konversi daya dan voltase",
        "format": "short_paragraph",
    },
    {
        "pillar": "practical_tips",
        "audience_problem": "Kabel USB-C murah sering cuma bisa isi daya, nggak bisa transfer file",
        "angle": "beda kabel charge-only vs kabel data",
        "format": "short_paragraph",
    },
    {
        "pillar": "practical_tips",
        "audience_problem": "Baterai HP cepat drop karena aplikasi background aktif terus",
        "angle": "cara batasi background activity tanpa root",
        "format": "short_paragraph",
    },
    # ── Questions (4) ──
    {
        "pillar": "questions",
        "audience_problem": "Kenapa charger 20W kadang terasa lebih panas dibanding charger laptop 65W?",
        "angle": "penjelasan ukuran bodi vs disipasi panas secara sederhana",
        "format": "short_paragraph",
    },
    {
        "pillar": "questions",
        "audience_problem": "Beneran ngaruh nggak sih matiin HP pas dicas ke keawetan baterai?",
        "angle": "mitos kebiasaan mengecas vs cara kerja BMS modern",
        "format": "short_paragraph",
    },
    {
        "pillar": "questions",
        "audience_problem": "Kenapa colokan USB-C di monitor kadang nggak ngisi daya laptop padahal kabel sama?",
        "angle": "power delivery wattage requirement yang kurang",
        "format": "short_paragraph",
    },
    {
        "pillar": "questions",
        "audience_problem": "Apakah refresh rate 120Hz beneran bikin baterai HP drop drastis dibanding 60Hz?",
        "angle": "efek refresh rate adaptif ke konsumsi daya",
        "format": "short_paragraph",
    },
    # ── Curiosity & Gimmick (4) ──
    {
        "pillar": "curiosity_gimmick",
        "audience_problem": "Fitur 'AI' di colokan listrik atau kipas angin yang sebenarnya cuma timer biasa",
        "angle": "label AI yang ditempel ke semua alat elektronik murah",
        "format": "short_paragraph",
    },
    {
        "pillar": "curiosity_gimmick",
        "audience_problem": "Gimmick kabel berlampu LED running yang diklaim bisa 'menstabilkan arus listrik'",
        "angle": "lampu hiasan vs fungsi kelistrikan nyata",
        "format": "short_paragraph",
    },
    {
        "pillar": "curiosity_gimmick",
        "audience_problem": "Stiker penangkal radiasi HP yang dijual mahal padahal cuma kertas hologram",
        "angle": "jualan ketakutan tanpa dasar sains",
        "format": "one_liner",
    },
    {
        "pillar": "curiosity_gimmick",
        "audience_problem": "Cooling pad laptop dengan 6 kipas dan RGB heboh tapi suhunya cuma turun 1 derajat",
        "angle": "aliran udara tertahan bodi laptop plastik",
        "format": "short_paragraph",
    },
]


def _get_live_client():
    """Return configured client or active 9router client if primary fails."""
    # Test primary
    try:
        primary = HttpLLMClient()
        primary.complete([{"role": "user", "content": "ping"}], max_tokens=5)
        print("Using primary LLM endpoint:", primary.config.base_url, f"({primary.config.model})")
        return primary
    except Exception as e:
        print(f"Primary endpoint unreachable/expired ({e}). Falling back to local 9router gateway...")
        api_key = (
            os.getenv("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
            or os.getenv("LLM_API_KEY")
            or "local-secret"
        )
        cfg = LLMConfig(
            base_url="http://localhost:20128/v1",
            model="opusantigravity",
            fallback_models=["opusantigravity"],
            api_key=api_key,
            timeout_seconds=60,
        )
        fallback = HttpLLMClient(config=cfg)
        fallback.complete([{"role": "user", "content": "ping"}], max_tokens=5)
        print("Using 9Router LLM endpoint: http://localhost:20128/v1 (opusantigravity)")
        return fallback


def _process_one_draft(idx: int, spec: dict, client: HttpLLMClient, base_data_dir: Path) -> dict:
    db_path = base_data_dir / f"eval_worker_{idx}.db"
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass
    migrate(db_path)

    pillar = spec["pillar"]
    problem = spec["audience_problem"]
    angle = spec["angle"]
    fmt = spec["format"]
    print(f"[{idx}/20 START] Pillar: {pillar} | Angle: {angle}")

    idea = IdeaCandidate(
        pillar=pillar,
        angle=angle,
        audience_problem=problem,
    )

    t_start = time.time()
    # Stage 1: Generate initial draft
    draft = write_draft(
        pillar=pillar,
        idea=idea,
        client=client,
        path=db_path,
        persist=True,
    )

    if draft is None:
        print(f"[{idx}/20 ERROR] write_draft returned None")
        return {
            "index": idx,
            "pillar": pillar,
            "audience_problem": problem,
            "angle": angle,
            "format": fmt,
            "original_draft": "",
            "cleaned_draft": "",
            "editorial_edits": [],
            "had_editorial_changes": False,
            "stage1_evaluation": {"score": 0, "verdict": REJECTED, "findings": [], "concerns": [], "notes": "Failed generation"},
            "rewritten_draft": None,
            "final_evaluation": {"content": "", "score": 0, "verdict": REJECTED, "findings": [], "concerns": [], "notes": "Failed generation"},
            "duration_seconds": round(time.time() - t_start, 2),
        }

    orig_content = draft.raw_content or draft.content
    cleaned_content = draft.content
    applied_edits = getattr(draft, "applied_edits", [])

    # Stage 2: Evaluate initial draft
    eval1 = evaluate_draft(
        cleaned_content,
        parts=draft.parts,
        pillar=pillar,
        draft_id=draft.draft_id,
        client=client,
        path=db_path,
        persist=True,
        format_name=fmt,
    )

    findings1 = [
        asdict(f) if hasattr(f, "__dataclass_fields__") else f
        for f in getattr(eval1, "findings", [])
    ]
    concerns1 = getattr(eval1, "concerns", [])
    notes1 = getattr(eval1, "notes", "")
    score1 = getattr(eval1, "total_score", 0.0)
    verdict1 = getattr(eval1, "verdict", REJECTED)

    print(f"[{idx}/20 STAGE1] Verdict: {verdict1} (Score: {score1}) | Findings: {len(findings1)}")

    # Stage 3: Rewrite loop if REVISE_ONCE
    rewritten_draft = None
    final_verdict = verdict1
    final_score = score1
    final_findings = findings1
    final_concerns = concerns1
    final_notes = notes1
    final_content = cleaned_content

    if verdict1 == REVISE_ONCE:
        print(f"[{idx}/20 REWRITE] Triggering rewrite loop...")
        feedback_items = []
        for f in findings1:
            sev = f.get("severity", "")
            code = f.get("code", "")
            ev = f.get("evidence", "")
            fix = f.get("suggested_fix", "")
            feedback_items.append(f"[{sev}] {code}: '{ev}' -> Saran: {fix}")
        if not feedback_items and concerns1:
            feedback_items.extend(concerns1)
        feedback_str = "\n".join(feedback_items)

        draft2 = write_draft(
            pillar=pillar,
            idea=idea,
            client=client,
            revision_feedback=feedback_str,
            path=db_path,
            persist=True,
        )
        if draft2:
            rewritten_draft = {
                "raw_content": draft2.raw_content or draft2.content,
                "cleaned_content": draft2.content,
                "applied_edits": getattr(draft2, "applied_edits", []),
            }
            eval2 = evaluate_draft(
                draft2.content,
                parts=draft2.parts,
                pillar=pillar,
                draft_id=draft2.draft_id,
                client=client,
                path=db_path,
                persist=True,
                format_name=fmt,
            )
            final_verdict = getattr(eval2, "verdict", REJECTED)
            final_score = getattr(eval2, "total_score", 0.0)
            final_findings = [
                asdict(f) if hasattr(f, "__dataclass_fields__") else f
                for f in getattr(eval2, "findings", [])
            ]
            final_concerns = getattr(eval2, "concerns", [])
            final_notes = getattr(eval2, "notes", "")
            final_content = draft2.content
            print(f"[{idx}/20 REWRITE DONE] Verdict: {final_verdict} (Score: {final_score})")

    duration = round(time.time() - t_start, 2)
    print(f"[{idx}/20 COMPLETED] in {duration}s -> {final_verdict} ({final_score})")

    return {
        "index": idx,
        "pillar": pillar,
        "audience_problem": problem,
        "angle": angle,
        "format": fmt,
        "original_draft": orig_content,
        "cleaned_draft": cleaned_content,
        "editorial_edits": applied_edits,
        "had_editorial_changes": (orig_content != cleaned_content),
        "stage1_evaluation": {
            "score": score1,
            "verdict": verdict1,
            "findings": findings1,
            "concerns": concerns1,
            "notes": notes1,
        },
        "rewritten_draft": rewritten_draft,
        "final_evaluation": {
            "content": final_content,
            "score": final_score,
            "verdict": final_verdict,
            "findings": final_findings,
            "concerns": final_concerns,
            "notes": final_notes,
        },
        "duration_seconds": duration,
    }


def run_20_drafts_generation() -> list[dict]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    base_data_dir = REPO_ROOT / "data" / "workers_eval"
    base_data_dir.mkdir(parents=True, exist_ok=True)

    client = _get_live_client()
    results = []

    print(f"\nStarting parallel evaluation of {len(DRAFT_SPECIFICATIONS)} drafts across 5 pillars (max_workers=5)...")
    t0_all = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_process_one_draft, idx, spec, client, base_data_dir): idx
            for idx, spec in enumerate(DRAFT_SPECIFICATIONS, 1)
        }
        for fut in as_completed(futures):
            try:
                rec = fut.result()
                results.append(rec)
            except Exception as exc:
                idx = futures[fut]
                print(f"[ERROR] Draft #{idx} failed: {exc}")

    results.sort(key=lambda r: r["index"])
    total_time = round(time.time() - t0_all, 2)
    print(f"\nAll 20 drafts finished in {total_time}s.")
    return results


def generate_markdown_report(results: list[dict], out_path: Path):
    total = len(results)
    verdict_counts = {}
    severity_counts = {"BLOCKING": 0, "MAJOR": 0, "MINOR": 0}
    scores = []
    editorial_changes_count = 0
    rewritten_count = 0

    for r in results:
        fe = r["final_evaluation"]
        v = fe["verdict"]
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        scores.append(fe["score"])
        if r["had_editorial_changes"]:
            editorial_changes_count += 1
        if r["rewritten_draft"] is not None:
            rewritten_count += 1
        for f in fe["findings"]:
            sev = f.get("severity")
            if sev in severity_counts:
                severity_counts[sev] += 1

    avg_score = round(sum(scores) / len(scores), 2) if scores else 0.0

    lines = [
        "# V2 Editorial Quality System — 20 Live Drafts Evaluation Report",
        "",
        "## Executive Summary",
        "",
        f"- **Total Drafts Evaluated**: {total}",
        f"- **Average Quality Gate Score**: {avg_score} / 100",
        f"- **Drafts with Editorial Pre-Processing Changes**: {editorial_changes_count} / {total}",
        f"- **Drafts Triggering Rewrite Loop (REVISE_ONCE)**: {rewritten_count} / {total}",
        "",
        "### Verdict Breakdown",
        "",
        "| Verdict | Count | Share | Description |",
        "|---|---|---|---|",
        f"| `APPROVED_AUTO` | {verdict_counts.get(APPROVED_AUTO, 0)} | {round(verdict_counts.get(APPROVED_AUTO, 0)/total*100, 1)}% | Eligible for direct publishing without human review |",
        f"| `NEEDS_REVIEW` | {verdict_counts.get(NEEDS_REVIEW, 0)} | {round(verdict_counts.get(NEEDS_REVIEW, 0)/total*100, 1)}% | Held in queue, requires operator `/approve` command |",
        f"| `REVISE_ONCE` | {verdict_counts.get(REVISE_ONCE, 0)} | {round(verdict_counts.get(REVISE_ONCE, 0)/total*100, 1)}% | Flagged with major issues, returned for one rewrite pass |",
        f"| `REJECTED` | {verdict_counts.get(REJECTED, 0)} | {round(verdict_counts.get(REJECTED, 0)/total*100, 1)}% | Vetoed / blocking defects, permanently dropped |",
        "",
        "### Critic Finding Severity Distribution",
        "",
        f"- **BLOCKING**: {severity_counts['BLOCKING']} (factual errors, safety violations, forced metaphors, thread padding)",
        f"- **MAJOR**: {severity_counts['MAJOR']} (spelling typos, format mismatches, awkward phrasing, unnatural voice)",
        f"- **MINOR**: {severity_counts['MINOR']} (slight wordiness, mild formality)",
        "",
        "---",
        "",
        "## Editorial Changes & Rewrite Showcase",
        "",
    ]

    # Showcase changes
    changed = [r for r in results if r["had_editorial_changes"] or r["rewritten_draft"]]
    if not changed:
        lines.append("*All generated drafts matched clean editorial formatting on first pass.*")
    else:
        for r in changed:
            idx = r["index"]
            pillar = r["pillar"]
            lines.extend([
                f"### Case #{idx}: [{pillar.upper()}] {r['angle']}",
                "",
                f"- **Premise**: {r['audience_problem']}",
                f"- **Declared Format**: `{r['format']}`",
                "",
                "#### Text Comparison",
                "",
                "| Version | Content |",
                "|---|---|",
                f"| **Original (Model Raw)** | {r['original_draft']} |",
                f"| **Cleaned (Editorial Pass)** | {r['cleaned_draft']} |",
            ])
            if r["rewritten_draft"]:
                lines.append(f"| **Rewritten (After Critic Feedback)** | {r['rewritten_draft']['cleaned_content']} |")
            lines.extend([
                "",
                f"- **Final Verdict**: `{r['final_evaluation']['verdict']}` (Score: {r['final_evaluation']['score']})",
                f"- **Critic Notes**: {r['final_evaluation']['notes']}",
                "",
            ])

    lines.extend([
        "---",
        "",
        "## Complete Dossier of 20 Drafts",
        "",
    ])

    for r in results:
        idx = r["index"]
        pillar = r["pillar"]
        fe = r["final_evaluation"]
        se1 = r["stage1_evaluation"]
        lines.extend([
            f"### #{idx}. [{pillar.upper()}] {r['angle']}",
            "",
            f"- **Problem / Premise**: {r['audience_problem']}",
            f"- **Format**: `{r['format']}`",
            f"- **Stage 1**: Verdict `{se1['verdict']}` | Score `{se1['score']}`",
            f"- **Final**: Verdict `{fe['verdict']}` | Score `{fe['score']}`",
            "",
            "**Final Content**:",
            f"> {fe['content']}",
            "",
            "**Critic Notes**:",
            f"> {fe['notes']}",
            "",
        ])

        if fe["findings"]:
            lines.append("**Structured Findings**:")
            lines.append("")
            lines.append("| Severity | Code | Evidence | Suggested Fix |")
            lines.append("|---|---|---|---|")
            for f in fe["findings"]:
                lines.append(f"| `{f.get('severity')}` | `{f.get('code')}` | {f.get('evidence')} | {f.get('suggested_fix')} |")
            lines.append("")
        else:
            lines.append("*No critic defects flagged.*")
            lines.append("")

        lines.append("---")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Saved Markdown report to {out_path}")


def main():
    results = run_20_drafts_generation()
    out_json = REPO_ROOT / "docs" / "v2" / "editorial_overhaul_20_drafts.json"
    out_md = REPO_ROOT / "docs" / "v2" / "editorial_overhaul_20_drafts.md"
    out_json.parent.mkdir(parents=True, exist_ok=True)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Saved JSON results to {out_json}")

    generate_markdown_report(results, out_md)


if __name__ == "__main__":
    main()
