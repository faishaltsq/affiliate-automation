import pytest
from unittest.mock import patch
from src.clipper.punchline import select_multiple_climax_punchlines


def test_select_multiple_climax_punchlines_fallback():
    transcripts = [
        {"file": "video1.mp4", "start": 5.0, "end": 10.0, "text": "Emas naik 2 kali lipat"},
        {"file": "video2.mp4", "start": 12.0, "end": 18.0, "text": "Tanpa surat kita tetap terima"},
    ]
    # Without LLM or LLM returning None, fallback produces N items
    res = select_multiple_climax_punchlines("Campaign Test", transcripts, count=2)
    assert len(res) == 2
    assert res[0]["target_file"] == "video1.mp4"
    assert res[1]["target_file"] == "video2.mp4"
    assert res[0]["punchline_hook"] != res[1]["punchline_hook"]


@patch("src.clipper.punchline.requests.post")
def test_select_multiple_climax_punchlines_llm_success(mock_post):
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "choices": [{
            "message": {
                "content": '''[
                    {"target_filename": "v1.mp4", "climax_start": 61.5, "climax_duration": 3.5, "punchline_hook": "JUAL EMAS UNTUNG 2X LIPAT?!", "reason": "Profit 2x"},
                    {"target_filename": "v2.mp4", "climax_start": 15.0, "climax_duration": 3.5, "punchline_hook": "EMAS TANPA SURAT DITERIMA?!", "reason": "No receipt"}
                ]'''
            }
        }]
    }
    transcripts = [
        {"file": "path/v1.mp4", "start": 60.0, "end": 65.0, "text": "untung 2 kali lipat"},
        {"file": "path/v2.mp4", "start": 14.0, "end": 20.0, "text": "tanpa surat aman"},
    ]
    res = select_multiple_climax_punchlines("Campaign Test", transcripts, count=2)
    assert len(res) == 2
    assert res[0]["punchline_hook"] == "JUAL EMAS UNTUNG 2X LIPAT?!"
    assert res[1]["punchline_hook"] == "EMAS TANPA SURAT DITERIMA?!"
    assert res[0]["target_file"] == "path/v1.mp4"
    assert res[1]["target_file"] == "path/v2.mp4"
