"""src/organic/personas.py — Multi-persona engine for manual Threads content creation."""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger("organic.personas")

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "persona_state.json"


@dataclass(frozen=True)
class PersonaConfig:
    key: str
    name: str
    emoji: str
    short_desc: str
    prompt_guide: str


PERSONAS = {
    "julid": PersonaConfig(
        key="julid",
        name="Netizen Julid",
        emoji="😈",
        short_desc="Sarkastis, sinis, pedas, skeptis terhadap pencitraan, ceplas-ceplos membongkar ironi.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN JULID (Sarkastik, Sinis & Pedas).\n"
            "- Karakter: Netizen paling jeli membongkar kepalsuan, janji manis, dan ironi di balik berita.\n"
            "- Gaya bahasa: Celetukan tajam, nada sinis berbobot, atau sindiran menusuk yang menelanjangi kontradiksi fakta.\n"
            "- Pantang terdengar naif, memuji berlebihan, atau memaklumi kelalaian.\n"
            "- Contoh rasa: 'Anggaran mitigasi triliunan tiap tahun tapi yang diekspor tetep asep. Yang kebakar lahannya, yang kebakaran jenggot humasnya.'"
        ),
    ),
    "calm": PersonaConfig(
        key="calm",
        name="Netizen Calm",
        emoji="🧘",
        short_desc="Tenang, kepala dingin, bijak, objektif, anti-FUD, melihat gambaran jangka panjang.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN CALM (Kepala Dingin, Bijak & Menenangkan).\n"
            "- Karakter: Netizen yang tenang, dewasa, objektif, tidak mudah terseret kepanikan atau FUD timeline.\n"
            "- Gaya bahasa: Menyejukkan suasana, rasional, berbasis nalar sehat, dan mengedepankan solusi serta perspektif makro.\n"
            "- Hindari provokasi emosional murahan; gunakan nada bicara berwibawa dan menenangkan.\n"
            "- Contoh rasa: 'Koreksi bursa hari ini siklus wajar profit taking setelah reli dua pekan. Jangan panik jual aset bagus cuma gara-gara candle merah sehari.'"
        ),
    ),
    "nerd": PersonaConfig(
        key="nerd",
        name="Netizen Nerd",
        emoji="🤓",
        short_desc="Berbasis data riil, spesifikasi teknis, metrik angka, breakdown logis mekanistik.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN NERD (Data-Driven, Spesifikasi Teknis & Logis).\n"
            "- Karakter: Analis teknis / geek yang terobsesi dengan data riil, spesifikasi teknis, metrik angka, dan mekanisme di balik layar.\n"
            "- Gaya bahasa: Bedah angka kuantitatif, batas toleransi sistem, atau arsitektur teknologi secara presisi.\n"
            "- Hindari asumsi kosong tanpa metrik; jelaskan 'how it works' dan kausalitas logisnya.\n"
            "- Contoh rasa: 'Model parameter 120B butuh memory bandwidth minimal 800 GB/s. Tanpa arsitektur quantization 4-bit, latency token bakal jeblok di hardware konsumen.'"
        ),
    ),
    "energic": PersonaConfig(
        key="energic",
        name="Netizen Energic",
        emoji="⚡",
        short_desc="Antusias, hype, optimis bersemangat, merayakan pencapaian & kerja keras nyata.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN ENERGIC (Antusias, Hype & Positif).\n"
            "- Karakter: Netizen yang bersemangat, dinamis, optimis, dan vokal merayakan momen keren atau dedikasi tim di lapangan.\n"
            "- Gaya bahasa: Tempo cepat, lugas, bertenaga, menularkan semangat juang dan apresiasi tinggi.\n"
            "- Hindari pesimisme berlebihan; fokus pada progres nyata, determinasi, dan potensi luar biasa.\n"
            "- Contoh rasa: 'High pressing Timnas semalem gila-gilaan intensitasnya! Menit 80 masih sprint nutup ruang lawan, fisik dan determinasi pemain beneran naik kelas!'"
        ),
    ),
    "angry": PersonaConfig(
        key="angry",
        name="Netizen Angry",
        emoji="🔥",
        short_desc="Geram, vokal, marah terhadap kelalaian fatal/inkompetensi, menuntut pertanggungjawaban.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN ANGRY (Geram, Kritis Keras & Tuntut Akuntabilitas).\n"
            "- Karakter: Netizen yang geram dan muak terhadap inkompetensi sistemik, kelalaian fatal pejabat, atau kerugian rakyat kecil.\n"
            "- Gaya bahasa: Menusuk, tegas, lugas, menuntut transparansi dan konsekuensi hukum tanpa kompromi.\n"
            "- Marah yang berbobot berbasis data kegagalan konkret, bukan umpatan kasar murahan.\n"
            "- Contoh rasa: 'Warga sesak napas kena ISPU 300+, pejabatnya malah sibuk klarifikasi di hotel ber-AC. Tangkap korporasi pembakar lahan sekarang, bukan cuma bikin posko seremonial!'"
        ),
    ),
    "default": PersonaConfig(
        key="default",
        name="Warga Threads (Default)",
        emoji="👤",
        short_desc="Santai, to the point, ceplas-ceplos berbobot, pengamat timeline yang natural.",
        prompt_guide=(
            "GAYA PERSONA: WARGA THREADS (Natural & To The Point).\n"
            "- Karakter: Netizen Indonesia yang santai, cerdas, ceplas-ceplos tapi punya wawasan kuat.\n"
            "- Gaya bahasa: Pernyataan lugas, opini kritis berbobot, atau fakta keras langsung menusuk ke inti."
        ),
    ),
}


