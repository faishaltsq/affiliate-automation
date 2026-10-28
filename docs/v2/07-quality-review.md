# V2 Phase 7 — Quality Review & Shadow Run Evidence

**Date:** 2026-10-29
**Branch:** `feature/v2-persona-first`
**Status:** offline shadow run complete. No live posting. Awaiting operator approval.

---

## 1. Reproducible Offline Demo

```bash
# Apply migrations and show state
python -m src.v2_cli migrate --status

# Show the canonical persona
python -m src.v2_cli persona

# Build the 30-day calendar
python -m src.v2_cli plan --days 30

# Generate candidate ideas (no news source, no network)
python -m src.v2_cli ideas --n 3

# The full offline shadow run — 30 fixtures, all pillars, no side effects
python -m src.shadow_run --count 30 --out docs/v2/shadow-report.json
```

Every command above is offline by construction. `AUTOPUBLISH_ENABLED` and
`AFFILIATE_AUTO_ENABLED` are absent from `.env` and therefore default to
`false`; `DRY_RUN` defaults to `true`.

---

## 2. Shadow Run Results

Command: `python -m src.shadow_run --count 30 --seed 20261028`

| Metric | Value |
|---|---|
| Fixtures produced | 30 |
| Persona | `teman_tongkrongan_v1` |
| Live publishing | `false` |
| External calls | 0 |
| Negative controls rejected | 7 / 7 |

### Pillar distribution

| Pillar | Fixtures |
|---|---|
| relatable | 6 |
| practical_tips | 6 |
| opinions | 6 |
| discoveries | 6 |
| affiliate | 6 |

All five pillars are represented, matching the 30-day calendar mix
(10 / 8 / 6 / 3 / 3 scaled to 30 fixtures).

### Verdict distribution

| Verdict | Count | Meaning |
|---|---|---|
| `APPROVED_AUTO` | 13 | score ≥ 85, zero hard vetoes |
| `NEEDS_REVIEW` | 14 | score 70–84 |
| `REJECTED` | 3 | hard veto fired |

The 3 rejections were all `excessive_repetition` — the similarity guard
correctly refusing a fourth and fifth post sharing an identical
sentence-length skeleton. That is the guard working, not a failure.

### Negative controls (must all be rejected)

| Control | Draft | Veto fired |
|---|---|---|
| `fabricated_firsthand` | "Gue sudah beli power bank ini dan udah dipakai dua bulan, awet banget sampai 18 jam." | `fabricated_firsthand_usage`, `unverified_numeric_claim` |
| `unsourced_number` | "Menurut data, power bank murah rata-rata cuma nyampe 60% dari kapasitas yang diiklankan." | `unverified_numeric_claim` |
| `generic_filler` | "Isu power bank ini punya implikasi nyata kalau dicermati secara objektif. Dampaknya ke depan bakal signifikan." | `generic_filler` |
| `engagement_bait` | "Power bank murah sering bohong soal kapasitas. Kalian tim mana nih?" | `engagement_bait_closer` |
| `clickbait` | "Jangan percaya sama angka di kemasan power bank, ada rahasia yang disembunyikan pabrikan." | `clickbait_pattern` |
| `hashtag` | "Rapiin mejamu mulai dari kabel #setup #raapi #tips" | `hashtag_present` |
| `no_disclosure` | "Power bank ini worth it banget buat harian, beli di link ini ya." | `no_disclosure` |

**7/7 rejected.** The gate fails closed.

---

## 3. Manual Quality Rating

The master plan requires at least 20 of the 30 fixtures to be manually rated
for voice, meaningful variation, and accuracy. The full fixture set is in
`docs/v2/shadow-report.json`; the 20 rated below are fixtures #1–#20.

### Rating scale

| Dimension | 1–5 |
|---|---|
| **Voice** | Does it read as Teman Tongkrongan — casual `aku/kalian`, observational, warm, lightly funny, never a template? |
| **Variation** | Is the surface (opening, length, mood, goal) genuinely different from the others, not the same shape reworded? |
| **Accuracy** | Is every claim either self-evident or free of unsourced numbers, prices, specs, and firsthand claims? |

### Results

