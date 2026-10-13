"""tests/clipper/test_account_manager.py — Unit tests for TikTok account manager."""

import json
from pathlib import Path
from src.clipper.account_manager import list_accounts, get_account, set_account_active, register_account


def test_register_and_list_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr("src.clipper.account_manager.ACCOUNTS_DIR", tmp_path)

    # Register 2 accounts
    res1 = register_account("akun1", display_name="Akun Satu", username="@akunsatu", style="energetic")
    res2 = register_account("akun2", display_name="Akun Dua", username="@akundua", style="calm")

    assert Path(res1["config_path"]).exists()
    assert Path(res2["config_path"]).exists()

    # List all
    accs = list_accounts(only_active=False)
    assert len(accs) == 2
    assert accs[0]["name"] == "akun1"
    assert accs[0]["active"] is False
    assert accs[0]["has_cookies"] is False

    # Simulate cookies added and activate akun1
    (tmp_path / "akun1" / "cookies.txt").write_text("test-cookie")
    ok = set_account_active("akun1", True)
    assert ok is True

    # List active only
    active_accs = list_accounts(only_active=True)
    assert len(active_accs) == 1
    assert active_accs[0]["name"] == "akun1"
    assert active_accs[0]["username"] == "@akunsatu"
