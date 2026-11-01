"""src/content/writer.py — post candidate generation with no fallback filler.

V1's generator, when the model returned unparseable JSON, published:

    "Isu {topic} ini punya implikasi nyata kalau dicermati secara objektif.
     Dampaknya ke depan bakal signifikan."

That sentence is content-farm filler and it reached a live account. V2's writer
has no such path: a failed generation returns None, the slot is skipped, and
nothing is written. Acceptance criterion #4.

The writer also never receives a news headline. It receives an audience
problem and an angle from the strategy layer, so it produces useful content
with every trend source disabled — acceptance criterion #2.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from src.audience.signals import AudienceSignal, get_signals
from src.core.clock import utc_iso
from src.core.llm import LLMClient, LLMError, FakeLLMClient, complete_json, get_llm_client
from src.core.models import ContentDraft, DRAFT
from src.core.storage import connect, transaction
from src.logger import get_logger
from src.persona.profile import get_active_persona, persona_prompt_block
from src.persona.reference_library import get_editorial_reference_prompt
from src.persona.voice import (
    VoiceVariation, decide_format, length_guide, mood_guide, opening_guide,
    goal_guide, random_variation, should_allow_thread, structure_for_format,
    varied_variation,
)
from src.strategy.ideation import IdeaCandidate, generate_ideas, save_idea

log = get_logger("content_writer")

# Variation history is kept in memory per process; the durable copy lives in
# content_ideas.angle so a restart does not reset the anti-repetition window.
_variation_history: list[tuple] = []


@dataclass
class DraftResult:
    """A draft plus the provenance needed to evaluate and audit it."""

    content: str
    parts: list[str]
    structure: str
    pillar: str
    angle: str
    audience_problem: str
    persona_version: str
    variation: dict
    evidence: str = ""
    disclosure: str = ""
    raw_content: str = ""
    applied_edits: list[dict] = field(default_factory=list)
    idea_id: Optional[int] = None
    draft_id: Optional[int] = None
    created_at: str = field(default_factory=utc_iso)
    @property
    def idea_pillar(self) -> str:
        return self.pillar


def _writer_prompt(
    idea: IdeaCandidate,
    signal: Optional[AudienceSignal],
    variation: VoiceVariation,
    evidence: str,
    revision_feedback: Optional[str] = None,
    path=None,
    persona=None,
) -> str:
    p = persona or get_active_persona(path=path)
    identity = persona_prompt_block(p)

    var_lines = "\n".join([
        f"- Format: {variation.format}",
        f"- Pembuka: {opening_guide(variation.opening)}",
        f"- Panjang: {length_guide(variation.length)}",
        f"- Mood: {mood_guide(variation.mood)}",
        f"- Tujuan: {goal_guide(variation.goal)}",
    ])

    if evidence.strip():
        evidence_block = (
            "BUKTI YANG BOLEH DIPAKAI (satu-satunya sumber fakta):\n"
            f"{evidence.strip()}\n"
            "- Setiap angka, harga, spesifikasi, atau klaim faktual HARUS ada di blok ini.\n"
            "- Kalau tidak ada di blok ini, jangan ditulis sama sekali.\n"
        )
    else:
        evidence_block = (
            "BUKTI YANG BOLEH DIPAKAI: (tidak ada)\n"
            "- DILARANG memakai angka, persentase, harga, atau spesifikasi apa pun.\n"
            "- DILARANG menyebut hasil riset, studi, atau survei.\n"
            "- Tulis versi tanpa angka: observasi, pengalaman umum, dan trade-off saja.\n"
        )

    revision_block = ""
    if revision_feedback:
        revision_block = (
            "\nREVISI DIMINTA OPERATOR (wajib dipatuhi):\n"
            f"{revision_feedback.strip()}\n"
        )

    # Durable feedback memory. Distinct from `revision_feedback`: that is the
    # operator's note on THIS draft, this is the rolling window of everything
    # they have rejected recently. Without it a rejection is a message that
    # scrolls away and the next draft is generated from the same prompt.
    memory_block = ""
    try:
        from src.persona.editorial_memory import revision_guidance

        guidance = revision_guidance(path=path)
        if guidance:
            memory_block = f"\n{guidance}\n"
    except Exception:  # noqa: BLE001 — memory is an enhancement, never a blocker
        memory_block = ""

    is_thread = structure_for_format(variation.format) == "thread"
    if is_thread:
        schema = (
            '{\n  "parts": ["bagian 1", "bagian 2", "bagian 3"],\n'
            '  "disclosure": ""\n}'
        )
        structure_rule = (
            "- Tulis tepat 2-4 bagian pendek. Tiap bagian maksimal 3 kalimat dan satu ide.\n"
            "- Jangan gabungkan jadi satu paragraf panjang.\n"
        )
    else:
        schema = '{\n  "content": "postingan",\n  "disclosure": ""\n}'
        structure_rule = (
            "- Satu postingan utuh. Boleh satu kalimat atau beberapa paragraf pendek "
            "sesuai panjang yang diminta.\n"
        )
    ref_block = get_editorial_reference_prompt()

    # Affiliate drafts must ship their own disclosure: the gate rejects an
    # undisclosed commercial post, and a writer that leaves the field empty
    # turns every affiliate slot into a rewrite.
    if idea.pillar == "affiliate":
        from src.commerce.disclosure import DISCLOSURE_SHORT

        disclosure_block = (
            "\nKEWAJIBAN AFILIASI:\n"
            f'- Isi field "disclosure" dengan teks ini persis: "{DISCLOSURE_SHORT}"\n'
            "- Sebut nama produk dan angkanya HANYA dari blok bukti di atas.\n"
        )
    else:
        disclosure_block = (
            '\nIsi field "disclosure" dengan string kosong ("") '
            "kecuali postingan ini memang berbayar.\n"
        )

    return f"""Kamu adalah penulis konten untuk akun Threads berikut.

