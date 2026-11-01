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
from src.persona.reference_library import get_editorial_reference_prompt
log = None  # set lazily to avoid import cycles in tests


def _logger():
    global log
    if log is None:
        from src.logger import get_logger
        log = get_logger("content_critic")
    return log


SEVERITY_BLOCKING = "BLOCKING"
SEVERITY_MAJOR = "MAJOR"
SEVERITY_MINOR = "MINOR"
SEVERITIES = (SEVERITY_BLOCKING, SEVERITY_MAJOR, SEVERITY_MINOR)


@dataclass
class Finding:
    code: str
    severity: str  # BLOCKING, MAJOR, MINOR
    evidence: str = ""
    suggested_fix: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
        }


CRITIC_SCHEMA = """{
  "persona_voice": 0-20,
  "audience_relevance": 0-20,
  "natural_writing": 0-20,
  "specific_useful_content": 0-20,
  "evidential_correctness": 0-15,
  "novelty": 0-5,
  "notes": "satu paragraf evaluasi singkat bahasa Indonesia",
  "findings": [
    {
      "code": "spelling_typo | format_mismatch | awkward_phrasing | formal_language | forced_humor | repetitive_structure | absolute_claim | generic_ai_voice | forced_metaphor | unsafe_practical_advice | redundant_parts | artificial_thread_padding | trivial_observation",
      "severity": "BLOCKING | MAJOR | MINOR",
      "evidence": "kutipan teks spesifik yang bermasalah",
      "suggested_fix": "saran perbaikan konkret"
    }
  ],
  "concerns": ["daftar ringkas masalah konkret"],
  "reason_codes": ["kode masalah yang terdeteksi"]
}"""


@dataclass
class CriticVerdict:
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    notes: str = ""
    concerns: list[str] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    ok: bool = True
    error: str = ""

    @property
    def band(self) -> str:
        if self.total_score >= 85:
            return "auto"
        if self.total_score >= 70:
            return "review"
        return "revise"

    @property
    def has_blocking(self) -> bool:
        return any(f.severity == SEVERITY_BLOCKING for f in self.findings)

    @property
    def has_major(self) -> bool:
        return any(f.severity == SEVERITY_MAJOR for f in self.findings)
