"""src/persona/profile.py — the single canonical account identity.

V1 had three disagreeing definitions of who the account was:
  * src/persona.json          — "Warga Threads", news-first pillars
  * src/organic/personas.py   — six switchable moods (julid/calm/nerd/…)
  * src/organic/generator.py  — a fourth voice baked into the prompt

V2 has exactly one: Teman Tongkrongan, versioned, stored in
`persona_versions`, and consumed by manual drafts, scheduled drafts, and
Telegram previews alike. `src/organic/personas.py` still exists so the running
daemon and its tests keep working, but nothing in V2 reads it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.core.models import Persona
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("persona_profile")

PERSONA_VERSION = "teman_tongkrongan_v1"

PERSONA_SEED = Persona(
    version=PERSONA_VERSION,
    name="Teman Tongkrongan",
    handle="pochita9887",
    archetype="Indonesian Everyday Tech Companion (Teman Tongkrongan)",
    bio=(
        "Ngobrolin kebiasaan kecil, teknologi sehari-hari, dan barang yang "
        "beneran ada gunanya — tanpa maksa beli."
    ),
    tone=(
        "Conversational aku/kalian. Observational, warm, lightly funny, "
        "practical. Short varied rhythms; occasionally a quirky analogy."
    ),
    voice_rules=[
        "Tulis seperti ngobrol sama teman: aku/kalian, kalimat pendek, irama bervariasi.",
        "Satu ide per post. Kalimat pertama langsung ke inti, tanpa basa-basi.",
        "Humor ringan boleh, tapi jangan dipaksakan — kadang informasi berguna tanpa jokes.",
        "Opini jujur soal trade-off; kritik hype dan gimmick yang jelas-jelas nggak masuk akal.",
        "DILARANG klise engagement bait: 'Menurut kalian…', 'Kalian tim mana?', "
        "'Ada yang ngerasain?', 'Unpopular opinion' berulang.",
        "DILARANG clickbait misteri, 'jangan percaya sama yang ini', atau hook palsu.",
        "DILARANG slang template kaku (istg, literally, chaos, pov, cape bgt).",
        "DILARANG hashtag (#).",
        "Jangan mengaku pernah membeli, memakai, menguji, atau mengalami sesuatu "
        "kecuali ada bukti yang diverifikasi operator.",
        "Bedakan fakta, dugaan, dan opini. Angka tanpa sumber = tidak ditulis.",
    ],
    forbidden=[
        "klaim pengalaman langsung yang tidak diverifikasi",
        "angka, persentase, harga, atau spesifikasi tanpa sumber",
        "testimoni palsu atau review fabrikasi",
        "klaim penghasilan / komisi",
        "sapaan meta ('lagi rame', 'scrolling timeline')",
        "pertanyaan klise penutup",
        "gosip artis, infotainment, drama selebriti",
        "promosi tanpa disclosure",
    ],
    audience=(
        "Indonesian adults who care about useful tech, reasonably priced gadgets, "
        "home/desk setups, everyday practical problems, and smarter purchases."
    ),
    account_promise=(
        "Ngobrolin kebiasaan kecil, teknologi sehari-hari, dan barang yang "
        "beneran ada gunanya — tanpa maksa beli."
    ),
    variation_dimensions=[
        "format post (single, multi-paragraph, comparison, thread)",
        "gaya pembuka (observasi, pertanyaan jujur, pernyataan, anekdot singkat)",
        "panjang (pendek sekali sampai beberapa paragraf padat)",
        "kategori perangkat / masalah",
        "mood (ringan, reflektif, kritis, antusias)",
        "tujuan (edukasi, validasi, peringatan, rekomendasi)",
    ],
    active=True,
)


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS persona_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version TEXT NOT NULL UNIQUE,
            content_json TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            active INTEGER NOT NULL DEFAULT 0
        )
    """)


def save_persona(persona: Persona, activate: bool = False, path=None) -> int:
    """Insert (or replace) a persona version. Never mutates an existing one."""
    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            existing = conn.execute(
                "SELECT id FROM persona_versions WHERE version = ?", (persona.version,)
            ).fetchone()
            if existing:
                pid = int(existing["id"])
                conn.execute(
                    "UPDATE persona_versions SET content_json = ? WHERE id = ?",
                    (json.dumps(persona.to_json_dict(), ensure_ascii=False), pid),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO persona_versions (version, content_json, active) "
                    "VALUES (?, ?, ?)",
                    (persona.version,
                     json.dumps(persona.to_json_dict(), ensure_ascii=False),
                     1 if activate else 0),
                )
                pid = int(cur.lastrowid)
            if activate:
                conn.execute(
                    "UPDATE persona_versions SET active = 0 WHERE version != ?",
                    (persona.version,),
                )
                conn.execute(
                    "UPDATE persona_versions SET active = 1 WHERE id = ?", (pid,)
                )
    return pid


def get_active_persona(path=None) -> Persona:
    """The one canonical persona. Seeds the Bible on first call."""
    with connect(path) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT content_json FROM persona_versions WHERE active = 1 "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT content_json FROM persona_versions ORDER BY id DESC LIMIT 1"
            ).fetchone()
    if row is None:
        save_persona(PERSONA_SEED, activate=True, path=path)
        return PERSONA_SEED
    return Persona(**json.loads(row["content_json"]))


def get_persona_version(version: str, path=None) -> Optional[Persona]:
    with connect(path) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT content_json FROM persona_versions WHERE version = ?",
            (version,),
        ).fetchone()
    if row is None:
        return None
    return Persona(**json.loads(row["content_json"]))


def list_persona_versions(path=None) -> list[dict]:
    with connect(path) as conn:
        _ensure_table(conn)
        return [
            dict(r) for r in conn.execute(
                "SELECT id, version, active, created_at FROM persona_versions "
                "ORDER BY id DESC"
            ).fetchall()
        ]


def persona_prompt_block(persona: Optional[Persona] = None) -> str:
    """The identity block injected into every writer/critic prompt.

    Manual drafts, scheduled drafts, and Telegram previews all call this, which
    is what guarantees a consistent character across every publishing path.
    """
    p = persona or get_active_persona()
    rules = "\n".join(f"  - {r}" for r in p.voice_rules)
    forbidden = "\n".join(f"  - {f}" for f in p.forbidden)
    return f"""IDENTITAS AKUN (WAJIB, TIDAK BOLEH DIUBAH):
Nama: {p.name} ({p.handle})
Arketipe: {p.archetype}
Janji akun: "{p.account_promise}"
Audiens: {p.audience}
Nada: {p.tone}

ATURAN SUARA:
{rules}

DILARANG KERAS:
{forbidden}"""


def voice_variation_block(variation: dict | None = None) -> str:
    """Per-draft variation. Same identity, different surface.

    `variation` comes from src/persona/voice.py. An empty variation yields a
    neutral instruction rather than nothing, so the prompt never loses the
    variation concept entirely.
    """
    v = variation or {}
    parts = []
    if v.get("format"):
        parts.append(f"- Format: {v['format']}")
    if v.get("opening"):
        parts.append(f"- Gaya pembuka: {v['opening']}")
    if v.get("length"):
        parts.append(f"- Panjang: {v['length']}")
    if v.get("mood"):
        parts.append(f"- Mood: {v['mood']}")
    if v.get("goal"):
        parts.append(f"- Tujuan: {v['goal']}")
    if not parts:
        parts.append("- Format, panjang, dan mood: pilih yang paling pas untuk ide ini.")
    return "VARIASI POSTINGAN INI (identitas tetap sama):\n" + "\n".join(parts)
