"""account_manager.py — Manage multiple TikTok accounts for Clipper pipeline."""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("clipper.account_manager")

ACCOUNTS_DIR = Path("data/tiktok/accounts")


def _load_account(account_dir: Path) -> Optional[dict]:
    """Load a single account config from its directory."""
    config_path = account_dir / "config.json"
    cookies_path = account_dir / "cookies.txt"

    if not config_path.exists():
        return None

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load config for %s: %s", account_dir.name, e)
        return None

    return {
        "name": account_dir.name,
        "display_name": cfg.get("display_name", account_dir.name),
        "username": cfg.get("username", f"@{account_dir.name}"),
        "active": cfg.get("active", False),
        "style": cfg.get("style", "default"),
        "cookies_path": str(cookies_path),
        "has_cookies": cookies_path.exists(),
    }


def list_accounts(only_active: bool = False) -> list[dict]:
    """List all registered TikTok accounts."""
    if not ACCOUNTS_DIR.exists():
        return []

    accounts = []
    for d in sorted(ACCOUNTS_DIR.iterdir()):
        if d.is_dir():
            acc = _load_account(d)
            if acc:
                if only_active and (not acc["active"] or not acc["has_cookies"]):
                    continue
                accounts.append(acc)

    return accounts


def get_account(name: str) -> Optional[dict]:
    """Get a specific account by folder name."""
    acc_dir = ACCOUNTS_DIR / name
    if acc_dir.is_dir():
        return _load_account(acc_dir)
    return None


def set_account_active(name: str, active: bool) -> bool:
    """Toggle account active status."""
    config_path = ACCOUNTS_DIR / name / "config.json"
    if not config_path.exists():
        return False

    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        cfg["active"] = active
        config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        logger.error("Failed to update account %s: %s", name, e)
        return False


def get_next_account_name() -> str:
    """Find the next auto-incremented folder name like pochita_1, pochita_2, pochita_3..."""
    if not ACCOUNTS_DIR.exists():
        return "pochita_1"
    existing = [d.name for d in ACCOUNTS_DIR.iterdir() if d.is_dir()]
    i = 1
    while f"pochita_{i}" in existing:
        i += 1
    return f"pochita_{i}"


def register_account(name: str = "", display_name: str = "", username: str = "", style: str = "default") -> dict:
    """Create a new account directory with config (user still needs to add cookies.txt manually)."""
    if not name or name.strip() == "":
        name = get_next_account_name()
    acc_dir = ACCOUNTS_DIR / name
    acc_dir.mkdir(parents=True, exist_ok=True)

    cfg = {
        "display_name": display_name or name,
        "username": username or f"@{name}",
        "active": False,
        "style": style,
    }

    config_path = acc_dir / "config.json"
    config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "name": name,
        "config_path": str(config_path),
        "cookies_path": str(acc_dir / "cookies.txt"),
        "status": "registered — tambahkan cookies.txt lalu aktifkan dengan /activate <nama>",
    }
