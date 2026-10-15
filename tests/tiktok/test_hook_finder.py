"""tests/tiktok/test_hook_finder.py"""

from src.tiktok.hook_finder import _sanitize_windows, MIN_CLIP_SEC, MAX_CLIP_SEC


def test_sanitize_drops_overlaps():
    raw = [
        {"start": 0, "end": 60, "hook_title": "A", "reason": ""},
        {"start": 30, "end": 90, "hook_title": "B", "reason": ""},   # overlaps A
        {"start": 200, "end": 260, "hook_title": "C", "reason": ""},
    ]
    out = _sanitize_windows(raw, total_duration=1000, count=3)
    assert [w["hook_title"] for w in out] == ["A", "C"]


def test_sanitize_clamps_to_source_duration():
    raw = [{"start": 950, "end": 5000, "hook_title": "TAIL", "reason": ""}]
    out = _sanitize_windows(raw, total_duration=1000, count=1)
    assert out[0]["end"] == 1000
    assert out[0]["duration"] <= MAX_CLIP_SEC


def test_sanitize_expands_too_short_window():
    raw = [{"start": 100, "end": 110, "hook_title": "SHORT", "reason": ""}]
    out = _sanitize_windows(raw, total_duration=1000, count=1)
    assert out[0]["duration"] >= MIN_CLIP_SEC


def test_sanitize_caps_to_requested_count():
    raw = [
        {"start": 0, "end": 60, "hook_title": "1", "reason": ""},
        {"start": 100, "end": 160, "hook_title": "2", "reason": ""},
        {"start": 200, "end": 260, "hook_title": "3", "reason": ""},
    ]
    assert len(_sanitize_windows(raw, total_duration=1000, count=2)) == 2


def test_sanitize_ignores_garbage_rows():
    raw = [
        {"start": "abc", "end": 10},
        {"start": 50, "end": 50},
        {"start": 100, "end": 160, "hook_title": "OK", "reason": ""},
    ]
    out = _sanitize_windows(raw, total_duration=1000, count=5)
    assert len(out) == 1 and out[0]["hook_title"] == "OK"


def test_hook_title_uppercased():
    raw = [{"start": 0, "end": 60, "hook_title": "  a long hook title  ", "reason": ""}]
    out = _sanitize_windows(raw, total_duration=1000, count=1)
    assert out[0]["hook_title"] == "A LONG HOOK TITLE"


def test_sanitize_orders_chronologically():
    raw = [
        {"start": 500, "end": 560, "hook_title": "LATER", "reason": ""},
        {"start": 100, "end": 160, "hook_title": "EARLIER", "reason": ""},
    ]
    out = _sanitize_windows(raw, total_duration=1000, count=2)
    assert [w["hook_title"] for w in out] == ["EARLIER", "LATER"]
