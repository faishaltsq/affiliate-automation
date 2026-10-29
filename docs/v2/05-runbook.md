# V2 — Runbook

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 1. Preflight

```bash
# Confirm flags are safe
python -m src.v2_cli health
```

Expected: `autopublish=False`, `affiliate_auto=False`, `dry_run=True`.

If any live flag is on and you did not intend it, stop and fix `.env` first.

## 2. Daily Operations

### Check state

```bash
python -m src.v2_cli health        # flags, tables, queue, metric coverage
python -m src.v2_cli audit --limit 20
```

### Build / inspect the calendar

```bash
python -m src.v2_cli plan --days 30
```

### Generate ideas (offline)

```bash
python -m src.v2_cli ideas --n 3
```

### Generate a draft without publishing

```bash
python -m src.v2_cli draft --pillar practical_tips
```

### Reconcile posts against Threads

```bash
python -m src.v2_cli sync
```

This never deletes a row. A `429`, `5xx`, `401`, `403`, or timeout records
`last_sync_status` and leaves the post untouched. Only an authoritative `404`
tombstones it.

## 3. Migrations

```bash
python -m src.v2_cli migrate --status   # inspect
python -m src.v2_cli migrate            # apply
```

Always back up first:

```bash
mkdir data\backups\v2
copy data\posts.db data\backups\v2\posts_%date:~10,4%%date:~4,2%%date:~7,2%.db
```

## 4. Pause / Resume

```bash
# Via Telegram (operator only)
/pause
/resume
```

The pause state lives in `scheduler_state` and survives a restart. A paused
scheduler still answers `/status`.

## 5. Recovering an `UNKNOWN_OUTCOME` Job

A publish that timed out is **not** retried automatically.

```bash
python -m src.v2_cli audit --limit 50    # find the operation_id
```

1. Check the Threads account manually. Did the post appear?
2. **Yes** → the job should be reconciled as published. The recovery sweep does
   this when the API lookup confirms it.
3. **No** → the job can be requeued safely.
4. **Cannot tell** → leave it. `is_safe_to_restart()` returns `False` while any
   job is `UNKNOWN_OUTCOME`, which is the correct answer: do not restart into a
   possible duplicate.

## 6. Restart Procedure

```bash
# 1. Pre-flight
python -c "from src.orchestration.recovery import is_safe_to_restart; \
print(is_safe_to_restart())"

# 2. If safe, restart the V2 process. On boot it runs startup_sweep():
#    - reclaims expired leases
#    - reconciles UNKNOWN_OUTCOME jobs via API lookup
#    - requeues FAILED jobs that still have retry budget
#    - reports anything needing a human
```

Pending work is preserved. Nothing is published twice.

## 7. Adding a Verified Affiliate Product

```bash
# Via Telegram (operator only)
/products_add https://vt.tokopedia.com/t/XXXX/?af_id=12345
```

The link is imported as `UNVERIFIED`. It becomes `VERIFIED` only after an
operator confirms it came from the official affiliate dashboard and supplies an
expiry. A marketplace search URL is refused at import.

Until a product is `VERIFIED` + approved, its calendar slot runs as organic
content.

## 8. Quiet Hours

Default 23:00–06:00 WIB. Configurable:

```
V2_QUIET_START_HOUR=23
V2_QUIET_END_HOUR=6
```

The scheduler sleeps until quiet hours end rather than polling.

## 9. Health Signals

| Signal | Healthy |
|---|---|
| `scheduler_paused` | `false` |
| `queue[QUEUED]` | small and draining |
| `oldest_pending_age_seconds` | well under the lease (300 s) |
| `metric_coverage.coverage_ratio` | ≥ 0.5 (below that, averages are indicative only) |

## 10. Offline Integration Rehearsal

Runs the entire chain — calendar → scheduler → ideation → writer → quality gate
→ publication queue → publish → recovery — against an isolated database with a
fake LLM and the dry-run adapter.

```bash
python -m src.integration_check --verbose
python -m src.integration_check --out docs/v2/integration-report.json
```

18 checks, all of which must pass. It is part of the test suite
(`tests/v2/test_integration_check.py`), so a regression fails the build.

This is the check that found the publisher/adapter interface mismatch: the
state machine expected `submit()` while the adapter exposed
`publish_text()`, so injecting the real dry-run adapter raised
`NotImplementedError` and every dry-run publish failed. Unit tests missed it
because each used its own fake.

## 11. Cutover Preflight

Read-only. Reports what would block a live cutover without changing anything.

```bash
python -m src.integration_check --preflight
```

Current output flags one item: **`TELEGRAM_OPERATOR_IDS` is empty.** With no
allowlist the V2 command centre denies every mutation, so `/approve`, `/pause`,
and `/rollback` would all be unusable. Populate it with the numeric Telegram
user IDs of the real operators before enabling `V2_TELEGRAM_COMMANDS_ENABLED`.

## 12. Known Limitations

- `cv2`, `psutil`, `praw`, and `playwright` are not installed in this
  environment, so `tests/bot/`, `tests/clipper/`, `tests/tiktok/`, and
  `tests/test_multi_topic_bot.py` cannot be collected. This is unchanged from
  the pre-V2 baseline.
- No live LLM run has been performed. The shadow run exercises the gate with
  hand-written exemplars; writer quality from a cold start is unverified.
- The affiliate catalog is empty. No commission-ready product exists yet.
- The V2 scheduler is not enabled (`V2_SCHEDULER_ENABLED=false`). The legacy
  daemon continues to run the account.
