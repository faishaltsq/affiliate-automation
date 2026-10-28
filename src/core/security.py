"""src/core/security.py — Telegram operator authorization.

Chat membership is NOT authorization. Anyone added to the supergroup could
previously fire /publish or press an inline Approve button. V2 requires the
sender's numeric Telegram user ID to appear in TELEGRAM_OPERATOR_IDS.

When the allowlist is empty the safe default is DENY for every mutation.
Read-only commands may still be served to group members.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from src.core.clock import utc_iso
from src.core.config import get_settings


@dataclass(frozen=True)
class Actor:
    """Who performed an action, normalised from a Telegram message/callback."""

    user_id: Optional[int]
    username: str
    chat_id: str
    is_private: bool
    display: str

    @property
    def label(self) -> str:
        if self.username:
            return f"@{self.username}"
        if self.user_id is not None:
            return f"id:{self.user_id}"
        return "unknown"


def actor_from_message(msg: dict) -> Actor:
    frm = msg.get("from") or {}
    chat = msg.get("chat") or {}
    uid = frm.get("id")
    try:
        uid = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        uid = None
    username = str(frm.get("username") or frm.get("first_name") or "user")
    chat_id = str(chat.get("id", ""))
    return Actor(
        user_id=uid,
        username=username,
        chat_id=chat_id,
        is_private=chat.get("type") == "private",
        display=f"{username}({uid})",
    )


def actor_from_callback(cbq: dict) -> Actor:
    frm = cbq.get("from") or {}
    msg = cbq.get("message") or {}
    chat = msg.get("chat") or {}
    uid = frm.get("id")
    try:
        uid = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        uid = None
    username = str(frm.get("username") or frm.get("first_name") or "user")
    return Actor(
        user_id=uid,
        username=username,
        chat_id=str(chat.get("id", "")),
        is_private=chat.get("type") == "private",
        display=f"{username}({uid})",
    )


class AuthorizationDenied(Exception):
    """Raised when an actor attempts a mutation they are not allowed to make."""

    def __init__(self, actor: Actor, action: str):
        self.actor = actor
        self.action = action
        self.at = utc_iso()
        super().__init__(
            f"actor {actor.label} denied '{action}' at {self.at}"
        )


def operator_ids(extra: Iterable[int] = ()) -> frozenset[int]:
    ids = set(get_settings().telegram.operator_ids)
    ids.update(int(i) for i in extra)
    return frozenset(ids)


def is_operator(actor: Actor, extra: Iterable[int] = ()) -> bool:
    """True only when the actor's numeric ID is explicitly allowlisted.

    An empty allowlist denies everyone — fail closed. A missing user_id
    (malformed update) also denies.
    """
    if actor.user_id is None:
        return False
    return actor.user_id in operator_ids(extra)


def require_operator(actor: Actor, action: str, extra: Iterable[int] = ()) -> None:
    if not is_operator(actor, extra):
        raise AuthorizationDenied(actor, action)


def is_known_chat(actor: Actor) -> bool:
    """True when the message came from the configured forum chat or a DM.

    Used for read-only commands: group members may ask /status, but only
    allowlisted operators may mutate.
    """
    cfg = get_settings().telegram
    if actor.is_private:
        return True
    if not cfg.chat_id:
        return True
    return actor.chat_id == cfg.chat_id


# ── Commands classified by blast radius ─────────────────────────────────────
MUTATION_COMMANDS = frozenset({
    "/approve", "/revise", "/reject", "/pause", "/resume", "/autopost",
    "/rollback", "/publish", "/post", "/post_text", "/post_photo", "/chain",
    "/thread", "/affiliate", "/clearqueue", "/sync", "/products_add",
    "/products_remove", "/experiment_start",
})

READONLY_COMMANDS = frozenset({
    "/persona", "/plan", "/ideas", "/draft", "/schedule", "/analytics",
    "/products", "/status", "/help", "/start", "/menu", "/posts", "/queue",
    "/trending", "/topics",
})


def command_requires_operator(text: str) -> bool:
    """Classify a raw message/callback payload by required privilege."""
    body = (text or "").strip()
    if not body:
        return False
    # Inline callback payloads carry the same command vocabulary.
    for prefix in ("pub:", "cancel:", "pub_aff:", "cancel_aff:", "reroll_aff:",
                   "set_persona:", "approve:", "reject:", "revise:"):
        if body.startswith(prefix):
            return True
    head = body.split(maxsplit=1)[0].lower().split("@")[0]
    if head in MUTATION_COMMANDS:
        return True
    if head in READONLY_COMMANDS:
        return False
    # Unknown command: treat as mutation (fail closed).
    return body.startswith("/")
