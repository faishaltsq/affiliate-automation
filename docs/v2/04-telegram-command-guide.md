# V2 — Telegram Command Guide

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 0. Authorization Model

**Chat membership is not authorization.** Every mutation command and every
inline button requires the sender's **numeric Telegram user ID** to appear in
`TELEGRAM_OPERATOR_IDS` (comma-separated, in `.env`).

```
TELEGRAM_OPERATOR_IDS=111222333,444555666
```

- An **empty** allowlist denies every mutation. Fail closed.
- Read-only commands may be served to any member of the configured forum chat.
- Unknown commands are treated as mutations.
- Inline buttons use opaque, single-use, 15-minute tokens bound to the operator
  who received them. A replay by anyone else fails with `actor_mismatch`.

## 1. Read-Only Commands

Available to any member of the configured forum chat.

| Command | Purpose |
|---|---|
| `/persona` | Active identity, version, voice summary |
| `/plan` | Today's ideas and tomorrow's slot |
| `/ideas` | A new ranked set of candidate ideas |
| `/draft` | Generate a preview (no publish) |
| `/schedule` | Upcoming queue, rendered in WIB |
| `/analytics [7d\|30d]` | Verified metrics with coverage caveats |
| `/products` | Validated catalog and affiliate link status |
| `/status` | Health, queue depth, last successes/failures |
| `/help` | Command list |

## 2. Mutation Commands

**Require an allowlisted operator ID.**

| Command | Purpose |
|---|---|
| `/approve <id>` | Authorize a specific evaluated draft |
| `/revise <id> <reason>` | Regenerate with structured feedback |
| `/reject <id> <reason>` | Persist the reason; history is not erased |
| `/pause` | Pause the scheduler (persisted, survives restart) |
| `/resume` | Resume the scheduler |
| `/autopost on\|off` | Explicit authorization. **Default OFF.** |
| `/rollback` | Operator-controlled safe rollback to legacy mode (no data deletion) |
| `/products_add <url>` | Import a product link for verification |
| `/products_remove <id>` | Revoke a product from the catalog |

## 3. Inline Review Buttons

A draft preview arrives with buttons. Each button carries an opaque token, not
a draft ID.

```
[ ✅ Approve ]  [ ✏️ Revise ]  [ ❌ Reject ]
```

Token lifecycle:

1. Minted when the preview is sent (`issue_callback_token`).
2. Opaque — `secrets.token_urlsafe(16)`, no draft ID in the payload.
3. Single-use — consumed on first press.
4. 15-minute TTL — expired tokens are rejected and purged.
5. Bound to the issuing operator's user ID.

Rejection reasons: `unknown_token`, `already_consumed`, `expired`,
`actor_mismatch`, `not_an_operator`.

## 4. Forum Topics (preserved)

The existing routing is unchanged:

| Topic | Thread ID (default) | Agent |
|---|---|---|
| 📦 Affiliate / Threads | `TG_THREADS_THREAD_ID` (9) | `threads_agent` |
| 🎵 TikTok | `TG_TIKTOK_THREAD_ID` (83) | `tiktok_agent` |
| 🎬 Clipper | `TG_CLIPPER_THREAD_ID` (246) | `clipper_agent` |

Messages from an unexpected group chat are ignored. Private DMs are routed by
content inference, as before.

## 5. What a Denied Command Looks Like

```
⛔ You are not authorized to run /approve.
Your user ID (999888777) is not on the operator allowlist.
```

The attempt is recorded in `audit_events` with `actor_type='operator'` and the
denied action. No state changes.

## 6. Enabling the V2 Command Set

The commands above are **dark by default**. The existing bot keeps working
untouched until this is set:

```
V2_TELEGRAM_COMMANDS_ENABLED=true
```

`handle_message()` in `src/bot/telegram_listener.py` tries the V2 router first.
It only claims a message when:

1. the text starts with `/`,
2. the command is one V2 recognises, and
3. `V2_TELEGRAM_COMMANDS_ENABLED` is true.

Anything else falls through to the legacy handlers unchanged. A V2 routing
error is caught and logged, then falls through too — a broken V2 command can
never take the existing bot down.

### Implementation

`src/bot/v2_commands.py`. Every handler receives the caller's database `path`
and (for `/draft`) an injectable LLM client, so:

- a test dispatches against an isolated database and a fake model,
- `tests/v2/test_no_live_db_writes.py` asserts that a full command sweep leaves
  the live `data/posts.db` byte-for-byte unchanged,
- `organic_posts` is checked for immutability on every run.

That guard exists because an earlier iteration accepted `path` but never
forwarded it, and every test run wrote its fixtures into the production
database beside real operator history.

## 7. Secrets

No command ever prints a token, API key, or full access token. `redact_secrets()`
strips:

- `?access_token=…` and equivalent query parameters
- `Authorization: Bearer …` headers
- Any value matching a known secret environment variable

Logs and Telegram messages both pass through it.
