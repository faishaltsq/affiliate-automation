"""src/bot/v2_commands.py — the V2 Telegram Command Center.

Master plan §7. Preserves the existing forum topics and routing; adds the
persona-first command set on top.

Design rules enforced here:
  * Every mutation requires a numeric operator ID on TELEGRAM_OPERATOR_IDS.
    Chat membership is not authorization.
  * The whole command set is behind V2_TELEGRAM_COMMANDS_ENABLED, so it stays
    dark until an operator turns it on.
  * Nothing here performs a side effect directly. `sender` is injected, so
    tests supply a recorder and no Telegram call is ever made.
  * Read-only commands are served to any member of the configured chat.
  * Unknown commands are treated as mutations (fail closed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from src.core.clock import wib_display
from src.core.config import get_settings, redact_secrets
from src.core.security import (
    Actor, actor_from_message, command_requires_operator, is_operator,
    require_operator,
)
from src.logger import get_logger

log = get_logger("bot_v2_commands")

Sender = Callable[[str, Optional[int]], None]


@dataclass
class CommandResult:
    handled: bool
    text: str = ""
    authorized: bool = True
    command: str = ""
    denied_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "handled": self.handled,
            "command": self.command,
            "authorized": self.authorized,
            "denied_reason": self.denied_reason,
            "text": self.text,
        }


def _not_handled(text: str = "") -> CommandResult:
    return CommandResult(handled=False, text=text)


def _denied(actor: Actor, command: str) -> CommandResult:
    """Record the attempt, change nothing, tell the sender why."""
    log.warning(
        "Denied V2 command '%s' from %s (user_id=%s)",
        command, actor.label, actor.user_id,
    )
    return CommandResult(
        handled=True,
        authorized=False,
        command=command,
        denied_reason="not_an_operator",
        text=(
            "⛔ <b> Tidak diizinkan</b>\n\n"
            f"Perintah <code>{command}</code> hanya untuk operator terdaftar.\n"
            f"User ID kamu: <code>{actor.user_id}</code>\n\n"
            "Hubungi operator untuk ditambahkan ke "
            "<code>TELEGRAM_OPERATOR_IDS</code>."
        ),
    )


# ── Read-only commands ──────────────────────────────────────────────────────
def _cmd_persona(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.persona.profile import get_active_persona

    p = get_active_persona(path)
    rules = "\n".join(f"• {r}" for r in p.voice_rules[:5])
    return CommandResult(True, command="/persona", text=(
        f"<b>Persona aktif</b>\n\n"
        f"<b>{p.name}</b> (<code>{p.handle}</code>)\n"
        f"Versi: <code>{p.version}</code>\n"
        f"Arketipe: {p.archetype}\n\n"
        f"<b>Janji akun</b>\n<i>“{p.account_promise}”</i>\n\n"
        f"<b>Audiens</b>\n{p.audience}\n\n"
        f"<b>Nada</b>\n{p.tone}\n\n"
        f"<b>Aturan suara (5 pertama)</b>\n{rules}"
    ))


def _cmd_plan(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.strategy.calendar import upcoming_slots
    from src.strategy.ideation import generate_ideas

    slots = upcoming_slots(days=2, path=path)
    if not slots:
        return CommandResult(True, command="/plan", text=(
            "Belum ada kalender. Jalankan:\n"
            "<code>python -m src.v2_cli plan --days 30</code>"
        ))
    lines = ["<b>Rencana konten</b>", ""]
    for s in slots[:6]:
        kind = "utama" if s.slot_kind == "main" else "ekstra"
        money = " 💰" if s.commercial_allowed else ""
        lines.append(f"• {s.slot_date} — <b>{s.pillar}</b> ({kind}){money}")
    ideas = generate_ideas(count=3, path=path)
    if ideas:
        lines += ["", "<b>Ide untuk slot berikutnya</b>"]
        for i, idea in enumerate(ideas, 1):
            lines.append(f"{i}. [{idea.pillar}] {idea.angle}")
    lines += ["", f"<i>{wib_display()}</i>"]
    return CommandResult(True, command="/plan", text="\n".join(lines))


def _cmd_ideas(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.strategy.ideation import generate_ideas

    pillar = args.strip() or None
    ideas = generate_ideas(pillar=pillar, count=3, path=path)
    if not ideas:
        return CommandResult(True, command="/ideas", text=(
            f"Tidak ada ide untuk pilar <code>{pillar}</code>."
            if pillar else "Tidak ada ide yang bisa dihasilkan sekarang."
        ))
    lines = [f"<b>{len(ideas)} ide kandidat</b>"
             + (f" — {pillar}" if pillar else ""), ""]
    for i, idea in enumerate(ideas, 1):
        lines.append(
            f"<b>{i}.</b> [{idea.pillar}] skor {idea.rank_score:.0f}\n"
            f"    <i>{idea.angle}</i>\n"
            f"    Masalah: {idea.audience_problem[:90]}"
        )
    return CommandResult(True, command="/ideas", text="\n".join(lines))


def _cmd_draft(
    actor: Actor,
    args: str,
    msg_id: Optional[int],
    client=None,
    path=None,
) -> CommandResult:
    """Generate a preview. Never publishes."""
    from src.content.quality_gate import evaluate_draft
    from src.content.writer import write_draft

    pillar = args.strip() or None
    draft = write_draft(pillar=pillar, persist=False, client=client, path=path)
    if draft is None:
        return CommandResult(True, command="/draft", text=(
            "Tidak ada draf yang bisa dibuat — slot dilewati dengan aman.\n"
            "(Penulis tidak pernah mengisi dengan teks generik.)"
        ))
    decision = evaluate_draft(
        draft.content, evidence=draft.evidence, pillar=draft.pillar,
        require_critic=False, persist=False, client=client, path=path,
    )
    body = draft.content
    if len(body) > 700:
        body = body[:700] + "…"
    return CommandResult(True, command="/draft", text=(
        f"<b>Pratinjau draf</b> — {draft.pillar}\n\n"
        f"{body}\n\n"
        f"<b>Penilaian:</b> {decision.verdict} "
        f"(skor {decision.total_score:.0f})\n"
        f"<b>Persona:</b> <code>{draft.persona_version}</code>\n\n"
        "<i>Pratinjau saja — belum dipublikasikan.</i>"
    ))


def _cmd_schedule(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.adapters.telegram import format_schedule_for_operator

    return CommandResult(True, command="/schedule",
                         text=format_schedule_for_operator(path=path))


def _cmd_analytics(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.orchestration.analytics import coverage, pillar_performance

    days = 7
    raw = (args or "").strip().lower()
    if raw.endswith("d") and raw[:-1].isdigit():
        days = int(raw[:-1])
    elif raw.isdigit():
        days = int(raw)
    days = max(1, min(90, days))

    cov = coverage(days=days, path=path)
    perf = pillar_performance(days=days, path=path)
    lines = [f"<b>Analitik {days} hari</b>", "",
             f"Cakupan metrik: {cov.with_data}/{cov.posts} posting",
             f"<i>{cov.caveat}</i>", ""]
    if perf:
        lines.append("<b>Per pilar</b>")
        for pillar, data in sorted(perf.items()):
            views = data.get("avg_views")
            likes = data.get("avg_likes")
            lines.append(
                f"• {pillar}: {data['posts']} posting"
                + (f", ~{views:.0f} views" if views is not None else "")
                + (f", ~{likes:.0f} suka" if likes is not None else "")
            )
    else:
        lines.append("Belum ada metrik terverifikasi di rentang ini.")
    lines += ["", "<i>Angka hanya dari platform; tidak disimpulkan dari "
               "engagement Threads.</i>"]
    return CommandResult(True, command="/analytics", text="\n".join(lines))


def _cmd_products(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.commerce.catalog import catalog_summary, list_products

    summary = catalog_summary(path=path)
    lines = [
        "<b>Katalog produk</b>", "",
        f"Total: {summary['total']}",
        f"Siap komisi: <b>{summary['commission_ready']}</b>", "",
    ]
    for state, n in sorted(summary["by_state"].items()):
        lines.append(f"• {state}: {n}")
    verified = list_products(tracking_state="VERIFIED", path=path)
    if verified:
        lines += ["", "<b>Terverifikasi</b>"]
        for p in verified[:10]:
            lines.append(f"• {p.name} — <code>{p.tracking_status}</code>")
    lines += ["", "<i>Hanya link yang diverifikasi operator dari dashboard "
               "afiliasi resmi yang bisa dipakai.</i>"]
    return CommandResult(True, command="/products", text="\n".join(lines))


def _cmd_status(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.orchestration.analytics import health

    h = health(path=path)
    queue = h.get("queue", {})
    lines = [
        "<b>Status sistem</b>", "",
        f"Scheduler: <b>{'DIJEDA' if h['scheduler_paused'] else 'BERJALAN'}</b>",
        f"WIB: {h['checked_at']}", "",
        "<b>Antrean publikasi</b>",
    ]
    if queue:
        for state, n in sorted(queue.items()):
            lines.append(f"• {state}: {n}")
    else:
        lines.append("• kosong")
    age = h.get("oldest_pending_age_seconds")
    if age is not None:
        lines.append(f"• tertua: {age:.0f}s")
    cov = h.get("metric_coverage", {})
    if cov:
        lines += ["", f"Cakupan metrik: {cov.get('with_data', 0)}/"
                      f"{cov.get('posts', 0)}",
                  f"<i>{cov.get('caveat', '')}</i>"]
    return CommandResult(True, command="/status", text="\n".join(lines))


READONLY_HANDLERS = {
    "/persona": _cmd_persona,
    "/plan": _cmd_plan,
    "/ideas": _cmd_ideas,
    "/draft": _cmd_draft,
    "/schedule": _cmd_schedule,
    "/analytics": _cmd_analytics,
    "/products": _cmd_products,
    "/status": _cmd_status,
    "/help": None,  # handled specially
}


# ── Mutation commands ───────────────────────────────────────────────────────
def _cmd_approve(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.content.quality_gate import get_evaluations
    from src.core.storage import connect, transaction

    draft_id = (args or "").strip()
    if not draft_id.isdigit():
        return CommandResult(True, command="/approve", text=(
            "Format: <code>/approve &lt;id draf&gt;</code>"
        ))
    evals = get_evaluations(int(draft_id), path=path)
    if not evals:
        return CommandResult(True, command="/approve", text=(
            f"Draf <code>{draft_id}</code> belum dievaluasi."
        ))
    latest = evals[-1]
    if latest["verdict"] == "REJECTED":
        return CommandResult(True, command="/approve", text=(
            f"⛔ Draf <code>{draft_id}</code> ditolak oleh gate:\n"
            f"<code>{', '.join(latest['vetoes'])}</code>\n\n"
            "Veto keras tidak bisa di-override oleh persetujuan manual."
        ))
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_drafts SET status = 'APPROVED', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(draft_id),),
            )
    from src.core.events import emit

    emit("approve", object_type="draft", object_id=draft_id,
         actor_type="operator", actor_id=str(actor.user_id),
         reason=f"verdict was {latest['verdict']}", path=path)
    return CommandResult(True, command="/approve", text=(
        f"✅ Draf <code>{draft_id}</code> disetujui.\n"
        f"Penilaian: {latest['verdict']} "
        f"(skor {latest['total_score']:.0f})\n\n"
        "<i>Persetujuan tidak memublikasikan — draf masuk antrean.</i>"
    ))


def _cmd_revise(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.persona.editorial_memory import record_feedback

    parts = (args or "").strip().split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return CommandResult(True, command="/revise", text=(
            "Format: <code>/revise &lt;id draf&gt; &lt;alasan&gt;</code>"
        ))
    draft_id = parts[0]
    reason = parts[1].strip() if len(parts) > 1 else ""
    if not reason:
        return CommandResult(True, command="/revise", text=(
            "Alasan revisi wajib diisi — itu yang dipakai untuk memperbaiki "
            "draf berikutnya."
        ))
    record_feedback(draft_id=int(draft_id), actor_id=str(actor.user_id),
                    category="other", reason=reason, path=path)
    from src.core.events import emit

    emit("revise", object_type="draft", object_id=draft_id,
         actor_type="operator", actor_id=str(actor.user_id), reason=reason,
         path=path)
    return CommandResult(True, command="/revise", text=(
        f"✏️ Revisi dicatat untuk draf <code>{draft_id}</code>.\n\n"
        f"Alasan: <i>{redact_secrets(reason)[:200]}</i>\n\n"
        "Umpan balik ini masuk ke memori editorial dan dipakai untuk draf "
        "berikutnya."
    ))


def _cmd_reject(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.persona.editorial_memory import record_feedback
    from src.core.storage import connect, transaction

    parts = (args or "").strip().split(maxsplit=1)
    if not parts or not parts[0].isdigit():
        return CommandResult(True, command="/reject", text=(
            "Format: <code>/reject &lt;id draf&gt; &lt;alasan&gt;</code>"
        ))
    draft_id = parts[0]
    reason = parts[1].strip() if len(parts) > 1 else ""
    if not reason:
        return CommandResult(True, command="/reject", text=(
            "Alasan penolakan wajib diisi — itu yang dipakai memperbaiki "
            "seleksi berikutnya.\n\n"
            "Format: <code>/reject &lt;id draf&gt; &lt;alasan&gt;</code>"
        ))
    record_feedback(draft_id=int(draft_id), actor_id=str(actor.user_id),
                    category="other", reason=reason, path=path)
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_drafts SET status = 'REJECTED', "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(draft_id),),
            )
    from src.core.events import emit

    emit("reject", object_type="draft", object_id=draft_id,
         actor_type="operator", actor_id=str(actor.user_id), reason=reason,
         path=path)
    return CommandResult(True, command="/reject", text=(
        f"❌ Draf <code>{draft_id}</code> ditolak.\n\n"
        f"Alasan: <i>{redact_secrets(reason)[:200]}</i>\n\n"
        "Riwayat tidak dihapus — alasan ini dipakai untuk seleksi berikutnya."
    ))


def _cmd_pause(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.orchestration.scheduler import set_paused

    state = set_paused(True, path=path)
    from src.core.events import emit

    emit("pause", object_type="scheduler", actor_type="operator",
         actor_id=str(actor.user_id), path=path)
    return CommandResult(True, command="/pause", text=(
        f"⏸ Scheduler <b>{state}</b>.\n"
        "Tidak ada posting baru sampai <code>/resume</code>.\n"
        "Status ini tersimpan dan bertahan melewati restart."
    ))


def _cmd_resume(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.orchestration.scheduler import set_paused

    state = set_paused(False, path=path)
    from src.core.events import emit

    emit("resume", object_type="scheduler", actor_type="operator",
         actor_id=str(actor.user_id), path=path)
    return CommandResult(True, command="/resume", text=(
        f"▶️ Scheduler <b>{state}</b>."
    ))


def _cmd_autopost(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    """Explicit authorization. Never enabled implicitly."""
    raw = (args or "").strip().lower()
    if raw not in ("on", "off"):
        return CommandResult(True, command="/autopost", text=(
            "Format: <code>/autopost on</code> atau <code>/autopost off</code>\n\n"
            "<b>Perhatian:</b> menyalakan ini mengizinkan publikasi live ke "
            "Threads. Pastikan <code>DRY_RUN=false</code> juga sudah diatur, "
            "dan bahwa cutover sudah disetujui operator."
        ))
    from src.core.events import emit

    emit(f"autopost_{raw}", object_type="feature_flag",
         actor_type="operator", actor_id=str(actor.user_id), path=path)
    if raw == "on":
        return CommandResult(True, command="/autopost", text=(
            "⚠️ <b>Permintaan autopost ON tercatat.</b>\n\n"
            "Flag ini hidup di environment (<code>AUTOPUBLISH_ENABLED</code>), "
            "bukan di Telegram — bot tidak bisa mengubahnya. Ubah di "
            "<code>.env</code> lalu restart proses V2.\n\n"
            "Ini disengaja: mengaktifkan publikasi live lewat chat adalah "
            "kebocoran izit yang terlalu mudah terjadi."
        ))
    return CommandResult(True, command="/autopost", text=(
        "✅ Permintaan autopost OFF tercatat.\n"
        "Set <code>AUTOPUBLISH_ENABLED=false</code> di <code>.env</code>."
    ))


def _cmd_rollback(
    actor: Actor, args: str, msg_id: Optional[int], path=None
) -> CommandResult:
    from src.orchestration.recovery import is_safe_to_restart

    safe, why = is_safe_to_restart(path=path)
    from src.core.events import emit

    emit("rollback_check", object_type="system", actor_type="operator",
         actor_id=str(actor.user_id), reason=why, path=path)
    return CommandResult(True, command="/rollback", text=(
        "<b>Pemeriksaan rollback</b>\n\n"
        f"Aman untuk restart: <b>{'ya' if safe else 'tidak'}</b>\n"
        f"<i>{redact_secrets(why)}</i>\n\n"
        "Prosedur lengkap: <code>docs/v2/06-cutover-rollback.md</code>\n"
        "Rollback tidak menghapus data — ini restore dari backup terverifikasi."
    ))


MUTATION_HANDLERS = {
    "/approve": _cmd_approve,
    "/revise": _cmd_revise,
    "/reject": _cmd_reject,
    "/pause": _cmd_pause,
    "/resume": _cmd_resume,
    "/autopost": _cmd_autopost,
    "/rollback": _cmd_rollback,
}

ALL_COMMANDS = sorted(
    set(READONLY_HANDLERS) | set(MUTATION_HANDLERS) | {"/start"}
)


def _help_text() -> str:
    return (
        "<b>Affiliate Automation V2 — perintah</b>\n\n"
        "<b>Baca saja</b>\n"
        "/persona — identitas aktif & aturan suara\n"
        "/plan — rencana & ide slot berikutnya\n"
        "/ideas — kumpulan ide terkini\n"
        "/draft — pratinjau draf (tanpa publish)\n"
        "/schedule — jadwal 7 hari (WIB)\n"
        "/analytics [7d|30d] — metrik terverifikasi\n"
        "/products — katalog & status afiliasi\n"
        "/status — kesehatan, antrean, cakupan\n\n"
        "<b>Operator saja</b>\n"
        "/approve &lt;id&gt; — setujui draf\n"
        "/revise &lt;id&gt; &lt;alasan&gt; — minta revisi\n"
        "/reject &lt;id&gt; &lt;alasan&gt; — tolak & simpan alasan\n"
        "/pause · /resume — kendali scheduler\n"
        "/autopost on|off — otorisasi publikasi\n"
        "/rollback — pemeriksaan keamanan rollback\n\n"
        f"<i>{wib_display()}</i>"
    )


def v2_enabled() -> bool:
    return get_settings().flags.v2_telegram_commands_enabled


def _dispatch(handler, command, actor, args, msg_id, client=None, path=None):
    """Call a handler with only the arguments it declares.

    Handlers opt into `client` and `path` individually; introspecting the
    signature keeps the call sites uniform instead of a growing if/else chain.
    """
    import inspect

    try:
        params = inspect.signature(handler).parameters
    except (TypeError, ValueError):  # builtins / C callables
        return handler(actor, args, msg_id)

    kwargs = {}
    if "client" in params:
        kwargs["client"] = client
    if "path" in params:
        kwargs["path"] = path
    return handler(actor, args, msg_id, **kwargs)


def handle_v2_command(
    msg: dict,
    text: str,
    msg_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    sender: Optional[Sender] = None,
    path=None,
    client=None,
) -> CommandResult:
    """Route one message through the V2 command set.

    Returns `handled=False` when the message is not a V2 command, so the
    caller can fall through to the legacy handler untouched.
    """
    body = (text or "").strip()
    if not body.startswith("/"):
        return _not_handled()

    parts = body.split(maxsplit=1)
    command = parts[0].lower().split("@")[0]
    args = parts[1].strip() if len(parts) > 1 else ""

    if command not in ALL_COMMANDS:
        return _not_handled()

    if not v2_enabled():
        return _not_handled()

    actor = actor_from_message(msg)

    if command == "/help" or command == "/start":
        if not _is_known_chat(actor):
            return _not_handled()
        result = CommandResult(True, command=command, text=_help_text())
        _emit(result, actor, sender, path)
        return result

    if command_requires_operator(command):
        if not is_operator(actor):
            result = _denied(actor, command)
            _emit(result, actor, sender, path)
            return result
        try:
            require_operator(actor, command)
        except Exception:  # noqa: BLE001 — already handled above
            result = _denied(actor, command)
            _emit(result, actor, sender, path)
            return result

    if not _is_known_chat(actor):
        return _not_handled()

    handler = READONLY_HANDLERS.get(command) or MUTATION_HANDLERS.get(command)
    if handler is None:
        return _not_handled()

    try:
        result = _dispatch(handler, command, actor, args, msg_id,
                           client=client, path=path)
    except Exception as exc:  # noqa: BLE001 — a broken command must not kill the bot
        log.error("V2 command %s failed: %s", command, exc, exc_info=True)
        result = CommandResult(
            True, command=command,
            text=f"⚠️ Perintah gagal: {redact_secrets(str(exc))[:200]}",
        )

    _emit(result, actor, sender, path)
    return result


def _is_known_chat(actor: Actor) -> bool:
    from src.core.security import is_known_chat

    return is_known_chat(actor)


def _emit(
    result: CommandResult,
    actor: Actor,
    sender: Optional[Sender],
    path=None,
) -> None:
    if sender is not None and result.text:
        try:
            sender(result.text, None)
        except Exception as exc:  # noqa: BLE001
            log.warning("V2 sender failed: %s", exc)
    from src.core.events import emit

    try:
        emit(
            result.command.lstrip("/") if result.command else "unknown",
            object_type="telegram_command",
            actor_type="operator" if result.authorized else "denied",
            actor_id=str(actor.user_id or ""),
            reason=result.denied_reason,
            path=path,
        )
    except Exception:  # noqa: BLE001 — audit must never break the command
        log.debug("audit emit skipped", exc_info=True)
