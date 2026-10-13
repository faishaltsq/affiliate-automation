import os
import json
import logging
import tempfile
import subprocess
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


def _query_punchline_llm(campaign_title: str, transcripts: List[Dict]) -> Optional[Dict]:
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

    prompt = f"""Kamu adalah Video Editor & Hook Specialist untuk TikTok & Reels.
Tugasmu: Dari transkrip bahan footage berikut, temukan momen PUNCHLINE / KLIMAKS paling menegangkan, seru, atau krusial yang benar-benar mewakili judul video ini.

Judul Campaign: "{campaign_title}"

Transkrip Bahan Video:
{transcript_summary}

Aturan Penting:
1. Pilih 1 momen klimaks dengan durasi 3.0 sampai 4.0 detik yang paling cocok dijadikan 'COLD OPEN / HOOK TEASER' di detik 0 video.
2. Buat hook text singkat (maksimal 6 kata, ALL CAPS) yang memancing rasa penasaran penonton.
3. Output HANYA JSON murni tanpa markdown/backticks, dengan struktur berikut:
{{
    "target_filename": "nama_file_yang_dipilih.mp4",
    "climax_start": 10.5,
    "climax_duration": 3.5,
    "punchline_hook": "DETIK-DETIK KETAHUAN EMAS PALSU!",
    "reason": "Momen konfrontasi penjual tertangkap basah"
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


def select_climax_punchline(campaign_title: str, transcripts: List[Dict]) -> Optional[Dict]:
    """Select the best climax punchline from footage transcripts."""
    if not transcripts:
        return None

    res = _query_punchline_llm(campaign_title, transcripts)
    if res and res.get("target_file"):
        return res

    # Fallback heuristic if LLM query fails
    first_item = transcripts[0]
    return {
        "target_file": first_item["file"],
        "climax_start": first_item.get("start", 0.0),
        "climax_duration": 3.5,
        "punchline_hook": "PUNCHLINE MOMENT!",
        "reason": "Fallback to first transcript dialogue snippet.",
    }


def extract_climax_teaser(video_path: str, start_sec: float, duration_sec: float, out_path: str) -> bool:
    """Extract a precise slice of video for the cold open hook."""
    if not os.path.exists(video_path):
        return False

    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_sec:.2f}",
        "-i", video_path,
        "-t", f"{duration_sec:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        out_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 1000
    except Exception as e:
        logger.error(f"Failed to extract climax teaser: {e}")
        return False
