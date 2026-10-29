# V2 Phase 0 — Repository Audit, Baseline & Freeze

**Date:** 2026-10-28
**Branch:** `feature/v2-persona-first` (created from `master`)
**Baseline tree SHA:** `94c04e132105003a86ae302b7568a6ad57a1f11e`
**Working tree at audit start:** clean except two untracked spec files
  (`AFFILIATE_AUTOMATION_V2_MASTER_PLAN.md`, `AFFILIATE_AUTOMATION_V2_AGENT_PROMPT.md`)

---

## 1. Environment

| Item | Value |
|---|---|
| OS | Windows 10.0.26200 (win32), x64 |
| Python | 3.12.10 |
| pytest | 8.3.4 |
| Installed | `requests`, `python-dotenv`, `APScheduler`, `pytest` |
| **Missing** | `psutil`, `cv2` (opencv), `praw`, `playwright`, `pandas`, `cloudinary`, `yt-dlp`, `tiktok-uploader` |

The missing packages are the reason several existing test modules cannot be
collected. They are **not** installed in this environment, so those suites are
recorded as *uncollectable* rather than failing. No attempt was made to install
them: the V2 core must not depend on them, and installing heavy binaries
(opencv, playwright browsers) is an operator decision.

---

## 2. Baseline Test Results

Command:

```
python -m pytest tests/ -q --no-header -p no:cacheprovider \
  --ignore=tests/bot --ignore=tests/clipper --ignore=tests/tiktok \
  --ignore=tests/test_multi_topic_bot.py
```

| Result | Count |
|---|---|
| **Passed** | **119** |
| Failed | 0 |
| Errors | 0 |
| Duration | 5.35 s |

### Collection exclusions and why

| Path | Reason | Error |
|---|---|---|
| `tests/bot/*` | imports `src.bot.clipper_agent` → `src.clipper.pipeline` → `src.tiktok.subtitle_detector` → `import cv2` | `ModuleNotFoundError: No module named 'cv2'` |
| `tests/clipper/*` | same `cv2` chain | `ModuleNotFoundError: No module named 'cv2'` |
| `tests/tiktok/*` | same `cv2` chain | `ModuleNotFoundError: No module named 'cv2'` |
| `tests/test_multi_topic_bot.py` | imports `src.bot.telegram_listener` → `src.singleton` → `import psutil` | `ModuleNotFoundError: No module named 'psutil'` |

**Consequence for V2:** any new V2 module must be importable without `psutil`,
`cv2`, `praw`, or `playwright`. V2 tests are therefore written to run in this
exact environment and are added to the *collectable* set. The video/bot suites
remain gated on their optional dependencies and are re-run only when the
operator installs them.

### Baseline test inventory (119 passing)

| File | Tests | Covers |
|---|---|---|
| `tests/test_affiliate_threshold.py` | 20 | engagement gate, buying-intent comment picking, chain-part dedup, disaster skip |
| `tests/test_chain_pipeline.py` | — | 4-part chain structure, bonus reply |
| `tests/test_chain_pipeline_numbers.py` | — | unsourced-figure stripping / regeneration |
| `tests/test_content_ops.py` | — | QC, scorer, metadata, editor |
| `tests/test_guardrails.py` | — | JSON repair, quota counter, sanitizer, backoff |
| `tests/test_multi_topic_bot.py` | *(uncollectable)* | Telegram listener routing |
| `tests/test_organic_editorial_tone.py` | — | sensitive-context override, tone rewrite |
| `tests/test_product_extractor.py` | — | product metadata extraction |
| `tests/test_threads_affiliate_preview.py` | — | affiliate draft preview |
| `tests/test_threads_bot_personas.py` | — | manual persona routing |
| `tests/test_threads_personas.py` | — | persona lookup, tags, state persistence |

---

## 3. Live Processes (must not be disturbed)

Two production processes are running right now, both launched from the Hermes
venv (`.../environments/144c22db.../venv/Scripts/python.exe`):

| PID | Command | Role |
|---|---|---|
| **13584** | `main.py organic --daemon` | 12-posts/day organic scheduler |
| **27300** | `main.py bot` | Telegram listener + TikTok scheduler |

