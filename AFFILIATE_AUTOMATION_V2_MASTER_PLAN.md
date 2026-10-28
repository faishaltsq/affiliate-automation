# AFFILIATE AUTOMATION V2 — MASTER IMPLEMENTATION PLAN

Version: 1.0 | Planning baseline: 2026-10-28
Repository: https://github.com/faishaltsq/affiliate-automation
Inspected default branch: `master`; tree SHA: `94c04e132105003a86ae302b7568a6ad57a1f11e`
Status: IMPLEMENTATION PLAN ONLY. No repository mutations or production postings were performed while producing this document.

## 0. Executive Decision

Remake the repository as a **persona-first, audience-centric content and affiliate automation system**. Primary persona is **Teman Tongkrongan**: Indonesian, casual, observational, warm, light humor, practical and critical of gimmicks. The editorial direction is everyday relatable moments, useful tips, informed opinions, useful product discoveries, and contextually appropriate affiliate recommendations. This is **not** a news aggregator with a new writing prompt.

Preserve current Threads, Telegram, TikTok, and Clipper production integrations behind stable adapters. Refactor shared infrastructure and replace Threads editorial/affiliate orchestration first. Do not rewrite working download, rendering, and upload implementations merely for aesthetics. Refactoring must be justified with tests. Local-first Python and SQLite, using configurable LLM endpoints; no VPS, no required web dashboard, no unnecessary vector DB or microservices.

## 1. Actual Repository Baseline (Static Audit)

Relevant existing paths:
- `src/persona.json` still describes 'Warga Threads' and a news-first content strategy.
- `src/organic/personas.py` defines manual voice presets (julid, calm, nerd, energic, angry, default), not a comprehensive account identity and audience policy.
- `src/organic/planner.py` prioritizes breaking news and weights football heavily in several time slots.
- `src/organic/generator.py` generates news-based copy, supports generic fallback text after invalid JSON, and uses image-oriented prompt paths.
- `src/organic/publisher.py` calls the generator and immediately invokes the Threads publication adapter (no independent editorial gate).
- `src/organic/daemon.py` targets 12 posts/day, periodically syncs status and processes an affiliate queue.
- `src/organic/db.py` deletes posts from SQLite for all non-200 responses in `sync_with_threads_status`, conflating temporary API errors with confirmed deletion.
- `src/organic/feedback.py` refreshes 24h+ post views/likes/replies; useful baseline, but engagement is not an affiliate or follower-quality metric by itself.
- `src/affiliate/comment_engine.py` has engagement thresholds, but existing product-link selection can still prioritize traffic over contextual product fit.
- `src/affiliate/tiktok.py` can fall back to a TikTok search URL or fabricated affiliate-like search URL; neither is proof of a commission-trackable product link.
- `src/bot/telegram_listener.py` routes by Telegram chat/topic, but current inspected message routing has no sender-ID authorization gate for sensitive commands.
- `src/bot/threads_agent.py` is a large mixed-purpose module with manual draft approval, affiliate commands, and chat logic.
- `src/publish/threads.py` implements live publishing and API calls; preserve behind an adapter and introduce uncertain-outcome recovery / duplicate prevention.
- Existing test suites cover Threads/persona, affiliate thresholds, Telegram, TikTok, and Clipper. Run and record baseline before modifications.

These are static findings; confirm full call graphs, running configuration, and test status locally.

## 2. Goals and Non-goals

### Primary goals
1. A recognizable consistent account identity (one persona with variation, not random personality-switching).
2. An independently useful organic content engine that does not require live news.
3. A product-safe, authentic, nonspammy affiliate system that operates only with verified and relevant product links.
4. Quality-controlled automated Threads posting, human-review via Telegram, and safe recovery after crashes or ambiguous network failures.
5. Durable learning from operator rejections, audience signals, and actual platform metrics.
6. Local-first reliability: background operation, backoff, idempotency, migration/rollback, auditability, and secret hygiene.
7. Preserve functioning TikTok/Clipper video automation while introducing clean shared infrastructure.

