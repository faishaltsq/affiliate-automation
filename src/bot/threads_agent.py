"""threads_agent.py — Conversational AI Operator for Threads & Affiliate Marketing (@pochita9887).

Handles Threads-specific slash commands, affiliate URL auto-commenting,
content creation, and strategic conversations.
"""

import json
import logging
import os
import re
import sqlite3
import requests
from typing import Optional

from src.config import (
    TELEGRAM_BOT_TOKEN,
    TG_AFFILIATE_CHAT_ID,
    TG_THREADS_THREAD_ID,
    LLM_API_KEY,
    LLM_MODEL,
    OPENROUTER_URL,
    DATA_DIR,
    PERSONA_FILE,
)
from src.affiliate.comment_engine import (
    check_and_comment_our_posts,
    comment_on_specific_thread,
    get_post_metrics,
)
from src.affiliate.queue_manager import enqueue, get_pending
from src.organic.db import (
    get_today_post_count,
    get_last_post_time,
    get_recent_posts,
    sync_with_threads_status,
)

logger = logging.getLogger("bot.threads_agent")
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# E-commerce affiliate URL patterns
AFFILIATE_PATTERNS = [
    r'https?://(?:www\.)?shopee\.(?:co\.id|com|vn|ph|th|my|sg)/[^\s]+',
    r'https?://(?:www\.)?tiktok\.com/[^\s]+',
    r'https?://vt\.tiktok\.com/[^\s]+',
    r'https?://(?:www\.)?tokopedia\.com/[^\s]+',
    r'https?://tokopedia\.link/[^\s]+',
    r'https?://(?:www\.)?lazada\.co\.id/[^\s]+',
    r'https?://s\.lazada\.co\.id/[^\s]+',
    r'https?://s\.shopee\.co\.id/[^\s]+',
]

# Threads post URL patterns
THREADS_PATTERNS = [
    r'https?://(?:www\.)?threads\.(?:net|com)/@[\w.]+/post/[\w-]+',
]


