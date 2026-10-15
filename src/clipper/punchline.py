import os
import json
import logging
import tempfile
import subprocess
import requests
from typing import List, Dict, Optional

from pathlib import Path
from dotenv import dotenv_values

logger = logging.getLogger("clipper.punchline")

HERMES_HOME = os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes"))
_h_env = dotenv_values(Path(HERMES_HOME) / ".env")
_p_env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
_ALL = {**_h_env, **_p_env}

LLM_ENDPOINT = os.environ.get("OPENROUTER_BASE_URL") or _ALL.get("OPENROUTER_BASE_URL", "http://localhost:20128/v1/chat/completions")
LLM_API_KEY = (
    os.environ.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or _ALL.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or ""
)
LLM_MODEL = os.environ.get("OPENROUTER_MODEL") or _ALL.get("OPENROUTER_MODEL", "ag/gemini-3.8-flash-high")


def transcribe_footage_clips(video_paths: List[str], max_sample_sec: float = 25.0) -> List[Dict]:
    """
    Transcribe the first `max_sample_sec` seconds of candidate footage clips using Whisper.
    Returns list of dicts: [{"file": path, "start": float, "end": float, "text": str}]
    """
    try:
        from src.tiktok.subtitles import _get_whisper_model
        model = _get_whisper_model()
    except Exception as e:
        logger.warning(f"Could not load whisper model for footage transcription: {e}")
        return []

    results = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for vp in video_paths:
            if not os.path.exists(vp):
                continue
            audio_path = os.path.join(tmp_dir, f"audio_{os.path.basename(vp)}.wav")
            # Extract first max_sample_sec seconds of audio
            cmd = [
                "ffmpeg", "-y",
                "-i", vp,
                "-t", f"{max_sample_sec:.2f}",
                "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                audio_path
            ]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
                    continue

                segments, _ = model.transcribe(audio_path, language="id", beam_size=1)
                for seg in segments:
                    txt = seg.text.strip()
                    if txt:
                        results.append({
                            "file": vp,
                            "filename": os.path.basename(vp),
                            "start": round(seg.start, 2),
                            "end": round(seg.end, 2),
                            "text": txt,
                        })
            except Exception as e:
                logger.debug(f"Failed to transcribe {vp}: {e}")
                continue

    return results


