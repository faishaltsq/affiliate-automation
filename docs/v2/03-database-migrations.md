# V2 — Database Migrations

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 1. Principles

1. **Additive only.** New columns and new tables. Nothing is dropped.
2. **Idempotent.** Running `migrate()` twice is a no-op the second time.
3. **Reversible where feasible.** Additive migrations are not auto-reversed;
   rollback is a backup restore (see `06-cutover-rollback.md`).
4. **Backed up first.** Every migration is preceded by a checksum-verified copy.
5. **User data is never overwritten or silently reused.**

## 2. Runner

```python
from src.core.storage import migrate, migration_status

migrate()            # apply everything pending
migration_status()   # {'current_version', 'latest_version', 'pending', 'applied'}
```

CLI:

```bash
python -m src.v2_cli migrate --status
python -m src.v2_cli migrate
```

## 3. Migration Chain

### v0 — `baseline`

Guarantees the legacy tables exist. On the production database every statement
is a no-op; on a fresh database they are created with the *legacy* column set so
the still-running daemon keeps working unchanged.

Creates (if absent): `schema_migrations`, `organic_posts`, `scheduler_state`,
`affiliate_queue`, `affiliate_comment_history`.

### v1 — `core_safety`

**Adds to `organic_posts`** (the R1 data-loss fix):

| Column | Type | Purpose |
|---|---|---|
| `status` | `TEXT NOT NULL DEFAULT 'published'` | lifecycle: `published` / `deleted` |
| `deleted_at` | `TIMESTAMP` | when a confirmed deletion was observed |
| `delete_reason` | `TEXT DEFAULT ''` | why (e.g. "confirmed 404 from Threads API") |
| `last_sync_status` | `TEXT DEFAULT ''` | last reconciliation outcome |
| `last_sync_at` | `TIMESTAMP` | when it was last checked |
| `persona_version` | `TEXT DEFAULT ''` | which Persona Bible wrote it |
| `pillar_v2` | `TEXT DEFAULT ''` | V2 pillar, kept separate from the legacy `pillar` |

Indexes: `idx_organic_posts_status`, `idx_organic_posts_threads_id`.

**New tables:**

- `audit_events` — every state transition, with `before_json` / `after_json`,
  actor, reason, timestamp. Secrets are redacted on write.
- `schema_migrations` — version bookkeeping.
- `telegram_callback_tokens` — opaque, single-use, expiring button tokens.
- `operator_sessions` — last-seen and denied-count per operator.

### v2 — `persona_strategy`

**New tables:**

| Table | Purpose |
|---|---|
| `persona_versions` | versioned Persona Bible, one `active` |
| `audience_signals` | pain points with `source_url`, `confidence`, `status` |
| `content_ideas` | pillar, angle, `source_ids_json`, `idea_state`, `planned_date` |
| `calendar_slots` | `slot_date`, pillar, problem, angle, format, `commercial_allowed`, `slot_kind` |
| `content_drafts` | idea link, content, structure, persona version, draft version, status |
| `content_evaluations` | `scores_json`, `vetoes_json`, verdict, total score |
| `publication_jobs` | `operation_id UNIQUE`, state, lease, attempts, published id |
| `performance_snapshots` | **nullable** views/likes/replies + `source_status` |
| `feedback_events` | draft, actor, category, reason |
| `products` | link states, evidence, expiry, reviewer status |
| `promotions` | product ↔ post, evidence, disclosed |
| `experiment_assignments` | draft, hypothesis, variant, attribution window |

## 4. What Deliberately Did NOT Change

- `organic_posts` existing columns and all 131 rows.
- `article_context` (1712 rows), `posts` (21), `used_images` (6),
  `affiliate_comment_history` (4), `affiliate_queue` (1),
  `scheduler_state` (1), `ranara_posts` (0).
- `data/guardrails.db` — untouched.

## 5. Verification Evidence

Run against a **copy** of the live database:

```
BEFORE  user_version=0  organic_posts=131  integrity=ok
MIGRATE v0:baseline, v1:core_safety, v2:persona_strategy
AFTER   user_version=2  organic_posts=131  integrity=ok
        all 131 rows status='published'
AGAIN   applied=[]      organic_posts=131  integrity=ok
```

## 6. Rollback

Because every migration is additive, "rolling back" is a backup restore:

```bash
# 1. Stop the V2 process (never the legacy daemon without approval)
# 2. Restore the verified pre-migration copy
copy data\backups\v2\posts_<timestamp>.db data\posts.db
# 3. Verify
python -c "import sqlite3; c=sqlite3.connect('data/posts.db'); \
print(c.execute('PRAGMA integrity_check').fetchone()[0]); \
print(c.execute('SELECT COUNT(*) FROM organic_posts').fetchone()[0])"
```

Full procedure: `docs/v2/06-cutover-rollback.md`.
