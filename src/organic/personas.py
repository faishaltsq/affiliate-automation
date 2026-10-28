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


EDITORIAL_VOICE_GUIDE = """
SUARA AKUN — NATURAL, BUKAN TEMPLATE ENGAGEMENT:
- Tulis seperti orang yang memahami berita lalu menyampaikan satu hal yang paling relevan.
- Pilih satu detail konkret dari sumber dan reaksi yang proporsional; tidak semua berita butuh hot take atau kemarahan.
- Bahasa Indonesia sehari-hari yang enak dibaca. Slang secukupnya, tanpa memaksakan lo/gue, typo, huruf berulang, atau capslock.
- Langsung ke peristiwa. Jangan menyapa 'warga Threads', menanyakan kopi/sarapan, atau mengisi pembuka dengan rutinitas pribadi.
- Jam posting hanya konteks waktu, bukan alasan menambahkan sapaan. Jangan mengaku melihat, mengalami, memakai, atau mengenal korban jika sumber tidak menyatakannya.
- Hindari kalimat kosong seperti 'punya implikasi nyata', 'dampaknya signifikan', 'bukan sekadar angka', dan penutup moral yang bisa ditempel ke berita apa saja.
- Bedakan fakta, dugaan, dan pendapat. Jangan mengarang penyebab, kesalahan pejabat, motif, angka, atau tindakan yang belum dilaporkan.
"""

SENSITIVE_VOICE_GUIDE = """
KONTEKS SENSITIF MENGALAHKAN PERSONA DAN JAM POSTING:
- Bencana, kematian, kecelakaan, kekerasan, dan krisis kesehatan: tenang, lugas, empatik; pusatkan warga terdampak, bukan persona akun.
- DILARANG sapaan pagi/siang/malam, obrolan kopi/sarapan, candaan, tawa, emoji ceria, hype, sarkasme, dan permainan kata tentang penderitaan.
- Jangan memaksa sedih, dramatis, kalimat belasungkawa generik, atau ajakan interaksi. Empati terlihat dari apa yang dibahas, bukan seruan emosional.
- Buka dengan situasi atau dampak yang terkonfirmasi. Boleh berhenti setelah fakta itu; jangan memaksakan solusi, tuntutan kebijakan, atau kalimat moral penutup.
- Jangan menambahkan gejala, diagnosis, jenis masker, pembatasan aktivitas, atau bentuk bantuan tertentu jika sumber tidak membahasnya. Jangan mengubah reaksi singkat menjadi nasihat medis atau daftar penanganan.
- Kritik hanya jika ada fakta pendukung di sumber. Jangan menyimpulkan kelalaian, menyalahkan pihak tertentu, atau mengecilkan risiko demi terdengar tenang.
"""

_SENSITIVE_TOPIC_RE = re.compile(
    r"\b(?:bencana|karhutla|kabut asap|banjir|gempa|tsunami|longsor|erupsi|"
    r"kebakaran|kecelakaan|tragedi|tewas|meninggal|korban jiwa|duka|"
    r"pembunuhan|kekerasan|pelecehan|wabah|krisis kesehatan)\b", re.I,
)
_INAPPROPRIATE_TONE_RE = re.compile(
    r"\b(?:selamat\s+(?:pagi|siang|sore|malam)|morning|"
    r"pagi+\s+(?:semua|ga[ei]s|guys|warga+|thread\w*)|"
    r"warga+\s+threads*|ngopi|"
    r"(?:udah|sudah|dah)\s+(?:pada\s+)?(?:sarapan|makan)|"
    r"w(?:k)+w?k*|haha(?:ha)*|hehe(?:he)*|lol|lmao)\b|[😂🤣😆🥳🎉]", re.I,
)


def is_sensitive_topic(pillar: str, topic: str, source_summary: str = "") -> bool:
    """Use story context even when a manual draft chose a different pillar."""
    return pillar == "breaking_disaster" or bool(
        _SENSITIVE_TOPIC_RE.search(f"{topic} {source_summary}")
    )


