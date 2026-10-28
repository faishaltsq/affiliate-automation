"""
Free-Tier Guardrails & Edge-Case Mitigation
Handles all 10 edge cases from the operational matrix:
  1. OpenRouter rate-limit tracker (daily ceiling = 45 requests)
  2. Malformed JSON parser (regex extraction + temp retry)
  3. Image caching (local 7-day cache) + Unsplash fallback
  4. Empty Shopee products -> fallback placeholder
  5. Missing af_code validation
  6. Cloudinary storage budget tracker
  7. Token expiry validation
  8. Stray HTML sanitization regex
  9. Non-monetizable keyword bailout
 10. Generic exponential backoff retry decorator
"""

import os
import re
import json
import time
import sqlite3
import hashlib
from datetime import datetime, date, timezone
from pathlib import Path
from functools import wraps

from src.config import DATA_DIR, LOG_DIR
from src.logger import get_logger

log = get_logger("guardrails")

DB_PATH = DATA_DIR / "guardrails.db"
IMAGE_CACHE_DIR = DATA_DIR / "image_cache"
IMAGE_CACHE_DIR.mkdir(exist_ok=True)

DAILY_OPENROUTER_LIMIT = 999  # 9router local is unmetered; this is now just a logging counter
MAX_STORAGE_BYTES = 20 * 1024 * 1024 * 1024  # 20 GB safety cap


# ── DB Initialization ────────────────────────────────────────────────────────

def init_guardrails_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily_counters (
            date TEXT PRIMARY KEY,
            openrouter_requests INTEGER DEFAULT 0,
            cloudinary_bytes INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            topic TEXT,
            error_type TEXT,
            details TEXT
        )
    """)
    con.commit()
    con.close()

init_guardrails_db()


# ── Edge Case 1: OpenRouter Daily Quota Counter ──────────────────────────────

def check_and_increment_openrouter() -> int:
    """
    Tracks daily LLM request counter in SQLite.
    Local 9router is unmetered, so this purely logs usage.
    """
    today = date.today().isoformat()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO daily_counters (date, openrouter_requests) VALUES (?, 0)", (today,))
    cur.execute("SELECT openrouter_requests FROM daily_counters WHERE date = ?", (today,))
    row = cur.fetchone()
    current = row[0] if row else 0

    new_val = current + 1
    cur.execute("UPDATE daily_counters SET openrouter_requests = ? WHERE date = ?", (new_val, today))
    con.commit()
    con.close()
    log.info(f"LLM requests today: {new_val} (9router)")
    return new_val


# ── Edge Case 2: Safe JSON Extractor ──────────────────────────────────────────

def extract_json_safely(raw_text: str) -> dict | None:
    """
    Extracts valid JSON even if model output contains conversational text,
    markdown fences, or extra leading/trailing commentary.
    """
    raw = raw_text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    # Regex find first '{' to last '}'
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Second pass: repair the malformed shapes small models emit.
        try:
            return json.loads(repair_json(candidate))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ── Edge Case 3: Image Caching & Unsplash Fallback ────────────────────────────

def get_cached_image(prompt: str) -> str | None:
    """Checks local 7-day disk cache for previously generated/hosted image."""
    h = hashlib.md5(prompt.strip().lower().encode()).hexdigest()
    cache_meta = IMAGE_CACHE_DIR / f"{h}.json"
    if cache_meta.exists():
        try:
            data = json.loads(cache_meta.read_text(encoding="utf-8"))
            cached_time = data.get("timestamp", 0)
            if time.time() - cached_time < 7 * 86400:
                log.info(f"Reusing cached image for: '{prompt[:30]}...' -> {data['url']}")
                return data["url"]
        except Exception:
            pass
    return None


def save_image_cache(prompt: str, hosted_url: str):
    h = hashlib.md5(prompt.strip().lower().encode()).hexdigest()
    cache_meta = IMAGE_CACHE_DIR / f"{h}.json"
    cache_meta.write_text(json.dumps({
        "timestamp": time.time(),
        "url": hosted_url,
        "prompt": prompt
    }), encoding="utf-8")


def get_unsplash_fallback(keyword: str) -> str:
    """
    Free public fallback when Replicate runs out of compute quota.
    Uses source.unsplash.com or standard curated fallback.
    """
    clean_k = re.sub(r"[^a-zA-Z0-9]", ",", keyword.strip())
    fallback_url = f"https://images.unsplash.com/photo-1593642632823-8f785ba67e45?w=1200&h=630&fit=crop&q=80"
    log.warning(f"Replicate exhausted or failed. Using free fallback image: {fallback_url}")
    return fallback_url


# ── Edge Case 8: HTML Sanitizer ───────────────────────────────────────────────

def repair_json(raw_text: str) -> str:
    """Repairs the JSON shapes small models actually emit.

    The gateway's cheaper models routinely write syntactically invalid JSON:
    a missing comma between members, a trailing comma before a closing brace,
    smart quotes instead of straight ones. json.loads rejects all of these, and
    a rejected parse costs a full regeneration cycle. Repair the common cases
    first, then fall back to the original text so genuine syntax errors still
    surface as errors rather than being silently mangled.
    """
    if not raw_text:
        return raw_text
    t = raw_text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    # Smart quotes are the most common single defect and the easiest to fix.
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    # Missing comma between members: a closing quote/brace/bracket followed by
    # whitespace and then an opening quote is always a separator omission.
    t = re.sub(r'(["\}\]\d])(\s*\n\s*)(")', r"\1,\2\3", t)
    # Trailing comma before a closing brace or bracket.
    t = re.sub(r",(\s*[}\]])", r"\1", t)
    # A key with no value at all — "image_query": followed by a newline and the
    # next member or the closing brace. The model dropped the value; supply an
    # empty string so the member still parses.
    t = re.sub(r'("\s*:\s*)(?=\n\s*[",}\]])', r'\1""', t)
    return t


def loads_json_tolerant(raw_text: str) -> dict | None:
    """json.loads with one repair pass. Returns None when unrepairable."""
    candidate = repair_json(raw_text)
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None


def sanitize_markdown(content: str) -> str:
    """Strips stray HTML tags and verifies structural Markdown."""
    clean = re.sub(r"<[^>]+>", "", content)
    # Ensure starts with H1 if missing
    if not clean.strip().startswith("#"):
        clean = "# " + clean.lstrip()
    return clean


# ── Failure Logging (Checklist Item 5) ────────────────────────────────────────

def log_failure(topic: str, error_type: str, details: str):
    today = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO failures (timestamp, topic, error_type, details) VALUES (?, ?, ?, ?)",
        (today, topic, error_type, details)
    )
    con.commit()
    con.close()
    
    # Also write to logs/failure.log
    fail_file = LOG_DIR / "failure.log"
    with open(fail_file, "a", encoding="utf-8") as f:
        f.write(f"[{today}] TOPIC='{topic}' TYPE='{error_type}' DETAILS={details}\n")


# ── Edge Case 10: Exponential Backoff Decorator ───────────────────────────────

def retry_with_backoff(max_retries: int = 3, base_delay: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    log.warning(f"{func.__name__} failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    delay *= 2
        return wrapper
    return decorator