### Non-goals for initial V2 cutover
- No new standalone web frontend, 3D office, complex multi-agent framework, or always-on external monitoring service.
- No full rewrite of already working video renderer/downloader/uploader.
- No auto-replying to arbitrary strangers or mass engagement automation.
- No generated fake product reviews, fake firsthand experience, artificial testimonials, or inaccurate earnings attribution.
- No automatic posting of unverified affiliate links.

## 3. Persona Contract

ID: `teman_tongkrongan_v1`
Audience: Indonesian adults interested in useful tech, reasonable-cost gadgets, home/desk setups, everyday practical problems, and smarter purchases.
Voice: conversational `aku/kalian`, short varied rhythms, observations, light humor, occasional quirky analogies. No forced slang, cliche engagement bait, clickbait mysteries, repetitive 'unpopular opinion', or mandatory questions.
Account promise: 'Ngobrolin kebiasaan kecil, teknologi sehari-hari, dan barang yang beneran ada gunanya — tanpa maksa beli.'
Do not impersonate a particular real person; never claim having bought/tested/used a product or having lived an event unless verified user-supplied evidence exists. The persona may express opinions and general preferences without claiming invented personal history. Sensitive news is not a pillar.

Content pillars (first 30-post trial):
- `relatable` 10/30: observational humor and daily behavior
- `practical_tips` 8/30: genuinely useful, checkable and often no-purchase solutions
- `opinions` 6/30: honest trade-offs and debates without ragebait
- `discoveries` 3/30: useful item categories, tech features, or tools
- `affiliate` 3/30: disclosure-labeled, source-verified recommendations only

These weights are initial experimental hypotheses, not algorithm guarantees. If affiliate inputs cannot be verified, replace the affiliate slot with relevant organic content; do not fabricate links. One principal post/day and optional 2–3 extras/week after QC. Text-first; visual only where it materially helps and has verified licensing/permissions.

Variation dimensions: post format, opening style, length, device/problem category, emotional mood, and goal. Do not conflate voice with a rigid template. Support short posts, concise useful multi-paragraph posts, comparison threads, and deeper chains only when content justifies them. Maintain a rolling anti-repetition window and semantic deduplication.

## 4. Architecture: Modular Monolith

Suggested packages, migrated in vertical slices rather than a huge rename:

```
src/
  core/
    config.py                  # typed env config, feature flags, redaction
    llm.py                     # provider abstraction, timeout, retries, quotas
    models.py                  # typed shared DTOs
    clock.py                   # UTC storage, WIB presentation
    events.py                  # audit event records
    security.py                # Telegram operator ACL
    storage.py                 # SQLite connection and transaction helpers
  persona/
    profile.py                 # single canonical persona definition
    voice.py                   # style variation within identity
    editorial_memory.py        # bounded structured feedback retrieval
  audience/
    research.py                # source adapters: seed questions, comments, opt-in feeds
    signals.py                 # structured audience pain points and provenance
  strategy/
    calendar.py                # 30-day rolling plan, not hardcoded post text
    ideation.py                # 3 different creative ideas per slot
    ranking.py                 # audience + novelty + commercial-fit constraints
  content/
    writer.py                  # post candidates, no fallback filler
    verifier.py                # claim provenance / evidence policy
    critic.py                  # independent assessment + hard veto rules
    similarity.py              # near-duplicate and repetitive template guard
    quality_gate.py            # publish / review / revise / reject
  commerce/
    catalog.py                 # verified product list + TTL
    affiliate_links.py         # source provenance, tracking validation
    relevance.py               # post/product/audience-context match
    disclosure.py              # transparent affiliate labels
  orchestration/
    scheduler.py               # durable scheduler state
    publisher.py               # exactly one logical publication operation
    recovery.py                # ambiguous API outcome reconciliation
    analytics.py               # insight snapshots
    experiments.py             # rotating approved formats & tests
  adapters/
    threads.py                 # wrap existing Threads publishing client
    telegram.py                # wrap current listener/forum routing
    tiktok.py                  # connect existing product/video adapters
  bot/                         # existing bot implementation, gradually slimmed
  tiktok/                      # preserved video pipeline
  clipper/                     # preserved video pipeline
  organic/                     # temporary legacy wrappers, then retire
```

