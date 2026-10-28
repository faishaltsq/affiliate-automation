# V2 — Cutover & Rollback

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`
**Status:** NOT APPROVED FOR LIVE CUTOVER. Awaiting explicit operator approval.

---

## 1. Current State

| Item | State |
|---|---|
| Feature branch | `feature/v2-persona-first` (from `master@94c04e1`) |
| `AUTOPUBLISH_ENABLED` | **unset → false** |
| `AFFILIATE_AUTO_ENABLED` | **unset → false** |
| `DRY_RUN` | **unset → true** |
| `V2_SCHEDULER_ENABLED` | **unset → false** |
| Legacy daemon (PID 13584) | **still running, untouched** |
| Legacy Telegram listener (PID 27300) | **still running, untouched** |
| Live posts made by V2 | **0** |
| Affiliate links posted by V2 | **0** |
| External API calls by V2 | **0** |

## 2. Verified Backups

| File | Size | SHA-256 | Integrity |
|---|---|---|---|
| `data/backups/v2/posts_20261028_183718.db` | 2,011,136 B | `c3c764015d7ab4af2a96bd5160f49c86cb95d14c794202379d98e9abb51dc36e` | `ok` (131 posts) |
| `data/backups/v2/guardrails_20261028_183722.db` | 163,840 B | see `.sha256` sidecar | `ok` |

Checksum sidecars sit beside each backup.

## 3. Rollback Procedure

Because all V2 migrations are **additive**, rollback is a backup restore. No
schema reversal is required.

### 3.1 Stop V2 only

```bash
# Do NOT stop the legacy daemon or the Telegram listener.
# They are independent processes running the V1 pipeline.
```

If a V2 scheduler process is running, stop it. Confirm nothing is mid-publish:

```bash
python -c "from src.orchestration.recovery import is_safe_to_restart; \
ok, why = is_safe_to_restart(); print(ok, why)"
```

If `ok` is `False` because of an `UNKNOWN_OUTCOME` job, resolve it first —
restoring a backup while a publish is ambiguous risks a duplicate after restore.

### 3.2 Restore the database

```bash
copy /Y data\backups\v2\posts_20261028_183718.db data\posts.db
```

### 3.3 Verify the restore

```bash
python -c "
import sqlite3, hashlib
c = sqlite3.connect('data/posts.db')
print('integrity:', c.execute('PRAGMA integrity_check').fetchone()[0])
print('organic_posts:', c.execute('SELECT COUNT(*) FROM organic_posts').fetchone()[0])
print('user_version:', c.execute('PRAGMA user_version').fetchone()[0])
c.close()
h = hashlib.sha256(open('data/posts.db','rb').read()).hexdigest()
print('sha256:', h)
print('matches backup:', h == 'c3c764015d7ab4af2a96bd5160f49c86cb95d14c794202379d98e9abb51dc36e')
"
```

Expected: `integrity: ok`, `organic_posts: 131`, `user_version: 0`,
`matches backup: True`.

### 3.4 Confirm the legacy pipeline is healthy

```bash
python main.py organic --status
```

The V1 daemon should report normally. It never stopped, so it will simply
resume reading the restored database.

### 3.5 Disable V2 flags

Remove or set to false in `.env`:

```
AUTOPUBLISH_ENABLED=false
AFFILIATE_AUTO_ENABLED=false
DRY_RUN=true
V2_SCHEDULER_ENABLED=false
V2_TELEGRAM_COMMANDS_ENABLED=false
```

### 3.6 Rollback is complete when

- [ ] `data/posts.db` checksum matches the pre-migration backup
- [ ] `PRAGMA integrity_check` returns `ok`
- [ ] `organic_posts` row count is 131
- [ ] `python main.py organic --status` runs without error
- [ ] All V2 live flags are false
- [ ] No V2 process is running

## 4. Cutover Procedure (NOT YET APPROVED)

Do not perform any of this without explicit operator approval.

### 4.1 Preconditions

- [ ] Operator has reviewed `docs/v2/07-quality-review.md`
- [ ] Operator has manually rated ≥ 20 shadow-run fixtures
- [ ] `TELEGRAM_OPERATOR_IDS` is populated with real operator IDs
- [ ] At least one product is `VERIFIED` in the catalog, or the affiliate
      pillar is confirmed to run as organic
- [ ] A fresh checksum-verified backup exists
- [ ] Threads and TikTok affiliate platform policies have been re-checked
      against current terms
- [ ] The full test suite passes (511 tests)

### 4.2 Shadow period (no live posting)

Run the V2 scheduler with `V2_SCHEDULER_ENABLED=true` but
`AUTOPUBLISH_ENABLED=false`. Drafts are generated and queued; nothing is
published. Review the queue for at least 7 days.

### 4.3 Limited live enablement

Only after the shadow period is clean:

1. Set `AUTOPUBLISH_ENABLED=true`, keep `DRY_RUN=true` for one more day.
2. Set `DRY_RUN=false` with `V2_MAIN_POSTS_PER_DAY=1` and
   `V2_MAX_EXTRA_POSTS_PER_WEEK=0`.
3. Pause the legacy daemon (`/pause` on the legacy bot, or stop PID 13584) so
   the two systems cannot both post.
4. Observe for the agreed window.
5. Only then consider raising the volume caps.

### 4.4 Keeping the legacy path

The legacy daemon and its code are preserved. Rollback at any point is the
procedure in §3. **Retain the rollback path for the full observation window.**

## 5. Risk Register

| Risk | Mitigation |
|---|---|
| Duplicate post after a publish timeout | `UNKNOWN_OUTCOME` + API reconciliation; never blind-retry |
| Post history lost on a transient API error | Tombstones only on authoritative 404 |
| Unauthorized publish via Telegram | Numeric operator allowlist + single-use callback tokens |
| Unverified affiliate link posted | 4-state catalog; relevance gate; mandatory disclosure |
| Content-farm filler on the account | No fallback path in the writer; `generic_filler` veto |
| Two schedulers posting at once | V2 and legacy are separate processes; pause one before enabling the other |
| Migration corrupts data | Additive + idempotent + verified backup first |
| Overfitting to a handful of views | Minimum-sample floors in the experiment evaluator |
