"""motionklip_parser.py — Extract structured campaign briefs from Motionklip Discord messages."""

import re
import logging
from typing import Any, Optional

logger = logging.getLogger("clipper.motionklip_parser")


def _extract_cpm(text: str) -> int:
    """Extract CPM (Cost Per Mille / rate per 1,000 views) from text."""
    # Pattern 1: e.g. "Rp 15.000 / 1.000 views", "Rp 10.000 / 1k views", "15k / 1k views"
    match = re.search(
        r'(?:Rp\.?\s*)?([0-9]+(?:[\.,][0-9]+)?)\s*(k)?\s*(?:/|per)\s*(?:1\.?000|1k|1000|k|views?|view)',
        text,
        re.IGNORECASE,
    )
    if match:
        val_str = match.group(1)
        has_k = bool(match.group(2))
        return _parse_number_indonesian(val_str, has_k)

    # Pattern 2: Keyword prefix e.g. "Reward: Rp 15.000", "Hadiah Rp 10.000", "CPM: Rp 15.000"
    match = re.search(
        r'(?:reward|hadiah|cpm|rate|payout|bayaran)\s*[:\*]*\s*(?:Rp\.?\s*)?([0-9]+(?:[\.,][0-9]+)?)\s*(k)?',
        text,
        re.IGNORECASE,
    )
    if match:
        val_str = match.group(1)
        has_k = bool(match.group(2))
        return _parse_number_indonesian(val_str, has_k)

    return 0


def _parse_number_indonesian(val_str: str, has_k: bool) -> int:
    """Convert Indonesian formatted number string (e.g. '15.000', '15k', '12.5k') to integer."""
    val_str = val_str.strip()
    if has_k:
        val = float(val_str.replace(",", "."))
        return int(val * 1000)

    # Indonesian thousand separator is dot '.' (e.g. 15.000 = 15000)
    cleaned = val_str.replace(".", "").replace(",", "")
    if cleaned.isdigit():
        val = int(cleaned)
        if val < 100:  # e.g. someone wrote 15 meaning 15k
            return val * 1000
        return val
    return 0


def _extract_budget_percentage(text: str) -> float:
    """Extract remaining budget pool percentage (e.g. 'Sisa 85%'). Defaults to 100.0."""
    match = re.search(
        r'(?:sisa|tersisa|remaining|pool|budget)[^0-9%\n]*([0-9]+(?:[\.,][0-9]+)?)\s*%',
        text,
        re.IGNORECASE,
    )
    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except ValueError:
            pass
    return 100.0


def _extract_durations(text: str) -> tuple[int, int]:
    """Extract min and max duration in seconds. Defaults to (15, 180)."""
    # Range pattern: e.g. "30 - 60 detik", "30-60s", "Durasi Clip: 30 - 60 detik"
    match = re.search(
        r'(?:durasi|duration)?[^0-9\n]*([0-9]+)\s*(?:-|s/d|sampai|to)\s*([0-9]+)\s*(?:detik|s|sec|second)?',
        text,
        re.IGNORECASE,
    )
    if match:
        try:
            min_d = int(match.group(1))
            max_d = int(match.group(2))
            if min_d > 0 and max_d >= min_d:
                return min_d, max_d
        except ValueError:
            pass

    return 15, 180


def _extract_hashtags(text: str) -> list[str]:
    """Extract unique hashtags while preserving order."""
    seen = set()
    tags = []
    for tag in re.findall(r'#\w+', text):
        if tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
    return tags