Design rule: business/domain services have no hardwired Telegram chat IDs, Threads account IDs, provider secrets, or direct production HTTP calls. Each public side effect occurs through an adapter supporting fake/dry-run implementations. Validate model JSON using typed schemas or equivalent explicit checks. Use the existing provider configuration; do not create 8 permanent agents. Generation/writing/critique may be separate LLM roles calling the same endpoint.

## 5. End-to-End Content Flow

`scheduled_slot -> persona_context -> audience_signals -> idea_candidates(3) -> candidate_ranker -> evidence_fetcher_if_needed -> writer -> independent_critic -> hard_policy_checks -> decision`.

Possible decisions: `APPROVED_AUTO`, `NEEDS_REVIEW`, `REVISE_ONCE`, `REJECTED`. A draft must have a reason-coded decision. Approved candidates enter a persistent publication queue; a separate publisher adapter claims and performs one operation at a time. The scheduled slot can safely be skipped if no acceptable draft exists.

After publish: store Threads ID -> periodically fetch measured insights with request status -> log real metrics -> retrieve operator feedback -> update strategy weights with minimum-sample safeguards. Do not train model weights; improve future prompts/selection via structured memory and data.

Suggested initial scoring out of 100:
- Persona voice 20
- Audience relevance 20
- Natural writing 20
- Specific useful content 20
- Evidential correctness 15
- Novelty 5

`>=85`: eligible for auto-publish only if every hard gate passed; `70-84`: human review; `<70`: one or two bounded revisions, else skip. These values are configurable heuristics, NOT proven predictors of engagement.

Hard veto: unverified numeric claim, fabricated firsthand usage, unknown source on time-sensitive facts, incompatible product, uncertain affiliate tracking, no disclosure, near-duplicate, excessive repetition, unsafe/sensitive framing, unknown image rights, stale/invalid draft, unauthorized operator action.

## 6. Affiliate System Remake

Product record schema:
`id, name, category, source_platform, source_url, original_affiliate_url, verified_affiliate_url, tracking_status, evidence_url, price_if_verified, checked_at, expires_at, restrictions, reviewer_status`.

Affiliate URL states: `UNVERIFIED`, `VERIFIED`, `EXPIRED`, `REVOKED`; no generic TikTok search URL may be represented as a confirmed affiliate link. If affiliate API is not available, allow operator import of an exact link generated in the user's official affiliate dashboard, with provenance and manual validation. Do not assume a handcrafted `af_id` parameter yields commission.

Relevance engine checks whether this product directly solves the post's stated problem and whether the audience context supports a recommendation. Affiliate is blocked when link/product relevance is missing even when a post has high views or many replies. All commercial posts and appropriate replies carry clear disclosure. Validate current Threads/TikTok affiliate conditions and platform policies before enabling live promotion. Never silently insert links into unrelated viral discussions. Respect platform rate limits and do not auto-comment for the sole purpose of inflating engagement.

Commerce metrics must distinguish `impressions`, `clicks` if verifiable, `attributed_orders` if provided by the affiliate platform, and `commission` if actually reported. Do not infer conversion from Threads likes.

## 7. Telegram Command Center

