"""Central configuration — loads .env and exposes all settings."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ── Paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
PERSONA_FILE = BASE_DIR / "src" / "persona.json"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ── Reddit ─────────────────────────────────────────────
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "affiliate_trend_bot")

# ── LLM (9router) ───────────────────────────────────
HERMES_ENV_PATH = Path.home() / "AppData" / "Local" / "hermes" / ".env"
_hermes_env = {}
if HERMES_ENV_PATH.exists():
    from dotenv import dotenv_values
    _hermes_env = dotenv_values(HERMES_ENV_PATH)

# Primary: 9router local antigravity gemini 3.8 flash
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or _hermes_env.get("NINEROUTER_BASE_URL") or "http://localhost:20128/v1"
LLM_MODEL = os.getenv("LLM_MODEL") or "ag/gemini-3.8-flash-high"
# Fallback: Mortera Cloud direct (NOT via 9router) — Muse Spark
MORTERA_BASE_URL = os.getenv("MORTERA_BASE_URL") or "https://mortera.cloud/v1"
MORTERA_MODEL = os.getenv("MORTERA_MODEL") or "muse-spark-1.3"
MORTERA_API_KEY = os.getenv("MORTERA_API_KEY") or ""
MORTERA_URL = f"{MORTERA_BASE_URL.rstrip('/')}/chat/completions"
LLM_API_KEY = (
    os.getenv("LLM_API_KEY")
    or os.getenv("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or _hermes_env.get("HERMES_CUSTOM_LOCALHOST_20128_API_KEY")
    or "local-secret"
)

OPENROUTER_URL = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"

# ── Image Gen ──────────────────────────────────────────
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
REPLICATE_MODEL = os.getenv(
    "REPLICATE_MODEL",
    "ac732df83cea7fff18b8472768c88ad041fa750ff7682a21affe81863cbe77e4",
)

# ── Cloudinary ─────────────────────────────────────────
CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY", "")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET", "")

# ── Shopee Affiliate (deprecated) ────────────────────
SHOPEE_CLIENT_ID = os.getenv("SHOPEE_CLIENT_ID", "")
SHOPEE_CLIENT_SECRET = os.getenv("SHOPEE_CLIENT_SECRET", "")
SHOPEE_AFFILIATE_CODE = os.getenv("SHOPEE_AFFILIATE_CODE", "")

# ── TikTok Shop Affiliate ─────────────────────────────
TIKTOK_AFFILIATE_ID = os.getenv("TIKTOK_AFFILIATE_ID", "")  # username / shop name, e.g. pochitalkshop
TIKTOK_APP_ID = os.getenv("TIKTOK_APP_ID", "")
TIKTOK_APP_SECRET = os.getenv("TIKTOK_APP_SECRET", "")
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")

# ── Meta (Facebook + Threads) ─────────────────────────
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_ACCESS_TOKEN = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
INSTAGRAM_BUSINESS_ID = os.getenv("INSTAGRAM_BUSINESS_ID", "")
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")

# ── Telegram Bot & Forum Topics ────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_AFFILIATE_CHAT_ID = os.getenv("TG_AFFILIATE_CHAT_ID", "-1003757350269")
TG_AFFILIATE_THREAD_ID = os.getenv("TG_AFFILIATE_THREAD_ID", "9")
TG_THREADS_THREAD_ID = os.getenv("TG_THREADS_THREAD_ID", TG_AFFILIATE_THREAD_ID)
TG_TIKTOK_THREAD_ID = os.getenv("TG_TIKTOK_THREAD_ID", "83")

# ── Discord / Motionklip ──────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
MOTIONKLIP_GUILD_ID = os.getenv("MOTIONKLIP_GUILD_ID", "")
MOTIONKLIP_CHANNELS = [x.strip() for x in os.getenv("MOTIONKLIP_CHANNELS", "").split(",") if x.strip()]

# ── Monitoring (Optional) ─────────────────────────────
AHREFS_TOKEN = os.getenv("AHREFS_TOKEN", "")
GRAMMARLY_TOKEN = os.getenv("GRAMMARLY_TOKEN", "")
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
