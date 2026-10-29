# V2 — Module Fate, Changelog & Cutover Checklist

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 1. Module Fate Table

Every module in the repository, and what happened to it.

### Retained — untouched, still doing its job

| Module | Why retained |
|---|---|
| `src/publish/threads.py` | Live Threads publishing works. Wrapped by `adapters/threads.py`; not rewritten. |
| `src/publish/telegram.py` | Telegram image/text sending works. |
| `src/publish/social.py` | Facebook Graph integration works. |
| `src/bot/telegram_listener.py` | The live listener. **Modified** only to try the V2 router first and fall through on anything it does not claim. |
| `src/bot/threads_agent.py` | 60 KB of manual draft + affiliate + chat logic. Still the manual path. |
| `src/bot/tiktok_agent.py` | TikTok command surface. Untouched. |
| `src/bot/clipper_agent.py` | Clipper command surface. Untouched. |
| `src/bot/menu_buttons.py` | Menu rendering. Untouched. |
| `src/affiliate/shopee.py` | Shopee Open API v2 with correct HMAC signing. Untouched. |
| `src/affiliate/queue_manager.py` | Affiliate queue. Untouched. |
| `src/affiliate/comment_engine.py` | The legacy engagement-gated reply engine. **Retained but bypassed by V2** — see §3. |
| `src/affiliate/tiktok.py` | Contains the synthetic-URL builders. **Retained because the running daemon imports it**; V2 refuses both URL shapes. |
| `src/tiktok/*` (16 modules) | The entire video pipeline. Untouched. |
| `src/clipper/*` (14 modules) | The entire clipping pipeline. Untouched. |
| `src/content_ops/*` | Batch production. Untouched. |
| `src/image/*` | Image generation and editorial cards. Untouched. |
| `src/monitor/analytics.py` | Metrics snapshots. Untouched. |
| `src/brief/*` | Brief generation. Untouched. |
| `src/trends/*` | Trend research. **Retained but no longer required** — V2 generates with every trend source disabled. |
| `src/scraper/*` | Product metadata extraction. Untouched. |
| `src/config.py`, `src/logger.py`, `src/guardrails.py` | Shared legacy infrastructure. Untouched. |
| `src/worker.py`, `src/trend_job.py`, `src/singleton.py` | Legacy entry points. Untouched. |

### Wrapped — V2 talks to it through an adapter

| Module | Adapter |
|---|---|
| `src/publish/threads.py` | `src/adapters/threads.py` |
| `src/bot/telegram_listener.py` routing | `src/bot/v2_commands.py` + `src/adapters/telegram.py` |
| `src/brief/generator.py::_llm` | `src/core/llm.py` (V2 uses its own client; the legacy one is untouched) |

### Refactored — behaviour changed, interface preserved

| Module | Change |
|---|---|
| `src/organic/db.py` | `sync_with_threads_status()` no longer DELETEs rows on non-200. Delegates schema creation to the V2 migration runner. `init_organic_db()` still works standalone. |
| `src/organic/personas.py` | **Retired from V2.** Still on disk because the legacy daemon and its tests import it. Superseded by `src/persona/profile.py`. |
| `src/persona.json` | **Retired from V2.** Same reason. |
| `src/organic/planner.py` | **Bypassed by V2.** Still on disk, still imported by the legacy daemon. Superseded by `src/strategy/calendar.py` + `ideation.py`. |
| `src/organic/generator.py` | **Bypassed by V2.** Superseded by `src/content/writer.py`. |
| `src/organic/publisher.py` | **Bypassed by V2.** Superseded by the gate + queue. |
| `src/organic/daemon.py` | **Bypassed by V2.** Superseded by `src/orchestration/scheduler.py`. |
| `src/organic/feedback.py` | **Bypassed by V2.** Superseded by `src/orchestration/analytics.py`. |

### New — V2 only

`src/core/` (config, llm, models, clock, security, storage, events, post_sync) ·
`src/persona/` (profile, voice, editorial_memory) · `src/audience/signals.py` ·
`src/strategy/` (calendar, ideation) · `src/content/` (writer, verifier, critic,
similarity, quality_gate) · `src/commerce/` (catalog, relevance, disclosure) ·
`src/orchestration/` (scheduler, publisher, recovery, analytics, experiments) ·
`src/adapters/` (threads, telegram) · `src/bot/v2_commands.py` ·
`src/v2_cli.py` · `src/shadow_run.py` · `src/integration_check.py`

### Removed

**Nothing was deleted.** No file was removed from the repository. The legacy
pipeline is intact and runnable.

---

## 2. Changelog

```
f108e35 chore(v2): refresh shadow report timestamp
3a926cf feat(v2): offline integration rehearsal + cutover preflight
4c6c9a0 docs(v2): record legacy process shutdown
d211d2f chore(v2): refresh shadow report timestamp
571ef4f feat(v2): Telegram command center + feedback wiring (#13, #14)
f16b5bf fix(v2): wire feedback memory into the writer (#14)
baf2490 feat(v2): phase 7 shadow run, quality review, cutover docs
19d0c12 feat(v2): phase 6 analytics, feedback memory, experiments
8c132e2 feat(v2): phase 5 verified affiliate catalog, relevance, disclosure
2a4ca95 feat(v2): phase 4 scheduler, state machine, recovery, adapters
88da23c feat(v2): phase 3 writer, verifier, critic, gate
b49ff35 feat(v2): phase 2 persona, signals, calendar, ideation
fe69cae feat(v2): phase 0 audit + phase 1 core safety infrastructure
```

### Bugs found and fixed during implementation

