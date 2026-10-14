"""tests/clipper/test_guideline_filter.py — Verify guideline & watermark tutorial URLs are excluded from footage assets."""

from src.clipper.motionklip_parser import parse_discord_campaign_message, _extract_labeled_urls


def test_guideline_and_watermark_links_excluded_from_assets():
    raw_message = {
        "id": "1556638531588464740",
        "content": (
            "# Apiipp X Motion Klip\n"
            "Campaign: Apiipp Ramadan\n"
            "Reward: 15.000 CPM\n"
            "Pool: Sisa 100%\n"
            "Durasi: 30 - 60 detik\n\n"
            "📌 **Ketentuan & Do's**:\n"
            "- Wajib menggunakan watermark Motionklip\n"
            "- [Panduan Penempatan Watermark](https://youtube.com/shorts/fgQXQW0XgJM?feature=share)\n"
            "- [Template Watermark Motionklip](https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view?usp=sharing)\n\n"
            "🎬 **Bahan & Footage**:\n"
            "- [Footage Utama Drive](https://drive.google.com/drive/folders/1fYmBAQfmDvXNG8JZgcr7ZLqqEg_t3UCn)\n"
            "- Youtube: https://www.youtube.com/watch?v=7ZaeJJDT-UI\n"
        ),
        "embeds": [],
        "attachments": [],
    }

    parsed = parse_discord_campaign_message(raw_message)
    asset_urls = [a["url"] for a in parsed["assets"]]

    # Ensure guideline / tutorial / watermark links are NOT in assets
    assert "https://youtube.com/shorts/fgQXQW0XgJM?feature=share" not in asset_urls
    assert "https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view?usp=sharing" not in asset_urls

    # Ensure genuine footage links ARE kept
    assert "https://drive.google.com/drive/folders/1fYmBAQfmDvXNG8JZgcr7ZLqqEg_t3UCn" in asset_urls
    assert "https://www.youtube.com/watch?v=7ZaeJJDT-UI" in asset_urls


def test_raw_url_with_guideline_prefix_excluded():
    text = (
        "Tutorial Penempatan Watermark: https://youtube.com/shorts/fgQXQW0XgJM\n"
        "Aturan Penempatan: https://example.com/rules-video.mp4\n"
        "Bahan Video: https://www.youtube.com/watch?v=real_footage_123\n"
    )
    extracted = _extract_labeled_urls(text)
    urls = [x["url"] for x in extracted]

    assert "https://youtube.com/shorts/fgQXQW0XgJM" not in urls
    assert "https://example.com/rules-video.mp4" not in urls
    assert "https://www.youtube.com/watch?v=real_footage_123" in urls
