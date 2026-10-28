"""
Brief Generation Layer
- generate_brief()     — LLM call → Markdown brief
- compliance_check()   — LLM call → JSON evaluation
- revise_brief()       — LLM call → revised Markdown
- run_brief_pipeline() — generates + compliance-loops up to 2 revisions
"""

import json
import re
import time
import requests

from src.config import (
    LLM_API_KEY,
    LLM_MODEL,
    LLM_FALLBACK_MODELS,
    OPENROUTER_URL,
    DATA_DIR,
)
from src.logger import get_logger
from src.guardrails import (
    check_and_increment_openrouter,
    extract_json_safely,
    sanitize_markdown,
    log_failure,
    retry_with_backoff,
)

log = get_logger("brief")


def _call_provider(url: str, key: str, model: str, messages: list[dict], temperature: float, max_tokens: int) -> str:
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    r = requests.post(url, json=payload, headers=headers, timeout=60)
    r.raise_for_status()
    data = r.json()
    choices = data.get("choices", [])
    if choices and "message" in choices[0]:
        msg = choices[0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
        if content:
            return content.strip()
    raise RuntimeError(f"Empty LLM output from {model}: {data}")


# ── LLM helper (guarded with auto-fallback) ───────────────────────────────────

@retry_with_backoff(max_retries=2, base_delay=2.0)
def _llm(messages: list[dict], temperature: float = 0.3, max_tokens: int = 4096) -> str:
    check_and_increment_openrouter()

    # Only inject a generic system prompt when the caller hasn't provided one.
    # Bot agents (tiktok_agent, threads_agent) pass their own persona system prompt;
    # overriding it here would destroy their character.
    if not messages or messages[0].get("role") != "system":
        system_msg = {
            "role": "system",
            "content": (
                "You are a direct, concise assistant. "
                "Output ONLY the final requested content. "
                "Do NOT think step by step or write down reasoning. "
                "Start your response immediately with the content."
            ),
        }
        full_messages = [system_msg] + messages
    else:
        full_messages = messages

    # 1. Primary: Trust Is Gold gateway, then its model fallback chain.
    # Same endpoint and key, so a 503 on one model usually means that upstream
    # slot is cold — the next model in the chain is a fresh roll.
    chain = [LLM_MODEL] + [m for m in LLM_FALLBACK_MODELS if m != LLM_MODEL]
    errors = []
    for model in chain:
        try:
            return _call_provider(OPENROUTER_URL, LLM_API_KEY, model, full_messages, temperature, max_tokens)
        except Exception as e_model:
            errors.append(f"{model}: {e_model}")
            log.warning(f"LLM ({model}) failed: {e_model}. Trying next in chain...")
    raise RuntimeError(f"All LLM providers failed. Chain: {'; '.join(errors)}")


# ── 2.2 Generate Brief ───────────────────────────────────────────────────────

def generate_brief(topic: str, keyword: str) -> str:
    """Returns a 1200-word affiliate-content Markdown brief."""
    prompt = f"""You are an affiliate-content strategist.
Given the topic "{topic}" and primary keyword "{keyword}", create a brief that includes:
- Title (H1)
- 4 H2 sub-headings
- 150-word intro
- 3-bullet "key benefits" section
- 150-word recommendation paragraph with a CTA "Click here to buy now and save 20% with our exclusive link."
- Disclosure line: "This post contains affiliate links."
- Use these placeholders exactly: {{{{product_name}}}}, {{{{price}}}}, {{{{affiliate_url}}}}
Word target: 1200.
Output must be clean Markdown."""
    log.info(f"Generating brief: topic='{topic}', keyword='{keyword}'")
    brief = _llm([{"role": "user", "content": prompt}], temperature=0.2)
    brief = sanitize_markdown(brief)
    log.info("Brief generated")
    return brief


# ── 2.4 Compliance Check ─────────────────────────────────────────────────────

def compliance_check(brief: str) -> dict:
    """
    Returns dict: {approved, score, reasons, required_fixes, suggested_title}
    """
    prompt = f"""You are a strict compliance reviewer for affiliate content.
Output MUST be a single raw JSON object with NO preamble, NO markdown codeblocks, and NO conversational text.
Required JSON format:
{{"approved": true, "score": 90, "reasons": ["well structured"], "required_fixes": [], "suggested_title": "Best Ergonomic Mouse"}}

Brief to review:
{brief}"""
    raw = _llm([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=4096)

    result = extract_json_safely(raw)
    if result:
        log.info(f"Compliance: approved={result.get('approved')}, score={result.get('score')}")
        return result

    # Edge Case 2: retry once at temp 0.2 for cleaner JSON
    log.warning("Compliance JSON malformed on first try, retrying at temp=0.2")
    raw2 = _llm([{"role": "user", "content": prompt}], temperature=0.2, max_tokens=700)
    result2 = extract_json_safely(raw2)
    if result2:
        log.info(f"Compliance (retry): approved={result2.get('approved')}, score={result2.get('score')}")
        return result2

    log.error("Compliance JSON parsing failed after retry — marking as rejected")
    log_failure("compliance_check", "malformed_json", raw[:200])
    return {"approved": False, "score": 0, "reasons": ["JSON parse failure"], "required_fixes": [raw[:200]], "suggested_title": ""}


# ── 2.5 Revision ─────────────────────────────────────────────────────────────

def revise_brief(brief: str, feedback: list[str]) -> str:
    feedback_text = "\n".join(f"- {f}" for f in feedback)
    prompt = f"""You are revising an affiliate brief based on compliance feedback.
Original brief:
{brief}

Required fixes:
{feedback_text}

Fix all required issues, keep the structure, and output ONLY the revised Markdown (no preamble)."""
    log.info("Revising brief...")
    revised = _llm([{"role": "user", "content": prompt}], temperature=0.5)
    return revised


# ── Combined pipeline ─────────────────────────────────────────────────────────

def run_brief_pipeline(topic: str, keyword: str, max_revisions: int = 2) -> str:
    """
    Generates brief → checks compliance → revises up to max_revisions times.
    Returns the final approved (or best-effort) Markdown brief.
    """
    brief = generate_brief(topic, keyword)

    for attempt in range(max_revisions + 1):
        compliance = compliance_check(brief)
        if compliance.get("approved"):
            log.info(f"Brief approved on attempt {attempt}")
            break
        if attempt < max_revisions:
            fixes = compliance.get("required_fixes", [])
            log.info(f"Revision {attempt + 1}/{max_revisions}: {len(fixes)} fixes needed")
            brief = revise_brief(brief, fixes)
        else:
            log.warning("Max revisions reached — using best-effort brief")

    # persist
    (DATA_DIR / f"brief_{_slug(topic)}.md").write_text(brief, encoding="utf-8")
    return brief


def _slug(text: str) -> str:
    return text.lower().replace(" ", "_")[:32]


if __name__ == "__main__":
    result = run_brief_pipeline("Portable SSD", "portable SSD")
    print(result[:500])
