# V2 — Architecture

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 1. Shape

A **modular monolith**. Python + SQLite + Telegram + the existing configurable
LLM endpoint. Local-first, runs on a Windows laptop, no VPS, no mandatory web
dashboard, no vector DB, no microservices.

Business logic has **no** hardwired chat IDs, account IDs, provider secrets, or
direct production HTTP calls. Every external side effect goes through an adapter
with a fake/dry-run implementation.

## 2. Package Map

```
src/
  core/           # shared infrastructure — no domain logic
    config.py       typed env config, feature flags, secret redaction
    llm.py          provider abstraction + model fallback + FakeLLMClient
    models.py       typed DTOs for the whole lifecycle
    clock.py        UTC storage, WIB presentation, quiet-hour maths
    security.py     Telegram operator ACL
    storage.py      SQLite connections, transactions, migration runner
    events.py       audit trail
    post_sync.py    safe Threads status reconciliation (tombstones)
  persona/
    profile.py        the ONE canonical Persona Bible, versioned
    voice.py          variation inside identity
    editorial_memory.py  bounded structured feedback retrieval
  audience/
    signals.py        audience pain points with provenance
  strategy/
    calendar.py       30-day rolling slot calendar
    ideation.py       3 distinct ranked ideas per slot
  content/
    writer.py         draft generation, NO fallback filler
    verifier.py       claim provenance / evidence policy
    critic.py         independent assessment
    similarity.py     near-duplicate + repetitive-template guard
    quality_gate.py   publish / review / revise / reject
  commerce/
    catalog.py        verified product catalog + link provenance
    relevance.py      post/product/audience-context match
    disclosure.py     transparent affiliate labels
  orchestration/
    scheduler.py      durable, pausable, quiet-hours aware
    publisher.py      exactly one logical publication operation
    recovery.py       ambiguous-outcome reconciliation
    analytics.py      insight snapshots + coverage caveats
    experiments.py    rotating formats with sample floors
  adapters/
    threads.py        wraps the existing Threads publishing client
    telegram.py       wraps the listener/forum routing + callback tokens
  bot/ tiktok/ clipper/   # PRESERVED — untouched
  organic/                # PRESERVED — legacy wrappers, still running
  v2_cli.py               offline control surface
  shadow_run.py           reproducible offline demo
```

## 3. End-to-End Content Flow

```
scheduled_slot
  → persona_context            (persona/profile.py)
  → audience_signals           (audience/signals.py)
  → idea_candidates(3)         (strategy/ideation.py)
  → candidate_ranker
  → evidence_fetcher_if_needed
  → writer                     (content/writer.py)
  → independent_critic         (content/critic.py)
  → hard_policy_checks         (content/quality_gate.py)
  → decision
```

Decisions: `APPROVED_AUTO` · `NEEDS_REVIEW` · `REVISE_ONCE` · `REJECTED`.
Every decision is reason-coded and persisted.

Approved candidates enter `publication_jobs`. A separate publisher adapter
claims and performs **one** operation at a time.

```
QUEUED → PUBLISHING → PUBLISHED
                    → FAILED
                    → UNKNOWN_OUTCOME  (reconciled by API query, never republished)
```

After publish: store the Threads ID → periodically fetch insights with request
status → log real metrics → retrieve operator feedback → update strategy weights
with minimum-sample safeguards.

## 4. The Adapter Seam

```
business logic ──▶ ThreadsAdapter / TelegramAdapter
                        │
                        ├── DryRun*Adapter   (default; zero side effects)
                        └── Live*Adapter     (requires credentials AND live flags)
```

`get_threads_adapter()` returns the live adapter only when
`AUTOPUBLISH_ENABLED=true` **and** `DRY_RUN=false`. Everything else — tests,
shadow runs, dry-run CLI commands — gets the dry-run adapter.

## 5. Why `UNKNOWN_OUTCOME` Exists

The Meta Threads API has no idempotency key. A `threads_publish` call that times
out may have already succeeded server-side. Blindly retrying produces a
duplicate post on a live account.

So: a timeout marks the job `UNKNOWN_OUTCOME` and stops. Recovery asks the API
whether the container was published. Only a definitive "not found" requeues it.
An inconclusive lookup stays parked for a human.

## 6. Database

One SQLite file (`data/posts.db`), WAL mode, 30 s busy timeout. Migrations are
additive, idempotent, and versioned in `schema_migrations`. **No table is ever
dropped.** Confirmed deletion is a tombstone (`status='deleted'` +
`deleted_at` + `delete_reason`), never a `DELETE`.

`performance_snapshots.views/likes/replies` are **nullable on purpose**: `NULL`
means the API gave us nothing, `0` means we measured zero. Collapsing the two is
what made V1 treat a failed fetch as a post with no reach.

## 7. Legacy Preservation

| Module | Fate |
|---|---|
| `src/publish/threads.py` | **Preserved.** Wrapped by `adapters/threads.py`. |
| `src/bot/telegram_listener.py` | **Preserved.** Still routing the live bot. |
| `src/bot/threads_agent.py` | **Preserved.** Manual draft flow still works. |
| `src/affiliate/*` | **Preserved.** The running daemon imports them. V2 does not. |
| `src/tiktok/*`, `src/clipper/*` | **Preserved, untouched.** |
| `src/organic/*` | **Preserved.** `db.py` now delegates schema to the V2 migration runner and its destructive sync is replaced by the safe reconciler. |
| `src/persona.json`, `src/organic/personas.py` | **Retired from V2.** Superseded by `persona/profile.py`. Still on disk because the legacy daemon reads them. |

## 8. Configuration Surface

All live side effects are behind flags that default to OFF:

| Variable | Default | Effect when true |
|---|---|---|
| `AUTOPUBLISH_ENABLED` | `false` | Scheduler may enqueue for live publishing |
| `AFFILIATE_AUTO_ENABLED` | `false` | Affiliate links may be attached |
| `DRY_RUN` | `true` | When false, live adapters are used |
| `V2_SCHEDULER_ENABLED` | `false` | V2 scheduler loop runs |
| `V2_TELEGRAM_COMMANDS_ENABLED` | `false` | V2 Telegram commands active |
| `TELEGRAM_OPERATOR_IDS` | *(empty = deny all)* | Numeric IDs allowed to mutate |
