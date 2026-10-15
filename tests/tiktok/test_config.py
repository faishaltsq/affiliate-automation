"""tests/tiktok/test_config.py — guards the source_clip config block against removal."""

from src.tiktok.config import load_config


def test_source_clip_block_and_scheduler_mode_present():
    """source_clip block must exist with these values, and the scheduler must stay in reminder mode."""
    cfg = load_config()

    sc = cfg.get("source_clip")
    assert sc, "source_clip block missing from config"
    assert sc["whisper_model"] == "base"
    assert sc["hook_title_seconds"] == 10
    assert cfg["scheduler"]["mode"] == "reminder"