def _extract_urls(text: str) -> list[str]:
    """Extract all HTTP/HTTPS links from text."""
    urls = []
    seen = set()
    for raw in re.findall(r'https?://[^\s<>"\')]+', text):
        cleaned = re.sub(r'[.,;!?)>\]]+$', '', raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _clean_bullet(line: str) -> str:
    """Strip bullet markers and markdown decorators."""
    cleaned = re.sub(r'^(?:[-*•]|\d+[\.)])\s*', '', line).strip()
    return cleaned.strip('*_')


def _extract_dos_and_donts_from_text(text: str) -> tuple[list[str], list[str]]:
    """Parse Do's and Don'ts sections from unstructured text using a state machine."""
    dos: list[str] = []
    donts: list[str] = []

    state = "NONE"

    for line in text.splitlines():
        line_str = line.strip()
        if not line_str:
            continue

        lower = line_str.lower()

        # Check section transitions
        is_dont_header = any(k in lower for k in ["don't", "dont", "larangan", "pantangan"]) and (
            "🚫" in line_str or "**" in line_str or line_str.endswith(":")
        )
        is_do_header = any(k in lower for k in ["do's", "dos", "syarat", "ketentuan", "wajib", "aturan"]) and (
            "📌" in line_str or "**" in line_str or line_str.endswith(":")
        ) and not is_dont_header

        is_other_header = any(k in lower for k in ["bahan", "footage", "reward", "budget", "durasi", "hashtag"]) and (
            "**" in line_str or line_str.endswith(":") or any(e in line_str for e in ["🎬", "💰", "🎯", "⏱", "🏷"])
        )

        if is_dont_header:
            state = "DONTS"
            continue
        elif is_do_header:
            state = "DOS"
            continue
        elif is_other_header:
            state = "OTHER"
            continue

        # Extract item under active state
        cleaned = _clean_bullet(line_str)
        if not cleaned:
            continue

        if state == "DOS":
            dos.append(cleaned)
        elif state == "DONTS":
            donts.append(cleaned)

    return dos, donts


def parse_discord_campaign_message(raw_msg: dict) -> dict:
    """
    Parse raw Discord message JSON into structured campaign detail dictionary.

    Returns:
        dict with fields:
            id: f"mk-{raw_msg['id']}"
            title: campaign title
            cpm: integer CPM
            budget_percentage: remaining pool percentage
            min_duration: min clip length in seconds
            max_duration: max clip length in seconds
            hashtags: list of mandatory/recommended hashtags
            dos: list of guidelines / do's
            donts: list of forbidden items / don'ts
            assets: list of asset dicts with url, display_name, is_required
            source: "motionklip"
            raw: original message payload
    """
    raw_id = str(raw_msg.get("id", ""))
    campaign_id = raw_id if raw_id.startswith("mk-") else f"mk-{raw_id}"

    content = raw_msg.get("content") or ""
    embeds = raw_msg.get("embeds") or []
    attachments = raw_msg.get("attachments") or []

    # Combine all textual components for full search
    text_chunks = [content]
    for embed in embeds:
        if embed.get("title"):
            text_chunks.append(embed["title"])
        if embed.get("description"):
            text_chunks.append(embed["description"])
        for field in embed.get("fields", []):
            if field.get("name"):
                text_chunks.append(field["name"])
            if field.get("value"):
                text_chunks.append(field["value"])

    full_text = "\n".join(t for t in text_chunks if t)

    # 1. Title Extraction
    title = ""
    # Check content for explicit CAMPAIGN header
    title_match = re.search(
        r'(?:📢|🎯|📌)?\s*\*{0,2}(?:NEW\s+CAMPAIGN|CAMPAIGN)\s*:\s*([^\n\*]+)',
        content,
        re.IGNORECASE,
    )
    if title_match:
        title = title_match.group(1).strip()
    elif embeds and embeds[0].get("title"):
        title = embeds[0]["title"].strip()
    else:
        # Fallback to first non-empty line of content
        lines = [re.sub(r'[*_#~`]', '', l).strip() for l in content.splitlines() if l.strip()]
        if lines:
            title = lines[0]
        else:
            title = "Motionklip Campaign"

    # 2. CPM
    cpm = _extract_cpm(full_text)

    # 3. Budget percentage
    budget_pct = _extract_budget_percentage(full_text)

    # 4. Durations
    min_dur, max_dur = _extract_durations(full_text)

    # 5. Hashtags
    hashtags = _extract_hashtags(full_text)

    # 6. Do's and Don'ts
    dos: list[str] = []
    donts: list[str] = []

    # First check embed fields
    for embed in embeds:
        for field in embed.get("fields", []):
            fname = field.get("name", "").strip().lower()
            fval = field.get("value", "")
            if any(k in fname for k in ["don't", "dont", "larangan", "pantangan"]):
                donts.extend([_clean_bullet(l) for l in fval.splitlines() if _clean_bullet(l)])
            elif any(k in fname for k in ["do's", "dos", "syarat", "ketentuan", "aturan"]):
                dos.extend([_clean_bullet(l) for l in fval.splitlines() if _clean_bullet(l)])

    # If empty or supplementary, extract from text content
    text_dos, text_donts = _extract_dos_and_donts_from_text(content)
    if text_dos:
        for item in text_dos:
            if item not in dos:
                dos.append(item)
    if text_donts:
        for item in text_donts:
            if item not in donts:
                donts.append(item)

    # 7. Assets / Footage URLs
    seen_urls = set()
    assets = []

    # Extract from full text
    for url in _extract_urls(full_text):
        if url not in seen_urls:
            seen_urls.add(url)
            assets.append({
                "url": url,
                "display_name": "",
                "is_required": False,
            })

    # Extract from attachments
    for att in attachments:
        att_url = att.get("url") or att.get("proxy_url")
        if att_url and att_url not in seen_urls:
            seen_urls.add(att_url)
            assets.append({
                "url": att_url,
                "display_name": att.get("filename", ""),
                "is_required": False,
            })

    return {
        "id": campaign_id,
        "title": title,
        "cpm": cpm,
        "budget_percentage": budget_pct,
        "min_duration": min_dur,
        "max_duration": max_dur,
        "hashtags": hashtags,
        "dos": dos,
        "donts": donts,
        "hooks": "",
        "main_focus": "",
        "persona": "",
        "purpose": "",
        "cta": "",
        "assets": assets,
        "source": "motionklip",
        "raw": raw_msg,
    }