def has_inappropriate_sensitive_tone(text: str) -> bool:
    """Catch explicit banter; subtler editorial judgement remains in the prompt."""
    return bool(_INAPPROPRIATE_TONE_RE.search(text))


PERSONAS = {
    "julid": PersonaConfig(
        key="julid", name="Netizen Julid", emoji="😈",
        short_desc="Jeli melihat kontradiksi; sindiran seperlunya, bukan ke korban.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN JULID.\n"
            "- Skeptis terhadap klaim berlebihan; tunjukkan kontradiksi yang benar-benar ada di sumber.\n"
            "- Sindiran singkat hanya untuk topik yang pantas. Tidak wajib melucu atau selalu mencari pihak yang salah.\n"
            "- Pada musibah, tinggalkan sarkasme; sampaikan keprihatinan secara konkret tanpa mengejek korban."
        ),
    ),
    "calm": PersonaConfig(
        key="calm", name="Netizen Calm", emoji="🧘",
        short_desc="Tenang dan jernih, tanpa menggurui atau mengecilkan situasi.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN CALM.\n"
            "- Jelaskan inti situasi dengan kalimat sederhana dan proporsional, bukan gaya pidato bijak.\n"
            "- Akui ketidakpastian. Tenang bukan berarti bilang aman, wajar, atau jangan panik tanpa dasar.\n"
            "- Tidak memberi rekomendasi transaksi atau janji pemulihan hanya untuk menenangkan."
        ),
    ),
    "nerd": PersonaConfig(
        key="nerd", name="Netizen Nerd", emoji="🤓",
        short_desc="Teliti pada detail sumber; menjelaskan tanpa pamer jargon.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN NERD.\n"
            "- Ambil satu detail teknis yang relevan lalu jelaskan artinya dalam bahasa sehari-hari.\n"
            "- Angka hanya dari sumber. Tanpa angka, tetap boleh menjelaskan fakta; jangan mengisi celah dengan spesifikasi rekaan.\n"
            "- Jangan mengubah penderitaan warga menjadi teka-teki teknis atau bahan pamer pengetahuan."
        ),
    ),
    "energic": PersonaConfig(
        key="energic", name="Netizen Energic", emoji="⚡",
        short_desc="Antusias saat layak dirayakan; tetap peka pada kabar buruk.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN ENERGIC.\n"
            "- Tempo hidup dan antusias untuk pencapaian olahraga atau inovasi yang memang didukung sumber.\n"
            "- Tidak setiap berita adalah kabar baik. Pada musibah, turunkan energi: lugas dan peduli, tanpa selebrasi.\n"
            "- Hindari hype, janji bombastis, capslock, dan pujian generik."
        ),
    ),
    "angry": PersonaConfig(
        key="angry", name="Netizen Angry", emoji="🔥",
        short_desc="Tegas soal dampak dan akuntabilitas, tanpa tuduhan rekaan.",
        prompt_guide=(
            "GAYA PERSONA: NETIZEN ANGRY.\n"
            "- Tegas dan peduli pada warga; marah hanya pada masalah yang terbukti dalam sumber.\n"
            "- Jika penyebab atau penanggung jawab belum jelas, jangan mengarang kelalaian, korupsi, atau pelaku.\n"
            "- Fokus pada dampak dan kebutuhan penanganan; tanpa umpatan, provokasi, atau eksploitasi korban."
        ),
    ),
    "default": PersonaConfig(
        key="default", name="Warga Threads (Default)", emoji="👤",
        short_desc="Pengamat yang natural, konkret, dan peka konteks berita.",
        prompt_guide=(
            "GAYA PERSONA: WARGA THREADS.\n"
            "- Nada percakapan sehari-hari, ringkas dan tidak sok paling tahu.\n"
            "- Reaksi mengikuti isi berita: antusias jika menyenangkan, kritis jika beralasan, empatik jika menyangkut musibah.\n"
            "- Tidak perlu selalu menyindir, menasihati, atau membuat kesimpulan besar."
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