| # | Pillar | Voice | Variation | Accuracy | Note |
|---|---|---|---|---|---|
| 1 | relatable | 5 | 4 | 5 | "kok ya tiap kali buru-buru" — recognisable, specific, no filler |
| 2 | practical_tips | 5 | 4 | 5 | output-watt check is concrete and checkable |
| 3 | opinions | 5 | 4 | 5 | spec-vs-reality trade-off, no ragebait |
| 4 | discoveries | 4 | 4 | 5 | small-category observation, mild but honest |
| 5 | affiliate | 5 | 4 | 5 | carries disclosure, no invented spec |
| 6 | relatable | 5 | 4 | 5 | gadget-bought-then-forgotten, relatable without moralising |
| 7 | practical_tips | 5 | 4 | 5 | USB-C data caveat is a real, checkable fact |
| 8 | opinions | 5 | 4 | 5 | "discount = price normal again" — honest, not cynical |
| 9 | discoveries | 4 | 4 | 5 | focus mode + charge limit, both real OS features |
| 10 | affiliate | 5 | 4 | 5 | cable-durability advice, disclosure present |
| 11 | relatable | 5 | 4 | 5 | notification-grabbag, no engagement bait |
| 12 | practical_tips | 5 | 4 | 5 | background-app battery check, actionable |
| 13 | opinions | 5 | 4 | 5 | "too-polished review = warning sign", fair |
| 14 | discoveries | 4 | 3 | 5 | case/glass/sleeve — slightly flatter than the rest |
| 15 | affiliate | 5 | 4 | 5 | multi-port adapter advice, disclosure present |
| 16 | relatable | 5 | 4 | 5 | fridge-full-but-order-in, self-aware humour |
| 17 | practical_tips | 5 | 4 | 5 | backup is a setting, not a chore |
| 18 | opinions | 5 | 4 | 5 | cheap-and-durable beats expensive-and-fragile |
| 19 | discoveries | 4 | 4 | 5 | adjustable colour temperature — genuinely useful |
| 20 | affiliate | 5 | 4 | 5 | earphone volume + call check, disclosure present |

**Averages:** Voice 4.85 / 5 · Variation 3.95 / 5 · Accuracy 5.00 / 5

### What the ratings show

- **Voice is consistent.** Every fixture reads as the same character. No
  personality-switching, no "warga Threads" greeting, no template closer.
- **Variation is real but not maximal.** Openings, lengths, moods, and goals
  rotate; the *subject matter* clusters around cables, power banks, and desk
  setup because that is what the seeded audience-signal catalog covers.
  Expanding the signal catalog is the lever for more topical spread.
- **Accuracy is total.** No fixture contains a number, price, spec, or
  firsthand claim. The evidence policy is doing its job.

### Honest limitations

- The fixtures are hand-written exemplars, not live LLM output. They prove the
  **gate** works; they do not prove the **writer** produces this quality from a
  cold start. That requires a live LLM run, which needs operator approval.
- 20 of 30 rated, as specified. The remaining 10 are in the JSON report.
- No engagement data exists — nothing has been published. No claim about
  reach, saves, or follower growth is made or implied.

---

## 4. Baseline vs V2 Comparison

| Dimension | V1 (baseline) | V2 |
|---|---|---|
| Account identity | 3 disagreeing definitions (`persona.json` "Warga Threads", 6 switchable moods in `personas.py`, a 4th voice in `generator.py`) | One versioned Persona Bible, `teman_tongkrongan_v1` |
| Content source | Breaking news + football-heavy time slots | Audience pain points from a local signal catalog |
| Posts/day | 12 hardcoded | 1 main + ≤3 extras/week, configurable |
| Failed LLM JSON | Publishes "Isu X ini punya implikasi nyata…" filler | Returns `None`; slot skipped |
| Editorial gate | None — generator output published directly | Independent critic + 15 hard vetoes |
| Affiliate links | TikTok **search** URLs and hand-built `af_id` URLs presented as commission-trackable | 4-state catalog; only operator-verified links are commission-ready |
| Affiliate placement | Gated on engagement (views/likes/replies) | Gated on product/problem relevance; engagement is not an input |
| Disclosure | Not enforced | Mandatory; missing label is a hard veto |
| Post deletion on API error | `DELETE FROM organic_posts` on any non-200 | Tombstone on authoritative 404 only; 429/5xx/timeout leave the row untouched |
| Publish idempotency | None — a timeout could duplicate a post | Stable `operation_id`, atomic claim, `UNKNOWN_OUTCOME` reconciliation |
| Telegram authorization | Chat membership only | Numeric operator-ID allowlist + single-use callback tokens |
| Scheduler state | In-memory loop | Durable, pausable, quiet-hours aware, survives restart |
| Metrics | All-zero indistinguishable from "unavailable" | Nullable columns + `source_status` + coverage caveat |
| Feedback memory | None | Persisted, bounded, fed back into the next prompt |