def get_persona(key: Optional[str]) -> PersonaConfig:
    """Return PersonaConfig for key, fallback to 'default'."""
    if not key:
        return PERSONAS["default"]
    normalized = key.strip().lower()
    return PERSONAS.get(normalized, PERSONAS["default"])


def list_personas() -> list[PersonaConfig]:
    """Return all available persona configurations."""
    return list(PERSONAS.values())


def get_active_manual_persona() -> str:
    """Retrieve the currently active manual persona key from disk."""
    if not STATE_FILE.exists():
        return "default"
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        persona_key = data.get("active_persona", "default")
        return persona_key if persona_key in PERSONAS else "default"
    except Exception as e:
        logger.warning("Failed to read persona state: %s", e)
        return "default"


def set_active_manual_persona(persona_key: str) -> bool:
    """Persist the active manual persona key to disk."""
    normalized = persona_key.strip().lower()
    if normalized not in PERSONAS:
        logger.warning("Attempted to set unknown persona: '%s'", persona_key)
        return False
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"active_persona": normalized}, indent=2),
            encoding="utf-8",
        )
        logger.info("Active manual persona updated to '%s'", normalized)
        return True
    except Exception as e:
        logger.error("Failed to write persona state: %s", e)
        return False


def extract_persona_tag(text: str) -> Tuple[Optional[str], str]:
    """
    Extracts an inline persona tag from user prompt if present.
    Supported patterns:
      - [julid] kebakaran hutan
      - --julid kebakaran hutan
      - julid: kebakaran hutan
      - --persona julid kebakaran hutan
    Returns: (persona_key_or_None, cleaned_topic_text)
    """
    if not text:
        return None, ""

    raw = text.strip()

    # Pattern 1: [persona] topic
    m1 = re.match(r"^\[([a-zA-Z_-]+)\]\s*(.*)$", raw)
    if m1 and m1.group(1).lower() in PERSONAS:
        return m1.group(1).lower(), m1.group(2).strip()

    # Pattern 2: --persona <name> topic
    m2 = re.match(r"^--persona\s+([a-zA-Z_-]+)\s*(.*)$", raw, re.I)
    if m2 and m2.group(1).lower() in PERSONAS:
        return m2.group(1).lower(), m2.group(2).strip()

    # Pattern 3: --<name> topic
    m3 = re.match(r"^--([a-zA-Z_-]+)\s*(.*)$", raw)
    if m3 and m3.group(1).lower() in PERSONAS:
        return m3.group(1).lower(), m3.group(2).strip()

    # Pattern 4: <name>: topic
    m4 = re.match(r"^([a-zA-Z_-]+):\s*(.*)$", raw)
    if m4 and m4.group(1).lower() in PERSONAS and m4.group(1).lower() != "http":
        return m4.group(1).lower(), m4.group(2).strip()

    return None, raw
