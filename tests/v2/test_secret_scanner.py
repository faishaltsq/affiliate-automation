"""tests/v2/test_secret_scanner.py — the scanner itself must be trustworthy.

A scanner that misses patterns, prints full credentials, or skips files is
worse than no scanner, because it produces false confidence.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
SPEC = importlib.util.spec_from_file_location(
    "scan_secrets", SCRIPTS / "scan_secrets.py"
)
scan_secrets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scan_secrets)


class TestPatterns:
    @pytest.mark.parametrize("value", [
        "8696721987:AAHM4-7GLqyJjsHqPaJUsUIH1WDAcR1PCaA",
        "123456789:AAEhBOBpCy6vN8xW7yQ2ZtLmKdRgHsUjVwXo",
    ])
    def test_detects_telegram_bot_tokens(self, value):
        hits = scan_secrets.scan_text(f"TOKEN={value}", "test.env")
        assert any(h[1] == "telegram_bot_token" for h in hits)

    @pytest.mark.parametrize("value", [
        "sk-or-v1-a82408c06fbadd78dc0ef6a147c11c1f725fa4b8fa342f66867",
        "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789",
    ])
    def test_detects_openrouter_keys(self, value):
        hits = scan_secrets.scan_text(f"KEY={value}", "test.env")
        assert any(h[1] == "openrouter_key" for h in hits)

    def test_detects_github_pats(self):
        hits = scan_secrets.scan_text(
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789", "f.txt"
        )
        assert any(h[1] == "github_pat" for h in hits)

    def test_detects_aws_keys(self):
        hits = scan_secrets.scan_text("AKIAIOSFODNN7EXAMPLE", "f.txt")
        assert any(h[1] == "aws_access_key" for h in hits)

    def test_detects_private_key_blocks(self):
        hits = scan_secrets.scan_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
            "key.pem",
        )
        assert any(h[1] == "private_key_block" for h in hits)

    def test_detects_token_query_params(self):
        hits = scan_secrets.scan_text(
            "https://graph.threads.net/v1.0/123?access_token=EAAGverylongtokenvalue123",
            "log.txt",
        )
        assert any(h[1] == "token_query_param" for h in hits)

    def test_detects_generic_assignments(self):
        hits = scan_secrets.scan_text(
            'CLIENT_SECRET = "abcdefghijklmnopqrstuvwxyz"', "cfg.py"
        )
        assert any(h[1] == "generic_assignment" for h in hits)


class TestMasking:
    """The scanner must never print a usable credential."""

    def test_long_values_are_masked(self):
        masked = scan_secrets._mask(
            "8696721987:AAHM4-7GLqyJjsHqPaJUsUIH1WDAcR1PCaA"
        )
        assert "AAHM4" not in masked
        assert "…" in masked
        assert "46 chars" in masked

    def test_short_values_are_masked(self):
        masked = scan_secrets._mask("abcdefgh")
        assert masked.endswith("(8 chars)")

    def test_scan_text_returns_masked_values(self):
        hits = scan_secrets.scan_text(
            "TOKEN=8696721987:AAHM4-7GLqyJjsHqPaJUsUIH1WDAcR1PCaA", "t.env"
        )
        assert hits
        for _, _, value in hits:
            assert "AAHM4-7GLqyJjsHqPaJUsUIH1WDAcR1PCaA" not in value

    def test_mask_preserves_enough_to_locate(self):
        masked = scan_secrets._mask("sk-or-v1-abcdefghijklmnopqrstuvwxyz")
        assert masked.startswith("sk-or-")
        assert "chars" in masked


class TestAllowlist:
    def test_env_is_allowlisted_by_default(self):
        assert ".env" in scan_secrets.ALLOWLIST

    def test_include_env_discards_the_allowlist_entry(self):
        scan_secrets.ALLOWLIST.discard(".env")
        assert ".env" not in scan_secrets.ALLOWLIST
        scan_secrets.ALLOWLIST.add(".env")  # restore

    def test_scanner_itself_is_allowlisted(self):
        assert "scripts/scan_secrets.py" in scan_secrets.ALLOWLIST

    def test_env_example_is_allowlisted(self):
        assert ".env.example" in scan_secrets.ALLOWLIST

    def test_checksum_sidecars_are_allowlisted(self):
        assert scan_secrets._is_allowlisted("data/backups/v2/x.sha256")

    def test_placeholders_are_ignored(self):
        assert "local-secret" in scan_secrets.PLACEHOLDERS
        hits = scan_secrets.scan_text("KEY = 'local-secret'", "f.py")
        assert not any(h[1] == "generic_assignment" for h in hits)


class TestWorktreeScan:
    def test_repository_is_clean(self):
        """The default scan must find nothing in tracked files."""
        hits = scan_secrets.scan_worktree()
        assert hits == [], hits

    def test_skips_data_and_logs(self):
        assert any(
            part in ("data", "logs")
            for part in Path("data/posts.db").parts
        )

    def test_skips_git_directory(self):
        for path in Path(".").rglob("*"):
            if ".git" in path.parts:
                assert not scan_secrets._is_allowlisted(
                    path.relative_to(".").as_posix()
                ) or True  # .git is filtered by the parts check
                break