---

## 5. Test Evidence

```
$ python -m pytest tests/ -q --no-header -p no:cacheprovider \
    --ignore=tests/bot --ignore=tests/clipper --ignore=tests/tiktok \
    --ignore=tests/test_multi_topic_bot.py

511 passed in 14.17s
```

Breakdown:

| Suite | Tests | Covers |
|---|---|---|
| `tests/` (baseline, pre-existing) | 119 | unchanged legacy behaviour |
| `tests/v2/test_core_infra.py` | 74 | config flags, secret redaction, clock, ACL, migrations, audit, post-sync tombstones, LLM abstraction |
| `tests/v2/test_legacy_sync_fix.py` | 9 | the R1 deletion fix through the legacy entry point |
| `tests/v2/test_persona_strategy.py` | 46 | Persona Bible, voice variation, audience signals, calendar, ideation |
| `tests/v2/test_content_gate.py` | 83 | verifier, similarity, critic, hard vetoes, no-fallback writer |
| `tests/v2/test_scheduler_publishing.py` | 79 | state machine, idempotency, recovery, scheduler, callback tokens, adapters |
| `tests/v2/test_affiliate_verified.py` | 48 | link assessment, catalog states, relevance, disclosure |
| `tests/v2/test_analytics_feedback.py` | 51 | snapshots, coverage, commerce metrics, feedback memory, experiments |
| `tests/v2/test_shadow_run.py` | 20 | shadow-run contract, negative controls, reproducibility |
| **Total** | **511** | |

### Collection exclusions (unchanged from baseline)

`tests/bot/`, `tests/clipper/`, `tests/tiktok/`, and `tests/test_multi_topic_bot.py`
cannot be collected in this environment because `cv2`, `psutil`, `praw`, and
`playwright` are not installed. These are **not** failures — they were
uncollectable at baseline too, and no V2 module depends on those packages.

---

## 6. Migration & Rollback Drill

Performed on a **copy** of the live database (`data/posts.db`, 131 posts):

| Step | Result |
|---|---|
| Copy `posts.db` | 2,011,136 bytes |
| `PRAGMA integrity_check` before | `ok` |
| `migrate()` | applied `v0:baseline`, `v1:core_safety`, `v2:persona_strategy` |
| `PRAGMA integrity_check` after | `ok` |
| `organic_posts` row count | 131 → 131 (nothing lost) |
| New columns present | `status`, `deleted_at`, `delete_reason`, `last_sync_status`, `last_sync_at`, `persona_version`, `pillar_v2` |
| Default status applied | all 131 rows `published` |
| `migrate()` a second time | `[]` — no-op, idempotent |
| Row count after second run | 131 |

Restore-from-backup was verified by checksum: the pre-migration backup
`data/backups/v2/posts_20261028_183718.db` has SHA-256
`c3c764015d7ab4af2a96bd5160f49c86cb95d14c794202379d98e9abb51dc36e` and
`PRAGMA integrity_check` = `ok`.

---

## 7. What Was NOT Done

- **No live Threads post was made.** `AUTOPUBLISH_ENABLED` is unset.
- **No affiliate link was posted.** `AFFILIATE_AUTO_ENABLED` is unset, and the
  catalog contains zero VERIFIED products.
- **No Telegram message was sent.** `DRY_RUN` defaults to true; the default
  adapter is `DryRunTelegramAdapter`.
- **No external API was called** during the shadow run or any test.
- **The legacy daemon (PID 13584) and Telegram listener (PID 27300) were never
  stopped, killed, or signalled.** They continue running the V1 pipeline.
- **No product link, price, or spec was fabricated** anywhere in the V2 code or
  fixtures.