{identity}

PROSES KREATIF EDITORIAL (Sol-Inspired Conversational):
1. Premis Audiens: Mulai dari masalah audiens nyata.
2. Observasi Menarik: Pilih sudut pandang yang segar, santai, dan cerdas.
3. Panjang Mengikuti Substansi: Tentukan panjang dari substansi, bukan dari kuota kata. Observasi sederhana tuntas dalam 15-20 kata; trade-off atau tips teknis nyata bisa 50-70 kata. Jangan paksakan kalimat pengisi.
4. Uji Baca Bersuara (Read-Aloud): Kalimat harus mengalir alami seperti obrolan orang sungguhan, bukan esai tugas sekolah atau postingan LinkedIn.
5. Bebas Klise AI: DILARANG penutup motivasi generik ('semangat beraktivitas', 'gunakan teknologi dengan bijak') atau lelucon formulaic basi.

TUGAS:
Tulis satu postingan untuk masalah audiens ini:
  Masalah: {idea.audience_problem}
  Sudut pandang: {idea.angle}
  Pilar konten: {idea.pillar}

{evidence_block}
{revision_block}
{memory_block}

VARIASI POSTINGAN INI (identitas tetap sama):
{var_lines}

ATURAN STRUKTUR & GAYA BAHASA:
{structure_rule}
- Kalimat pertama langsung ke inti. Tanpa sapaan, tanpa 'lagi rame', tanpa meta-commentary.
- DILARANG membuka dengan pertanyaan retoris yang menunda poin ('Kenapa masih ...?', 'Pernah nggak sih ...?', 'Sadar nggak ...?'). Kalau ide itu berupa pertanyaan, ubah jadi pernyataan.
- DILARANG menutup dengan pertanyaan klise ('menurut kalian', 'gimana menurut kalian', 'ada yang ngerasain', 'kalian tim mana').
- DILARANG hashtag (#).
- DILARANG mengaku pernah membeli, memakai, menguji, atau mengalami produk/kejadian.
- DILARANG promosi, link, atau nama produk kecuali pilar affiliate dan sudah disetujui.
- DILARANG personifikasi benda mati ('nakal', 'ngambek', 'lelah', 'butuh perhatian'). Benda mati tidak berakal.
- DILARANG transisi klise AI ('niatnya biar praktis... kenyataannya...', 'di era serba digital', 'saksi bisu').
- DILARANG penutup motivasi klise ('semangat beraktivitas', 'jadikan hari ini produktif').
- DILARANG saran teknis/praktis berbahaya (kabel tembaga terbuka harus diganti/diisolasi, bukan dibiarkan).
- Bahasa Indonesia natural santai ala kawan tongkrongan, tidak kaku buku teks, tidak terjemahan bahasa Inggris harfiah.
- TIDAK WAJIB menggunakan kata 'aku' atau 'kalian'. Utamakan kalimat mengalir alami dan isi bermanfaat tanpa menyelipkan kata ganti secara mekanis.
- FORMAT NYATA: Jika format 'multi_paragraph', WAJIB dipisahkan jeda baris ganda (\\n\\n) menjadi 2 paragraf nyata yang logis. Jangan gabungkan jadi 1 bongkahan teks tanpa jeda.

{ref_block}
{disclosure_block}
OUTPUT: hanya JSON valid, tanpa markdown fence, tanpa teks lain.
Skema:
{schema}"""


def _repair_json(raw: str) -> str:
    """Repair the structural JSON defects real models actually emit.

    Observed against tig/step-5-unrestricted on this prompt:

        {
         "content": "...."
         "disclosure":
        }

    A key with **no value at all**. `json.loads` rejects it and the draft is
    lost even though the prose is fine. Other defects handled: a missing comma
    between members, structural smart quotes, a trailing comma, and a stray
    markdown fence.

    Rules this repair obeys:
      * It only ever supplies EMPTY values. It never invents, truncates, or
        rewrites content — a repaired draft carries exactly the words the
        model produced.
      * Smart quotes are only converted when they are *structural*. A smart
        quote that opens a value (right after `:` `,` `[` `{`) or closes one
        (right before `,` `}` `]`) is a delimiter and becomes `"`. A smart
        quote inside a string is content and is left alone.
      * It is idempotent: repairing already-repaired text is a no-op, so a
        retry cannot corrupt a draft that was already recovered.
      * It is bounded: one pass per rule, no recursion.
      * If it cannot produce parseable JSON it returns the input unchanged and
        the caller fails closed.
    """
    import re

    t = raw.strip()

    # Strip a markdown fence if the model added one despite instructions.
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
        t = t.strip()

    # Structural smart quotes only.
    t = re.sub(r'([:,\[{]\s*)\u201c', r'\1"', t)
    t = re.sub(r'\u201d(?=\s*[,}\]])', '"', t)

    # A key with no value: `"key":` followed by , } ] or a newline. The colon,
    # any whitespace, AND the newline are all consumed, and `""` is written in
    # their place. Consuming the newline is what makes this idempotent — if it
    # were left behind, a second pass would read it as a missing comma.
    t = re.sub(
        r'("(?:[^"\\]|\\.)*"\s*:\s*)[\n\r\s]*(?=[,}\]])',
        r'\1""',
        t,
    )

    # Missing comma between members: a closing quote or bracket at end of line
    # followed by a key on the next line.
    t = re.sub(r'("\s*|\]\s*)\n(\s*")', r'\1,\n\2', t)

    # Trailing comma before a closing bracket.
    t = re.sub(r",\s*([}\]])", r"\1", t)

    return t


def _parse_draft_payload(raw: str) -> tuple[str, list[str], str]:
    """Parse the writer's JSON. Raises on anything unusable — no fallback.

    One bounded structural repair is attempted before giving up. The repair
    only supplies empty values for malformed keys; it never fabricates
    content, so a draft that survives it is still entirely the model's words.
    """
    import json

    text = raw.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_json(raw)
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned non-JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")

    disclosure = str(data.get("disclosure") or "").strip()
    if "parts" in data:
        parts = data.get("parts")
        if not isinstance(parts, list) or not parts:
            raise LLMError("thread draft has no usable parts")
        cleaned = [str(p).strip() for p in parts if str(p).strip()]
        if not cleaned:
            raise LLMError("thread draft parts are empty")
        return "\n\n".join(cleaned), cleaned, disclosure
    content = str(data.get("content") or "").strip()
    if not content:
        raise LLMError("draft content is empty")
    return content, [], disclosure


class _StaticClient:
    """Adapter so _parse_draft_payload can reuse complete_json's strictness."""

    def __init__(self, text: str):
        self._text = text

    def complete(self, messages, temperature=0.4, max_tokens=1024):
        from src.core.llm import LLMResponse
        return LLMResponse(text=self._text, model="static", latency_ms=0, attempts=1)


def _affiliate_locked() -> bool:
    """True while affiliate content is not authorised.

    `AFFILIATE_AUTO_ENABLED` is the single switch for commercial output. While
    it is off, an affiliate slot must not produce a recommendation: the writer
    downgrades the slot to organic content instead of generating a post the
    gate would then reject for missing disclosure or unverified tracking.
    """
    from src.core.config import get_settings

    return not get_settings().flags.live_affiliate_allowed


def write_draft(
    pillar: Optional[str] = None,
    idea: Optional[IdeaCandidate] = None,
    evidence: str = "",
    revision_feedback: Optional[str] = None,
    client: Optional[LLMClient] = None,
    rng=None,
    path=None,
    persist: bool = True,
    persona=None,
) -> Optional[DraftResult]:
    """Generate one draft. Returns None when no acceptable draft exists.
    means: publish filler instead.

    An affiliate slot is downgraded to organic while affiliate output is
    locked, so the account keeps its rhythm without a commercial claim nobody
    authorised.
    """
    import random as _random

    r = rng or _random.Random()
    llm = client or get_llm_client()

    if pillar == "affiliate" and _affiliate_locked():
        log.info(
            "Affiliate is locked (AFFILIATE_AUTO_ENABLED off); "
            "downgrading the slot to practical_tips."
        )
        pillar = "practical_tips"

    if idea is None:
        ideas = generate_ideas(pillar=pillar, count=3, rng=r, path=path)
        if not ideas:
            log.warning("No ideas available for pillar=%s; slot skipped.", pillar)
            return None
        idea = ideas[0]

    signal = None
    if idea.signal_id:
        for s in get_signals(limit=500, path=path):
            if s.id == idea.signal_id:
                signal = s
                break

    try:
        allow_t = should_allow_thread(idea, evidence=evidence, pillar=pillar)
        variation = varied_variation(_variation_history, r, allow_threads=allow_t)
    except TypeError:
        variation = varied_variation(_variation_history, r)
    prompt = _writer_prompt(
        idea, signal, variation, evidence, revision_feedback, path=path, persona=persona
    )
    try:
        resp = llm.complete(
            [{"role": "user", "content": prompt}],
            temperature=0.85,
            max_tokens=900,
        )
    except LLMError as exc:
        log.warning("Writer LLM failed: %s — slot skipped, no filler.", exc)
        return None
    except Exception as exc:  # noqa: BLE001
        # A transport error, a timeout, or an unexpected provider response is
        # still "no draft", not a crash. The scheduler loop has its own guard,
        # but a writer that raises on a bad HTTP status turns one flaky call
        # into a failed scheduling pass.
        log.warning(
            "Writer call failed unexpectedly (%s: %s) — slot skipped, no filler.",
            type(exc).__name__, exc,
        )
        return None

    try:
        content, parts, disclosure = _parse_draft_payload(resp.text)
    except LLMError as exc:
        log.warning("Writer produced unusable output (%s) — slot skipped, no filler.", exc)
        return None
    # The chosen variation decides the shape, not whatever the model felt like
    # returning. A single_snappy variation that comes back with `parts` is a
    # model that ignored the schema: reject it rather than silently publishing
    # a thread nobody asked for.
    expected_structure = structure_for_format(variation.format)
    if expected_structure == "single" and parts:
        log.warning(
            "Writer returned a thread for a %s variation; slot skipped.",
            variation.format,
        )
        return None
    if expected_structure == "thread" and not parts:
        log.warning(
            "Writer returned a single post for a %s variation; slot skipped.",
            variation.format,
        )
        return None

    # Editorial cleanup & compression pass on valid structure
    from src.content.editor import edit_and_clean_draft

    raw_content = content
    content, parts, eff_format, applied_edits = edit_and_clean_draft(
        content, parts=parts, intended_format=variation.format
    )
    if eff_format != variation.format:
        variation.format = eff_format
    persona = persona or get_active_persona(path)
    result = DraftResult(
        content=content,
        parts=parts,
        structure="thread" if parts else "single",
        pillar=idea.pillar,
        angle=idea.angle,
        audience_problem=idea.audience_problem,
        persona_version=persona.version,
        variation=variation.to_dict(),
        evidence=evidence,
        disclosure=disclosure,
        raw_content=raw_content,
        applied_edits=applied_edits,
    )

    _variation_history.append(variation.key())
    if len(_variation_history) > 12:
        _variation_history.pop(0)

    if persist:
        result.idea_id = save_idea(idea, path=path)
        result.draft_id = save_draft(result, path=path)

    return result


def write_draft_with_rewrite_loop(
    pillar: Optional[str] = None,
    client: Optional[object] = None,
    critic_client: Optional[object] = None,
    evidence: str = "",
    path=None,
    persist: bool = True,
    persona=None,
) -> tuple[Optional[DraftResult], Optional[object]]:
    """Generate a draft with a bounded 2-stage rewrite loop.

    If Critic returns REVISE_ONCE on the first draft, the critic's concerns
    are fed back to the writer for exactly one rewrite attempt.
    """
    from src.content.quality_gate import (
        APPROVED_AUTO, NEEDS_REVIEW, REJECTED, REVISE_ONCE, evaluate_draft,
    )

    draft = write_draft(
        pillar=pillar, client=client, evidence=evidence, path=path, persist=persist, persona=persona,
    )
    if draft is None:
        return None, None
    format_name1 = draft.variation.get("format", "") if isinstance(draft.variation, dict) else ""
    eval1 = evaluate_draft(
        draft.content,
        parts=draft.parts,
        evidence=evidence,
        pillar=draft.pillar,
        draft_id=draft.draft_id,
        client=critic_client or client,
        path=path,
        persist=persist,
        format_name=format_name1,
        persona=persona,
    )

    if eval1.verdict in (APPROVED_AUTO, NEEDS_REVIEW) or eval1.verdict == REJECTED:
        return draft, eval1

    # Stage 2: REVISE_ONCE -> feed feedback back for 1 rewrite attempt
    feedback_items = []
    if hasattr(eval1, "findings") and eval1.findings:
        for f in eval1.findings:
            sev = f.get("severity", "")
            code = f.get("code", "")
            ev = f.get("evidence", "")
            fix = f.get("suggested_fix", "")
            feedback_items.append(f"[{sev}] {code}: '{ev}' -> Saran: {fix}")
    elif hasattr(eval1, "concerns") and eval1.concerns:
        feedback_items.extend(eval1.concerns)
    if not feedback_items:
        feedback_items = ["Perbaiki alur, hindari klise AI dan personifikasi berlebihan."]
    concerns = "\n".join(feedback_items)

    draft2 = write_draft(
        pillar=draft.pillar,
        client=client,
        evidence=evidence,
        revision_feedback=concerns,
        path=path,
        persist=persist,
        persona=persona,
    )
    if draft2 is None:
        return draft, eval1

    format_name2 = draft2.variation.get("format", "") if isinstance(draft2.variation, dict) else ""
    eval2 = evaluate_draft(
        draft2.content,
        parts=draft2.parts,
        evidence=evidence,
        pillar=draft2.pillar,
        draft_id=draft2.draft_id,
        client=critic_client or client,
        path=path,
        persist=persist,
        format_name=format_name2,
        persona=persona,
    )
    return draft2, eval2
def save_draft(draft: DraftResult, path=None) -> int:
    editorial_metadata = {
        "premise": draft.audience_problem,
        "pillar": draft.pillar,
        "angle": draft.angle,
        "format": draft.variation.get("format", "") if isinstance(draft.variation, dict) else "",
        "opening": draft.variation.get("opening", "") if isinstance(draft.variation, dict) else "",
        "mood": draft.variation.get("mood", "") if isinstance(draft.variation, dict) else "",
        "goal": draft.variation.get("goal", "") if isinstance(draft.variation, dict) else "",
        "payoff": draft.variation.get("goal", "") if isinstance(draft.variation, dict) else "",
        "structure": draft.structure,
    }
    with connect(path) as conn:
        with transaction(conn):
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(content_drafts)").fetchall()]
            if "editorial_metadata_json" in cols:
                cur = conn.execute(
                    """
                    INSERT INTO content_drafts
                        (idea_id, content, structure, persona_version, draft_version,
                         status, parts_json, image_query, disclosure, editorial_metadata_json)
                    VALUES (?, ?, ?, ?, 1, ?, ?, '', ?, ?)
                    """,
                    (
                        draft.idea_id, draft.content, draft.structure,
                        draft.persona_version, DRAFT,
                        json.dumps(draft.parts, ensure_ascii=False), draft.disclosure,
                        json.dumps(editorial_metadata, ensure_ascii=False),
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO content_drafts
                        (idea_id, content, structure, persona_version, draft_version,
                         status, parts_json, image_query, disclosure)
                    VALUES (?, ?, ?, ?, 1, ?, ?, '', ?)
                    """,
                    (
                        draft.idea_id, draft.content, draft.structure,
                        draft.persona_version, DRAFT,
                        json.dumps(draft.parts, ensure_ascii=False), draft.disclosure,
                    ),
                )
            return int(cur.lastrowid)


def get_draft(draft_id: int, path=None) -> Optional[dict]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM content_drafts WHERE id = ?", (draft_id,)
        ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["parts"] = json.loads(out.get("parts_json") or "[]")
    out["editorial_metadata"] = json.loads(out.get("editorial_metadata_json") or "{}")
    return out


def set_draft_status(draft_id: int, status: str, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_drafts SET status = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (status, draft_id),
            )


def bump_draft_version(draft_id: int, path=None) -> int:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_drafts SET draft_version = draft_version + 1, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (draft_id,),
            )
            row = conn.execute(
                "SELECT draft_version FROM content_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
            return int(row["draft_version"]) if row else 0


def recent_draft_contents(limit: int = 30, path=None) -> list[str]:
    """Recent draft bodies, for the similarity guard."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT content FROM content_drafts ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["content"] for r in rows]


def recent_published_contents(limit: int = 30, path=None) -> list[str]:
    """Published organic_posts bodies — the real duplication surface."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT content FROM organic_posts "
            "WHERE content IS NOT NULL AND content != '' "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [r["content"] for r in rows]


def reset_variation_history() -> None:
    _variation_history.clear()
