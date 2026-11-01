"""src/content/editor.py — content pre-evaluation cleanup and compression.

Enforces editorial quality before the critic evaluation:
1. Corrects clear Indonesian spelling errors without altering factual claims or specs.
2. Compresses content by removing redundant explanations, trailing generic conclusions,
   and engagement bait while preserving useful facts.
3. Validates and normalizes format against intended structure (preferring concise single posts
   over padded multi-paragraph posts).
"""
from __future__ import annotations

import re
from typing import Optional

# Common clear spelling errors / typos in Indonesian tech & everyday writing.
# Format: (regex_pattern, replacement)
SPELLING_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bsebagnya\b", re.IGNORECASE), "sebagiannya"),
    (re.compile(r"\bbatrei?\b", re.IGNORECASE), "baterai"),
    (re.compile(r"\bbatere\b", re.IGNORECASE), "baterai"),
    (re.compile(r"\bkuwalitas\b", re.IGNORECASE), "kualitas"),
    (re.compile(r"\bkwalitas\b", re.IGNORECASE), "kualitas"),
    (re.compile(r"\bpraktek\b", re.IGNORECASE), "praktik"),
    (re.compile(r"\bresiko\b", re.IGNORECASE), "risiko"),
    (re.compile(r"\bsekedar\b", re.IGNORECASE), "sekadar"),
    (re.compile(r"\bobsi\b", re.IGNORECASE), "opsi"),
    (re.compile(r"\btekhnologi\b", re.IGNORECASE), "teknologi"),
    (re.compile(r"\bteknology\b", re.IGNORECASE), "teknologi"),
    (re.compile(r"\bkouta\b", re.IGNORECASE), "kuota"),
    (re.compile(r"\bquata\b", re.IGNORECASE), "kuota"),
    (re.compile(r"\bkuatir\b", re.IGNORECASE), "khawatir"),
    (re.compile(r"\bkawatir\b", re.IGNORECASE), "khawatir"),
    (re.compile(r"\bngcas\b", re.IGNORECASE), "ngecas"),
    (re.compile(r"\bngecharge\b", re.IGNORECASE), "ngecas"),
    (re.compile(r"\bmeriview\b", re.IGNORECASE), "me-review"),
    (re.compile(r"\bmerivew\b", re.IGNORECASE), "me-review"),
    (re.compile(r"\bdi cas\b", re.IGNORECASE), "dicas"),
]

# Generic concluding platitudes and tautologies that restate prior points without new info.
GENERIC_CONCLUSION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:kapasitas\s+besar|spek\s+tinggi|fitur\s+banyak)\s+nggak\s+ada\s+gunanya\s+kalau\s+[^.!?]+[.!?]?$", re.IGNORECASE),
    re.compile(r"(?:semua|semuanya)\s+balik\s+lagi\s+ke\s+kebutuhan\s+(?:masing-masing|kalian|kita)[.!?]?$", re.IGNORECASE),
    re.compile(r"pilihan\s+terbaik\s+adalah\s+yang\s+(?:paling\s+)?sesuai\s+(?:sama\s+)?kebutuhan[.!?]?$", re.IGNORECASE),
    re.compile(r"intinya\s+(?:sih\s+)?,?\s*jangan\s+sampai\s+salah\s+(?:beli|pilih)[.!?]?$", re.IGNORECASE),
    re.compile(r"(?:gimana\s+menurut\s+kalian|menurut\s+kalian|ada\s+yang\s+ngalamin\s+juga|kalian\s+tim\s+mana)\s*\??$", re.IGNORECASE),
]

# Redundant filler preambles that add no information.
FILLER_PREAMBLES: list[re.Pattern] = [
    re.compile(r"^perlu\s+diingat\s+(?:bahwa\s+)?,?\s*", re.IGNORECASE),
    re.compile(r"^perlu\s+diketahui\s+(?:bahwa\s+)?,?\s*", re.IGNORECASE),
    re.compile(r"^bisa\s+dibilang\s+(?:bahwa\s+)?,?\s*", re.IGNORECASE),
]


def _match_case(orig: str, repl: str) -> str:
    """Preserve TitleCase or lowerCase of original word."""
    if orig.isupper():
        return repl.upper()
    if orig and orig[0].isupper():
        return repl.capitalize()
    return repl.lower()


def correct_spelling(text: str) -> tuple[str, list[dict]]:
    """Correct clear spelling errors without touching numbers, brands, or specs."""
    if not text:
        return text, []

    edits = []
    out = text

    for pattern, repl in SPELLING_REPLACEMENTS:
        matches = list(pattern.finditer(out))
        if not matches:
            continue
        # Apply in reverse offset order to keep indices valid
        for m in reversed(matches):
            orig_word = m.group(0)
            cased_repl = _match_case(orig_word, repl)
            start, end = m.span()
            out = out[:start] + cased_repl + out[end:]
            edits.append({
                "original": orig_word,
                "corrected": cased_repl,
                "reason": "spelling_typo",
            })

    return out, edits


