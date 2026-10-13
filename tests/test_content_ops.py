"""
test_content_ops.py — Unit tests for Faceless Content Operations Engine.
"""

from unittest.mock import patch
from src.content_ops.scorer import score_candidate
from src.content_ops.metadata import generate_metadata_and_hook
from src.content_ops.qc import run_qc_check


def test_score_candidate_high_quality():
    cand = {
        "id": "abc12345",
        "title": "Mancing Monster Toman Babon Strike Brutal di Rawa Liar",
        "channel": "Angler Adventure",
        "duration": 45,
        "view_count": 250_000,
        "width": 1080,
        "height": 1920,
        "chapters": [{"title": "Strike Monster", "start_time": 5.0}]
    }
    res = score_candidate(cand, target_niche="mancing")
    assert res["passed"] is True
    assert res["overall_score"] >= 4.0
    assert res["scores"]["retention"] == 5
    assert res["scores"]["visual_quality"] == 5
    assert res["scores"]["niche_fit"] == 5


def test_score_candidate_reject_off_niche_or_podcast():
    cand = {
        "id": "def67890",
        "title": "Podcast Obrolan Santai Bareng Teman Kantor",
        "channel": "Random Channel",
        "duration": 1800,
        "view_count": 500,
        "width": 1920,
        "height": 1080,
    }
    res = score_candidate(cand, target_niche="mancing")
    assert res["passed"] is False
    assert res["overall_score"] < 4.0


def test_metadata_generator_fallback():
    # Test generation without hitting external LLM
    with patch("src.content_ops.metadata._llm", side_effect=Exception("API down")):
        meta = generate_metadata_and_hook("Mancing Ikan Mas Babon", niche="mancing")
        assert "hook_text" in meta
        assert meta["hook_text"].isupper()
        assert "tiktok" in meta
        assert "shorts" in meta
        assert len(meta["tiktok"]["hashtags"]) >= 3


def test_qc_gate_check_logic():
    dummy_meta = {
        "tiktok": {
            "caption": "Momen strike paling memuaskan hari ini! Kalau di spot kalian, umpan apa yang paling gacor?",
            "hashtags": ["#mancing", "#fishing", "#strike"]
        },
        "reels": {
            "caption": "Sensasi tarikan strike ikan liar di alam bebas.",
            "hashtags": ["#mancingmania", "#fishinglife", "#outdoors"]
        },
        "shorts": {
            "title": "Mancing Liar — Strike Monster Tak Terduga!",
            "description": "Kompilasi momen strike paling memuaskan saat mancing di alam bebas.",
            "hashtags": ["#Shorts", "#Fishing", "#Mancing"]
        }
    }
    # Test with non-existent file -> should fail gracefully
    qc = run_qc_check("non_existent_file.mp4", dummy_meta, "STRIKE BABON!")
    assert qc["passed"] is False
    assert qc["checks"]["file_exists"] is False
