# Affiliate Content Automation

**Persona-first Threads automation.** The system writes, evaluates, schedules,
and publishes posts in one consistent identity, and holds every draft for
operator approval.

Legacy news-first Threads, TikTok/Shorts, Clipper, and affiliate pipelines are
**disabled** and their data is preserved. See
[`docs/v2/06-cutover-rollback.md`](docs/v2/06-cutover-rollback.md).

## Run it

```bash
python main.py bot        # Telegram listener (V2 command set)
python main.py v2         # content runtime (long-running)
python main.py v2 --once  # one scheduling pass
```

Both are supervised by `scripts/watchdog.py`.

## How a post reaches the account

```
calendar slot → writer → quality gate → QUEUED (status EVALUATED)
                                          ↓
                      /approve <id>   →  status APPROVED
                                          ↓
                          runtime drain → PUBLISHED
```

Nothing publishes without `/approve`. A draft the gate rejects is never
enqueued at all.

## Offline tools

```bash
python -m src.v2_runtime --status   # flags, queue, safety
python -m src.v2_cli health         # flags, tables, row counts
python -m src.v2_cli plan           # content calendar
python -m src.v2_cli draft          # one draft, no publish
python -m src.integration_check     # end-to-end rehearsal, no side effects
```

## Safety defaults

| Flag | Default | Effect |
|---|---|---|
| `AUTOPUBLISH_ENABLED` | `false` | No live publishing |
| `AFFILIATE_AUTO_ENABLED` | `false` | Affiliate locked; slots downgrade to organic |
| `DRY_RUN` | `true` | Every adapter is a no-op |
| `V2_REVIEW_REQUIRED` | `true` | Nothing publishes without operator approval |

See [`.env.example`](.env.example) for the full list, and
[`docs/v2/05-runbook.md`](docs/v2/05-runbook.md) for daily operations.

## Credentials

Only these are needed for the V2 path:

| Service | Where to get |
|---|---|
| LLM gateway (OpenAI-compatible) | `LLM_BASE_URL` + `LLM_API_KEY` in `.env` |
| Telegram bot | @BotFather -> `TELEGRAM_BOT_TOKEN` |
| Meta Graph API (Threads) | https://developers.facebook.com -> App -> Tokens |

Reddit, Replicate, Cloudinary, and Shopee credentials belong to the disabled
legacy pipelines and are not read by V2.

## Tests

```bash
python -m pytest tests/v2 -q --no-header -p no:cacheprovider
```

617 tests cover the persona, gate, scheduler, publisher, recovery, commands,
and adapters. `tests/bot`, `tests/clipper`, `tests/tiktok`, and
`tests/test_multi_topic_bot.py` belong to the disabled pipelines and need
`cv2`, `psutil`, `praw`, and `playwright`.

## Documentation

[`docs/v2/`](docs/v2/) - audit, product spec, architecture, migrations,
Telegram command guide, runbook, cutover/rollback, quality review.