def _critic_prompt(
    draft_text: str,
    evidence: str,
    pillar: str,
    parts: Optional[list[str]] = None,
    format_name: str = "",
    persona=None,
    path=None,
) -> str:
    p = persona or get_active_persona(path=path)
    identity = persona_prompt_block(p)
    evidence_block = (
        f"BUKTI YANG TERSEDIA:\n{evidence.strip()}\n"
        if evidence.strip() else
        "BUKTI YANG TERSEDIA: (tidak ada — draft tidak boleh memuat angka atau klaim faktual)\n"
    )
    parts_block = ""
    if parts and len(parts) > 1:
        parts_formatted = "\n".join(f"Bagian {idx+1}: {p_item}" for idx, p_item in enumerate(parts))
        parts_block = f"\nBAGIAN-BAGIAN THREAD:\n{parts_formatted}\n"

    format_block = f"FORMAT YANG DITUJU: {format_name}\n" if format_name else ""
    deal_block = ""
    if pillar in ("affiliate", "deal") or format_name in ("deal_validation", "affiliate_deal"):
        deal_block = (
            "CATATAN KHUSUS PILAR DEAL & AFILIASI:\n"
            "- Draf adalah rekomendasi deal / belanja diskon bergaya Threads.\n"
            "- Penggunaan hook diskon (misal 'PLOT TWIST: ...' atau variasi kapital ringkas) dan pertanyaan validasi "
            "komunitas ('Yang udah punya, validasi dong...') adalah format yang disengaja dan SAH. "
            "JANGAN menandainya sebagai generic_ai_voice atau clickbait.\n\n"
        )
    ref_block = get_editorial_reference_prompt()

    return f"""Kamu adalah editor independen. Nilai draf berikut secara jujur, ketat, dan berbasis keparahan (severity).
Kamu BUKAN penulisnya dan tidak perlu memuji.

{identity}

PILAR KONTEN: {pillar}
{format_block}{deal_block}{evidence_block}
DRAF:
\"\"\"
{draft_text}
\"\"\"
{parts_block}

UJIAN KUALITAS & NATURALITAS (7 PERTANYAAN EVALUASI & DEFECT CHECKS):
1. read_aloud_unnatural: Apakah draf terdengar canggung, kaku, atau patah-patah saat dibaca bersuara? [Severity: MAJOR]
2. point_obscure: Apakah poin utamanya langsung dipahami, atau berbelit-belit dan kabur? [Severity: MAJOR]
3. bland_writing: Apakah tulisannya santai tapi hambar tanpa karakter atau sudut pandang menarik? [Severity: MAJOR]
4. no_interesting_thought: Apakah ada pemikiran menarik/segar, atau cuma menyatakan hal basi yang sudah semua orang tahu? [Severity: MAJOR]
5. removable_filler: Apakah ada kalimat pengisi mubazir yang bisa dihapus tanpa mengurangi substansi? [Severity: MAJOR]
6. forced_humor: Apakah humornya garing atau dipaksakan ke dalam topik yang seharusnya lugas? [Severity: MAJOR]
7. ai_writing_assignment: Apakah draf terasa seperti orang sungguhan berkomunikasi atau seperti bot AI yang sedang menyelesaikan tugas esai/LinkedIn ('semangat beraktivitas')? [Severity: BLOCKING jika esai motivasi klise, MAJOR jika gaya kaku]
8. spelling_typo: Apakah ada kata salah ketik / typo nyata (contoh: 'Sebagnya' bukan 'Sebagian')? [Severity: MAJOR]
9. format_mismatch: Jika format multi_paragraph, apakah benar terdiri dari minimal 2 paragraf nyata terpisah jeda baris ganda? Jika hanya 1 paragraf padat, tandai format_mismatch. [Severity: MAJOR]
10. awkward_phrasing: Apakah pembuka atau susunan kalimat berbelit-belit, janggal, atau terjemahan harfiah? [Severity: MAJOR]
11. formal_language: Apakah bahasanya terlalu formal/akademis untuk obrolan santai kawan tongkrongan? [Severity: MINOR atau MAJOR]
12. forced_metaphor / forced_personification: Apakah menganalogikan benda mati punya niat/perasaan/lelah ('kabel nakal', 'baterai ngambek', 'laptop menjerit')? [Severity: BLOCKING]
13. repetitive_structure: Apakah kalimat berulang atau ada kesimpulan klise generik di akhir yang mengulang poin sebelumnya? [Severity: MAJOR]
14. absolute_claim: Apakah membuat klaim yang terlalu mutlak tanpa dasar ('pasti rusak', 'semua orang salah')? [Severity: MAJOR]
15. generic_ai_voice: Klise AI ('niatnya... kenyataannya...', 'di era serba digital', 'saksi bisu'). [Severity: BLOCKING atau MAJOR]
16. artificial_thread_padding: Observasi sepele yang dipaksa jadi thread multi-part. [Severity: BLOCKING]
17. unsafe_practical_advice: Saran teknis berbahaya (contoh: kabel terkelupas dibiarkan). [Severity: BLOCKING]

KLASIFIKASI SEVERITY PADA TEMUAN (findings):
- BLOCKING: Masalah faktual/keamanan berbahaya (unsafe advice, klaim palsu, personifikasi benda mati ekstrim, thread padding parah, klise AI motivasi). Draf ini WAJIB ditolak (REJECTED).
- MAJOR: Masalah bahasa atau format serius (typo nyata, kalimat berbelit-belit, format mismatch, klise AI, bahasa kaku, hambar, filler). Draf ini TIDAK BOLEH lolos otomatis (APPROVED_AUTO), WAJIB revisi (REVISE_ONCE).
- MINOR: Sedikit canggung tapi masih wajar dan tidak mengganggu pemahaman pembaca.

PEMBOBOTAN SKOR (0-100):
- persona_voice (0-20): terdengar seperti kawan tongkrongan, bukan bot/template?
- audience_relevance (0-20): menyentuh masalah audiens nyata?
- natural_writing (0-20): bahasa Indonesia santai alami, irama hidup, tidak kaku?
- specific_useful_content (0-20): isi konkret bermanfaat, bukan kalimat kosong?
- evidential_correctness (0-15): klaim/angka didukung bukti?
- novelty (0-5): tidak mengulang pembuka/template klise?

PENTING: Jangan beri skor tinggi (>=85) jika ada temuan MAJOR atau BLOCKING.
Semua temuan wajib dimasukkan ke array `findings`.

{ref_block}

OUTPUT: hanya JSON valid sesuai skema:
{CRITIC_SCHEMA}"""