Must preserve current forum topics and routing. New Threads commands (exact syntax may be adapted to existing command dispatcher):
- `/persona` — active identity, version, voice summary
- `/plan` — today's 1–3 ideas and tomorrow's slot
- `/ideas` — get a new ranked set of candidate ideas
- `/draft` — generate preview (no publish)
- `/approve <id>` — authorize a specific evaluated draft
- `/revise <id> <reason>` — regenerate with structured feedback
- `/reject <id> <reason>` — persist reason, not erase history
- `/schedule` — upcoming queue and local-time rendering
- `/pause` / `/resume` — controlled scheduler state
- `/autopost on|off` — explicit authorization; default OFF until cutover
- `/analytics [7d|30d]` — verified metrics and coverage caveats
- `/products` — validated catalog and affiliate status
- `/status` — health, queue, last successes/failures
- `/rollback` — operator-controlled safe rollback to legacy mode (no data deletion)

Inline review buttons must use opaque draft IDs, exact message/chat and sender authorization, short-lived callback records, idempotency, and explicit stale-content handling. Telegram chat membership is not sufficient for mutation permissions; maintain allowed Telegram user IDs in environment configuration. Never print secrets, full access tokens, or sensitive data in logs.

## 8. Local-first Scheduler and Publishing Reliability

- Store durable UTC schedule slots; display WIB. `1 main/day`, optional 2–3 extras/week. Quiet hours configurable. No mandatory filler posts.
- Separate scheduler process from command listener or share one coordinated queue. Avoid duplicate scheduler instances and unsafe concurrency. Preserve existing singleton logic where useful.
- Use SQLite busy timeouts, WAL when compatible, transactions, and an atomic queue-claim/lease mechanism.
- State machine: `IDEA -> DRAFT -> EVALUATED -> APPROVED|REVIEW|REJECTED -> QUEUED -> PUBLISHING -> PUBLISHED|FAILED|UNKNOWN_OUTCOME`.
- Use stable `operation_id`, claim lease, retry budget, exponential backoff with jitter and rate-limit handling.
- A timeout after API submit means the outcome may be unknown. Do not blindly republish; reconcile status by persisted container/post ID, API query where supported, or mark manual review. Recovery must be designed around actual Threads API constraints.
- Ensure `dry_run=True` eliminates ALL side effects (including affiliate comments, Telegram operations when test isolation requires, and paid model calls when offline test mode is set).
- Shutdown/restart must preserve pending work, not publish duplicate content.
- Runtime may still use the current `main.py` CLI, with backward-compatible aliases.

## 9. Database Migration and Feedback Memory

Migrations must be additive, reversible where feasible, idempotent, and backed up before execution. Do not reuse or overwrite user content silently.

Suggested tables:
- `persona_versions(id, version, content_json, created_at, active)`
- `audience_signals(id, category, problem, source_url, verified_at, confidence, status)`
- `content_ideas(id, pillar, angle, source_ids_json, idea_state, planned_date)`
- `content_drafts(id, idea_id, content, structure, persona_version, draft_version, status, created_at)`
- `content_evaluations(id, draft_id, scores_json, vetoes_json, verdict, evaluator_version)`
- `publication_jobs(id, draft_id, operation_id UNIQUE, state, claimed_at, attempts, published_id, last_error)`
- `performance_snapshots(id, threads_id, fetched_at, views, likes, replies, source_status)`
- `feedback_events(id, draft_id, actor_id, category, reason, created_at)`
- `products(id, source, name, verified_url, affiliate_url, tracking_state, checked_at, expires_at)`
- `promotions(id, product_id, post_id, evidence, disclosed, status)`
- `experiment_assignments(id, draft_id, hypothesis, variant, attribution_window)`
- `audit_events(id, actor_type, action, object_type, object_id, before_json, after_json, created_at)`

Preserve existing `organic_posts` rows, Threads IDs, and metrics. Add `status`/`deleted_at` tombstones; never delete records merely because an API call returned non-200. Distinguish `200`, confirmed authoritative not-found/deletion after additional checks, `429`, `5xx`, transport timeout, and permission/auth failures. Never use all-zero metrics to mean 'insights unavailable'. Retain raw evaluation/feedback reason codes even after final publication or manual deletion.

