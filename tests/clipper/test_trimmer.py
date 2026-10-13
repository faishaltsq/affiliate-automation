import pytest
from src.clipper.trimmer import extract_dialogue_beats, is_basa_basi

def test_is_basa_basi():
    assert is_basa_basi("Halo selamat datang bu, kenalan dulu dengan saya") is True
    assert is_basa_basi("Wah kadarnya cuma 10 karat ini bermasalah") is False

def test_extract_dialogue_beats_preserves_sentence_integrity():
    segments = [
        {"start": 0.0, "end": 4.5, "text": "Halo selamat datang di toko kami apa kabar bu."},  # Basa-basi
        {"start": 5.0, "end": 18.0, "text": "Hari ini saya mau cek emas ini warisan ibu saya ada suratnya."},  # Context setup
        {"start": 18.5, "end": 35.0, "text": "Pas kita tes pakai spektrometer, kadarnya cuma 10 karat padahal suratnya 24 karat."},  # Climax
        {"start": 36.0, "end": 48.0, "text": "Waduh berarti saya kena tipu ya mas waktu beli dulu?"},  # Payoff
    ]
    beats = extract_dialogue_beats(segments, target_duration=35.0)
    assert len(beats) >= 1
    total_dur = sum(b["end"] - b["start"] for b in beats)
    assert 25.0 <= total_dur <= 40.0
    # The first beat should start after the pleasantry greeting
    assert beats[0]["start"] >= 4.5
