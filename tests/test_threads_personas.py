"""tests/test_threads_personas.py — Test persona retrieval, tag extraction, and state management."""

import pytest
from pathlib import Path
from src.organic.personas import (
    PERSONAS,
    get_persona,
    list_personas,
    get_active_manual_persona,
    set_active_manual_persona,
    extract_persona_tag,
    STATE_FILE,
)


def test_persona_lookup():
    for key in ["julid", "calm", "nerd", "energic", "angry", "default"]:
        p = get_persona(key)
        assert p.key == key
        assert len(p.name) > 0
        assert len(p.emoji) > 0
        assert len(p.prompt_guide) > 20

    # Fallback on invalid key
    fallback = get_persona("unknown_xyz")
    assert fallback.key == "default"


def test_extract_persona_tag():
    # Bracket pattern
    p, topic = extract_persona_tag("[julid] kabut asap kalimantan")
    assert p == "julid"
    assert topic == "kabut asap kalimantan"

    # Flag pattern
    p, topic = extract_persona_tag("--nerd perkembangan chip AI")
    assert p == "nerd"
    assert topic == "perkembangan chip AI"

    # Colon pattern
    p, topic = extract_persona_tag("angry: harga pangan naik")
    assert p == "angry"
    assert topic == "harga pangan naik"

    # Verbose flag pattern
    p, topic = extract_persona_tag("--persona calm koreksi saham hari ini")
    assert p == "calm"
    assert topic == "koreksi saham hari ini"

    # No tag
    p, topic = extract_persona_tag("timnas indonesia vs jepang")
    assert p is None
    assert topic == "timnas indonesia vs jepang"


def test_persona_state_persistence(tmp_path, monkeypatch):
    test_state_file = tmp_path / "persona_state.json"
    monkeypatch.setattr("src.organic.personas.STATE_FILE", test_state_file)

    assert get_active_manual_persona() == "default"

    assert set_active_manual_persona("julid") is True
    assert get_active_manual_persona() == "julid"

    assert set_active_manual_persona("nerd") is True
    assert get_active_manual_persona() == "nerd"

    # Invalid persona should be rejected
    assert set_active_manual_persona("non_existent") is False
    assert get_active_manual_persona() == "nerd"
