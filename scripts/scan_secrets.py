"""scripts/scan_secrets.py — secret scanner for the worktree and git history.

Master plan §11: "secret scanner on worktree and git history; rotate any
exposed secrets, do not merely delete a committed file."

Deleting a file does not remove it from history. This scanner reports every
occurrence so an operator knows what to rotate.

Usage:
    python scripts/scan_secrets.py            # worktree only (fast)
    python scripts/scan_secrets.py --history  # every commit reachable from HEAD
    python scripts/scan_secrets.py --all      # worktree + all branches
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Patterns are deliberately conservative: a match means "look at this", not
# "this is definitely a secret". False positives are cheaper than a missed
# credential.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("telegram_bot_token", re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("openrouter_key", re.compile(r"\bsk-or-v1-[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("generic_assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|api[_-]?secret|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|app[_-]?secret|password|passwd|secret[_-]?key)"
        r"\s*[=:]\s*['\"][A-Za-z0-9_\-./+]{16,}['\"]"
    )),
    ("token_query_param", re.compile(
        r"(?i)[?&](?:access_token|api_key|apikey|sig|signature|secret|token)"
        r"=[A-Za-z0-9_\-./+]{16,}"
    )),
]

# Files that legitimately contain these shapes.
ALLOWLIST = {
    "scripts/scan_secrets.py",       # the patterns themselves
    ".env.example",                  # placeholders, all values empty
    "tests/v2/test_core_infra.py",   # SUPERSECRETKEY proves redaction works
    "tests/v2/test_secret_scanner.py",  # deliberately-shaped test fixtures
    "docs/v2/00-audit.md",           # documents a checksum, not a secret
    "docs/v2/06-cutover-rollback.md",
    # .env is the operator's local credential store and is gitignored by
    # design. Flagging it on every run trains the operator to ignore the
    # report, so it is checked separately by --include-env.
    ".env",
}

# Values that are obviously not secrets.
PLACEHOLDERS = {
    "", "your_key_here", "changeme", "xxx", "todo", "none", "null",
    "local-secret", "redacted", "example",
}


def _is_allowlisted(rel: str) -> bool:
    return rel in ALLOWLIST or rel.endswith(".sha256")


def _mask(value: str) -> str:
    """Show enough to locate a credential, not enough to use it.

    A scanner that prints full secrets turns a report into a leak — the output
    ends up in CI logs, screenshots, and chat transcripts.
    """
    v = value.strip().strip("'\"")
    if len(v) <= 8:
        return f"{v[:2]}…({len(v)} chars)"
    return f"{v[:6]}…{v[-2:]} ({len(v)} chars)"


def scan_text(text: str, source: str) -> list[tuple[str, str, str]]:
    """Return (source, pattern_name, masked_match) for every hit."""
    hits = []
    for name, pattern in PATTERNS:
        for m in pattern.finditer(text):
            value = m.group(0)
            if value.strip().strip("'\"") in PLACEHOLDERS:
                continue
            hits.append((source, name, _mask(value)))
    return hits


def scan_worktree() -> list[tuple[str, str, str]]:
    """Scan tracked files in the working tree."""
    hits = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO).as_posix()
        if _is_allowlisted(rel):
            continue
        if any(part in (".git", "__pycache__", "node_modules", "venv",
                        ".venv", "data", "logs") for part in path.parts):
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits.extend(scan_text(text, rel))
    return hits


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout


def scan_history(ref: str = "HEAD") -> list[tuple[str, str, str]]:
    """Scan every blob reachable from `ref`."""
    hits = []
    revs = _git("rev-list", ref).split()
    for rev in revs:
        files = _git("ls-tree", "-r", "--name-only", rev).splitlines()
        for rel in files:
            if _is_allowlisted(rel):
                continue
            blob = _git("show", f"{rev}:{rel}")
            if not blob:
                continue
            for source, name, value in scan_text(blob, f"{rev[:8]}:{rel}"):
                hits.append((source, name, value))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python scripts/scan_secrets.py",
        description="Scan the worktree and git history for exposed secrets.",
    )
    parser.add_argument("--history", action="store_true",
                        help="scan every commit reachable from HEAD")
    parser.add_argument("--all", action="store_true",
                        help="scan worktree plus every branch")
    parser.add_argument("--include-env", action="store_true",
                        help="also scan the local .env (gitignored by design)")
    args = parser.parse_args(argv)

    print("Secret scan — Affiliate Automation")
    print()

    if args.include_env:
        ALLOWLIST.discard(".env")
    worktree_hits = scan_worktree()
    print(f"Worktree: {len(worktree_hits)} hit(s)")
    for source, name, value in worktree_hits:
        print(f"  [{name}] {source}: {value}")

    history_hits: list[tuple[str, str, str]] = []
    if args.history or args.all:
        refs = ["HEAD"]
        if args.all:
            refs = [r.strip() for r in _git("for-each-ref", "--format=%(refname)").splitlines() if r.strip()]
        for ref in refs:
            history_hits.extend(scan_history(ref))
        # De-duplicate across refs.
        seen = set()
        unique = []
        for hit in history_hits:
            key = (hit[0].split(":", 1)[-1], hit[1], hit[2])
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)
        history_hits = unique
        print(f"History ({len(refs)} ref(s)): {len(history_hits)} hit(s)")
        for source, name, value in history_hits:
            print(f"  [{name}] {source}: {value}")

    total = len(worktree_hits) + len(history_hits)
    print()
    if total == 0:
        print("CLEAN — no secrets found.")
        return 0

    print(f"{total} potential secret(s) found.")
    print()
    print("If any are real credentials:")
    print("  1. Rotate them at the provider. Deleting the file is NOT enough —")
    print("     the value remains in git history.")
    print("  2. Add the file to .gitignore if it is a local config.")
    print("  3. Rewrite history only with operator approval, and force-push")
    print("     with coordination.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
