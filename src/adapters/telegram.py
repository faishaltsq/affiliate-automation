"""src/adapters/telegram.py — V2 wrapper around the existing Telegram listener.

Preserves the current forum topics and routing. Adds what V1 lacked:
  * short-lived opaque callback tokens instead of raw draft IDs in button data
  * sender-ID authorization checked at the adapter boundary
  * a dry-run mode that suppresses every outbound call

The existing `src/bot/telegram_listener.py` keeps working untouched; this
adapter is what new V2 commands use.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from src.core.clock import sqlite_utc, to_utc, utc_now, wib_display
from src.core.config import get_settings, redact_secrets
from src.core.security import (
    Actor, actor_from_callback, actor_from_message, is_operator,
)
from src.core.storage import connection, transaction
from src.logger import get_logger

log = get_logger("adapters_telegram")

CALLBACK_TTL_SECONDS = 900  # 15 minutes


@dataclass
class CallbackToken:
    token: str
    draft_id: str
    action: str
    actor_id: str
    chat_id: str
    message_id: Optional[int]
    expires_at: str
    consumed_at: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        from src.core.clock import parse_sqlite_utc

        dt = parse_sqlite_utc(self.expires_at)
        if dt is None:
            return True
        return utc_now() > dt


class TelegramAdapter:
    """Every Telegram side effect goes through here."""

    def send_text(self, chat_id: str, text: str, thread_id: Optional[str] = None,
                  reply_to: Optional[int] = None) -> dict:
        raise NotImplementedError

    def send_with_buttons(self, chat_id: str, text: str, buttons: list[dict],
                          thread_id: Optional[str] = None) -> dict:
        raise NotImplementedError

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        raise NotImplementedError


class DryRunTelegramAdapter(TelegramAdapter):
    def __init__(self):
        self.sent: list[dict] = []

    def send_text(self, chat_id, text, thread_id=None, reply_to=None) -> dict:
        self.sent.append({"op": "send_text", "chat_id": chat_id,
                          "thread_id": thread_id, "text": text})
        log.info("[DRY RUN] telegram send suppressed: %s",
                 redact_secrets(text[:60]))
        return {"dry_run": True}

    def send_with_buttons(self, chat_id, text, buttons, thread_id=None) -> dict:
        self.sent.append({"op": "send_with_buttons", "chat_id": chat_id,
                          "thread_id": thread_id, "text": text,
                          "buttons": buttons})
        log.info("[DRY RUN] telegram buttons suppressed: %s",
                 redact_secrets(text[:60]))
        return {"dry_run": True}

    def answer_callback(self, callback_query_id, text="") -> None:
        self.sent.append({"op": "answer_callback", "id": callback_query_id})


class LiveTelegramAdapter(TelegramAdapter):
    def __init__(self):
        cfg = get_settings().telegram
        if not cfg.configured:
            raise RuntimeError("LiveTelegramAdapter requires TELEGRAM_BOT_TOKEN")
        self._cfg = cfg
        self._api = f"https://api.telegram.org/bot{cfg.bot_token}"

    def send_text(self, chat_id, text, thread_id=None, reply_to=None) -> dict:
        import requests

        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if thread_id:
            payload["message_thread_id"] = int(thread_id)
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        r = requests.post(f"{self._api}/sendMessage", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()

    def send_with_buttons(self, chat_id, text, buttons, thread_id=None) -> dict:
        import requests

        payload = {
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "reply_markup": {"inline_keyboard": buttons},
        }
        if thread_id:
            payload["message_thread_id"] = int(thread_id)
        r = requests.post(f"{self._api}/sendMessage", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()

    def answer_callback(self, callback_query_id, text="") -> None:
        import requests

        try:
            requests.post(
                f"{self._api}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id, "text": text[:200]},
                timeout=5,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("answerCallbackQuery failed: %s", exc)


def get_telegram_adapter() -> TelegramAdapter:
    flags = get_settings().flags
    if flags.dry_run:
        return DryRunTelegramAdapter()
    return LiveTelegramAdapter()


# ── Callback token store ────────────────────────────────────────────────────
def issue_callback_token(
    draft_id: str,
    action: str,
    actor: Actor,
    message_id: Optional[int] = None,
    ttl_seconds: int = CALLBACK_TTL_SECONDS,
    path=None,
) -> str:
    """Mint an opaque, single-use, short-lived token for an inline button.

    V1 put the raw draft ID straight into the callback data, so anyone who saw
    the message could replay it. The token is random, expires, and is consumed
    on first use.
    """
    token = secrets.token_urlsafe(16)
    expires = sqlite_utc(utc_now() + timedelta(seconds=ttl_seconds))
    with connection(path) as conn:
        with transaction(conn):
            conn.execute(
                """
                INSERT INTO telegram_callback_tokens
                    (token, draft_id, action, actor_id, chat_id, message_id,
                     expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (token, str(draft_id), action, str(actor.user_id or ""),
                 str(actor.chat_id), message_id, expires),
            )
    return token


def consume_callback_token(
    token: str,
    actor: Actor,
    path=None,
) -> tuple[bool, str, Optional[CallbackToken]]:
    """Validate and consume a token. Returns (ok, reason, token_record).

    Checks, in order:
      1. token exists
      2. not already consumed (single use)
      3. not expired
      4. the actor is the same operator who received the button
      5. the actor is still on the operator allowlist

    Any failure leaves the token untouched so a genuine operator can retry
    with a fresh button.
    """
    with connection(path) as conn:
        row = conn.execute(
            "SELECT * FROM telegram_callback_tokens WHERE token = ?", (token,)
        ).fetchone()

    if row is None:
        return False, "unknown_token", None

    record = CallbackToken(
        token=row["token"], draft_id=row["draft_id"], action=row["action"],
        actor_id=row["actor_id"], chat_id=row["chat_id"],
        message_id=row["message_id"], expires_at=row["expires_at"],
        consumed_at=row["consumed_at"],
    )

    if record.consumed_at:
        return False, "already_consumed", record
    if record.is_expired:
        return False, "expired", record
    if str(actor.user_id or "") != record.actor_id:
        return False, "actor_mismatch", record
    if not is_operator(actor):
        return False, "not_an_operator", record

    now_s = sqlite_utc(utc_now())
    with connection(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                "UPDATE telegram_callback_tokens SET consumed_at = ? "
                "WHERE token = ? AND consumed_at IS NULL",
                (now_s, token),
            )
            if cur.rowcount != 1:
                return False, "already_consumed", record

    record.consumed_at = now_s
    return True, "ok", record


def purge_expired_tokens(path=None) -> int:
    with connection(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                "DELETE FROM telegram_callback_tokens "
                "WHERE expires_at < ? AND consumed_at IS NULL",
                (sqlite_utc(utc_now()),),
            )
            return int(cur.rowcount)


def format_schedule_for_operator(path=None) -> str:
    """Human-readable upcoming queue in WIB."""
    from src.strategy.calendar import upcoming_slots

    slots = upcoming_slots(days=7, path=path)
    if not slots:
        return "Belum ada kalender. Jalankan `python -m src.v2_cli plan` dulu."
    lines = ["<b>Jadwal 7 hari ke depan (WIB)</b>", ""]
    for s in slots:
        kind = "utama" if s.slot_kind == "main" else "ekstra"
        commercial = " 💰" if s.commercial_allowed else ""
        lines.append(
            f"• {s.slot_date} — <b>{s.pillar}</b> ({kind}){commercial}"
        )
    lines.append("")
    lines.append(f"<i>Dibuat {wib_display()}</i>")
    return "\n".join(lines)