def critique(
    draft_text: str,
    evidence: str = "",
    pillar: str = "",
    client: Optional[LLMClient] = None,
    parts: Optional[list[str]] = None,
    format_name: str = "",
    persona=None,
    path=None,
) -> CriticVerdict:
    """Score a draft with severity-aware structured findings."""
    import re

    llm = client or get_llm_client()
    prompt = _critic_prompt(
        draft_text, evidence, pillar, parts=parts, format_name=format_name,
        persona=persona, path=path,
    )
    try:
        data = complete_json(
            llm, [{"role": "user", "content": prompt}],
            temperature=0.2, max_tokens=900,
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
    raw_concerns = data.get("concerns") or []
    if not isinstance(raw_concerns, list):
        raw_concerns = [raw_concerns]
    concerns = [str(c) for c in raw_concerns if c is not None]
    notes = str(data.get("notes") or "").strip()
    raw_codes = data.get("reason_codes") or []
    if not isinstance(raw_codes, list):
        raw_codes = [str(raw_codes)]
    reason_codes = [str(c).strip() for c in raw_codes if str(c).strip()]

    # Parse structured findings
    findings: list[Finding] = []
    raw_findings = data.get("findings") or []
    if isinstance(raw_findings, list):
        for rf in raw_findings:
            if not isinstance(rf, dict):
                continue
            code = str(rf.get("code") or "").strip()
            sev = str(rf.get("severity") or SEVERITY_MINOR).strip().upper()
            if sev not in SEVERITIES:
                sev = SEVERITY_MAJOR if ("major" in sev.lower() or "block" in sev.lower()) else SEVERITY_MINOR
            ev = str(rf.get("evidence") or "").strip()
            fix = str(rf.get("suggested_fix") or "").strip()
            if code:
                findings.append(Finding(code=code, severity=sev, evidence=ev, suggested_fix=fix))
                if code not in reason_codes:
                    reason_codes.append(code)

    # Backstop 1: Check obvious spelling typos in text
    if re.search(r"\bsebagnya\b", draft_text, re.IGNORECASE):
        if not any(f.code == "spelling_typo" for f in findings):
            findings.append(Finding(
                code="spelling_typo",
                severity=SEVERITY_MAJOR,
                evidence="Sebagnya",
                suggested_fix="Ganti dengan 'Sebagian' atau 'Sebagiannya'",
            ))
        if "spelling_typo" not in reason_codes:
            reason_codes.append("spelling_typo")

    # Backstop 2: Format mismatch check if format is declared multi_paragraph
    if format_name == "multi_paragraph" and "\n\n" not in draft_text.strip():
        if not any(f.code == "format_mismatch" for f in findings):
            findings.append(Finding(
                code="format_mismatch",
                severity=SEVERITY_MAJOR,
                evidence="Declared multi_paragraph but contains 1 paragraph without \\n\\n",
                suggested_fix="Pisahkan dengan jeda baris ganda (\\n\\n) atau gunakan format short_paragraph",
            ))
        if "format_mismatch" not in reason_codes:
            reason_codes.append("format_mismatch")

    # Backstop 3: Inspect concerns for critical defect keywords
    lower_concerns = " ".join(concerns).lower()
    if ("typo" in lower_concerns or "salah ketik" in lower_concerns) and not any(f.code == "spelling_typo" for f in findings):
        findings.append(Finding(
            code="spelling_typo",
            severity=SEVERITY_MAJOR,
            evidence="Critic noted spelling typo in concerns",
            suggested_fix="Perbaiki kata yang salah ketik",
        ))
    if ("format" in lower_concerns or "paragraf" in lower_concerns) and not any(f.code == "format_mismatch" for f in findings):
        findings.append(Finding(
            code="format_mismatch",
            severity=SEVERITY_MAJOR,
            evidence="Critic noted format/paragraph issue in concerns",
            suggested_fix="Sesuaikan format dan pembagian paragraf",
        ))

    return CriticVerdict(
        scores=scores,
        total_score=round(total, 2),
        notes=notes,
        concerns=[str(c) for c in concerns],
        reason_codes=reason_codes,
        findings=findings,
        ok=True,
    )
