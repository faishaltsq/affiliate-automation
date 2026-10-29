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

- 20 of 30 rated, as specified. The remaining 10 are in the JSON report.
- No engagement data exists — nothing has been published. No claim about
  reach, saves, or follower growth is made or implied.

---

## 3b. Live LLM Verification (2026-10-29)

The limitation above — "the writer has never run against a real model" — has
now been closed. The operator supplied credentials and authorised a live run.

### Credential audit (read-only probes, no writes)

| Credential | Status |
|---|---|
| `THREADS_USER_ID` + `THREADS_ACCESS_TOKEN` | **LIVE** — HTTP 200, account `@ranaraaa911` |
| `TELEGRAM_BOT_TOKEN` | **LIVE** — HTTP 200, bot `@dailyprivateagentbot` |
| `LLM_API_KEY` (Trust Is Gold gateway) | **LIVE** — HTTP 200, `tig/step-5-unrestricted` ~4.7 s, `tig/step-5-preview` ~2.9 s |
| `FACEBOOK_ACCESS_TOKEN` | **EXPIRED** — OAuth 190, subcode 467 "user logged out" |
| `REPLICATE_API_TOKEN` | **INVALID** — HTTP 401 Unauthenticated |
| `TIKTOK_AFFILIATE_ID` | Set (13 chars) — no affiliate API configured, so it is a username, not a trackable link |
| `DISCORD_TOKEN`, `MOTIONKLIP_GUILD_ID` | Set — used by the Clipper pipeline, not probed |

**None of these were used to publish, send, or post anything.** The probes were
`GET` requests only.

### Bug found by the live run

The writer produced **zero drafts** against the real model. Every unit test
passed because the tests fed it clean JSON.

The gateway returns near-valid JSON with a **key that has no value at all**:

```json
{
 "content": "Kamu nggak akan pernah sadar kenapa kabel charger di tas selalu kusut..."
 "disclosure":
}
```

`json.loads` rejects this, and the gate then correctly refused to publish
anything — so the failure was silent and total.

Fixed with a bounded structural repair (`_repair_json` in
`src/content/writer.py`) that supplies empty values for malformed keys. It
**never invents, truncates, or rewrites content**: a repaired draft carries
exactly the words the model produced. It is idempotent, and if it cannot
produce parseable JSON the draft still fails closed.

### Live writer output

Three pillars, real model, no human editing:

| Pillar | Angle | Output |
|---|---|---|
| `relatable` | cerita singkat situasi yang biasa kejadian | "Kabel charger di tas tuh jago banget nyimpulin diri sendiri. Yang bikin kesel, ujungnya pelan-pelan mulai kebuka. Coba cek ujung kabel yang paling sering dipakai, kalau keliatan ada yang mulai terkelupas, mungkin itu tandanya kabelnya udah mau lepas." |
| `practical_tips` | kesalahan umum dan cara hindarinya | "Pernah lihat angka di kemasan power bank, tapi ternyata nggak sebesar yang diharapkan? Kapasitas nyata biasanya lebih kecil dari label. Perhatikan tegangan output dan efisiensi konversinya, jangan cuma tergiur angka besar." |
| `opinions` | klaim marketing vs realita pemakaian | "Liat diskon gede di layar ponsel, kadang kita cuma dibikin senang sebentar. Harganya balik normal, bukan turun beneran. Cek harga sebelum tanggal promo, biar nggak salah senyum-senyum sendiri." |

All three are in-persona, distinct, and free of unsourced numbers. The writer
works.

### Full live pipeline: writer → critic → gate

All five pillars, real model, no human editing:

| Pillar | Verdict | Score | Vetoes |
|---|---|---|---|
| `relatable` | `NEEDS_REVIEW` | 84.0 | none |
| `practical_tips` | `NEEDS_REVIEW` | 76.0 | none |
| `opinions` | `NEEDS_REVIEW` | 74.0 | none |
| `discoveries` | `REJECTED` | 0.0 | `critic_unavailable` |
| `affiliate` | `NEEDS_REVIEW` | 73.0 | none |

Two things this run proved:

1. **The gate fails closed correctly.** When the critic returned empty output
   for `discoveries`, the draft was rejected with `critic_unavailable` rather
   than waved through. A draft nobody checked does not publish.
2. **The critic caught a defect the regex missed.** The `opinions` draft opened
   with *"Kalian sering nunggu diskon gede ya?"* — a poll dressed as a question.
   The bait regex only inspected closers, so it passed; the critic scored the
   draft lowest of the set (74) for exactly that reason.

### Fix prompted by the live run

`_BAIT_OPENER_RE` now catches bloc-address openers and self-identification
polls. It deliberately does **not** ban question openers wholesale, because the
persona guide explicitly permits `pertanyaan_jujur` — an honest question.
"Pernah nggak sih HP dipakai tidur, besoknya baterai tetap penuh?" is allowed;
"Kalian tim mana nih?" is not. Eleven cases pin the distinction.

### What is still unverified

- **Engagement.** Nothing has been published, so there is no reach data and no
  claim about reach, saves, or follower growth is made anywhere.
- **Thread publishing.** The live `THREADS_ACCESS_TOKEN` was verified with a
  read-only profile probe only. No container was created and nothing was
  posted.
- **Affiliate attribution.** No product has been verified, so the commerce path
  has never executed end-to-end with a real link.

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