Both write to `data/posts.db`. `scripts/watchdog.py` supervises them and
respawns them if they die. **No V2 change may break either process while it is
running.** All V2 work is additive: new packages, new tables, new CLI
subcommands. The legacy daemon keeps running on its own code path until the
operator approves cutover.

PID files: `data/rara_daemon.pid` (13584), `data/telegram_listener.pid` (27300).

---

## 4. Entry Points & Call Graph

```
main.py
├── organic            → src/organic/daemon.py:run_daemon / show_status
│                         └── execute_organic_post()  [publisher.py]
│                               ├── plan_single_post()          [planner.py]
│                               ├── generate_organic_post()     [generator.py]
│                               │     ├── _llm()                [brief/generator.py]
│                               │     ├── repair_json()         [guardrails.py]
│                               │     ├── fetch_topic_image_query() [trends/multi_source.py]
│                               │     └── generate_editorial_card() [image/card_generator.py]
│                               ├── post_threads()/post_thread_chain() [publish/threads.py]
│                               └── send_image_to_telegram()    [publish/telegram.py]
├── bot                → src/bot/telegram_listener.py:run_telegram_listener
│                         ├── ensure_single_instance()          [singleton.py]
│                         ├── handle_message()      → threads/clipper/tiktok agents
│                         ├── handle_callback_query()
│                         └── start_scheduler()                 [tiktok/scheduler.py]
├── tiktok             → src/tiktok/pipeline.py:run_pipeline
├── ops                → src/content_ops/pipeline.py:run_batch_production
├── link / trends / worker / full / monitor / single
└── (default)          → trends → worker
```

### Organic posting loop (the system being replaced)

```
daemon.run_daemon()
  └─ loop every 80–110 min (±15 jitter)
       ├─ _is_night() quiet hours 01:00–05:00 WIB
       ├─ execute_organic_post()
       │    ├─ get_today_post_count() >= DAILY_MAX(12) → skip
       │    ├─ plan_single_post()  ← planner.py (news-first, football-weighted)
       │    ├─ generate_organic_post()  ← generator.py (news-based copy)
       │    ├─ post_threads() / post_thread_chain()  ← publish/threads.py (LIVE)
       │    └─ log_organic_post()  ← organic/db.py
       ├─ _check_affiliate_queue()
       └─ refresh_post_metrics()  ← organic/feedback.py
```

### Affiliate reply loop

```
affiliate/comment_engine.py:check_and_comment_our_posts()
  ├─ get_our_posts()                       [Threads API]
  ├─ get_post_metrics()                    [Threads /insights]
  ├─ post_qualifies()                      [views≥250 OR likes≥20, AND replies≥5, AND age≥45m]
  ├─ pick_affiliate_reply_target()
  ├─ search_product()                      [affiliate/tiktok.py — Tokopedia scrape]
  ├─ generate reply via _llm()
  └─ post reply                            [Threads API — LIVE]
```

---

## 5. Database Baseline (`data/posts.db`, 2.0 MB)

| Table | Rows | Notes |
|---|---|---|
| `organic_posts` | **131** | the content history that must survive V2 |
| `article_context` | 1712 | RSS headline → summary/provenance cache |
| `posts` | 21 | legacy worker output |
| `used_images` | 6 | image dedup |
| `affiliate_comment_history` | 4 | **real commission-tracked links already posted** |
| `affiliate_queue` | 1 | one fired row |
| `ranara_posts` | 0 | empty legacy table |
| `scheduler_state` | 1 | `last_auto_persona = calm` |

`organic_posts` columns: `id, source, pillar, topic, content, image_url,
threads_id, created_at, post_structure, source_summary, views, likes, replies,
metrics_updated_at`.

`data/guardrails.db`: `daily_counters` (20), `failures` (182).

**No `status` / `deleted_at` column exists** — deletion is physical. This is the
data-loss bug Phase 1 fixes.

---

## 6. Risk Classification