| # | Bug | Found by |
|---|---|---|
| 1 | `organic/db.py` deleted posts on any non-200 (429/5xx/timeout) | Phase 0 audit |
| 2 | `generator.py` published generic filler on unparseable JSON | Phase 0 audit |
| 3 | `affiliate/tiktok.py` presented search URLs and `af_id` guesses as commission-trackable | Phase 0 audit |
| 4 | Engagement-bait regex missed "Kalian tim mana nih?" | Shadow run |
| 5 | `no_disclosure` control evaluated without affiliate flags | Shadow run |
| 6 | Repetitive-template threshold rejected ordinary two-sentence posts | Shadow run |
| 7 | Fixture generator replayed exemplars, producing exact duplicates | Shadow run |
| 8 | `posts_today()` compared a UTC timestamp against a WIB date | Phase 4 tests |
| 9 | `performance_snapshots` metrics were `NOT NULL`, making "unavailable" unrepresentable | Phase 6 tests |
| 10 | Command handlers accepted `path` but never forwarded it — tests wrote into the live DB | Phase 6 tests |
| 11 | `editorial_memory` read paths crashed on a fresh database | Feedback wiring |
| 12 | **Publisher `submit()` and adapter `publish_text()` were never connected** — every dry-run publish failed | Integration rehearsal |

Bug 12 is the notable one: it survived 600+ unit tests because each test used
its own fake. Only running the real chain end-to-end exposed it.

### Known limitations

1. **The writer has never run against a real LLM.** The shadow run proves the
   *gate* works using hand-written exemplars. Writer quality from a cold start
   is unverified.
2. **`cv2`, `psutil`, `praw`, `playwright` are not installed**, so
   `tests/bot/`, `tests/clipper/`, `tests/tiktok/`, and
   `tests/test_multi_topic_bot.py` cannot be collected. This is unchanged from
   the pre-V2 baseline.
3. **Two pre-existing TikTok test failures on `master`** (verified by checking
   out `master` and re-running): `test_scheduler_reminder.py` —
   `AttributeError: module 'src.tiktok' has no attribute 'pipeline'`. Not caused
   by V2, not fixed, because touching the TikTok pipeline is out of scope.
4. **The affiliate catalog is empty.** No commission-ready product exists.
5. **`TELEGRAM_OPERATOR_IDS` is empty**, so the V2 command centre would deny
   every mutation.
6. **The audience signal catalog is 30 hand-written seeds.** Topical breadth is
   limited by it; expanding the catalog is the lever for more variety.
7. **No image support.** V2 is text-first. `V_UNKNOWN_IMAGE_RIGHTS` exists as a
   veto code but no image-rights verification is implemented, because no image
   path is implemented either.

---

## 3. Legacy Affiliate Engine — status

`src/affiliate/comment_engine.py` is **retained but bypassed**. It still runs as
part of the legacy daemon and still gates replies on engagement thresholds.

V2 does not call it. The V2 affiliate path is:
`commerce/catalog.py` (verified links only) → `commerce/relevance.py`
(product/problem match, **no engagement input**) → `commerce/disclosure.py`
(mandatory label) → `content/quality_gate.py` (hard vetoes).

The two systems can coexist. When the legacy daemon is stopped and the V2
scheduler is enabled, the engagement-gated engine stops running with it.

---

## 4. Final Manual-Approval Checklist

**Do not perform any of this without explicit operator approval.**

### Pre-approval evidence to review

- [ ] `docs/v2/07-quality-review.md` — shadow-run results and the 20-fixture
      manual rating
- [ ] `docs/v2/shadow-report.json` — all 30 fixtures, machine-readable
- [ ] `python -m src.integration_check --verbose` — 18/18 checks
- [ ] `python -m pytest tests/ -q` — 653 passing
- [ ] `python -m src.integration_check --preflight` — no blockers

### Configuration

- [ ] `TELEGRAM_OPERATOR_IDS` populated with real numeric operator IDs
- [ ] `AUTOPUBLISH_ENABLED=false` (until the shadow period is complete)
- [ ] `AFFILIATE_AUTO_ENABLED=false`
- [ ] `DRY_RUN=true`
- [ ] `V2_SCHEDULER_ENABLED=false`
- [ ] `V2_TELEGRAM_COMMANDS_ENABLED=false`
- [ ] Fresh checksum-verified backup of `data/posts.db`

### Shadow period (no live posting)

- [ ] Enable `V2_SCHEDULER_ENABLED=true` with `AUTOPUBLISH_ENABLED=false`
- [ ] Observe the queue for at least 7 days
- [ ] Review generated drafts for persona consistency and topical variety
- [ ] Confirm no draft reaches the queue without passing the gate

### Limited live enablement

- [ ] `AUTOPUBLISH_ENABLED=true`, `DRY_RUN=true` for one more day
- [ ] `DRY_RUN=false` with `V2_MAIN_POSTS_PER_DAY=1`,
      `V2_MAX_EXTRA_POSTS_PER_WEEK=0`
- [ ] Confirm the legacy daemon is **stopped** (it is, as of 2026-10-29)
- [ ] Observe for the agreed window
- [ ] Only then consider raising the volume caps

### Affiliate enablement (separate decision)

- [ ] At least one product `VERIFIED` in the catalog, **or** the affiliate
      pillar confirmed to run as organic
- [ ] Current TikTok Shop and Threads affiliate terms re-checked
- [ ] Disclosure text reviewed
- [ ] `AFFILIATE_AUTO_ENABLED=true` only after the above

### Rollback readiness

- [ ] Rollback procedure rehearsed on a copy
      (`docs/v2/06-cutover-rollback.md`)
- [ ] Rollback path retained for the full observation window

### Sign-off

| Role | Name | Date | Signature |
|---|---|---|---|
| Operator | | | |
| Reviewer | | | |
