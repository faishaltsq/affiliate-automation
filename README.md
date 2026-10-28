# Affiliate Content Automation

Full pipeline: **Trend research → Brief → Image → Affiliate fetch → Publish → Monitor**

## Structure

```
affiliate-automation/
├── main.py                     # CLI launcher
├── .env.example                # Rename → .env, fill credentials
├── requirements.txt
├── data/                       # ranked_topics.json, briefs, posts.db, metrics
├── logs/                       # pipeline.log
└── src/
    ├── config.py               # All env vars
    ├── logger.py               # Centralized logging
    ├── trend_job.py            # Cron entry: research → ranked_topics.json
    ├── worker.py               # Worker: per-topic pipeline
    ├── trends/research.py      # Google Trends + Reddit + TikTok + rank
    ├── brief/generator.py      # LLM brief + compliance loop
    ├── image/generator.py      # Replicate SD + Cloudinary upload
    ├── affiliate/shopee.py     # Shopee Open API v2 + affiliate link
    ├── publish/social.py       # Facebook Graph + Threads API
    └── monitor/analytics.py   # Shopee CTR + Meta Insights snapshots
```

## Setup

```bash
cp .env.example .env
# Fill in all credentials in .env

pip install -r requirements.txt
```

## Usage

```bash
# Full pipeline (trends → worker)
python main.py full "portable SSD"

# Trend research only
python main.py trends "wireless headphones"

# Worker only (reads existing ranked_topics.json)
python main.py worker

# Single topic (bypass trends step)
python main.py single "Portable SSD"

# Pull metrics snapshot
python main.py monitor
```

## Cron (Linux/macOS)

```cron
# Run full pipeline daily at 08:00
0 8 * * * cd /path/to/affiliate-automation && python main.py full "gadgets" >> logs/cron.log 2>&1
```

## Windows Task Scheduler

```
Action: python C:\path\to\affiliate-automation\main.py full gadgets
Trigger: Daily at 08:00
```

## API Credentials Required

| Service | Where to get |
|---|---|
| Reddit | https://www.reddit.com/prefs/apps |
| OpenRouter | https://openrouter.ai/keys |
| Replicate | https://replicate.com/account/api-tokens |
| Cloudinary | https://cloudinary.com/console |
| Shopee Open Platform | https://open.shopee.com → My Apps |
| Meta Graph API | https://developers.facebook.com → App → Tokens |

## Key Fixes vs. Original Blueprint

| Issue | Fix |
|---|---|
| Shopee missing HMAC-SHA256 signature | `_sign()` in `shopee.py` implements v2 spec |
| Threads used IG Media API | Corrected to `graph.threads.net/v1.0/{user}/threads` |
| Google Trends JSONP parse | Proper response key path + pytrends fallback |
| TikTok unsigned request | Mobile UA + graceful warning to use RapidAPI in prod |
| `hmac.new` → `hmac.new` | Uses stdlib `hmac.new(key, msg, digestmod)` correctly |

---

# Affiliate Automation V2 (persona-first)

A parallel, additive implementation living on branch `feature/v2-persona-first`.
The V1 pipeline above is **preserved and still running**. V2 replaces the
news-first Threads content system with a persona-first one.

**Documentation:** [`docs/v2/`](docs/v2/) — audit, product spec, architecture,
migrations, Telegram guide, runbook, cutover/rollback, quality review.

## V2 Quick Start (offline)

```bash
# Inspect flags — everything live is OFF by default
python -m src.v2_cli health

# Apply additive migrations
python -m src.v2_cli migrate

# Show the canonical persona
python -m src.v2_cli persona

# Build the 30-day content calendar
python -m src.v2_cli plan --days 30

# Generate candidate ideas (no news source, no network)
python -m src.v2_cli ideas --n 3

# Generate a draft without publishing
python -m src.v2_cli draft --pillar practical_tips

# The reproducible offline shadow run
python -m src.shadow_run --count 30 --out docs/v2/shadow-report.json
```

## V2 Tests

```bash
python -m pytest tests/ -q --no-header -p no:cacheprovider \
  --ignore=tests/bot --ignore=tests/clipper --ignore=tests/tiktok \
  --ignore=tests/test_multi_topic_bot.py
```

`tests/bot/`, `tests/clipper/`, `tests/tiktok/`, and
`tests/test_multi_topic_bot.py` require `cv2`, `psutil`, `praw`, and
`playwright`, which are not installed in this environment. They were
uncollectable before V2 as well.

## V2 Safety Defaults

| Flag | Default | Effect |
|---|---|---|
| `AUTOPUBLISH_ENABLED` | `false` | No live publishing |
| `AFFILIATE_AUTO_ENABLED` | `false` | No affiliate links |
| `DRY_RUN` | `true` | Every adapter is a no-op |
| `V2_SCHEDULER_ENABLED` | `false` | V2 scheduler does not run |
| `TELEGRAM_OPERATOR_IDS` | *(empty)* | Denies every mutation |

See [`.env.example`](.env.example) for the full list.