| # | Finding | File | Severity | V2 disposition |
|---|---|---|---|---|
| R1 | `sync_with_threads_status()` **DELETEs** `organic_posts` rows on any non-200 (429, 5xx, 401, 403, timeout, transport error) | `src/organic/db.py:224-258` | **CRITICAL — data loss** | Phase 1: tombstones + status codes; never delete on transient failure |
| R2 | No sender-ID authorization on Telegram mutation commands; chat membership is the only gate | `src/bot/telegram_listener.py:33-66`, `src/bot/threads_agent.py:292+` | **CRITICAL — unauthorized publish** | Phase 1/4: operator allowlist from env, enforced on messages *and* callbacks |
| R3 | `publish/threads.py` has no idempotency: a timeout after `threads_publish` can be retried into a duplicate post | `src/publish/threads.py:37-50` | HIGH | Phase 4: `operation_id` + state machine + recovery reconciliation |
| R4 | `affiliate/tiktok.py` fabricates `af_id` Tokopedia URLs and TikTok **search** URLs presented as affiliate links | `src/affiliate/tiktok.py:22-36` | HIGH — false commission claim | Phase 5: `UNVERIFIED` state, operator-import only, no synthetic fallback |
| R5 | `generator.py` publishes generic filler `"Isu {topic} ini punya implikasi nyata…"` when LLM JSON is unparseable | `src/organic/generator.py:585-590` | HIGH — slop on live account | Phase 3: fail closed, no publishable filler |
| R6 | `publisher.py` calls generator then immediately publishes — no independent editorial gate | `src/organic/publisher.py:24-90` | HIGH | Phase 3/4: quality gate between draft and queue |
| R7 | `DAILY_MAX = 12` hardcoded, no durable scheduler state, no pause/resume | `src/organic/publisher.py:19`, `daemon.py:24` | MEDIUM | Phase 4: configurable, durable, pausable |
| R8 | Three disagreeing persona definitions (`persona.json` "Warga Threads", `personas.py` 6 manual moods, generator prompt) | `src/persona.json`, `src/organic/personas.py` | MEDIUM | Phase 2: single canonical Persona Bible |
| R9 | `planner.py` prioritises breaking news + heavy football weighting | `src/organic/planner.py:26-34,52-53` | MEDIUM | Phase 2: audience-first pillars, news optional |
| R10 | Secrets read via `os.getenv` at import time in 4 modules; `.env` holds 35 keys incl. `THREADS_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN` | `src/config.py`, `publish/threads.py`, `comment_engine.py`, `bot/threads_agent.py` | MEDIUM | Phase 1: typed config + redaction; `.env` is gitignored (verified) |
| R11 | `feedback.py` treats engagement as a quality signal; no affiliate-attribution distinction | `src/organic/feedback.py` | MEDIUM | Phase 6: separate impressions / clicks / attributed orders / commission |
| R12 | `comment_engine.py` can reply to unrelated viral posts if thresholds pass | `src/affiliate/comment_engine.py` | MEDIUM | Phase 5: relevance gate independent of engagement |
| R13 | `singleton.py` requires `psutil`, which is **not installed** → `main.py bot` cannot start in this environment | `src/singleton.py:11` | MEDIUM | Phase 1: lazy import with graceful degradation |
| R14 | No migration framework; schema created ad-hoc by `CREATE TABLE IF NOT EXISTS` scattered across modules | `organic/db.py`, `affiliate/queue_manager.py`, `comment_engine.py` | MEDIUM | Phase 1: versioned additive migration runner |

---

## 7. Verified Backups

Taken **before** any mutation, on the feature branch:

| File | Size | SHA-256 | Integrity |
|---|---|---|---|
| `data/backups/v2/posts_20261028_183718.db` | 2,011,136 B | `c3c764015d7ab4af2a96bd5160f49c86cb95d14c794202379d98e9abb51dc36e` | `ok` (131 organic_posts) |
| `data/backups/v2/guardrails_20261028_183722.db` | 163,840 B | see `guardrails_20261028_183722.sha256` | `ok` |

Checksum sidecar files (`*.sha256`) sit beside each backup. Restore procedure is
documented in `docs/v2/06-cutover-rollback.md`.

---

## 8. Freeze Actions Taken

1. Feature branch `feature/v2-persona-first` created from `master@94c04e1`.
2. Both SQLite databases backed up and checksum-verified.
3. **No live process was stopped, killed, or signalled.** PIDs 13584 and 27300
   continue running the legacy pipeline untouched.
4. `AUTOPUBLISH_ENABLED` and `AFFILIATE_AUTO_ENABLED` are **absent** from `.env`
   and therefore default to `false` in the V2 config (Phase 1 adds the typed
   defaults). No V2 code path can publish until they are explicitly set.