def compress_content(text: str) -> tuple[str, list[str]]:
    """Remove redundant explanations, unnecessary sentences, and generic conclusions.

    Preserves all facts, specs, numbers, and actionable tips.
    """
    if not text or not text.strip():
        return text, []

    removals = []
    paragraphs = text.split("\n\n")
    cleaned_paragraphs = []

    for para in paragraphs:
        p = para.strip()
        if not p:
            continue

        # Split into sentences while keeping punctuation
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
        if not sentences:
            continue

        cleaned_sentences = []
        for s in sentences:
            # Check for preamble filler
            orig_s = s
            for pf in FILLER_PREAMBLES:
                if pf.match(s):
                    s = pf.sub("", s)
                    removals.append(f"filler_preamble: '{orig_s[:30]}...'")
                    break

            # Check if sentence is a generic concluding platitude / redundant conclusion
            is_redundant = False
            for pat in GENERIC_CONCLUSION_PATTERNS:
                if pat.search(s):
                    # Only remove if it's not the ONLY sentence
                    if len(sentences) > 2:
                        is_redundant = True
                        removals.append(f"redundant_conclusion: '{s[:40]}...'")
                        break
            if not is_redundant:
                cleaned_sentences.append(s)

        if cleaned_sentences:
            cleaned_paragraphs.append(" ".join(cleaned_sentences))

    out_text = "\n\n".join(cleaned_paragraphs)
    return out_text, removals


def validate_and_normalize_format(
    content: str,
    parts: Optional[list[str]],
    intended_format: str,
) -> tuple[str, str, list[dict]]:
    """Validate actual formatting against intended structure.

    Prefer concise single post over a padded multi-paragraph post.
    Returns: (normalized_content, effective_format, format_issues)
    """
    issues = []
    effective_format = intended_format or "single_snappy"
    normalized_content = content.strip() if content else ""

    if not normalized_content:
        return normalized_content, effective_format, issues

    paragraphs = [p.strip() for p in normalized_content.split("\n\n") if p.strip()]

    # Multi-paragraph validation
    if intended_format == "multi_paragraph":
        if len(paragraphs) < 2:
            # Check if text is naturally a concise single post (< 50 words)
            words = normalized_content.split()
            if len(words) <= 50:
                # Prefer concise single post over artificial padding
                effective_format = "short_paragraph"
                issues.append({
                    "code": "format_normalized",
                    "severity": "MINOR",
                    "evidence": f"{len(words)} words in 1 paragraph",
                    "suggested_fix": "Normalized from multi_paragraph to short_paragraph",
                })
            else:
                # Try to split cleanly at a logical pivot if present
                split_patterns = [
                    r"(?<=[.!?])\s+(?=(?:Kesalahan\s+umum|Padahal|Daripada|Solusinya|Tips:|Mending)\b)",
                    r"(?<=[.!?])\s+(?=(?:Yang\s+berguna|Prioritas\s+pertama|Bandingkan)\b)",
                ]
                split_done = False
                for sp in split_patterns:
                    parts_split = re.split(sp, normalized_content, maxsplit=1)
                    if len(parts_split) == 2 and all(p.strip() for p in parts_split):
                        normalized_content = f"{parts_split[0].strip()}\n\n{parts_split[1].strip()}"
                        split_done = True
                        break
                if not split_done:
                    issues.append({
                        "code": "format_mismatch",
                        "severity": "MAJOR",
                        "evidence": "Format declared multi_paragraph but contains only 1 paragraph",
                        "suggested_fix": "Pisahkan dengan jeda baris ganda (\\n\\n) atau gunakan format short_paragraph",
                    })

    # Thread validation
    elif intended_format == "mini_thread":
        if not parts or len(parts) < 2:
            effective_format = "short_paragraph"
            issues.append({
                "code": "format_normalized",
                "severity": "MINOR",
                "evidence": f"Thread has {len(parts) if parts else 0} parts",
                "suggested_fix": "Normalized from mini_thread to short_paragraph",
            })

    return normalized_content, effective_format, issues


def edit_and_clean_draft(
    content: str,
    parts: Optional[list[str]] = None,
    intended_format: str = "",
) -> tuple[str, list[str], str, list[dict]]:
    """Complete editorial cleaning pass: spellcheck, compression, and format check.

    Returns: (cleaned_content, cleaned_parts, effective_format, applied_edits)
    """
    applied_edits = []

    # 1. Spelling correction
    spelled_content, spell_edits = correct_spelling(content)
    applied_edits.extend(spell_edits)

    spelled_parts = []
    if parts:
        for p in parts:
            p_clean, p_edits = correct_spelling(p)
            spelled_parts.append(p_clean)
            applied_edits.extend(p_edits)
    else:
        spelled_parts = []

    # 2. Content compression
    compressed_content, comp_removals = compress_content(spelled_content)
    for r in comp_removals:
        applied_edits.append({"action": "compression", "detail": r})

    compressed_parts = []
    if spelled_parts:
        for p in spelled_parts:
            p_comp, p_rem = compress_content(p)
            compressed_parts.append(p_comp)
            for r in p_rem:
                applied_edits.append({"action": "compression_part", "detail": r})

    # 3. Format validation & normalization
    norm_content, eff_format, format_issues = validate_and_normalize_format(
        compressed_content, compressed_parts, intended_format
    )
    applied_edits.extend(format_issues)

    return norm_content, compressed_parts, eff_format, applied_edits
