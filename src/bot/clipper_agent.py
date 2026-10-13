"""clipper_agent.py — Dedicated Telegram Bot Agent for Thread 246 (✂️ Clipper Automate)."""

import os
import re
import logging
import threading
from typing import Optional
from pathlib import Path
import requests
from dotenv import dotenv_values

from src.clipper.client import ClippoClient
from src.clipper.brief_analyzer import analyze_campaign_brief
from src.clipper.pipeline import run_clipper_pipeline
from src.clipper.account_manager import list_accounts, get_account, set_account_active, register_account, get_next_account_name

logger = logging.getLogger("bot.clipper_agent")

HERMES_HOME = os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes"))
_h_env = dotenv_values(Path(HERMES_HOME) / ".env")
_p_env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
_ALL_ENV = {**_h_env, **_p_env}

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or _ALL_ENV.get("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.environ.get("TG_AFFILIATE_CHAT_ID", "-1003757350269")
CLIPPER_THREAD_ID = int(os.environ.get("TG_CLIPPER_THREAD_ID", "246"))

_active_tasks: dict[str, bool] = {}


def _send_reply(text: str, reply_to_msg_id: Optional[int] = None, thread_id: Optional[int] = None):
    """Send HTML reply to Telegram Clipper Topic."""
    if not BOT_TOKEN:
        logger.warning("No BOT_TOKEN configured")
        return

    tid = thread_id if thread_id is not None else CLIPPER_THREAD_ID
    payload = {
        "chat_id": int(CHAT_ID),
        "message_thread_id": tid,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_to_msg_id:
        payload["reply_to_message_id"] = reply_to_msg_id

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=10,
        )
        if not r.ok:
            logger.warning("Telegram send failed (%d): %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.error("Error sending Telegram reply: %s", e)


def _cmd_campaigns(msg_id: int, thread_id: int):
    """List active Clippo campaigns sorted by CPM and budget."""
    _send_reply("🔍 Mengambil daftar campaign aktif dari Clippo...", reply_to_msg_id=msg_id, thread_id=thread_id)
    try:
        client = ClippoClient()
        campaigns = client.get_campaigns(limit=10)
        if not campaigns:
            _send_reply("⚠️ Tidak ada campaign aktif yang ditemukan.", reply_to_msg_id=msg_id, thread_id=thread_id)
            return

        lines = ["🔥 <b>Daftar Campaign Clippo Aktif:</b>\n"]
        for idx, c in enumerate(campaigns[:8], 1):
            cpm_str = f"Rp {c['cpm']:,}" if c.get("cpm") else "N/A"
            budget_str = f"{c['budget_percentage']:.1f}%"
            cid = c.get("id")
            lines.append(
                f"{idx}. <b>{c.get('title', 'Tanpa Judul')}</b>\n"
                f"   💰 CPM: <code>{cpm_str} / 1k views</code> | Sisa Budget: <b>{budget_str}</b>\n"
                f"   🏷 ID: <code>{cid}</code>\n"
                f"   👉 <i>Ketik /brief {cid} untuk lihat syarat & bahan</i>\n"
            )

        lines.append("💡 <i>Ketik /clip &lt;ID&gt; untuk langsung download, render & upload video.</i>")
        _send_reply("\n".join(lines), reply_to_msg_id=msg_id, thread_id=thread_id)
    except Exception as e:
        logger.exception("Error in _cmd_campaigns: %s", e)
        _send_reply(f"❌ Gagal mengambil campaign: {e}", reply_to_msg_id=msg_id, thread_id=thread_id)


def _cmd_brief(campaign_id: str, msg_id: int, thread_id: int):
    """Show detailed AI analysis of a specific campaign."""
    _send_reply(f"🤖 Menganalisis brief campaign <code>{campaign_id}</code>...", reply_to_msg_id=msg_id, thread_id=thread_id)
    try:
        client = ClippoClient()
        detail = client.get_campaign_detail(campaign_id)
        if not detail or not detail.get("title"):
            _send_reply(f"⚠️ Campaign <code>{campaign_id}</code> tidak ditemukan.", reply_to_msg_id=msg_id, thread_id=thread_id)
            return

        analysis = analyze_campaign_brief(detail)

        hooks_text = "\n".join(f"  • <i>\"{h}\"</i>" for h in analysis.get("winning_hooks", [])[:3])
        reqs_text = "\n".join(f"  ✅ {r}" for r in analysis.get("key_requirements", [])[:4])
        donts_text = "\n".join(f"  ❌ {d}" for d in analysis.get("forbidden_items", [])[:4])
        assets_text = "\n".join(f"  📹 <a href='{a['url']}'>{a.get('display_name') or 'Bahan Video'}</a>" for a in detail.get("assets", [])[:4])

        msg = (
            f"📋 <b>Brief & Aturan: {detail.get('title')}</b>\n\n"
            f"💰 <b>CPM:</b> Rp {detail.get('cpm', 0):,} / 1k views (Sisa: {detail.get('budget_percentage', 100):.1f}%)\n"
            f"⏱ <b>Durasi Klip:</b> {detail.get('min_duration', 15)}-{detail.get('max_duration', 180)} detik\n"
            f"🏷 <b>Hashtags:</b> {' '.join(detail.get('hashtags', []))}\n\n"
            f"💡 <b>Ide Winning Hook (0-3s):</b>\n{hooks_text or '  • Langsung ke produk'}\n\n"
            f"📌 <b>Syarat Mutlak (Do's):</b>\n{reqs_text or '  ✅ Ikuti panduan'}\n\n"
            f"🚫 <b>Dilarang (Don'ts):</b>\n{donts_text or '  ❌ Reupload tanpa edit'}\n\n"
            f"🎬 <b>Bahan Footage:</b>\n{assets_text or '  • Tersedia di platform'}\n\n"
            f"⚡ <i>Untuk langsung eksekusi, ketik:</i>\n<code>/clip {campaign_id}</code>"
        )
        _send_reply(msg, reply_to_msg_id=msg_id, thread_id=thread_id)
    except Exception as e:
        logger.exception("Error in _cmd_brief: %s", e)
        _send_reply(f"❌ Gagal analisis brief: {e}", reply_to_msg_id=msg_id, thread_id=thread_id)


def _cmd_accounts(msg_id: int, thread_id: int):
    """List all configured TikTok accounts."""
    accs = list_accounts()
    if not accs:
        _send_reply("⚠️ Belum ada akun TikTok terdaftar. Gunakan <code>/addaccount &lt;nama&gt;</code>.", reply_to_msg_id=msg_id, thread_id=thread_id)
        return

    lines = ["👥 <b>Daftar Akun TikTok Clipper:</b>\n"]
    for idx, a in enumerate(accs, 1):
        status = "🟢 AKTIF" if (a["active"] and a["has_cookies"]) else ("🟡 COOKIES KOSONG" if not a["has_cookies"] else "⚪ NONAKTIF")
        lines.append(
            f"{idx}. <b>{a['username']}</b> ({a['display_name']})\n"
            f"   Status: <b>{status}</b> | Style: <code>{a['style']}</code>\n"
            f"   Folder: <code>data/tiktok/accounts/{a['name']}</code>\n"
        )
    lines.append(
        "💡 <i>Kontrol akun:</i>\n"
        "• <code>/activate &lt;nama&gt;</code> — Aktifkan akun\n"
        "• <code>/deactivate &lt;nama&gt;</code> — Nonaktifkan akun\n"
        "• <code>/addaccount &lt;nama&gt;</code> — Daftarkan akun baru"
    )
    _send_reply("\n".join(lines), reply_to_msg_id=msg_id, thread_id=thread_id)


def _cmd_clip_async(campaign_id: str, source_url: Optional[str], target_accounts: Optional[list[str]], msg_id: int, thread_id: int):
    """Run clipper pipeline in background thread."""
    def worker():
        def progress_cb(text: str):
            _send_reply(text, thread_id=thread_id)

        try:
            res = run_clipper_pipeline(
                campaign_id=campaign_id,
                source_url=source_url,
                target_accounts=target_accounts,
                progress_callback=progress_cb,
            )
            if res.get("success"):
                upload_lines = []
                for u in res.get("upload_results", []):
                    icon = "✅" if u.get("success") else "❌"
                    upload_lines.append(f"{icon} <b>{u.get('account')}:</b>\n   <i>\"{u.get('caption', '')[:70]}...\"</i>")

                upload_summary = "\n".join(upload_lines)
                _send_reply(
                    f"🎉 <b>Klip Sukses Dibuat & Terunggah!</b>\n\n"
                    f"📌 <b>Campaign:</b> {res.get('title')}\n"
                    f"💰 <b>Potensi CPM:</b> Rp {res.get('cpm', 0):,} / 1k views\n"
                    f"👥 <b>Hasil Upload ({res.get('success_count')}/{res.get('total_accounts')} akun):</b>\n"
                    f"{upload_summary}\n\n"
                    f"📝 <b>Hashtags:</b> {' '.join(res.get('hashtags', []))}\n"
                    f"👉 <i>Salin link video dari akun di atas dan submit di app.clippo.id/campaigns/{campaign_id}!</i>",
                    thread_id=thread_id,
                )
            else:
                _send_reply(f"⚠️ <b>Pipeline Gagal:</b> {res.get('error')}", thread_id=thread_id)
        except Exception as e:
            logger.exception("Worker error: %s", e)
            _send_reply(f"❌ <b>Error:</b> <code>{e}</code>", thread_id=thread_id)

    t = threading.Thread(target=worker, daemon=True, name=f"clipper-{campaign_id}")
    t.start()


def handle_clipper_message(msg: dict, text: str, msg_id: int, thread_id: int):
    """Main message handler for Clipper Topic (246)."""
    t_clean = text.strip()
    t_lower = t_clean.lower()

    if t_lower.startswith("/start") or t_lower == "/help":
        help_msg = (
            "✂️ <b>Clipper Automate Agent (Clippo.id & Creators)</b>\n\n"
            "Bot ini otomatis mengambil campaign berbayar dari <b>Clippo.id</b>, menganalisis brief & aturan do's/don'ts, merender 9:16 vertical clip, dan mengunggahnya ke semua akun TikTok aktif dengan <b>caption unik per akun!</b>\n\n"
            "<b>Campaign:</b>\n"
            "• <code>/campaigns</code> — Lihat daftar campaign aktif (CPM & sisa budget)\n"
            "• <code>/brief [id]</code> — Analisis AI syarat, do's & don'ts, serta ide hook\n"
            "• <code>/clip [id]</code> — Upload ke semua akun aktif\n"
            "• <code>/clip [id] --account nama1,nama2</code> — Upload ke akun tertentu\n\n"
            "<b>Akun TikTok:</b>\n"
            "• <code>/accounts</code> — Lihat semua akun & statusnya\n"
            "• <code>/activate [nama]</code> — Aktifkan akun\n"
            "• <code>/deactivate [nama]</code> — Nonaktifkan akun\n"
            "• <code>/addaccount [nama]</code> — Daftarkan akun baru\n\n"
            "Kamu juga bisa ngobrol santai seputar strategi clipping!"
        )
        _send_reply(help_msg, reply_to_msg_id=msg_id, thread_id=thread_id)

    elif t_lower.startswith("/accounts") or "daftar akun" in t_lower:
        _cmd_accounts(msg_id, thread_id)

    elif t_lower.startswith("/activate ") or t_lower.startswith("/deactivate "):
        parts = t_clean.split()
        is_activate = parts[0].lower() == "/activate"
        if len(parts) > 1:
            acc_name = parts[1]
            ok = set_account_active(acc_name, is_activate)
            status_str = "diaktifkan ✅" if is_activate else "dinonaktifkan ⚪"
            _send_reply(
                f"Akun <code>{acc_name}</code> berhasil {status_str}." if ok
                else f"⚠️ Akun <code>{acc_name}</code> tidak ditemukan. Cek <code>/accounts</code>.",
                reply_to_msg_id=msg_id, thread_id=thread_id
            )
        else:
            cmd = "/activate" if is_activate else "/deactivate"
            _send_reply(f"Gunakan <code>{cmd} &lt;nama_akun&gt;</code>.", reply_to_msg_id=msg_id, thread_id=thread_id)

    elif t_lower.startswith("/addaccount"):
        parts = t_clean.split()
        acc_name = parts[1] if len(parts) > 1 else get_next_account_name()
        info = register_account(acc_name)
        _send_reply(
            f"✅ Folder akun <code>{acc_name}</code> berhasil disiapkan!\n\n"
            f"Silakan letakkan cookies akunmu di:\n"
            f"<code>{info['cookies_path']}</code>\n\n"
            f"Setelah itu aktifkan dengan:\n<code>/activate {acc_name}</code>",
            reply_to_msg_id=msg_id, thread_id=thread_id
        )

    elif t_lower.startswith("/campaigns") or "daftar campaign" in t_lower or "list campaign" in t_lower:
        _cmd_campaigns(msg_id, thread_id)

    elif t_lower.startswith("/brief"):
        parts = t_clean.split()
        if len(parts) > 1:
            _cmd_brief(parts[1], msg_id, thread_id)
        else:
            _send_reply("Ketik <code>/brief [campaign_id]</code>. Contoh: <code>/brief cmv0h59il0kfbcv26ffgw79ut</code>", reply_to_msg_id=msg_id, thread_id=thread_id)

    elif t_lower.startswith("/clip"):
        parts = t_clean.split()
        if len(parts) > 1:
            cid = parts[1]
            remaining = parts[2:]

            # Parse --account flag
            target_accounts = None
            source_url = None
            if "--account" in remaining:
                idx = remaining.index("--account")
                if idx + 1 < len(remaining):
                    target_accounts = remaining[idx + 1].split(",")
                    remaining = remaining[:idx] + remaining[idx + 2:]
            elif "--all" in remaining:
                target_accounts = None
                remaining = [x for x in remaining if x != "--all"]

            # Remaining non-flag arg = source URL
            url_args = [x for x in remaining if not x.startswith("--")]
            if url_args:
                source_url = url_args[0]

            _cmd_clip_async(cid, source_url, target_accounts, msg_id, thread_id)
        else:
            _send_reply(
                "Ketik <code>/clip [campaign_id]</code> untuk upload ke semua akun aktif.\n"
                "Atau pilih akun: <code>/clip [id] --account nama1,nama2</code>",
                reply_to_msg_id=msg_id, thread_id=thread_id
            )

    else:
        # Check if text contains a clippo URL
        m = re.search(r"app\.clippo\.id/campaigns/([a-zA-Z0-9]+)", t_clean)
        if m:
            _cmd_brief(m.group(1), msg_id, thread_id)
        else:
            _send_reply(
                "✂️ Mau mulai clipping? Ketik <code>/campaigns</code> untuk melihat peluang cuan tertinggi hari ini, atau <code>/clip [id]</code> untuk eksekusi ke semua akun!",
                reply_to_msg_id=msg_id,
                thread_id=thread_id,
            )
