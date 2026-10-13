"""
metadata.py — Generates viral hooks and multi-platform metadata (TikTok, IG Reels, YT Shorts)
adhering strictly to the Faceless Outdoor/Fishing/Camping Content SOP.
"""

import json
import logging
import re
from typing import Dict, Any

from src.brief.generator import _llm

logger = logging.getLogger("content_ops.metadata")

# Fallback deterministic copy templates per niche
FALLBACK_TEMPLATES = {
    "mancing": {
        "hooks": [
            "DAPAT INI SETELAH 3 JAM NUNGGU...",
            "TARIKAN GILA! SAMPAI MELENGKUNG...",
            "KALAU KAMU, UMPANNYA PAKAI APA?",
            "DETIK-DETIK MONSTER AIR MENYAMBAR!",
            "SATISFYING BANGET PAS REEL DITARIK!"
        ],
        "tiktok": {
            "caption": "Momen strike paling memuaskan hari ini! Kalau di spot kalian, umpan apa yang paling gacor?",
            "hashtags": ["#mancing", "#mancingikan", "#fishinglife", "#mancingliar", "#strike"]
        },
        "reels": {
            "caption": "Sensasi tarikan strike ikan liar di alam bebas. Hook mantap dan reel menjerit!",
            "hashtags": ["#mancingmania", "#fishingadventure", "#freshwaterfishing", "#outdoors"]
        },
        "shorts": {
            "title": "Mancing Liar di Sungai — Detik-detik Strike Monster Tak Terduga!",
            "description": "Kompilasi momen strike paling memuaskan saat mancing di alam bebas. Tarikan kuat dan reel menjerit sampai melengkung.",
            "hashtags": ["#Shorts", "#Fishing", "#Mancing", "#Outdoor"]
        }
    },
    "camping": {
        "hooks": [
            "CAMPING HUJAN DERAS PALING COZY...",
            "SETUP TENDA DI TENGAH HUTAN LIAR",
            "SUARA API UNGGUN MALAM INI SATISFYING BANGET",
            "SURVIVAL ALAM: MASAK STEAK DI TENDA",
            "RELA TINGGAL DI SINI BERHARI-HARI?"
        ],
        "tiktok": {
            "caption": "Suasana camping di tengah hujan lebat. Kalian lebih suka camping saat hujan atau cuaca cerah?",
            "hashtags": ["#camping", "#solocamping", "#bushcraft", "#raincamping", "#outdoors"]
        },
        "reels": {
            "caption": "Solo camping dan suara hujan deras di atas tarp. Relaksasi outdoor paling menenangkan.",
            "hashtags": ["#campinglife", "#bushcraftsurvival", "#cozycamping", "#naturelovers"]
        },
        "shorts": {
            "title": "Solo Camping Hujan Lebat di Hutan — Suasana Cozy dan Suara Hujan ASMR",
            "description": "Pengalaman solo camping saat hujan lebat di hutan belantara. Menikmati hangatnya api unggun dan ketenangan alam.",
            "hashtags": ["#Shorts", "#Camping", "#Bushcraft", "#ASMR"]
        }
    },
    "outdoor_satisfying": {
        "hooks": [
            "SATISFYING BANGET LIHAT HASILNYA!",
            "DETIK-DETIK PALING MELEWAKAN DI ALAM",
            "PROSESNYA BIKIN KECANDUAN NONTON",
            "DARI KAYU BEKAS JADI KARYA KEREN"
        ],
        "tiktok": {
            "caption": "Momen outdoor satisfying yang bikin rileks. Menurut kalian bagian mana yang paling memuaskan?",
            "hashtags": ["#satisfying", "#oddlysatisfying", "#outdoorlife", "#woodworking", "#relaxing"]
        },
        "reels": {
            "caption": "Karya seni outdoor dan keahlian tangan di alam bebas. Proses pengerjaan super memuaskan.",
            "hashtags": ["#satisfyingvideo", "#craftsmanship", "#naturecraft", "#satisfyinglife"]
        },
        "shorts": {
            "title": "Proses Pembuatan Shelter Alami yang Sangat Satisfying!",
            "description": "Video satisfying proses pengerjaan di alam terbuka. Suara alam dan hasil karya yang sangat rapi.",
            "hashtags": ["#Shorts", "#Satisfying", "#Outdoor", "#Handmade"]
        }
    }
}


def generate_metadata_and_hook(video_title: str, niche: str = "mancing") -> Dict[str, Any]:
    """
    Generate hook text and platform metadata according to SOP guidelines.
    Returns:
        {
            "hook_text": str,  # ALL CAPS, concise
            "tiktok": {"caption": str, "hashtags": list[str]},
            "reels": {"caption": str, "hashtags": list[str]},
            "shorts": {"title": str, "description": str, "hashtags": list[str]}
        }
    """
    niche_key = niche if niche in FALLBACK_TEMPLATES else "mancing"
    prompt = f"""Kamu adalah spesialis viral marketing & content operations untuk video pendek faceless di niche: {niche}.
Analisis judul sumber: "{video_title}"

Hasilkan metadata yang sesuai SOP:
1. Hook Text:
   - HURUF KAPITAL SEMUA, 3–7 KATA MAKSIMAL.
   - Pola: Momen aksi / rasa penasaran / pertanyaan ("DAPAT INI SETELAH 3 JAM...", "GILA NGGAK TUH...", "SATISFYING BANGET", "KALAU KAMU, UMPANNYA APA?").
2. TikTok Metadata:
   - Caption: 1–2 kalimat singkat diakhiri CTA pertanyaan yang memancing interaksi komentar.
   - 3-5 hashtag niche relevan (contoh: #mancing #mancingikan #fishinglife #outdoor).
3. Instagram Reels Metadata:
   - Caption: Fokus keyword + hook rasa penasaran (1-2 kalimat).
   - 3-5 hashtag relevan.
4. YouTube Shorts Metadata:
   - Title: Searchable title format Indonesia (contoh: "Mancing Ikan di Sungai — Dapat Ini Setelah 3 Jam!").
   - Description: 2-3 kalimat kaya keyword pencarian.
   - 3-5 hashtag relevan (wajib ada #Shorts).

Kembalikan HANYA format JSON valid berikut tanpa markdown/teks lain:
{{
  "hook_text": "HOOK HURUF BESAR",
  "tiktok": {{
    "caption": "Teks caption...",
    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4"]
  }},
  "reels": {{
    "caption": "Teks caption reels...",
    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4"]
  }},
  "shorts": {{
    "title": "Judul Searchable YouTube Shorts",
    "description": "Deskripsi 2-3 kalimat...",
    "hashtags": ["#Shorts", "#Tag2", "#Tag3"]
  }}
}}
"""
    try:
        raw = _llm([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1024)
        clean = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        clean = re.sub(r"\s*```$", "", clean.strip(), flags=re.MULTILINE).strip()
        data = json.loads(clean)
        if "hook_text" in data and "tiktok" in data and "shorts" in data:
            # Enforce uppercase hook
            data["hook_text"] = data["hook_text"].strip().upper()
            return data
    except Exception as e:
        logger.warning(f"LLM metadata generation failed, using SOP fallback template: {e}")

    # Fallback template
    fb = FALLBACK_TEMPLATES[niche_key]
    import random
    hook = random.choice(fb["hooks"])
    return {
        "hook_text": hook,
        "tiktok": fb["tiktok"],
        "reels": fb["reels"],
        "shorts": fb["shorts"],
    }