5. No external API call was made during this audit. All findings are from
   static reading plus local SQLite/pytest execution.

---

## 9. Phase 0 Exit Criteria

- [x] Branch created, baseline SHA recorded
- [x] Baseline tests executed and recorded (119 passed)
- [x] Both databases backed up with verified checksums
- [x] Call graph and entry points mapped
- [x] Risks classified with file:line references
- [x] Live processes identified and left running
- [x] Rollback path defined (see `docs/v2/06-cutover-rollback.md`)

**Phase 0 complete. Proceeding to Phase 1.**

---

## 10. Post-Implementation Observation (2026-10-29)

Re-checked the live processes after all seven phases were committed.

**Finding: the legacy daemon and Telegram listener are each running TWO
instances.**

| Process | PIDs observed |
|---|---|
| `main.py organic --daemon` | 26932, 35772 |
| `main.py bot` | 32308, 9496 |

The original PIDs (13584, 27300) were recycled by unrelated processes; the
watchdog (`scripts/watchdog.py`) respawned replacements. The duplicates are
consistent with the restart-loop history recorded in commit `d45d3ac`
("fix(ops): hentikan restart beruntun watchdog-vs-singleton") — the singleton
lock is advisory and a respawn can land while the previous holder is still
alive.

**Impact on V2: none.** No V2 code path was involved, and the database is
healthy (131 posts, `integrity_check` ok, all `status='published'`). The
additive V2 columns are present and the legacy daemon has been writing to the
migrated schema without error, which is itself useful evidence that the
migration is compatible with the live pipeline.

**Recommendation for the operator (not acted on):** investigate the duplicate
instances before enabling the V2 scheduler. Two daemons posting to the same
account is a duplicate-content risk independent of V2. The V2 publisher's
`operation_id` idempotency protects V2's own queue, but it cannot protect
against two *legacy* daemons.

**Not modified:** no process was killed, signalled, or restarted during this
audit or during any phase of the V2 implementation.


---

## 11. Legacy Processes Stopped (2026-10-29, operator request)

The operator asked for all process instances to be killed.

**Verified backup taken first:** `data/backups/v2/posts_pre_kill_20261029_091617.db`
(SHA-256 `166fcc322f9da4a4dc6c090ac17b5715a3609a1d83a0b53c6412b0c3939012b6`,
integrity `ok`, 131 posts).

### Stopped

| PID | Command |
|---|---|
| 4956 | `main.py organic --daemon` |
| 28848 | `main.py organic --daemon` |
| 32308 | `main.py bot` |
| 9496 | `main.py bot` |

### Checks performed before stopping

- **Watchdog:** no `watchdog.py` process was running, and no Windows Scheduled
  Task matches `rara` / `watchdog` / `affiliate` / `telegram`. Nothing would
  respawn them.
- **Database:** backed up and checksum-verified before any signal was sent.
- **Post-stop:** waited 20 s and re-enumerated. No respawn occurred.

### Verified after stopping

