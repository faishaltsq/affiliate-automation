"""src/content/critic.py — independent assessment of a draft.

The critic is a separate LLM role from the writer. It never sees the writer's
prompt, only the finished draft plus the persona contract and the evidence.
That separation is what makes it a real check rather than the writer grading
its own homework.

Hard vetoes live in quality_gate.py, not here. The critic produces scores and
notes; the gate decides.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from src.core.llm import LLMClient, LLMError, complete_json, get_llm_client
from src.persona.profile import get_active_persona, persona_prompt_block

log = None  # set lazily to avoid import cycles in tests


def _logger():
    global log
    if log is None:
        from src.logger import get_logger
        log = get_logger("content_critic")
    return log


CRITIC_SCHEMA = """{
  "persona_voice": 0-20,
  "audience_relevance": 0-20,
  "natural_writing": 0-20,
  "specific_useful_content": 0-20,
  "evidential_correctness": 0-15,
  "novelty": 0-5,
  "notes": "satu paragraf singkat bahasa Indonesia",
  "concerns": ["daftar masalah konkret, kosong jika tidak ada"]
}"""


@dataclass
class CriticVerdict:
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    notes: str = ""
    concerns: list[str] = field(default_factory=list)
    ok: bool = True
    error: str = ""

    @property
    def band(self) -> str:
        if self.total_score >= 85:
            return "auto"
        if self.total_score >= 70:
            return "review"
        return "revise"


def _critic_prompt(draft_text: str, evidence: str, pillar: str) -> str:
    persona = get_active_persona()
    identity = persona_prompt_block(persona)
    evidence_block = (
        f"BUKTI YANG TERSEDIA:\n{evidence.strip()}\n"
        if evidence.strip() else
        "BUKTI YANG TERSEDIA: (tidak ada — draft tidak boleh memuat angka atau klaim faktual)\n"
    )
    return f"""Kamu adalah editor independen. Nilai draf berikut secara jujur dan ketat.
Kamu BUKAN penulisnya dan tidak perlu memuji.

{identity}

PILAR KONTEN: {pillar}

{evidence_block}

DRAF:
\"\"\"
{draft_text}
\"\"\"

BERI SKOR 0-100 dengan pembobotan ini:
- persona_voice (0-20): apakah terdengar seperti Teman Tongkrongan, bukan bot atau template?
- audience_relevance (0-20): apakah benar-benar menyentuh masalah audiens yang disebut?
- natural_writing (0-20): bahasa Indonesia natural, irama bervariasi, tidak kaku?
- specific_useful_content (0-20): ada isi konkret yang berguna, bukan kalimat kosong?
- evidential_correctness (0-15): setiap angka/klaim didukung bukti di atas?
- novelty (0-5): tidak mengulang template atau pembuka klise?

Turunkan skor tanpa ampun kalau:
- ada angka/harga/spesifikasi tanpa bukti
- ada klaim "gue/aku sudah pakai/beli/coba"
- penutup berupa pertanyaan klise engagement bait
- kalimat generik yang bisa ditempel ke topik apa saja
- nada berbeda dari identitas akun di atas

OUTPUT: hanya JSON valid sesuai skema:
{CRITIC_SCHEMA}"""


def critique(
    draft_text: str,
    evidence: str = "",
    pillar: str = "",
    client: Optional[LLMClient] = None,
) -> CriticVerdict:
    """Score a draft. On LLM failure the verdict is `ok=False`, never a pass."""
    llm = client or get_llm_client()
    prompt = _critic_prompt(draft_text, evidence, pillar)
    try:
        data = complete_json(
            llm, [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=700,
        )
    except LLMError as exc:
        _logger().warning("Critic failed: %s", exc)
        return CriticVerdict(ok=False, error=f"critic_unavailable: {exc}")

    weights = {
        "persona_voice": 20, "audience_relevance": 20, "natural_writing": 20,
        "specific_useful_content": 20, "evidential_correctness": 15, "novelty": 5,
    }
    scores: dict[str, float] = {}
    for key, cap in weights.items():
        try:
            val = float(data.get(key, 0))
        except (TypeError, ValueError):
            val = 0.0
        scores[key] = max(0.0, min(float(cap), val))

    total = sum(scores.values())
    concerns = data.get("concerns") or []
    if not isinstance(concerns, list):
        concerns = [str(concerns)]
    notes = str(data.get("notes") or "").strip()

    return CriticVerdict(
        scores=scores,
        total_score=round(total, 2),
        notes=notes,
        concerns=[str(c) for c in concerns],
        ok=True,
    )
