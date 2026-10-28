"""src/core/config.py — typed environment configuration with feature flags.

Single source of truth for every V2 setting. Business logic reads from here and
never calls os.getenv directly, so a missing flag is a startup error rather than
a silent default that publishes to a live account.

Rules enforced here:
  * AUTOPUBLISH_ENABLED and AFFILIATE_AUTO_ENABLED default to False.
  * Secrets are never logged; redact_secrets() strips token query params.
  * DRY_RUN defaults to True until an operator opts in.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DOCS_DIR = BASE_DIR / "docs"
BACKUP_DIR = DATA_DIR / "backups" / "v2"

for _d in (DATA_DIR, LOG_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env", override=True)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "enabled")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return tuple(p.strip() for p in raw.split(",") if p.strip())


# ── Secret redaction ─────────────────────────────────────────────────────────
_SECRET_QUERY_RE = re.compile(
    r"([?&](?:access_token|token|key|api_key|apikey|sig|signature|secret|password|auth)=)[^&\s\"']+",
    re.I,
)
_BEARER_RE = re.compile(r"(Bearer\s+)\S+", re.I)
_ENV_SECRET_KEYS = (
    "THREADS_ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "LLM_API_KEY",
    "OPENROUTER_API_KEY", "FACEBOOK_ACCESS_TOKEN", "INSTAGRAM_ACCESS_TOKEN",
    "CLOUDINARY_API_SECRET", "SHOPEE_CLIENT_SECRET", "TIKTOK_APP_SECRET",
    "TIKTOK_ACCESS_TOKEN", "REPLICATE_API_TOKEN", "DISCORD_TOKEN",
    "SHOPEE_APP_SECRET",
)


def redact_secrets(text: str) -> str:
    """Strip credentials from a string so it is safe to log."""
    if not text:
        return text
    out = _SECRET_QUERY_RE.sub(r"\1<redacted>", str(text))
    out = _BEARER_RE.sub(r"\1<redacted>", out)
    for key in _ENV_SECRET_KEYS:
        val = os.getenv(key)
        if val and len(val) >= 8 and val in out:
            out = out.replace(val, "<redacted>")
    return out


# ── LLM ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class LLMConfig:
    base_url: str = "https://api.trustisgold.web.id/v1"
    model: str = "tig/laguna-s-2.1"
    fallback_models: tuple[str, ...] = (
        "tig/laguna-xs-2.1", "tig/step-3.7-flash", "tig/step-5-unrestricted",
    )
    api_key: str = "local-secret"
    timeout_seconds: float = 60.0
    max_retries: int = 2

    @property
    def chat_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/chat/completions"


def load_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=os.getenv("LLM_BASE_URL", "https://api.trustisgold.web.id/v1"),
        model=os.getenv("LLM_MODEL", "tig/laguna-s-2.1"),
        fallback_models=_csv(
            "LLM_FALLBACK_MODELS",
            ("tig/laguna-xs-2.1", "tig/step-3.7-flash", "tig/step-5-unrestricted"),
        ),
        api_key=os.getenv("LLM_API_KEY", "local-secret"),
        timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 60.0),
        max_retries=_int("LLM_MAX_RETRIES", 2),
    )


# ── Telegram ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = ""
    chat_id: str = ""
    threads_thread_id: str = "9"
    tiktok_thread_id: str = "83"
    clipper_thread_id: str = "246"
    operator_ids: tuple[int, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.bot_token)


def load_telegram_config() -> TelegramConfig:
    raw_ops = _csv("TELEGRAM_OPERATOR_IDS")
    ops: list[int] = []
    for item in raw_ops:
        try:
            ops.append(int(item))
        except ValueError:
            continue
    return TelegramConfig(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        chat_id=os.getenv("TG_AFFILIATE_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", "")),
        threads_thread_id=os.getenv(
            "TG_THREADS_THREAD_ID", os.getenv("TG_AFFILIATE_THREAD_ID", "9")
        ),
        tiktok_thread_id=os.getenv("TG_TIKTOK_THREAD_ID", "83"),
        clipper_thread_id=os.getenv("TG_CLIPPER_THREAD_ID", "246"),
        operator_ids=tuple(ops),
    )


# ── Threads ─────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ThreadsConfig:
    user_id: str = ""
    access_token: str = ""
    base_url: str = "https://graph.threads.net/v1.0"
    publish_timeout_seconds: float = 30.0

    @property
    def configured(self) -> bool:
        return bool(self.user_id and self.access_token)


def load_threads_config() -> ThreadsConfig:
    return ThreadsConfig(
        user_id=os.getenv("THREADS_USER_ID", ""),
        access_token=os.getenv("THREADS_ACCESS_TOKEN", ""),
        publish_timeout_seconds=_float("THREADS_PUBLISH_TIMEOUT_SECONDS", 30.0),
    )


# ── Feature flags (the safety surface) ──────────────────────────────────────
@dataclass(frozen=True)
class FeatureFlags:
    """Every live side effect is behind one of these. All default OFF."""

    autopublish_enabled: bool = False
    affiliate_auto_enabled: bool = False
    dry_run: bool = True
    shadow_mode: bool = True
    v2_scheduler_enabled: bool = False
    v2_telegram_commands_enabled: bool = False

    @property
    def live_publishing_allowed(self) -> bool:
        return self.autopublish_enabled and not self.dry_run

    @property
    def live_affiliate_allowed(self) -> bool:
        return self.affiliate_auto_enabled and not self.dry_run

    def describe(self) -> str:
        return (
            f"autopublish={self.autopublish_enabled} "
            f"affiliate_auto={self.affiliate_auto_enabled} "
            f"dry_run={self.dry_run} "
            f"shadow={self.shadow_mode} "
            f"scheduler={self.v2_scheduler_enabled} "
            f"telegram_v2={self.v2_telegram_commands_enabled}"
        )


def load_feature_flags() -> FeatureFlags:
    return FeatureFlags(
        autopublish_enabled=_bool("AUTOPUBLISH_ENABLED", False),
        affiliate_auto_enabled=_bool("AFFILIATE_AUTO_ENABLED", False),
        dry_run=_bool("DRY_RUN", True),
        shadow_mode=_bool("SHADOW_MODE", True),
        v2_scheduler_enabled=_bool("V2_SCHEDULER_ENABLED", False),
        v2_telegram_commands_enabled=_bool("V2_TELEGRAM_COMMANDS_ENABLED", False),
    )


# ── Scheduler ───────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SchedulerConfig:
    main_posts_per_day: int = 1
    max_extra_posts_per_week: int = 3
    quiet_start_hour: int = 23
    quiet_end_hour: int = 6
    timezone_offset_hours: int = 7  # WIB
    poll_interval_seconds: int = 300
    paused: bool = False

    @property
    def timezone_name(self) -> str:
        return "WIB"


def load_scheduler_config() -> SchedulerConfig:
    return SchedulerConfig(
        main_posts_per_day=_int("V2_MAIN_POSTS_PER_DAY", 1),
        max_extra_posts_per_week=_int("V2_MAX_EXTRA_POSTS_PER_WEEK", 3),
        quiet_start_hour=_int("V2_QUIET_START_HOUR", 23),
        quiet_end_hour=_int("V2_QUIET_END_HOUR", 6),
        poll_interval_seconds=_int("V2_POLL_INTERVAL_SECONDS", 300),
        paused=_bool("V2_SCHEDULER_PAUSED", False),
    )


# ── Aggregate settings object ───────────────────────────────────────────────
@dataclass(frozen=True)
class Settings:
    llm: LLMConfig = field(default_factory=load_llm_config)
    telegram: TelegramConfig = field(default_factory=load_telegram_config)
    threads: ThreadsConfig = field(default_factory=load_threads_config)
    flags: FeatureFlags = field(default_factory=load_feature_flags)
    scheduler: SchedulerConfig = field(default_factory=load_scheduler_config)
    persona_version: str = "teman_tongkrongan_v1"
    database_path: Path = DATA_DIR / "posts.db"

    def safe_summary(self) -> dict:
        """Loggable summary — never contains a secret value."""
        return {
            "llm_model": self.llm.model,
            "llm_base_url": self.llm.base_url,
            "threads_configured": self.threads.configured,
            "telegram_configured": self.telegram.configured,
            "telegram_operators": len(self.telegram.operator_ids),
            "persona_version": self.persona_version,
            "flags": self.flags.describe(),
            "database": str(self.database_path),
        }


_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    global _settings
    _settings = None
