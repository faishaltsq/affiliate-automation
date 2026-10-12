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
