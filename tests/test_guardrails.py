import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.guardrails import (
    extract_json_safely,
    sanitize_markdown,
    check_and_increment_openrouter,
    get_cached_image,
    save_image_cache,
    get_unsplash_fallback,
    log_failure,
)

def test_json_extraction():
    dirty = 'Here is the result:\n```json\n{"approved": true, "score": 95, "reasons": [], "required_fixes": [], "suggested_title": "SSD"}\n```\nHope this helps!'
    parsed = extract_json_safely(dirty)
    assert parsed is not None, "JSON extraction failed"
    assert parsed["approved"] is True
    assert parsed["score"] == 95
    print("✓ Edge Case 2: JSON extraction passed")

def test_html_sanitization():
    html_text = "Some text with <p>paragraph</p> and <b>bold</b> tags"
    cleaned = sanitize_markdown(html_text)
    assert "<p>" not in cleaned
    assert "<b>" not in cleaned
    assert cleaned.startswith("# ")
    print("✓ Edge Case 8: HTML sanitization passed")

def test_openrouter_counter():
    cnt = check_and_increment_openrouter()
    assert cnt >= 1
    print(f"✓ Edge Case 1: OpenRouter quota tracker ({cnt}/45) passed")

def test_image_cache_and_fallback():
    prompt = "sleek portable ssd review hero image"
    save_image_cache(prompt, "https://res.cloudinary.com/demo/image/upload/v1/ssd.jpg")
    cached = get_cached_image(prompt)
    assert cached == "https://res.cloudinary.com/demo/image/upload/v1/ssd.jpg"
    
    fallback = get_unsplash_fallback("portable SSD")
    assert "unsplash.com" in fallback
    print("✓ Edge Case 3: Image cache + Unsplash fallback passed")

def test_failure_logging():
    log_failure("portable_ssd", "shopee_empty", "No items returned for keyword")
    from src.config import LOG_DIR
    fail_file = LOG_DIR / "failure.log"
    assert fail_file.exists()
    assert "shopee_empty" in fail_file.read_text(encoding="utf-8")
    print("✓ Edge Case 5/Logs: Failure audit logging passed")

if __name__ == "__main__":
    test_json_extraction()
    test_html_sanitization()
    test_openrouter_counter()
    test_image_cache_and_fallback()
    test_failure_logging()
    print("\n✓ ALL GUARDRAIL AND EDGE-CASE AUDIT TESTS PASSED")
