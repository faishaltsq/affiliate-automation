"""Backfill clipper_uploads ledger from historical logs (run once).

Pairs "Upload ke @account" with the next clippo/output upload success, then
writes a fingerprint per (account, clip stem). Footage stem is the clip stem
minus the trailing _<start>_<dur> window marker.
"""
import glob
import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from src.tiktok.state import VideoStateDB

CLIP_RE = re.compile(r"clippo[\\/]output[\\/](clip_.+?\.mp4)")
ACCT_RE = re.compile(r"Upload ke @(\w+)")
SUCCESS_RE = re.compile(r"Subprocess upload succeeded")


def footage_stem(clip_stem: str) -> str:
    # clip_<footage>_<start>_<dur>  →  <footage>
    m = re.match(r"clip_(.+?)_\d+_\d+$", clip_stem)
    return m.group(1) if m else clip_stem


def fingerprint(account: str, stem: str) -> str:
    srcs = footage_stem(stem)
    raw = f"@{account}|{srcs}|{stem}".lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


pairs = []
for path in sorted(glob.glob("logs/tiktok/tiktok-2026-10-1*.log")):
    cur_acct = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = ACCT_RE.search(line)
            if m:
                cur_acct = m.group(1)
                continue
            if SUCCESS_RE.search(line):
                m2 = CLIP_RE.search(line)
                if m2 and cur_acct:
                    pairs.append((cur_acct, os.path.basename(m2.group(1))))
                    cur_acct = None

print(f"parsed upload pairs: {len(pairs)}")

db = VideoStateDB("data/tiktok/state.db")
added = 0
for acct, fname in pairs:
    stem = os.path.splitext(fname)[0]
    fp = fingerprint(acct, stem)
    if not db.is_clipper_uploaded(fp):
        db.mark_clipper_uploaded(fp, "backfill", f"@{acct}", fname)
        added += 1
db.close()

print(f"backfilled: {added} new fingerprints")
print(f"total ledger rows: {len(VideoStateDB('data/tiktok/state.db').recent_clipper_uploads(limit=999))}")