## 10. Content Calendar and Experimentation

Seed a 30-day **planning calendar**, not 30 permanently fixed post bodies. Target counts: 10 relatable, 8 practical, 6 opinions, 3 discoveries, 3 affiliate (conditional). Every slot stores `date, pillar, audience_problem, target_angle, format, source_constraints, commercial_allowed`.

Provide 3 creative ideas per daily slot; choose based on novelty, audience relevance, persona fit, and schedule balance. Initial themes may include home desk setup, charger cables, power bank selection, cheap gadgets, browser habits, product comparison decisions, phone stands, and shopping impulses. Keep humor non-repetitive and occasionally post genuinely useful information without jokes.

Experiment one variable at a time: hook type, length, humor intensity, content format, problem category, and posting time. Do not declare a winning format from one viral post or small samples. Produce daily short reports and weekly retrospectives with sample sizes and confidence caveats. Affiliate slots should convert to organic when evidence or trackability is insufficient.

## 11. Observability and Security

- `health`: scheduler heartbeat, queue oldest age, API error rate, LLM latency, costs where available, and last publication result.
- Structured logs with correlation ID `operation_id`, redact secrets and URLs with token query strings.
- Environment-only tokens, `env.example` placeholders, secret scanner on worktree and git history; rotate any exposed secrets, do not merely delete a committed file.
- Telegram allowlist for mutation operations and callbacks; reject unauthorized private messages as well as unwanted groups.
- No live external calls during unit tests. Explicit `AUTOPUBLISH_ENABLED=false` and `AFFILIATE_AUTO_ENABLED=false` during migrations and smoke tests.
- Product facts require actual evidence; avoid unlicensed product or editorial visuals. Text-only defaults where images add no value.
- Add network timeouts, bounded retries, rate limiting, cancellation on shutdown, and robust parsing of untrusted research feeds.

## 12. Work Packages and Dependencies

**Phase 0 — Discovery / Freeze**
Map actual entrypoints, background processes, cron/Task Scheduler behavior, forum IDs, environment contracts, DB schema and existing tests. Record SHA and baseline test results. Freeze live scheduler, create feature branch, back up DB, prepare rollback. Deliver `docs/v2/00-audit.md` and rollback procedure.

**Phase 1 — Core safety and migrations**
Secure Telegram operator commands, create typed config + feature flags, event/state persistence, SQLite additive migrations, fix dangerous post deletion, error/timeout handling. Deliver tests and migration evidence. No change in content policy yet.

**Phase 2 — Persona / audience-first strategy**
Create primary Persona Bible; retire news as required input; consolidate `src/persona.json` + manual/auto persona resolution. Add audience signal catalog, pillar scheduler, 30-day plan and 3-angle idea generation. Unit test independent generation with all trend sources disabled.

**Phase 3 — Writer / critic / gate**
Structured writer, validation, evidence provenance, deduplication, natural language variety, quality scoring, mandatory hard vetoes, no generic fallback, text-first support. Connect gate to both manual and scheduled publisher paths. Test failed LLM JSON and unsafe content never publish.

**Phase 4 — Durable scheduler + Telegram UX**
Review buttons, reject/revise reasons, authorized callbacks, publication state machine, queue leasing/idempotency, pause/resume and status, restart recovery, dry-run and no-side-effect tests.

**Phase 5 — Verified affiliate + TikTok/Clipper boundaries**
Rebuild verified product/link catalog and relevance gate; remove synthetic affiliate link fallbacks. Verify affiliate provider/attribution and policy limitations. Test no unrelated post gets link, preserve TikTok/Clipper interfaces and their regression tests. Do not prematurely rewrite video workflows.

**Phase 6 — Feedback, analytics, experiment planner**
Store snapshots and error statuses, normalization, feedback signals and model version, weekly report and sampling minimums. Protect against overfitting to a handful of views or followers.

