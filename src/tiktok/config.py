"""Configuration management for TikTok Video Automation Agent."""

from pathlib import Path
import json
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TIKTOK_DATA_DIR = BASE_DIR / "data" / "tiktok"
TIKTOK_DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = TIKTOK_DATA_DIR / "config.json"

DEFAULT_CONFIG = {
    "keywords": [
        "Outdoor Boys camping fishing 4K",
        "TysyTube Restoration satisfying 4K",
        "advoko MAKES bushcraft shelter 4K",
        "Bertram Craft and Wilderness relaxing 4K",
        "satisfying bricklaying masonry craftsmanship 4K",
    ],
    "videos_per_run": 1,
    "max_duration_seconds": 1800,
    "clip_duration_seconds": 360,
    "min_duration_seconds": 15,
    "crop_width": 1080,
    "crop_height": 1920,
    "caption_template": "{title} #satisfying #relaxing #diy #outdoor #fyp",
    "tiktok_cookies_path": str(TIKTOK_DATA_DIR / "cookies.txt"),
    "db_path": str(TIKTOK_DATA_DIR / "state.db"),
    "download_dir": str(TIKTOK_DATA_DIR / "downloads"),
    "output_dir": str(TIKTOK_DATA_DIR / "output"),
    "log_dir": str(BASE_DIR / "logs" / "tiktok"),
}


def load_config() -> dict:
    """Load config from data/tiktok/config.json or return DEFAULT_CONFIG."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg = DEFAULT_CONFIG.copy()
                cfg.update(saved)
                return cfg
        except Exception:
            pass
    # Save default config if not exists
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict) -> None:
    """Save config to data/tiktok/config.json."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