| Check | Result |
|---|---|
| Processes referencing `affiliate-automation` | none (only the agent's own `omp.exe` / `bun.exe` / `powershell.exe`) |
| `PRAGMA integrity_check` | `ok` |
| `organic_posts` | 131 |
| Stale PID files | `data/rara_daemon.pid` (28848), `data/telegram_listener.pid` (9496) |

The stale PID files are harmless: `src/singleton.py::ensure_single_instance()`
treats a dead PID as stale and overwrites it rather than exiting.

### Note on a false positive

`Get-CimInstance … -like '*main.py*'` also matches PID 13064, the **Hermes
dashboard** (`hermes_cli/main.py dashboard --port 9119`). It is a different
project that happens to share the filename and was deliberately left running.


---

## 12. Secret Scan (2026-10-29)

Master plan §11 requires a secret scanner over the worktree **and** git history.
`scripts/scan_secrets.py` implements it.

```
python scripts/scan_secrets.py              # tracked files
python scripts/scan_secrets.py --include-env  # also the local .env
python scripts/scan_secrets.py --history    # every commit reachable from HEAD
```

### Result

| Scope | Result |
|---|---|
| Tracked worktree | **clean — 0 hits** |
| `.env` | 2 real credentials (see below) |
| Git history | **clean — 0 hits** across 478 blobs / 1011 objects |

The history scan streams `git rev-list --objects` and reads each blob once via
`git cat-file --batch`. The original per-file `git show` implementation was
O(commits x files) subprocess calls and took over 600 seconds; the streaming
version completes in **5.9 seconds** and is covered by a test that fails if it
ever exceeds 60 seconds.

Correctness is verified rather than assumed: a test asserts the object listing
is substantial (>100 entries, >50 blobs) so a rewrite that silently returns
empty cannot pass, and a synthetic probe confirms detection still fires.

### The two credentials in `.env`

| Type | Status |
|---|---|
| Telegram bot token | Present in `.env`, gitignored, never committed |
| OpenRouter API key | Present in `.env`, gitignored, never committed |

`.gitignore` covers `.env`, `*.env`, and `.env.*` with an exception only for
`.env.example`, so neither value has ever entered git history. **No history
rewrite is required.**

### Action required from the operator

The scanner's own output initially printed these values in full. That is a leak
vector in itself — scanner output ends up in CI logs and chat transcripts — so
`_mask()` now shows only the first 6 and last 2 characters plus the length.

**Even so, both credentials should be rotated.** They have been displayed in
plaintext during this audit, and the Telegram bot token in particular grants
control of the bot. Rotation is a provider-side action only the operator can
take:

1. Telegram — `@BotFather` → `/revoke` → new token → update `.env`
2. OpenRouter — dashboard → revoke the key → new key → update `.env`

Deleting or editing `.env` is **not** sufficient on its own; the values were
exposed and must be considered compromised.

### Design note

`.env` is allowlisted in the default scan. Flagging the operator's legitimate
local credential store on every run trains them to ignore the report, so it is
checked only under `--include-env`. The default scan answers the question that
matters: *is anything secret committed?*


---

## 13. Credential Audit (2026-10-29, operator-supplied)

The operator supplied `.env` contents and authorised checking them. All probes
were **read-only `GET` requests** — nothing was published, sent, or posted.

### Live and working

| Credential | Evidence |
|---|---|
| `THREADS_USER_ID` + `THREADS_ACCESS_TOKEN` | HTTP 200, account `@ranaraaa911` |
| `TELEGRAM_BOT_TOKEN` | HTTP 200, bot `@dailyprivateagentbot` (id 8696721987) |
| `LLM_API_KEY` | HTTP 200 on `/chat/completions`; `tig/step-5-unrestricted` ~4.7 s, `tig/step-5-preview` ~2.9 s |

### Broken

| Credential | Error | Impact |
|---|---|---|
| `FACEBOOK_ACCESS_TOKEN` | OAuth 190, subcode 467 — "user logged out" | Facebook/Instagram publishing unavailable. Not used by V2. |
| `REPLICATE_API_TOKEN` | HTTP 401 Unauthenticated | Image generation unavailable. V2 is text-first, so no impact. |

### Configuration defect found

`LLM_BASE_URL` ends with a trailing slash (`.../v1/`), which makes a naive
`f"{base}/chat/completions"` produce `//chat/completions` → 404. Both
`src/config.py` and `src/core/config.py` already call `.rstrip("/")`, so **the
code is correct** — this is documented here only because it cost time to
diagnose and would bite anyone adding a third config path.

### Rotation still required

Two credentials were displayed in plaintext in chat during this session and
should be treated as compromised:

1. `TELEGRAM_BOT_TOKEN` — revoke via `@BotFather` → `/revoke`
2. `OPENROUTER_API_KEY` — revoke in the OpenRouter dashboard

Neither is in git history (verified: `git log --all -- .env` is empty, and the
history secret scan reports 0 hits across 478 blobs).

### TikTok affiliate

`TIKTOK_AFFILIATE_ID` is set to a username. No TikTok affiliate API credentials
exist (`TIKTOK_APP_ID`, `TIKTOK_APP_SECRET`, `TIKTOK_ACCESS_TOKEN` are all
absent). Per the master plan this means **no automatic affiliate link
generation is possible** — products must be imported manually from the official
dashboard with provenance and expiry. The V2 catalog enforces exactly that.
