import pytest
from src.clipper.motionklip_parser import parse_discord_campaign_message


def test_parse_discord_campaign_message_extracts_all_fields():
    raw_msg = {
        "id": "123456789012345678",
        "channel_id": "987654321098765432",
        "content": (
            "📢 **NEW CAMPAIGN: WINDAH BASUDARA X RUCAS**\n\n"
            "💰 **Reward:** Rp 15.000 / 1.000 views\n"
            "🎯 **Budget Pool:** Rp 20.000.000 (Sisa 85%)\n"
            "⏱ **Durasi Clip:** 30 - 60 detik\n"
            "🏷 **Hashtags:** #WindahBasudara #Rucas #MotionKlip #EzKlip\n"
            "📌 **Do's:**\n"
            "- Pakai hook yang heboh di 3 detik awal\n"
            "- Wajib tag @windahbasudara dan @rucas\n"
            "🚫 **Don'ts:**\n"
            "- Dilarang reupload mentah tanpa edit\n"
            "- Dilarang pakai footage yang berbau SARA\n\n"
            "🎬 **Bahan Footage:**\n"
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz\n"
            "https://youtu.be/sample_stream_vod"
        ),
        "embeds": [],
        "attachments": [],
    }
    result = parse_discord_campaign_message(raw_msg)
    assert result["id"] == "mk-123456789012345678"
    assert "WINDAH BASUDARA X RUCAS" in result["title"]
    assert result["cpm"] == 15000
    assert result["budget_percentage"] == 85.0
    assert result["min_duration"] == 30
    assert result["max_duration"] == 60
    assert "#WindahBasudara" in result["hashtags"]
    assert len(result["dos"]) >= 2
    assert len(result["donts"]) >= 2
    assert len(result["assets"]) == 2
    assert result["assets"][0]["url"] == "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_parse_discord_campaign_message_handles_embed_fallback():
    raw_msg = {
        "id": "999888777666",
        "channel_id": "111222333",
        "content": "",
        "embeds": [
            {
                "title": "Campaign EzKlip MLBB",
                "description": "Hadiah Rp 10.000 / 1k views. Hashtag #mlbb #ezklip. Bahan: https://youtu.be/mlbb123",
                "fields": [
                    {"name": "Do's", "value": "- Pakai gameplay epic\n- Edit transisi"},
                    {"name": "Don'ts", "value": "- No toxic chat"},
                ],
            }
        ],
        "attachments": [],
    }
    result = parse_discord_campaign_message(raw_msg)
    assert result["id"] == "mk-999888777666"
    assert "Campaign EzKlip MLBB" in result["title"]
    assert result["cpm"] == 10000
    assert "#mlbb" in result["hashtags"]
    assert len(result["dos"]) >= 2
    assert len(result["donts"]) >= 1
    assert any("youtu.be/mlbb123" in a["url"] for a in result["assets"])
    assert result["source"] == "motionklip"


def test_parse_discord_campaign_message_defaults_and_attachments():
    raw_msg = {
        "id": "mk-555666777",
        "channel_id": "111",
        "content": "Pengumuman campaign baru tanpa detail durasi atau sisa pool.",
        "embeds": [],
        "attachments": [
            {
                "url": "https://cdn.discordapp.com/attachments/111/video.mp4",
                "filename": "sample_video.mp4",
            }
        ],
    }
    result = parse_discord_campaign_message(raw_msg)
    assert result["id"] == "mk-555666777"
    assert result["cpm"] == 0
    assert result["budget_percentage"] == 100.0
    assert result["min_duration"] == 15
    assert result["max_duration"] == 180
    assert result["dos"] == []
    assert result["donts"] == []
    assert len(result["assets"]) == 1
    assert result["assets"][0]["url"] == "https://cdn.discordapp.com/attachments/111/video.mp4"
    assert result["assets"][0]["display_name"] == "sample_video.mp4"


def test_parse_discord_campaign_message_cpm_variations():
    cases = [
        ("Rate: Rp 25.000 / 1.000 views", 25000),
        ("Payout Rp 12.5k / 1k views", 12500),
        ("Rp 20.000/1000 views", 20000),
        ("Hadiah Rp 30k / views", 30000),
    ]
    for text, expected_cpm in cases:
        msg = {"id": "1", "content": text}
        res = parse_discord_campaign_message(msg)
        assert res["cpm"] == expected_cpm, f"Failed on {text}"

