"""brief_analyzer.py — AI-powered brief analysis & script generation for Clippo campaigns."""

import os
import re
import json
import logging
from pathlib import Path
from typing import Optional
import requests
from dotenv import dotenv_values

logger = logging.getLogger("clipper.brief_analyzer")

HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))
_h_env = dotenv_values(Path(HERMES_HOME) / ".env")
_p_env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
_ALL = {**_h_env, **_p_env}

LLM_URL = os.environ.get("OPENROUTER_BASE_URL", "http://localhost:20128/v1/chat/completions")
MODEL = os.environ.get("OPENROUTER_MODEL", "ag/gemini-3.8-flash-high")
FALLBACK_MODEL = None  # retired: fallback is now Mortera Cloud direct (see below)
MORTERA_URL = (
    os.environ.get("MORTERA_BASE_URL") or _ALL.get("MORTERA_BASE_URL", "https://mortera.cloud/v1")
).rstrip("/") + "/chat/completions"
MORTERA_MODEL = os.environ.get("MORTERA_MODEL") or _ALL.get("MORTERA_MODEL", "muse-spark-1.3")
MORTERA_API_KEY = os.environ.get("MORTERA_API_KEY") or _ALL.get("MORTERA_API_KEY") or ""
LLM_API_KEY = (
    os.environ.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or _ALL.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or ""
)


def _extract_first_hook(hooks_raw: str) -> str:
    """Extract the first clean hook line from raw campaign hook text."""
    if not hooks_raw:
        return ""
    # Try to find quoted hooks first
    quoted = re.findall(r'["\u201c\u201d]([^"\u201c\u201d]{10,})["\u201c\u201d]', hooks_raw)
    if quoted:
        return quoted[0].strip()
    # Otherwise take the first meaningful line
    for line in hooks_raw.split("\n"):
        line = line.strip()
        line = re.sub(r'^[\d\)\.\-\*]+\s*', '', line)  # strip numbering
        if len(line) > 10 and not line.lower().startswith(("bisa digunakan", "hook", "angle", "dll", "winning")):
            return line.strip('"\'')
    return hooks_raw.split("\n")[0][:80]


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
        "winning_hooks": [_extract_first_hook(hooks_raw)] if hooks_raw else [f"Wajib tahu ini tentang {title}!"],
        "key_requirements": dos[:5] if dos else ["Gunakan materi yang disediakan"],
        "forbidden_items": donts[:5] if donts else ["Dilarang reupload tanpa edit"],
        "cta_text": cta or "Cek sekarang juga!",
        "caption_recommendation": f"{title}\n\n{' '.join(hashtags)}",
    }

    try:
        headers = {"Authorization": f"Bearer {LLM_API_KEY}"} if LLM_API_KEY else {}
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "temperature": 0.3,
        }
        r = None
        # 1. Primary: 9router
        r = requests.post(LLM_URL, json=payload, headers=headers, timeout=18)
        if not r.ok:
            logger.warning("LLM brief analysis (%s) HTTP %s, trying Mortera fallback...", MODEL, r.status_code)
            # 2. Fallback: Mortera Cloud direct (Muse Spark)
            if MORTERA_API_KEY:
                payload["model"] = MORTERA_MODEL
                m_headers = {"Authorization": f"Bearer {MORTERA_API_KEY}"}
                r = requests.post(MORTERA_URL, json=payload, headers=m_headers, timeout=45)
            else:
                logger.warning("Mortera fallback skipped: MORTERA_API_KEY missing")
        if r is not None and r.ok:
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = re.sub(r"^```(?:json)?\s*", "", content)
                content = re.sub(r"\s*```$", "", content)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError:
                from src.clipper.punchline import _parse_json_loose
                parsed = _parse_json_loose(content)
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