**Phase 7 — Shadow -> review -> limited cutover**
Run fully offline tests; generate at least 30 diverse draft fixtures, human-evaluate 20, compare baseline vs V2, run parallel shadow mode **without live posting**, test migration and rollback, then request explicit operator approval for limited live enablement. Enable with one post/day and strict caps. Monitor and retain legacy rollback path for an observation window.

## 13. Acceptance Criteria and Quality Gates

Mandatory acceptance tests:
1. Consistent persona across manual, scheduled and Telegram drafts, regardless of model fallback.
2. Generator produces useful content with every news/trend source disabled.
3. 30-day calendar respects pillar distribution and variety; incomplete affiliate availability becomes organic.
4. Invalid model JSON/repeated low scores never become generic filler posts.
5. Claims about product specs/discount/usage without evidence are blocked.
6. Similarity guard catches exact copies and near-template duplicates.
7. Unknown/untracked product links cannot be labeled commission-ready.
8. No affiliate comment on unrelated posts, regardless of engagement thresholds.
9. Post history remains after confirmed deletion; transient `429`, `5xx`, `401`, `403`, timeouts do not trigger deletion.
10. All external side effects are mocked in unit/integration test; no accidental live publishing.
11. Timeout after publication does not trigger duplicate retry; unknown outcome preserved for reconciliation.
12. Scheduler respects local calendar slots, quiet hours, day cap, pause/resume, and cross-restart behavior.
13. Telegram unauthorized sender/callback cannot publish, approve, reject, change persona, or enable automation.
14. Reject/revise feedback is preserved and represented in the next selection context.
15. TikTok, Clipper, existing Telegram topics, and CLI integration smoke tests pass unchanged or with intentional documented migration.
16. Database migration applied twice does not corrupt data; rollback instructions and backup restore tested on a copy.
17. Product links, attribution, and promotional disclosures validated before **any** live affiliate post.
18. Secrets absent from code, tests, screenshots, logs and git history as far as local scanner can verify; reported exposures are rotated.

Quality sampling: at least 30 produced candidate posts across all 5 pillars, 20 manually rated for voice, meaningful variation and accuracy, with review results in `docs/v2/quality-review.md`. No arbitrary passing percentage claimed without actual test evidence.

## 14. Milestones and Deliverables

Required outputs on the feature branch:
- `docs/v2/00-audit.md`, `01-product-spec.md`, `02-architecture.md`, `03-database-migrations.md`, `04-telegram-command-guide.md`, `05-runbook.md`, `06-cutover-rollback.md`, `07-quality-review.md`.
- V2 Python implementation and tests (matching existing test style).
- Config example without secrets, updated README and CLI help.
- Fixture inputs and a reproducible offline demo command.
- Actual test results with failures called out; no fabricated pass claims.
- Table explaining each old module's fate: retained, wrapped, refactored, disabled, or removed with reason.
- Changelog of focused commits and known limitations.
- Final manual-approval checklist for production cutover.

## 15. Recommended Execution Protocol

Do not ask a coding agent to replace all files in one enormous unverified commit. Give the agent this master plan and the accompanying implementation prompt. Have it execute phases consecutively on a feature branch, committing verified increments. If a phase fails tests, stop before dependent phases. Do not activate live scheduling, contact external APIs using real credentials, or publish until operator approval. Use CLI `python -m pytest` and targeted offline smoke commands; adjust commands to the repository's actual setup. Explicitly report any missing credential / dependency / network limitation.

## 16. Success Definition

After V2 cutover, a person reading 20 successive Threads posts should perceive a consistent 'Teman Tongkrongan' character with varied topics and useful ideas; the account should not resemble a templated news bot or an affiliate spam feed. The system should publish fewer but better posts, learn transparently from real feedback, refrain from unverified promotions, and recover safely from interruptions without duplicate or unauthorized posting. Monetization success must be measured with actual affiliate attribution rather than inferred from engagement.