def _query_punchline_llm(
    campaign_title: str,
    transcripts: List[Dict],
    brief: Optional[Dict] = None,
) -> Optional[Dict]:
    """Query LLM to identify the exact punchline/climax moment from footage transcripts."""
    if not LLM_API_KEY:
        logger.warning("HERMES_CUSTOM_LOCALHOST_20128_API_KEY not set for punchline LLM query.")
        return None

    import requests

    # Format transcript items for the prompt
    formatted_lines = []
    for t in transcripts:
        fn = t.get("filename", os.path.basename(t.get("file", "")))
        formatted_lines.append(f"[{fn}] ({t['start']}s - {t['end']}s): \"{t['text']}\"")
    transcript_summary = "\n".join(formatted_lines[:50])

    guidelines_text = ""
    if brief:
        dos = brief.get("dos", [])
        donts = brief.get("donts", [])
        focus = brief.get("main_focus", "")
        guidelines_text = f"""
Pedoman Campaign (Wajib Dipatuhi):
- Do's: {', '.join(dos) if dos else '-'}
- Don'ts: {', '.join(donts) if donts else '-'}
- Main Focus: {focus or '-'}
"""

    prompt = f"""Kamu adalah Video Editor & Hook Specialist untuk TikTok & Reels.
Tugasmu: Dari transkrip bahan footage berikut, temukan momen PUNCHLINE / KLIMAKS paling menarik, wow, atau krusial yang benar-benar mewakili isi video secara faktual.

Judul Campaign: "{campaign_title}"
{guidelines_text}
Transkrip Bahan Video:
{transcript_summary}

Aturan Mutlak (Compliance & Factual Integrity):
1. FAKTUIL & RELEVAN: Hook teks dan momen cuplikan HARUS 100% sesuai dengan apa yang benar-benar diucapkan dan terjadi di transkrip. Jangan berhalusinasi atau berasumsi.
2. PATUHI GUIDELINES CAMPAIGN:
   - Jika brief meminta edukasi/positif atau melarang kata-kata tipu/menyerang, DILARANG membuat hook sensasional negatif (seperti 'emas palsu', 'nangkep penipu', 'surat bermasalah') bila customer di video sebenarnya puas / jujur.
   - Angkat angle nilai tinggi, keuntungan customer (misal: naik 2x lipat), transparansi timbangan/kadar, atau edukasi tanpa potongan.
3. Pilih 1 momen klimaks dengan durasi 3.0 sampai 4.0 detik yang paling cocok dijadikan 'COLD OPEN / HOOK TEASER' di detik 0 video.
4. Buat hook text singkat (maksimal 7 kata, ALL CAPS) yang memancing rasa penasaran penonton.
5. Output HANYA JSON murni tanpa markdown/backticks, dengan struktur berikut:
{{
    "target_filename": "nama_file_yang_dipilih.mp4",
    "climax_start": 10.5,
    "climax_duration": 3.5,
    "punchline_hook": "JUAL EMAS DI SINI MALAH UNTUNG 2X LIPAT?!",
    "reason": "Momen Juan mengungkap perhitungan harga naik 2 kali lipat"
}}"""

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": False,
    }

    try:
        resp = requests.post(
            LLM_ENDPOINT,
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=25,
        )
        if resp.status_code != 200:
            logger.error(f"LLM punchline query failed HTTP {resp.status_code}: {resp.text}")
            return None

        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(content)
        # Find matching full file path
        target_fn = data.get("target_filename", "")
        for t in transcripts:
            if target_fn and (target_fn in t.get("file", "") or target_fn in t.get("filename", "")):
                data["target_file"] = t["file"]
                break

        if "target_file" not in data and transcripts:
            data["target_file"] = transcripts[0]["file"]

        return data
    except Exception as e:
        logger.error(f"Error querying punchline LLM: {e}")
        return None


def select_climax_punchline(
    campaign_title: str,
    transcripts: List[Dict],
    brief: Optional[Dict] = None,
) -> Optional[Dict]:
    """Select the best climax punchline from footage transcripts with campaign compliance."""
    if not transcripts:
        return None

    res = _query_punchline_llm(campaign_title, transcripts, brief=brief)
    if res and res.get("target_file"):
        return res

    # Fallback heuristic if LLM query fails
    first_item = transcripts[0]
    return {
        "target_file": first_item["file"],
        "climax_start": first_item.get("start", 0.0),
        "climax_duration": 3.5,
        "punchline_hook": "JUAL EMAS DI SINI MALAH UNTUNG 2X LIPAT?!",
        "reason": "Fallback to first transcript dialogue snippet.",
    }


