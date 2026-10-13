import pytest
from unittest.mock import patch, MagicMock
from src.clipper.punchline import select_climax_punchline, extract_climax_teaser

def test_select_climax_punchline():
    sample_transcripts = [
        {"file": "part1.mp4", "text": "Halo kita lagi di toko emas mau cek barang.", "start": 0.0, "end": 5.0},
        {"file": "part7.mp4", "text": "Lho mas ini emasnya palsu ya? Jangan kabur mas!", "start": 10.0, "end": 14.0},
    ]
    mock_llm_resp = {
        "target_file": "part7.mp4",
        "climax_start": 10.0,
        "climax_duration": 3.5,
        "punchline_hook": "DETIK-DETIK NANGKEP PENJUAL EMAS PALSU!",
        "reason": "Direct confrontation caught on camera."
    }
    with patch("src.clipper.punchline._query_punchline_llm", return_value=mock_llm_resp):
        result = select_climax_punchline(
            campaign_title="OWNERS JUAL EMAS INDONESIA (Nangkep Penjual Emas Palsu - Part 2)",
            transcripts=sample_transcripts,
        )
        assert result["target_file"] == "part7.mp4"
        assert result["climax_start"] == 10.0
        assert "NANGKEP PENJUAL" in result["punchline_hook"]

def test_extract_climax_teaser_fallback_when_no_transcripts():
    # When no transcripts exist, gracefully return None or fallback to first file
    result = select_climax_punchline(
        campaign_title="Any Title",
        transcripts=[],
    )
    assert result is None