def _clean_text_for_telegram(text: str) -> str:
    """Strip internal reasoning/think tags and clean text."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    return text.strip()


def _send_reply(text: str, reply_to_message_id: Optional[int] = None, thread_id: Optional[int] = None):
    """Sends a formatted reply back to Telegram Threads topic with fallback."""
    target_thread = thread_id if thread_id is not None else TG_THREADS_THREAD_ID
    cleaned = _clean_text_for_telegram(text)
    if not cleaned:
        return

    payload = {
        "chat_id": int(TG_AFFILIATE_CHAT_ID),
        "text": cleaned,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if target_thread:
        payload["message_thread_id"] = int(target_thread)
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id

    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        if not r.ok and ("can't parse entities" in r.text.lower() or "bad request" in r.text.lower()):
            payload.pop("parse_mode", None)
            requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.error("Failed to send reply to Threads topic: %s", e)


def handle_threads_message(msg: dict, text: str, msg_id: int, thread_id: int):
    """Main router for messages inside Threads topic."""
    text_lower = text.lower().strip()

    # 1. Direct Slash Commands
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            _cmd_help(msg_id, thread_id)
            return
        elif cmd in ("/status", "/threads_status"):
            _cmd_status(msg_id, thread_id)
            return
        elif cmd == "/sync":
            _cmd_sync(msg_id, thread_id)
            return
        elif cmd == "/posts":
            _cmd_posts(msg_id, thread_id)
            return
        elif cmd == "/queue":
            _cmd_queue(msg_id, thread_id)
            return
        elif cmd == "/clearqueue":
            _cmd_clearqueue(msg_id, thread_id)
            return
        elif cmd == "/persona":
            _cmd_persona(msg_id, thread_id)
            return
        elif cmd == "/post":
            _cmd_post(msg_id, thread_id)
            return
        elif cmd == "/topic":
            _cmd_topic(args, msg_id, thread_id)
            return
        elif cmd == "/affiliate":
            _cmd_affiliate(args, msg_id, thread_id)
            return

    # 2. Check for affiliate URLs in text
    found_aff_urls = []
    for pat in AFFILIATE_PATTERNS:
        matches = re.findall(pat, text)
        found_aff_urls.extend(matches)

    found_threads_urls = []
    for pat in THREADS_PATTERNS:
        matches = re.findall(pat, text)
        found_threads_urls.extend(matches)

    if found_aff_urls:
        _handle_affiliate_urls(found_aff_urls[0], found_threads_urls, msg_id, thread_id)
        return

    # 3. Natural Language Intent Detection
    if any(k in text_lower for k in ["cek status", "status hari ini", "kuota threads", "kuota posting"]):
        _cmd_status(msg_id, thread_id)
        return
    elif text_lower.startswith("post tentang") or text_lower.startswith("buat thread tentang"):
        topic_arg = text.split(maxsplit=2)[-1].strip()
        _cmd_topic(topic_arg, msg_id, thread_id)
        return
    elif any(k in text_lower for k in ["post sekarang", "posting sekarang", "force post"]):
        _cmd_post(msg_id, thread_id)
        return
    elif any(k in text_lower for k in ["sync threads", "sinkronkan data", "sinkron threads"]):
        _cmd_sync(msg_id, thread_id)
        return

    # 4. Conversational AI Strategist Mode
    _chat_with_threads_agent(text, msg_id, thread_id)


def _cmd_help(msg_id: int, thread_id: int):
    msg = (
        "🤖 <b>Threads &amp; Affiliate Operator (@pochita9887)</b>\n\n"
        "Kamu bisa <b>chat santai</b> untuk diskusi tren, strategi, minta ide, atau drafting konten!\n\n"
        "<b>📊 Monitoring &amp; Sinkronisasi</b>\n"
        "/status — Ringkasan kuota hari ini, post terakhir, queue\n"
        "/sync — Sinkronisasi postingan aktif dengan Threads API\n"
        "/posts — 5 postingan terbaru &amp; metrik likes/replies\n"
        "/queue — Daftar link affiliate pending\n\n"
        "<b>📝 Konten &amp; Eksekusi</b>\n"
        "/post — Force posting 1 konten organik sekarang\n"
        "/topic &lt;judul&gt; — Buat &amp; posting topik spesifik\n"
        "/affiliate &lt;url&gt; — Tempel link affiliate di konten viral\n"
        "/clearqueue — Bersihkan antrian affiliate\n\n"
        "<b>⚙️ Konfigurasi</b>\n"
        "/persona — Lihat konfigurasi persona akun aktif\n\n"
        "💡 <i>Kirim link Shopee/Tokopedia langsung untuk auto-monetize!</i>"
    )
    _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_status(msg_id: int, thread_id: int):
    try:
        count = get_today_post_count()
        last = get_last_post_time() or "Belum ada"
        pending = get_pending()
        recent = get_recent_posts(1)
        last_topic = recent[0]["topic"][:50] if recent else "-"
        msg = (
            f"📊 <b>Status Akun &amp; Pipeline Threads</b>\n\n"
            f"📝 Post aktif hari ini: <b>{count}/12</b>\n"
            f"🕐 Post terakhir: <code>{last}</code>\n"
            f"📌 Topik terakhir: <i>{last_topic}</i>\n"
            f"⏳ Antrian affiliate: <b>{len(pending)}</b> pending"
        )
        _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_sync(msg_id: int, thread_id: int):
    try:
        res = sync_with_threads_status()
        cur_count = get_today_post_count()
        msg = (
            f"🔄 <b>Sinkronisasi Threads Berhasil</b>\n\n"
            f"✅ Post aktif di Threads: <b>{res['alive']}</b>\n"
            f"🗑️ Post dihapus dibersihkan dari DB: <b>{res['deleted']}</b>\n"
            f"📊 Kuota aktif hari ini: <b>{cur_count}/12</b>"
        )
        _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Sync error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_posts(msg_id: int, thread_id: int):
    try:
        posts = get_recent_posts(5)
        if not posts:
            _send_reply("Belum ada postingan.", reply_to_message_id=msg_id, thread_id=thread_id)
            return
        msg_out = "📋 <b>5 Postingan Terbaru &amp; Performa</b>\n\n"
        for p in posts:
            m = get_post_metrics(p["threads_id"])
            msg_out += (
                f"🆔 <code>{p['threads_id']}</code>\n"
                f"💬 <i>{p['topic'][:45]}</i>\n"
                f"❤️ <b>{m.get('likes',0)}</b> likes · 💬 <b>{m.get('replies',0)}</b> replies\n\n"
            )
        _send_reply(msg_out, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_queue(msg_id: int, thread_id: int):
    try:
        pending = get_pending()
        if not pending:
            _send_reply("✅ Antrian kosong. Tidak ada affiliate pending.", reply_to_message_id=msg_id, thread_id=thread_id)
            return
        msg_out = f"⏳ <b>{len(pending)} Antrian Affiliate Pending</b>\n\n"
        for item in pending:
            msg_out += f"• #{item['id']} — <code>{item['affiliate_url'][:55]}</code>\n"
        _send_reply(msg_out, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_clearqueue(msg_id: int, thread_id: int):
    try:
        conn = sqlite3.connect(DATA_DIR / "posts.db")
        conn.execute("UPDATE affiliate_queue SET status='cancelled' WHERE status='pending'")
        conn.commit()
        conn.close()
        _send_reply("🗑 Semua antrian affiliate pending dibatalkan.", reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_persona(msg_id: int, thread_id: int):
    try:
        with open(PERSONA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        p = data.get("persona", {})
        msg = (
            f"👤 <b>Persona Aktif Threads:</b>\n\n"
            f"• <b>Nama:</b> {p.get('name')}\n"
            f"• <b>Handle:</b> @{p.get('handle')}\n"
            f"• <b>Archetype:</b> {p.get('archetype')}\n"
            f"• <b>Tone:</b> {p.get('tone')}\n"
            f"• <b>Bio:</b> <i>\"{p.get('bio')}\"</i>"
        )
        _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_post(msg_id: int, thread_id: int):
    _send_reply("⚡ Mengambil tren terkini & memposting ke Threads...", reply_to_message_id=msg_id, thread_id=thread_id)
    try:
        from src.organic.publisher import execute_organic_post
        result = execute_organic_post()
        if result.get("skipped"):
            _send_reply(f"⏭ Dilewati: {result.get('reason')}", reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            msg = (
                f"✅ <b>Berhasil Diposting ke Threads!</b>\n\n"
                f"📌 <b>Topik:</b> <i>{result.get('topic','')}</i>\n"
                f"💬 <b>Konten:</b>\n<i>\"{result.get('content','')}\"</i>\n\n"
                f"🆔 <b>Threads ID:</b> <code>{result.get('threads_id','')}</code>"
            )
            _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_topic(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply("Format: <code>/topic &lt;topik atau berita&gt;</code>\nContoh: <code>/topic kenaikan pajak hiburan</code>", reply_to_message_id=msg_id, thread_id=thread_id)
        return
    _send_reply(f"⚡ Sedang generate & memposting tentang: <i>{args}</i>...", reply_to_message_id=msg_id, thread_id=thread_id)
    try:
        from src.organic.publisher import execute_organic_post_custom
        result = execute_organic_post_custom(args)
        msg = (
            f"✅ <b>Berhasil Diposting ke Threads!</b>\n\n"
            f"📌 <b>Topik:</b> <i>{args}</i>\n"
            f"💬 <b>Konten:</b>\n<i>\"{result.get('content','')}\"</i>\n\n"
            f"🆔 <b>Threads ID:</b> <code>{result.get('threads_id','')}</code>"
        )
        _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_affiliate(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply("Format: <code>/affiliate &lt;link shopee/tokopedia/tiktok&gt;</code>", reply_to_message_id=msg_id, thread_id=thread_id)
        return
    _send_reply(f"🔍 Mengecek postingan viral untuk ditempeli link affiliate...\n<code>{args[:60]}</code>", reply_to_message_id=msg_id, thread_id=thread_id)
    try:
        results = check_and_comment_our_posts(args, likes_threshold=20, replies_threshold=10)
        commented = [r for r in results if r.get("status") == "commented"]
        if commented:
            msg_out = f"🚀 <b>Ditempel di {len(commented)} postingan viral!</b>\n\n"
            for c in commented:
                msg_out += f"📌 {c['likes']} likes · {c['replies']} replies\n💬 <i>\"{c['comment'][:80]}\"</i>\n\n"
            _send_reply(msg_out, reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            row_id = enqueue(args)
            _send_reply(
                f"⏳ Belum ada postingan yang tembus threshold (≥20 likes / ≥10 replies).\n\n"
                f"✅ Link disimpan di antrian #{row_id} — daemon akan pasang otomatis begitu ada konten viral!",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_affiliate_urls(aff_url: str, found_threads_urls: list[str], msg_id: int, thread_id: int):
    """Handle affiliate URLs directly posted in conversation."""
    if found_threads_urls:
        target_thread = found_threads_urls[0]
        _send_reply(
            f"🎯 <b>Mendeteksi Target Thread Spesifik!</b>\n"
            f"🔗 <b>Target:</b> {target_thread}\n"
            f"🛒 <b>Produk:</b> {aff_url}\n\n"
            f"<i>Sedang menganalisis isi thread & membuat komentar kontekstual...</i>",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
        res = comment_on_specific_thread(target_thread, aff_url)
        if res.get("status") == "commented":
            _send_reply(
                f"✅ <b>Komentar Berhasil Ditempel!</b>\n\n"
                f"📝 <b>Komentar:</b>\n<i>\"{res.get('comment')}\"</i>\n\n"
                f"🆔 Reply ID: <code>{res.get('reply_id')}</code>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
        else:
            _send_reply(
                f"⚠️ <b>Gagal Menempel Komentar:</b> {res.get('message', 'Unknown error')}",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
        return

    _send_reply(
        f"🔍 <b>Menerima Link Affiliate!</b>\n"
        f"🛒 <b>Link:</b> {aff_url}\n\n"
        f"<i>Mengecek postingan kita yang tembus threshold (≥20 likes atau ≥10 komentar)...</i>",
        reply_to_message_id=msg_id,
        thread_id=thread_id,
    )
    results = check_and_comment_our_posts(affiliate_url=aff_url, likes_threshold=20, replies_threshold=10)
    commented = [r for r in results if r.get("status") == "commented"]
    if commented:
        msg_out = f"🚀 <b>Ditempel di {len(commented)} Postingan Ramai!</b>\n\n"
        for c in commented:
            msg_out += (
                f"📌 <b>Post ({c['likes']} likes, {c['replies']} replies):</b>\n"
                f"<i>\"{c['post_text']}\"</i>\n"
                f"💬 <b>Komentar:</b>\n<i>\"{c['comment']}\"</i>\n"
                f"🆔 Reply ID: <code>{c['reply_id']}</code>\n\n"
            )
        _send_reply(msg_out, reply_to_message_id=msg_id, thread_id=thread_id)
    else:
        row_id = enqueue(aff_url)
        _send_reply(
            f"⏳ <b>Belum ada postingan yang tembus target (≥20 likes / ≥10 komentar).</b>\n\n"
            f"📌 Link disimpan di <b>Antrian Affiliate</b> (ID: #{row_id}).\n"
            f"🤖 Daemon akan otomatis menempel komentar begitu ada postingan yang mencapai syarat!",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )


def _chat_with_threads_agent(user_message: str, msg_id: int, thread_id: int):
    """Conversational AI mode: Answer user questions like a social media strategist for Threads."""
    try:
        post_count = get_today_post_count()
        recent = get_recent_posts(3)
        pending = get_pending()

        recent_summary = ""
        for p in recent:
            recent_summary += f"- {p.get('topic')}: \"{p.get('content','')[:60]}...\"\n"

        system_prompt = (
            f"Kamu adalah Apex — Social Media Growth Specialist & AI Operator untuk akun Threads Indonesia (@pochita9887).\n"
            f"Karaktermu:\n"
            f"- Sangat paham tren sosmed Indonesia, algoritma Threads Meta, strategi engagement, dan affiliate marketing.\n"
            f"- Cerdas, santai, solutif, to the point, gaya bahasa asik (campuran santai dan pro).\n"
            f"- Kamu mengoperasikan pipeline otomatis yang memposting 10-12 konten organik per hari dan menempel komentar affiliate di konten viral.\n\n"
            f"Kondisi akun saat ini:\n"
            f"- Postingan hari ini: {post_count}/12\n"
            f"- Antrian affiliate: {len(pending)} pending\n"
            f"- 3 Postingan terakhir:\n{recent_summary or 'Belum ada'}\n\n"
            f"Jika user bertanya atau berdiskusi (ide konten, strategi, evaluasi, cara ningkatin views, dll), jawab actionable, cerdas, dan santai.\n"
            f"Jika user minta bikinin draft konten, berikan 2-3 opsi draft tajam (statement/insight/reaksi, bukan cuma pertanyaan).\n"
            f"Balas dalam Bahasa Indonesia. Jangan terlalu panjang."
        )

        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.7,
            "max_tokens": 1500,
            "stream": False,
        }
        r = requests.post(OPENROUTER_URL, json=payload, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        choice = data.get("choices", [{}])[0]
        msg_obj = choice.get("message", {})
        raw = msg_obj.get("content") or msg_obj.get("reasoning_content") or ""
        reply_text = _clean_text_for_telegram(raw)
        if not reply_text:
            reply_text = "Siap! Ada ide konten atau link affiliate yang mau di-generate? Ketik /draft atau langsung kirim tautannya."
        _send_reply(reply_text, reply_to_message_id=msg_id, thread_id=thread_id)

    except Exception as e:
        logger.error("Chat agent error in Threads topic: %s", e)
        _send_reply(f"⚠️ Maaf, ada kendala saat memproses pesanmu: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
