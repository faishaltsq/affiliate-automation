from src.clipper.motionklip_parser import _extract_labeled_urls, parse_discord_campaign_message
from src.clipper.pipeline import _is_valid_footage_source, run_clipper_pipeline
from unittest.mock import MagicMock, patch

def test_extract_labeled_urls_distinguishes_markdown_labels():
    """Guideline/watermark tutorial links must be excluded; real footage links must be kept."""
    raw_text = """
[Template Watermark](https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view?usp=sharing)
[Panduan Penempatan Watermark](https://youtube.com/shorts/fgQXQW0XgJM?feature=share)
Instagram: https://www.instagram.com/apiipp/
Tiktok: https://www.tiktok.com/@apiipp1294
Youtube: https://www.youtube.com/@AfifYulistian
Bahan Video: https://drive.google.com/drive/folders/1fYmBAQfmDvXNG8JZgcr7ZLqqEg_t3UCn
    """
    items = _extract_labeled_urls(raw_text)
    urls = {it["url"] for it in items}

    # Guideline / watermark tutorial links must NOT be extracted as footage
    assert "https://youtube.com/shorts/fgQXQW0XgJM?feature=share" not in urls
    assert "https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view?usp=sharing" not in urls

    # Genuine footage link must still be present
    assert "https://drive.google.com/drive/folders/1fYmBAQfmDvXNG8JZgcr7ZLqqEg_t3UCn" in urls


def test_is_valid_footage_source_filters_guidelines_watermarks_profiles():
    # Guidelines
    assert not _is_valid_footage_source("https://youtube.com/shorts/fgQXQW0XgJM", "Panduan Penempatan Watermark")
    assert not _is_valid_footage_source("https://youtube.com/shorts/ThbIdhbt0M0", "")
    assert not _is_valid_footage_source("https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view", "Template Watermark")

    # Profiles (not video footage)
    assert not _is_valid_footage_source("https://www.youtube.com/@AfifYulistian", "")
    assert not _is_valid_footage_source("https://www.instagram.com/apiipp/", "")
    assert not _is_valid_footage_source("https://www.tiktok.com/@apiipp1294", "")

    # Valid footage
    assert _is_valid_footage_source("https://drive.google.com/drive/folders/1fYmBAQfmDvXNG8JZgcr7ZLqqEg_t3UCn", "Bahan Video")
    assert _is_valid_footage_source("https://www.youtube.com/watch?v=7ZaeJJDT-UI", "VOD Gameplay")
    assert _is_valid_footage_source("https://www.youtube.com/live/Ut3YRf6oe7E", "")


def test_pipeline_refuses_to_clip_when_only_guidelines_present():
    fake_detail = {
        "id": "mk-1556638531588464740",
        "title": "Apiipp X Motion Klip",
        "cpm": 5000,
        "assets": [
            {"url": "https://drive.google.com/file/d/1mwmWHKKgZPs3ZUTtVFqe3GMUvbY7kc9H/view?usp=sharing", "display_name": "Template Watermark"},
            {"url": "https://youtube.com/shorts/fgQXQW0XgJM", "display_name": "Panduan Penempatan Watermark"},
            {"url": "https://www.youtube.com/@AfifYulistian", "display_name": "Youtube"},
        ],
        "dos": [],
        "donts": [],
        "hashtags": [],
        "category": "Creator",
    }

    mock_client = MagicMock()
    mock_client.get_campaign_detail.return_value = fake_detail

    notifications = []
    with patch("src.clipper.pipeline.get_campaign_provider", return_value=mock_client), \
         patch("src.clipper.pipeline.analyze_campaign_brief", return_value={"winning_hooks": ["Hook 1"]}), \
         patch("src.clipper.pipeline.list_accounts", return_value=[{"username": "test", "has_cookies": True}]):

        res = run_clipper_pipeline("mk-1556638531588464740", count=2, dry_run=False, progress_callback=lambda m: notifications.append(m))

        assert res["success"] is False
        assert "tidak menyediakan link video" in res["error"] or "panduan" in res["error"]
        assert any("tidak menyediakan link video" in n or "panduan" in n for n in notifications)
