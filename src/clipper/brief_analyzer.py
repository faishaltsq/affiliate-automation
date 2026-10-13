"""brief_analyzer.py — AI-powered brief analysis & script generation for Clippo campaigns."""

import os
import re
import json
import logging
from typing import Optional
import requests

logger = logging.getLogger("clipper.brief_analyzer")
LLM_URL = os.environ.get("OPENROUTER_BASE_URL", "http://localhost:20128/v1/chat/completions")
MODEL = os.environ.get("OPENROUTER_MODEL", "ag/gemini-3.8-flash-high")


def analyze_campaign_brief(campaign_detail: dict) -> dict:
    """Analyze a campaign detail object from Clippo and produce structured clipping guidance."""
    title = campaign_detail.get("title", "")
    dos = campaign_detail.get("dos", [])
    donts = campaign_detail.get("donts", [])
    hooks_raw = campaign_detail.get("hooks", "")
    main_focus = campaign_detail.get("main_focus", "")
    cta = campaign_detail.get("cta", "")
    hashtags = campaign_detail.get("hashtags", [])
    assets = campaign_detail.get("assets", [])

    dos_text = "\n".join(f"- {d}" for d in dos)
    donts_text = "\n".join(f"- {d}" for d in donts)

    system_prompt = (
        "Kamu adalah Senior Viral Content Strategist & Video Clipper expert TikTok & Shorts. "
        "Analisis brief campaign berikut dan hasilkan JSON valid dengan format persis:\n"
        "{\n"
        '  "summary": "Ringkasan pesan utama campaign dalam 1-2 kalimat padat",\n'
        '  "winning_hooks": ["3 ide hook teks/suara pembuka 0-3 detik yang paling memicu rasa penasaran"],\n'
        '  "key_requirements": ["3-5 poin syarat mutlak agar clip lolos review & dibayar"],\n'
        '  "forbidden_items": ["3-5 hal terlarang yang menyebabkan clip ditolak/banned"],\n'
        '  "cta_text": "Teks ajakan aksi di akhir video (5-10 detik)",\n'
        '  "caption_recommendation": "Rekomendasi caption lengkap beserta hashtag wajib"\n'
        "}\n"
        "Hanya outputkan JSON valid tanpa backticks markdown."
    )

    user_prompt = (
        f"Campaign: {title}\n"
        f"Fokus Utama: {main_focus}\n"
        f"Hook dari Brand: {hooks_raw}\n"
        f"Syarat Wajib (Do's):\n{dos_text or 'Tidak ada data spesifik'}\n"
        f"Larangan (Don'ts):\n{donts_text or 'Tidak ada data spesifik'}\n"
        f"CTA Wajib: {cta or 'Cek link di bio'}\n"
        f"Hashtags Wajib: {' '.join(hashtags)}\n"
    )

    analysis_res = {
        "summary": f"Campaign {title}",
        "winning_hooks": [hooks_raw] if hooks_raw else [f"Wajib tahu ini tentang {title}!"],
        "key_requirements": dos[:5] if dos else ["Gunakan materi yang disediakan"],
        "forbidden_items": donts[:5] if donts else ["Dilarang reupload tanpa edit"],
        "cta_text": cta or "Cek sekarang juga!",
        "caption_recommendation": f"{title} | {' '.join(hashtags)}",
    }

    try:
        r = requests.post(
            LLM_URL,
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "temperature": 0.3,
            },
            timeout=18,
        )
        if r.ok:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            parsed = json.loads(content)
            analysis_res.update(parsed)
    except Exception as e:
        logger.warning("LLM brief analysis fallback: %s", e)

    return {
        "campaign_id": campaign_detail.get("id"),
        "title": title,
        "cpm": campaign_detail.get("cpm", 0),
        "budget_percentage": campaign_detail.get("budget_percentage", 100),
        "min_duration": campaign_detail.get("min_duration", 15),
        "max_duration": campaign_detail.get("max_duration", 180),
        "hashtags": hashtags,
        "assets": assets,
        **analysis_res,
    }
