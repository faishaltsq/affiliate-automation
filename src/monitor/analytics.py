"""
Monitoring & Analytics Layer (Section 6 & 7)
- Shopee Affiliate dashboard metrics (CTR/CPC)
- Meta Insights (post reach / engagement)
- Lightweight wrappers — extend with Ahrefs/Grammarly/Sentry as needed
"""

import json
import requests
from datetime import datetime, timedelta, timezone

from src.config import (
    FACEBOOK_PAGE_ID,
    FACEBOOK_ACCESS_TOKEN,
    SHOPEE_CLIENT_ID,
    SHOPEE_CLIENT_SECRET,
    SHOPEE_AFFILIATE_CODE,
    AHREFS_TOKEN,
    GRAMMARLY_TOKEN,
    SENTRY_DSN,
    DATA_DIR,
)
from src.logger import get_logger

log = get_logger("monitor")


# ── Shopee Affiliate Report ──────────────────────────────────────────────────

def get_shopee_affiliate_report(start_date: str, end_date: str) -> dict:
    """
    Pulls CTR/CPC/conversion data from Shopee Affiliate Dashboard API.
    Dates as 'YYYY-MM-DD'.
    """
    if not SHOPEE_CLIENT_ID:
        return {"error": "shopee_credentials_not_set"}

    url = "https://partner.shopeemobile.com/api/v2/affiliate/report"
    headers = {"Authorization": f"Bearer {SHOPEE_AFFILIATE_CODE}"}  # placeholder
    params = {"start_date": start_date, "end_date": end_date}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        log.info(f"Shopee report: {start_date} → {end_date}")
        return data
    except Exception as e:
        log.error(f"Shopee affiliate report failed: {e}")
        return {"error": str(e)}


# ── Meta Insights ─────────────────────────────────────────────────────────────

def get_facebook_insights(metrics: str = "page_impressions,page_engaged_users") -> dict:
    """
    Pulls page-level insights from Meta Graph API.
    metrics: comma-separated insight names.
    """
    if not FACEBOOK_PAGE_ID or not FACEBOOK_ACCESS_TOKEN:
        return {"error": "facebook_credentials_not_set"}

    url = f"https://graph.facebook.com/v19.0/{FACEBOOK_PAGE_ID}/insights"
    params = {
        "metric": metrics,
        "access_token": FACEBOOK_ACCESS_TOKEN,
        "period": "day",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        log.info(f"Facebook insights fetched: {metrics}")
        return data
    except Exception as e:
        log.error(f"Facebook insights failed: {e}")
        return {"error": str(e)}


def get_post_engagement(post_id: str) -> dict:
    """Retrieves engagement metrics for a specific Facebook post."""
    if not FACEBOOK_ACCESS_TOKEN:
        return {"error": "facebook_credentials_not_set"}

    url = f"https://graph.facebook.com/v19.0/{post_id}/insights"
    params = {
        "metric": "post_impressions,post_engaged_users,post_clicks",
        "access_token": FACEBOOK_ACCESS_TOKEN,
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Post engagement fetch failed: {e}")
        return {"error": str(e)}


# ── Metrics Snapshot Saver ────────────────────────────────────────────────────

def save_metrics_snapshot() -> str:
    """
    Collects all available metrics and writes to data/metrics_<timestamp>.json.
    Safe to run on a cron schedule (won't crash on missing credentials).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    snapshot = {
        "timestamp": now.isoformat(),
        "shopee_report": get_shopee_affiliate_report(start, end),
        "facebook_insights": get_facebook_insights(),
    }

    out = DATA_DIR / f"metrics_{now.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    log.info(f"Metrics snapshot → {out}")
    return str(out)


if __name__ == "__main__":
    print(save_metrics_snapshot())
