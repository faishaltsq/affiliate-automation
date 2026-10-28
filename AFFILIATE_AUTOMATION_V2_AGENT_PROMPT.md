# CODING AGENT EXECUTION PROMPT — Affiliate Automation V2

You are the lead Python engineer, architecture owner, and QA reviewer for `https://github.com/faishaltsq/affiliate-automation`.

The attached `AFFILIATE_AUTOMATION_V2_MASTER_PLAN.md` is the **product and technical specification**. If it is not accessible, request it from the operator before editing. Your job is to implement the plan in the existing repository in verifiable, safe stages. Do not stop after writing another plan.

## Mission
Transform the existing news-first Threads affiliate workflow into a persona-first content automation studio for the Indonesian persona **Teman Tongkrongan**: casual, observant, mildly funny, practical, critical of hype, honest about product limitations, never inventing personal experience. Primary monetization is verified TikTok affiliate links. Target: sustainable audience fit, not maximum post volume.

## Must-do first
1. Inspect the actual checked-out branch, working tree, git status, entry points, current production scheduler, Telegram listener/command handlers, affiliate queue, Threads publisher, video pipelines, configuration, tests, SQLite schema and recent commits. Reconcile any divergence from the baseline tree SHA `94c04e132105003a86ae302b7568a6ad57a1f11e`.
2. Never overwrite uncommitted user changes. Create a feature branch, record baseline tests, take a verified SQLite backup, document recovery. Keep live automation disabled while migrating and testing.
3. Create `docs/v2/00-audit.md` with precise call graphs and risk classification, `docs/v2/01-product-spec.md`, then execute work packages below. Do not use live posting, live affiliate replies, external paid calls or real product links during tests.

## Architecture / execution
- Use a **modular monolith**, not microservices or an over-engineered autonomous multi-agent framework. Python + SQLite + Telegram + existing configurable LLM endpoint; local-first, runs on a Windows gaming laptop without VPS. No mandatory web dashboard.
- Keep TikTok and Clipper pipelines running through stable interfaces. Refactor shared config/security/health only when regression tests show compatibility. Do not rewrite the working renderer/downloaders unnecessarily.
- Build canonical account-level Persona Bible and versioning (not random manual mood personas), audience signal catalog, content calendar, 3-angle generator, evidence-aware writer, independent critic, hard veto quality gate, persistent queue, safe scheduler, metrics/feedback memory, verified products and affiliate relevance checks.
- Default daily schedule: 1 main post/day, optional 2–3 extras/week; 30-post seed pillars 10 relatable / 8 practical / 6 opinion / 3 discovery / 3 affiliate. Any affiliate slot lacking verified product tracking must downgrade to organic.
- Use typed payloads, schema validation, bounded retries, explicit statuses; never publish generic fallback copy, hallucinated statistics, fabricated product trials, unsupported prices or product specs.

## Specific existing weaknesses to resolve
- `src/persona.json`, `src/organic/personas.py` and scheduled generation disagree on account identity. Unify them with a single canonical persona across manual and automated workflows.
- `src/organic/planner.py` weights news and football. Replace with audience-first dynamic ideas and optional trend signals.
- `src/organic/generator.py` has generic invalid-JSON fallback; eliminate publishable filler.
- `src/organic/publisher.py` can publish without independent editorial acceptance; enforce same validation for all publishing paths including Telegram manual approval.
- `src/organic/daemon.py` hardcodes 12/day; replace with configurable durable state and safe pause/resume.
- `src/organic/db.py` deletes history on non-200 response; preserve tombstones and distinguish API failures from confirmed deletion.
- `src/affiliate/tiktok.py` produces fallback search URLs that do not prove affiliate tracking. Require actual operator-supplied or provider-verified product affiliate URLs with provenance and expiry.
- `src/affiliate/comment_engine.py` must reject irrelevant promotions regardless of engagement and include disclosure.
- `src/bot/telegram_listener.py` must authorize sender IDs and callbacks for mutation commands rather than trusting chat membership.
- `src/publish/threads.py` needs an ambiguous-outcome reconciliation and idempotent job design to prevent repeated publication after timeouts.

## Phase-by-phase implementation (commit after each verified phase)
PHASE 0 Baseline, branch, backup, run and document tests, call graph, risks, feature flags.
PHASE 1 Security, typed config, storage abstraction, additive SQLite migrations, audit trail, fix data deletion; add tests.
PHASE 2 Canonical persona, audience signals, 30-day planning, idea ranker, optional trending; tests for no-news mode and consistent identity.
PHASE 3 Writer, evidence verifier, independent critic, similarity guard, hard vetoes; test quality gate, malformed JSON, unsupported claims.
PHASE 4 Durable scheduling/publishing state machine, idempotency, restart/timeout recovery, Telegram drafts/reject/revise/approve/pause/resume/status/analytics, operator authorization; tests.
PHASE 5 Verified affiliate catalog, contextual relevance/disclosure, TikTok attribution limitations, preserve current video pipelines; integration tests.
PHASE 6 Performance snapshot ingestion, rejection memory, experimentation, daily/weekly summary, minimum-sample/uncertainty protection; tests.
PHASE 7 Offline shadow run generating >=30 varied draft fixtures, manual quality comparison of >=20, regression suite, rollback drill, cutover checklist. Stop before live posting and request explicit operator approval.

## Non-negotiable tests
- No external HTTP, paid LLM, live Threads publishing, Telegram side effects or affiliate comments in offline test suite.
- Bad or repetitive drafts fail closed and cannot enter publishing queue.
- Manual and automatic content have same persona, evidence and hard veto policies.
- No unverified or unrelated affiliate links ever posted.
- `429/5xx/timeout` sync responses never delete posts; confirmed deletions retain history.
- Retry after unknown publish outcome cannot duplicate a post.
- Unauthorized Telegram commands and callbacks are denied.
- Migrations repeat safely; restore from backup verified.
- Scheduler no-duplicates, pause/quiet hours/day caps/restart recovery validated.
- Existing TikTok/Clipper regression suite passes or explicit breakages are fixed and explained.

## Operations & deliverables
- Keep detailed results in `docs/v2/`, including audit, product spec, architecture, DB migrations, Telegram command guide, runbook, cutover & rollback, quality review.
- Include fixture-based examples, new test files, `.env.example` updates without secrets, CLI help and README updates.
- Show test command, actual passed/failed counts, source code paths, focused git commits, limitations, and side effects blocked in test mode.
- Never claim a test passed without running it; if blocked, state exactly why.
- Keep `AUTOPUBLISH_ENABLED=false` and `AFFILIATE_AUTO_ENABLED=false` throughout implementation and shadow testing.
- Do not push to the main production branch, make external account changes, or turn on live posting without explicit approval.

Execution behavior: start immediately with repository audit, create the feature branch and Phase 0 artifacts, then implement and test phases sequentially. Do not substitute a prose-only response for working code. Stop and report if a critical safety precondition fails or data cannot be backed up. Present the completion checklist and ask for cutover approval once verified.