def select_multiple_climax_punchlines(
    campaign_title: str,
    transcripts: List[Dict],
    count: int = 2,
    brief: Optional[Dict] = None,
) -> List[Dict]:
    """Select count distinct climax punchlines from footage transcripts."""
    if not transcripts:
        return []

    count = max(1, min(count, 5))
    if count == 1:
        single = select_climax_punchline(campaign_title, transcripts, brief=brief)
        return [single] if single else []

    # Prompt LLM for list of distinct angles
    if LLM_API_KEY:
        formatted_lines = []
        for t in transcripts:
            fn = t.get("filename", os.path.basename(t.get("file", "")))
            formatted_lines.append(f"[{fn}] ({t['start']}s - {t['end']}s): \"{t['text']}\"")
        transcript_summary = "\n".join(formatted_lines[:60])

        guidelines_text = ""
        if brief:
            dos = brief.get("dos", [])
            donts = brief.get("donts", [])
            guidelines_text = f"Do's: {', '.join(dos)}\nDon'ts: {', '.join(donts)}"

        prompt = f"""Kamu adalah Video Editor & Hook Specialist untuk TikTok & Reels.
Tugasmu: Dari transkrip bahan footage berikut, pilih {count} MOMEN PUNCHLINE / SUDUT PANDANG (ANGLES) BERBEDA untuk dibuat menjadi {count} video klip terpisah dengan HOOK yang BERBEDA-BEDA.

Judul Campaign: "{campaign_title}"
{guidelines_text}

Transkrip Bahan Video:
{transcript_summary}

Aturan:
1. Setiap video HARUS memiliki sudut pandang/topik yang berbeda (misal Video 1: keuntungan naik 2x, Video 2: emas tanpa surat/warisan, Video 3: potongan toko vs tanpa potongan).
2. Jika ada beberapa file footage berbeda, prioritaskan memilih footage file yang berbeda untuk tiap angle!
3. Hook singkat (maks 7 kata, ALL CAPS), faktual, dan patuhi brief (edukasi, jangan tuduh palsu/penipu bila di video tidak ada).
4. Output HANYA JSON array murni tanpa markdown:
[
  {{
    "target_filename": "nama_file.mp4",
    "climax_start": 10.5,
    "climax_duration": 3.5,
    "punchline_hook": "HOOK 1",
    "reason": "Alasan sudut pandang 1"
  }},
  ...
]"""
        try:
            resp = requests.post(
                LLM_ENDPOINT,
                headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                json={"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0.3, "stream": False},
                timeout=30,
            )
            if resp.status_code == 200:
                raw_txt = resp.json()["choices"][0]["message"]["content"].strip()
                if raw_txt.startswith("```"):
                    raw_txt = raw_txt.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                data = json.loads(raw_txt)
                if isinstance(data, list) and len(data) > 0:
                    results = []
                    for item in data[:count]:
                        target_fn = item.get("target_filename", "")
                        matched_path = None
                        for t in transcripts:
                            if target_fn and (target_fn in t.get("file", "") or target_fn in t.get("filename", "")):
                                matched_path = t["file"]
                                break
                        item["target_file"] = matched_path or transcripts[min(len(results), len(transcripts) - 1)]["file"]
                        results.append(item)
                    if len(results) >= count:
                        return results
        except Exception as e:
            logger.warning("Multi-punchline LLM query failed: %s", e)

    # Heuristic fallback across available transcripts
    results = []
    seen_files = set()
    for t in transcripts:
        if t["file"] not in seen_files:
            seen_files.add(t["file"])
            idx_num = len(results) + 1
            results.append({
                "target_file": t["file"],
                "climax_start": t.get("start", 0.0),
                "climax_duration": 3.5,
                "punchline_hook": f"INFO PENTING EMAS #{idx_num}!",
                "reason": f"Fallback footage {os.path.basename(t['file'])}",
            })
            if len(results) == count:
                break

    # If still not enough, repeat with timestamp offsets
    while len(results) < count and transcripts:
        fallback_t = transcripts[len(results) % len(transcripts)]
        results.append({
            "target_file": fallback_t["file"],
            "climax_start": fallback_t.get("start", 0.0),
            "climax_duration": 3.5,
            "punchline_hook": f"INFO MENARIK EMAS #{len(results) + 1}!",
            "reason": "Offset fallback",
        })

    return results


def extract_climax_teaser(video_path: str, start_sec: float, duration_sec: float, out_path: str) -> bool:
    """Extract a precise slice of video for the cold open hook."""
    if not os.path.exists(video_path):
        return False

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.2f}",
        "-i", video_path,
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264", "-preset", "slow", "-crf", "18",
        "-maxrate", "20M", "-bufsize", "25M",
        "-c:a", "aac", "-b:a", "320k",
        out_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    except Exception as e:
        logger.error(f"Failed to extract climax teaser: {e}")
        return False
