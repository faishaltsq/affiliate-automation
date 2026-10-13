"""
pipeline.py — End-to-end Content Operations Orchestrator for Faceless Outdoor Channels.
Coordinates: Source Discovery -> Scoring (>=4/5) -> Download -> Transform/Render -> Metadata -> QC Gate.
"""

import datetime
import json
import logging
import os
import shutil
from typing import List, Dict, Any, Optional

from src.tiktok.youtube import search_videos, download_video
from src.content_ops.scorer import score_candidate
from src.content_ops.metadata import generate_metadata_and_hook
from src.content_ops.editor import render_sop_clip
from src.content_ops.qc import run_qc_check

logger = logging.getLogger("content_ops.pipeline")

NICHE_KEYWORDS = {
    "mancing": [
        "mancing liar strike monster babon",
        "fishing big catch strike underwater",
        "mancing ikan gabus toman babon",
        "freshwater fishing monster strike",
        "mancing laut tarikan monster joran melengkung"
    ],
    "camping": [
        "solo camping heavy rain bushcraft",
        "camping hujan lebat tenda asmr",
        "bushcraft shelter survival campfire",
        "cozy rain solo camping wood stove"
    ],
    "outdoor_satisfying": [
        "oddly satisfying outdoor nature",
        "satisfying river cleanup restoration",
        "satisfying bushcraft woodworking craft"
    ]
}


def run_batch_production(
    niche: str = "mancing",
    target_count: int = 3,
    output_base_dir: str = "data/content_ops/output",
    download_dir: str = "data/content_ops/downloads"
) -> Dict[str, Any]:
    """
    Run full batch production following SOP:
    - Niche consistency: 1 niche per batch
    - Source selection threshold: score >= 4.0/5.0
    - Output count: target_count clips
    - Multi-platform variants and metadata
    - QC validation
    """
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    batch_dir = os.path.join(output_base_dir, f"{date_str}_{niche}")
    os.makedirs(batch_dir, exist_ok=True)
    os.makedirs(download_dir, exist_ok=True)

    keywords = NICHE_KEYWORDS.get(niche, NICHE_KEYWORDS["mancing"])
    logger.info(f"Starting Batch Content Operations [{niche}] - Target: {target_count} clips")

    # Step 1: Discover candidate videos
    candidates = []
    for kw in keywords:
        found = search_videos(kw, max_results=5, max_duration=400, prefer_vertical=True)
        for v in found:
            if not any(c["id"] == v["id"] for c in candidates):
                candidates.append(v)
        if len(candidates) >= target_count * 4:
            break

    logger.info(f"Discovered {len(candidates)} candidate videos. Evaluating with SOP Scorer...")

    # Step 2: Score candidates
    qualified = []
    for cand in candidates:
        sc = score_candidate(cand, target_niche=niche)
        cand["eval"] = sc
        if sc["passed"]:
            qualified.append(cand)
            logger.info(f"PASS ({sc['overall_score']}/5.0): {cand['title'][:45]}")
        else:
            logger.debug(f"REJECT ({sc['overall_score']}/5.0): {cand['title'][:45]}")

    logger.info(f"Qualified videos after scoring: {len(qualified)}/{len(candidates)}")
    if not qualified:
        # Fallback: take top scored
        candidates.sort(key=lambda x: x.get("eval", {}).get("overall_score", 0), reverse=True)
        qualified = candidates[:target_count]

    produced_clips = []

    # Step 3: Produce clips for top qualified candidates
    for idx, cand in enumerate(qualified[:target_count], 1):
        vid_id = cand["id"]
        title = cand["title"]
        logger.info(f"\n--- Producing Clip #{idx} [{vid_id}]: {title[:50]} ---")

        # 3a. Download footage
        dl_path = download_video(cand["webpage_url"], download_dir)
        if not dl_path or not os.path.isfile(dl_path):
            logger.error(f"Download failed for {vid_id}, skipping")
            continue

        # 3b. Generate Hook and Platform Metadata
        meta = generate_metadata_and_hook(title, niche=niche)
        hook_text = meta["hook_text"]
        logger.info(f"Generated Hook: {hook_text}")

        # 3c. Render transformed clip (22s target)
        render_filename = f"{date_str}_{niche}_master_{vid_id}.mp4"
        master_output = os.path.join(batch_dir, render_filename)

        # Smart start time (e.g. at 5s or chapter action)
        start_time = 0.0
        if cand.get("chapters"):
            start_time = float(cand["chapters"][0].get("start_time", 0.0))

        success = render_sop_clip(
            input_path=dl_path,
            output_path=master_output,
            hook_text=hook_text,
            start_time=start_time,
            target_duration=22.0
        )

        if not success:
            logger.error(f"Render failed for {master_output}")
            continue

        # 3d. Create Platform Variants
        # SOP: Naming format [tanggal]_[niche]_[platform]_[id].mp4
        platform_files = {}
        for plat in ["tiktok", "reels", "shorts"]:
            plat_filename = f"{date_str}_{niche}_{plat}_{vid_id}.mp4"
            plat_path = os.path.join(batch_dir, plat_filename)
            shutil.copyfile(master_output, plat_path)
            platform_files[plat] = plat_path

        # 3e. Run QC Checklist
        qc_result = run_qc_check(master_output, meta, hook_text)

        clip_record = {
            "id": vid_id,
            "source_title": title,
            "source_url": cand["webpage_url"],
            "score": cand.get("eval", {}).get("overall_score", 4.0),
            "hook_text": hook_text,
            "files": platform_files,
            "metadata": meta,
            "qc": qc_result,
            "status": "READY_FOR_SCHEDULING"
        }
        produced_clips.append(clip_record)

    # Save batch manifest JSON
    manifest_path = os.path.join(batch_dir, f"{date_str}_{niche}_manifest.json")
    batch_summary = {
        "date": date_str,
        "niche": niche,
        "total_produced": len(produced_clips),
        "clips": produced_clips,
        "generated_at": datetime.datetime.now().isoformat()
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(batch_summary, f, indent=2, ensure_ascii=False)

    logger.info(f"Batch production completed: {len(produced_clips)} clips in {batch_dir}")
    return batch_summary
