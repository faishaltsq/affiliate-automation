"""threads_agent.py — Conversational AI Operator for Threads & Affiliate Marketing (@pochita9887).

Handles Threads-specific slash commands, affiliate URL auto-commenting,
content creation, and strategic conversations.
"""

import json
import logging
import os
import re
import sqlite3
import time
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
from src.organic.personas import (
    get_persona,
    list_personas,
    get_active_manual_persona,
    set_active_manual_persona,
    extract_persona_tag,
)
from src.trends.multi_source import get_source_summary

logger = logging.getLogger("bot.threads_agent")
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# E-commerce affiliate URL patterns
AFFILIATE_PATTERNS = [
    r'https?://(?:[a-zA-Z0-9-]+\.)*shopee\.(?:co\.id|com|vn|ph|th|my|sg)/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*shp\.ee/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*shope\.ee/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*tiktok\.com/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*tokopedia\.com/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*tokopedia\.link/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*lazada\.(?:co\.id|com)/[^\s]+',
    r'https?://(?:[a-zA-Z0-9-]+\.)*blibli\.com/[^\s]+',
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


DRAFTS_FILE = DATA_DIR / "pending_threads_drafts.json"


def _load_drafts() -> dict:
    if DRAFTS_FILE.exists():
        try:
            return json.loads(DRAFTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_drafts(drafts: dict):
    try:
        DRAFTS_FILE.write_text(json.dumps(drafts, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save drafts: %s", e)


def _infer_pillar(topic: str) -> str:
    t = topic.lower()

    # 1. High-confidence keyword matching
    disaster_keywords = [
        "gempa", "banjir", "longsor", "bencana", "bmkg", "bnpb", "bpbd",
        "cuaca", "hujan", "badai", "angin kencang", "puting beliung", "erupsi",
        "gunung", "kebakaran", "karhutla", "asap", "kabut", "polusi", "ispu",
        "ispa", "kekeringan", "tsunami", "korban", "evakuasi", "darurat",
        "waspada", "krisis", "hutan", "kalimantan", "sumatera", "riau", "jambi",
        "korupsi", "kpk", "polisi", "tni", "kecelakaan"
    ]
    if any(k in t for k in disaster_keywords):
        return "breaking_disaster"

    football_keywords = [
        "bola", "sepakbola", "sepak bola", "timnas", "garuda", "pssi", "liga",
        "pemain", "gol", "juara", "skor", "madrid", "arsenal", "persib",
        "persija", "liverpool", "chelsea", "mu", "manchester", "barcelona",
        "ucl", "epl", "transfer", "stadion", "shin tae-yong", "pelatih",
        "striker", "klasemen", "derby", "motm", "offside", "wasit", "fc"
    ]
    if any(k in t for k in football_keywords):
        return "football_sports"

    market_keywords = [
        "saham", "ihsg", "bursa", "dividen", "investasi", "emiten", "fed",
        "wall street", "rupiah", "dolar", "inflasi", "reuters", "bloomberg",
        "ojk", "pinjol", "bank", "keuangan", "nasdaq", "s&p", "porto",
        "cuan", "cut loss", "reksadana", "obligasi", "crypto", "bitcoin",
        "ekonomi", "anggaran", "apbn", "bisnis"
    ]
    if any(k in t for k in market_keywords):
        return "market_finance_stocks"

    tech_keywords = [
        "ai", "teknologi", "gadget", "hp", "smartphone", "iphone", "samsung",
        "xiaomi", "nvidia", "chip", "semikonduktor", "android", "ios", "laptop",
        "software", "aplikasi", "robot", "openai", "gemini", "chatgpt",
        "deepseek", "claude", "gpu", "komputer", "layoff", "startup"
    ]
    if any(k in t for k in tech_keywords):
        return "tech_gadget_ai"

    # 2. Fast semantic categorization via LLM if ambiguous
    try:
        from src.brief.generator import _llm
        classify_prompt = (
            f"Klasifikasikan topik ini ke salah satu dari 4 kategori berikut:\n"
            f"- breaking_disaster (bencana alam, cuaca ekstrem, karhutla/asap, kecelakaan, krisis, hukum)\n"
            f"- football_sports (sepak bola, olahraga, atlet, liga)\n"
            f"- market_finance_stocks (saham, pasar modal, ekonomi, keuangan, bisnis)\n"
            f"- tech_gadget_ai (teknologi, AI, gadget, inovasi, sains)\n\n"
            f"Topik: \"{topic}\"\n"
            f"Jawab HANYA dengan nama salah satu kategori di atas, tanpa teks tambahan."
        )
        cat = _llm([{"role": "user", "content": classify_prompt}], temperature=0.1, max_tokens=20).strip().lower()
        for valid in ["breaking_disaster", "football_sports", "market_finance_stocks", "tech_gadget_ai"]:
            if valid in cat:
                return valid
    except Exception as e:
        logger.warning("Fast LLM pillar classification error: %s", e)

    return "breaking_disaster"


def _send_reply(
    text: str,
    reply_to_message_id: Optional[int] = None,
    thread_id: Optional[int] = None,
    reply_markup: Optional[dict] = None,
):
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
        "allow_sending_without_reply": True,
    }
    if target_thread:
        payload["message_thread_id"] = int(target_thread)
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        if not r.ok and ("can't parse entities" in r.text.lower() or "bad request" in r.text.lower()):
            payload.pop("parse_mode", None)
            requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.error("Failed to send reply to Threads topic: %s", e)


def _send_photo_with_markup(
    image_url: str,
    caption: str,
    reply_markup: Optional[dict] = None,
    thread_id: Optional[int] = None,
    reply_to_message_id: Optional[int] = None,
):
    """Sends a photo with optional inline keyboard buttons to Telegram Threads topic."""
    target_thread = thread_id if thread_id is not None else TG_THREADS_THREAD_ID
    cleaned_caption = _clean_text_for_telegram(caption)

    # 1. Direct file upload (100% reliable, bypasses Telegram external URL download blocks).
    # The caller's image always wins; the local card is only a fallback, otherwise
    # editorial_latest.jpg from an earlier post would be shipped on every send.
    photo_bytes = None
    if image_url and image_url.startswith("http"):
        try:
            resp = requests.get(image_url, timeout=10)
            if resp.ok and len(resp.content) > 1000:
                photo_bytes = resp.content
        except Exception:
            photo_bytes = None

    if not photo_bytes:
        local_card = DATA_DIR / "editorial_latest.jpg"
        if local_card.exists():
            try:
                photo_bytes = local_card.read_bytes()
            except Exception:
                photo_bytes = None

    data = {
        "chat_id": int(TG_AFFILIATE_CHAT_ID),
        "caption": cleaned_caption[:1024],
        "parse_mode": "HTML",
        "allow_sending_without_reply": True,
    }
    if target_thread:
        data["message_thread_id"] = int(target_thread)
    if reply_to_message_id:
        data["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)

    success = False
    if photo_bytes:
        try:
            files = {"photo": ("card.jpg", photo_bytes, "image/jpeg")}
            r = requests.post(f"{API}/sendPhoto", data=data, files=files, timeout=20)
            if r.ok:
                success = True
            elif "can't parse entities" in r.text.lower():
                data.pop("parse_mode", None)
                files = {"photo": ("card.jpg", photo_bytes, "image/jpeg")}
                r2 = requests.post(f"{API}/sendPhoto", data=data, files=files, timeout=20)
                if r2.ok:
                    success = True
            else:
                logger.warning("Multipart sendPhoto failed (%d): %s", r.status_code, r.text[:200])
        except Exception as e:
            logger.error("Multipart sendPhoto exception: %s", e)

    # 2. URL-based fallback
    if not success and image_url:
        try:
            payload = {
                "chat_id": int(TG_AFFILIATE_CHAT_ID),
                "photo": image_url,
                "caption": cleaned_caption[:1024],
                "parse_mode": "HTML",
                "allow_sending_without_reply": True,
            }
            if target_thread:
                payload["message_thread_id"] = int(target_thread)
            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            if reply_markup:
                payload["reply_markup"] = reply_markup
            r = requests.post(f"{API}/sendPhoto", json=payload, timeout=15)
            if r.ok:
                success = True
        except Exception as e:
            logger.error("URL sendPhoto exception: %s", e)

    # 3. Fallback to text + buttons if all image transmissions fail
    if not success:
        logger.warning("All photo send methods failed, falling back to text reply with markup")
        _send_reply(
            text=caption,
            reply_to_message_id=reply_to_message_id,
            thread_id=thread_id,
            reply_markup=reply_markup,
        )


def handle_threads_message(msg: dict, text: str, msg_id: int, thread_id: int):
    """Main router for messages inside Threads topic."""
    text_lower = text.lower().strip()

    # 0. Callback Actions (from inline buttons or explicit actions)
    if text.startswith("pub:"):
        draft_id = text[4:].strip()
        _handle_publish_draft(draft_id, msg_id, thread_id)
        return
    elif text.startswith("cancel:"):
        draft_id = text[7:].strip()
        _handle_cancel_draft(draft_id, msg_id, thread_id)
        return
    elif text.startswith("pub_aff:"):
        draft_id = text[8:].strip()
        _handle_publish_affiliate_draft(draft_id, msg_id, thread_id)
        return
    elif text.startswith("cancel_aff:"):
        draft_id = text[11:].strip()
        _handle_cancel_affiliate_draft(draft_id, msg_id, thread_id)
        return
    elif text.startswith("reroll_aff:"):
        draft_id = text[11:].strip()
        _handle_reroll_affiliate_draft(draft_id, msg_id, thread_id)
        return
    elif text.startswith("set_persona:"):
        p_key = text[12:].strip()
        if set_active_manual_persona(p_key):
            p = get_persona(p_key)
            _send_reply(
                f"✅ Persona manual berhasil diubah ke <b>{p.name}</b> {p.emoji}!\n\n"
                f"📝 <i>{p.short_desc}</i>\n"
                f"Draft manual berikutnya akan otomatis dibuat dengan gaya ini.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
        return

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
            _cmd_persona(args, msg_id, thread_id)
            return
        elif cmd in ("/julid", "/calm", "/nerd", "/energic", "/angry"):
            p_key = cmd[1:]
            if not args:
                p = get_persona(p_key)
                _send_reply(
                    f"Format: <code>/{p_key} &lt;topik atau berita&gt;</code>\n"
                    f"Gaya: {p.name} {p.emoji} (<i>{p.short_desc}</i>)\n"
                    f"Contoh: <code>/{p_key} kebakaran hutan kalimantan</code>",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return
            pillar = _infer_pillar(args)
            _create_and_preview_draft(args, pillar, "with_image", msg_id, thread_id, persona=p_key)
            return
        elif cmd in ("/publish", "/confirm", "/postingkan", "/yes"):
            _handle_publish_draft(args, msg_id, thread_id)
            return
        elif cmd in ("/cancel", "/batal", "/no"):
            _handle_cancel_draft(args, msg_id, thread_id)
            return
        elif cmd in ("/post", "/threads"):
            _cmd_post(msg_id, thread_id)
            return
        elif cmd == "/topic":
            _cmd_topic(args, msg_id, thread_id)
            return
        elif cmd == "/post_text":
            _cmd_post_text(args, msg_id, thread_id)
            return
        elif cmd == "/post_photo":
            _cmd_post_photo(args, msg_id, thread_id)
            return
        elif cmd in ("/chain", "/thread"):
            _cmd_chain(args, msg_id, thread_id)
            return
        elif cmd == "/affiliate":
            _cmd_affiliate(args, msg_id, thread_id)
            return
        elif cmd in ("/trending", "/trend", "/topik", "/topics"):
            _cmd_trending_topics(msg_id, thread_id)
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
    if not found_threads_urls:
        numeric_ids = re.findall(r'\b\d{15,20}\b', text)
        if numeric_ids:
            found_threads_urls.extend(numeric_ids)

    # If replying to a Telegram message, extract thread URL/ID from replied message
    if found_aff_urls and not found_threads_urls:
        reply_msg = msg.get("reply_to_message") or {}
        reply_text = reply_msg.get("text") or reply_msg.get("caption") or ""
        if reply_text:
            for pat in THREADS_PATTERNS:
                found_threads_urls.extend(re.findall(pat, reply_text))
            id_match = re.search(r'(?:Threads ID|ID)[:\s]+(?:<code>)?(\d{15,20})', reply_text)
            if id_match:
                found_threads_urls.append(id_match.group(1))

    if found_aff_urls:
        _handle_affiliate_urls(found_aff_urls[0], found_threads_urls, msg_id, thread_id)
        return

    # 3. Natural Language Intent Detection
    if any(k in text_lower for k in ["cek status", "status hari ini", "kuota threads", "kuota posting"]):
        _cmd_status(msg_id, thread_id)
        return
    elif any(k in text_lower for k in [
        "topik trending", "trend hari ini", "tren hari ini", "berita trending",
        "trending sekarang", "ide topik", "topik pilar", "topik hari ini", "trending topic", "trending berita"
    ]):
        _cmd_trending_topics(msg_id, thread_id)
        return
    elif text_lower.startswith("post tentang") or text_lower.startswith("buat thread tentang"):
        topic_arg = text.split(maxsplit=2)[-1].strip()
        _cmd_topic(topic_arg, msg_id, thread_id)
        return
    elif any(k in text_lower for k in ["post sekarang", "posting sekarang", "force post", "buat draft"]):
        _cmd_post(msg_id, thread_id)
        return
    elif any(k in text_lower for k in ["publish sekarang", "terbitkan draft", "postingkan draft", "confirm post", "lanjut posting"]):
        _handle_publish_draft("", msg_id, thread_id)
        return
    elif any(k in text_lower for k in ["batalkan draft", "cancel draft", "jangan posting", "batal posting"]):
        _handle_cancel_draft("", msg_id, thread_id)
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
        "<b>📝 Konten &amp; Eksekusi (Dengan Konfirmasi Preview)</b>\n"
        "/post — Susun draft postingan otomatis & minta konfirmasi\n"
        "/post_photo &lt;topik&gt; — Susun draft kartu berita editorial (opsional: <code>[julid] topik</code>)\n"
        "/post_text &lt;topik&gt; — Susun draft teks polos netizen (opsional: <code>[nerd] topik</code>)\n"
        "/chain &lt;topik&gt; — Susun draft thread rantai 4 bagian (pillar tech/market saja)\n"
        "/topic &lt;topik&gt; — Susun draft postingan topik spesifik\n"
        "/publish — Konfirmasi terbitkan draft aktif ke Threads\n"
        "/batal — Batalkan draft aktif yang menunggu konfirmasi\n\n"
        "<b>🎭 Kontrol Persona Manual</b>\n"
        "/persona — Menu ganti persona aktif (Julid, Calm, Nerd, Energic, Angry, Default)\n"
        "/julid &lt;topik&gt; — Draft kilat persona Netizen Julid 😈\n"
        "/calm &lt;topik&gt; — Draft kilat persona Netizen Calm 🧘\n"
        "/nerd &lt;topik&gt; — Draft kilat persona Netizen Nerd 🤓\n"
        "/energic &lt;topik&gt; — Draft kilat persona Netizen Energic ⚡\n"
        "/angry &lt;topik&gt; — Draft kilat persona Netizen Angry 🔥\n\n"
        "<b>📊 Monitoring &amp; Sinkronisasi</b>\n"
        "/status — Ringkasan kuota hari ini, post terakhir, queue\n"
        "/sync — Sinkronisasi postingan aktif dengan Threads API\n"
        "/posts — 5 postingan terbaru &amp; metrik likes/replies\n"
        "/queue — Daftar link affiliate pending\n"
        "/affiliate &lt;url&gt; — Tempel link affiliate di konten viral\n"
        "/clearqueue — Bersihkan antrian affiliate\n\n"
        "💡 <i>Setiap perintah manual akan menampilkan preview draft lengkap dengan tombol [Publish] &amp; [Batalkan]!</i>"
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
            views_cnt = m.get("views", 0)
            msg_out += (
                f"🆔 <code>{p['threads_id']}</code>\n"
                f"💬 <i>{p['topic'][:45]}</i>\n"
                f"👁 <b>{views_cnt}</b> views · ❤️ <b>{m.get('likes',0)}</b> likes · 💬 <b>{m.get('replies',0)}</b> replies\n\n"
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


def _cmd_trending_topics(msg_id: int, thread_id: int):
    """Fetch live trending topics — heat-ranked hot stories first, then per-pillar picks."""
    try:
        from src.trends.multi_source import fetch_trends_for_pillar, fetch_hot_topics, fetch_topic_image_query

        pillars = [
            ("tech_gadget_ai", "💻 Tech & Gadget AI"),
            ("market_finance_stocks", "📈 Pasar Saham & Ekonomi"),
            ("football_sports", "⚽ Sepak Bola"),
            ("breaking_disaster", "🚨 Bencana & Cuaca BMKG"),
        ]

        lines = [
            "🔥 <b>Topik Berita Trending Real-Time (Live RSS)</b>",
            "📅 <i>Aktual 24 jam terakhir (Google News, Antara, BMKG)</i>",
            "",
            "🏆 <b>PALING PANAS SAAT INI</b> <i>(ranking heat: dampak + liputan lintas media)</i>",
        ]

        try:
            hot = fetch_hot_topics(top_n=5)
        except Exception as e:
            hot = []
            logger.warning("hot topics fetch failed: %s", e)

        if hot:
            for idx, r in enumerate(hot, start=1):
                clean_cmd = re.sub(r'["\']', '', r["topic"]).strip()[:70]
                imgq = fetch_topic_image_query(r["topic"])
                lines.append(f"{idx}. <b>[{r['heat']}]</b> {r['topic'][:90]}")
                lines.append(f"   🖼 <i>{imgq}</i>")
                lines.append(f"   👉 <code>/post_photo {clean_cmd}</code>")
        else:
            lines.append("   <i>(Belum ada berita panas terdeteksi)</i>")

        lines.append("")
        lines.append("─────────────────────")
        lines.append("📌 <b>Per Pilar:</b>")
        lines.append("")

        for p_key, p_label in pillars:
            trends = fetch_trends_for_pillar(p_key)
            lines.append(f"<b>{p_label}:</b>")
            if trends:
                for idx, t in enumerate(trends[:2], start=1):
                    clean_cmd = re.sub(r'["\']', '', t).strip()[:70]
                    lines.append(f"{idx}. {t}")
                    lines.append(f"   👉 <code>/post_photo {clean_cmd}</code>")
            else:
                lines.append("   <i>(Belum ada headline baru)</i>")
            lines.append("")

        lines.append("💡 <i>Ketik atau klik salah satu perintah di atas untuk buat draft kartu editorial instan!</i>")
        msg_out = "\n".join(lines)
        _send_reply(msg_out, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        logger.error("Error fetching trending topics: %s", e)
        _send_reply(f"⚠️ Gagal mengambil topik trending: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_persona(args: str, msg_id: int, thread_id: int):
    """View or switch manual posting persona."""
    if args:
        target = args.strip().lower()
        if set_active_manual_persona(target):
            p = get_persona(target)
            _send_reply(
                f"✅ <b>Persona Manual Diubah!</b>\n\n"
                f"🎭 <b>Aktif:</b> {p.name} {p.emoji}\n"
                f"📝 <i>{p.short_desc}</i>\n\n"
                f"Setiap draft manual berikutnya akan otomatis memakai gaya ini.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
        else:
            _send_reply(
                f"⚠️ Persona '<code>{args}</code>' tidak dikenali.\n"
                f"Pilihan: <code>julid</code>, <code>calm</code>, <code>nerd</code>, <code>energic</code>, <code>angry</code>, <code>default</code>",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
        return

    # If no args: show interactive dashboard & selection buttons
    current_key = get_active_manual_persona()
    current_p = get_persona(current_key)
    all_personas = list_personas()

    lines = [
        "🎭 <b>Panel Pengaturan Persona Threads Manual</b>\n",
        f"📌 <b>Persona Aktif Saat Ini:</b> {current_p.name} {current_p.emoji}\n",
        "<b>Pilihan Persona yang Tersedia:</b>",
    ]
    for p in all_personas:
        mark = "👉 " if p.key == current_key else "• "
        lines.append(f"{mark}<b>{p.name}</b> {p.emoji}: <i>{p.short_desc}</i>")

    lines.append("\n<i>Klik tombol di bawah untuk langsung mengganti persona aktif:</i>")
    text = "\n".join(lines)

    keyboard = {
        "inline_keyboard": [
            [
                {"text": f"{'✅ ' if current_key=='julid' else ''}😈 Julid", "callback_data": "th:set_persona:julid"},
                {"text": f"{'✅ ' if current_key=='calm' else ''}🧘 Calm", "callback_data": "th:set_persona:calm"},
                {"text": f"{'✅ ' if current_key=='nerd' else ''}🤓 Nerd", "callback_data": "th:set_persona:nerd"},
            ],
            [
                {"text": f"{'✅ ' if current_key=='energic' else ''}⚡ Energic", "callback_data": "th:set_persona:energic"},
                {"text": f"{'✅ ' if current_key=='angry' else ''}🔥 Angry", "callback_data": "th:set_persona:angry"},
                {"text": f"{'✅ ' if current_key=='default' else ''}👤 Default", "callback_data": "th:set_persona:default"},
            ],
        ]
    }
    _send_reply(text, reply_to_message_id=msg_id, thread_id=thread_id, reply_markup=keyboard)


def _create_and_preview_draft(
    topic: str,
    pillar: str,
    post_format: str,
    msg_id: int,
    thread_id: int,
    persona: Optional[str] = None,
    post_structure: str = "single",
):
    active_p_key = persona or get_active_manual_persona()
    p_cfg = get_persona(active_p_key)
    format_label = "FOTO BERITA" if post_format == "with_image" else "TEKS POLOS"
    if post_structure == "chain":
        format_label = f"CHAIN {CHAIN_PART_COUNT} BAGIAN + {format_label}"
    preview_topic = (topic[:45] + "...") if len(topic) > 45 else topic
    _send_reply(
        f"⏳ Sedang menyusun draft (<b>{format_label}</b> | <b>{p_cfg.name} {p_cfg.emoji}</b>): <i>{preview_topic}</i>...\n"
        f"<i>(Draft akan ditampilkan untuk kamu review &amp; konfirmasi sebelum diposting)</i>",
        reply_to_message_id=msg_id,
        thread_id=thread_id,
    )

    try:
        from src.organic.generator import generate_organic_post, CHAIN_PART_COUNT

        post_data = generate_organic_post(
            topic=topic,
            pillar=pillar,
            post_format=post_format,
            post_style="snappy_take",
            persona=active_p_key,
            post_structure=post_structure,
            source_summary=get_source_summary(topic),
        )
        content = post_data["content"]
        image_url = post_data.get("image_url", "")
        parts = post_data.get("parts") or []
        bonus_reply = post_data.get("bonus_reply") or ""

        draft_id = f"d_{int(time.time())}"
        drafts = _load_drafts()
        drafts[draft_id] = {
            "id": draft_id,
            "topic": topic,
            "pillar": pillar,
            "content": content,
            "image_url": image_url,
            "post_format": post_format,
            "persona": active_p_key,
            "post_structure": "chain" if len(parts) > 1 else "single",
            "parts": parts,
            "bonus_reply": bonus_reply,
            "created_at": time.time(),
        }
        _save_drafts(drafts)

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Publish ke Threads", "callback_data": f"th:pub:{draft_id}"},
                    {"text": "❌ Batalkan", "callback_data": f"th:cancel:{draft_id}"},
                ]
            ]
        }

        pillar_label = pillar.replace("_", " ").upper()
        if parts:
            # Numbered per part so the reading order is reviewable before it
            # goes live — the order IS the retention mechanic.
            body = "\n\n".join(
                f"<b>{i + 1}/{len(parts)}</b>\n<i>{p}</i>" for i, p in enumerate(parts)
            )
            if bonus_reply:
                body += f"\n\n<b>↳ balasan bonus:</b>\n<i>{bonus_reply}</i>"
            caption = (
                f"🧵 <b>[PREVIEW CHAIN — {pillar_label}]</b>\n"
                f"🎭 <b>Persona:</b> {p_cfg.name} {p_cfg.emoji}\n"
                f"📌 <b>Topik:</b> <i>{topic}</i>\n\n"
                f"{body}\n\n"
                f"⚠️ <b>Konfirmasi:</b> Terbitkan thread ini ke Threads (@pochita9887)?"
            )
        else:
            caption = (
                f"📝 <b>[PREVIEW DRAFT — {pillar_label}]</b>\n"
                f"🎭 <b>Persona:</b> {p_cfg.name} {p_cfg.emoji}\n"
                f"📌 <b>Topik:</b> <i>{topic}</i>\n\n"
                f"💬 <b>Isi Postingan:</b>\n<i>\"{content}\"</i>\n\n"
                f"⚠️ <b>Konfirmasi:</b> Terbitkan postingan ini ke Threads (@pochita9887)?"
            )

        if image_url:
            _send_photo_with_markup(
                image_url=image_url,
                caption=caption,
                reply_markup=buttons,
                thread_id=thread_id,
                reply_to_message_id=msg_id,
            )
        else:
            _send_reply(
                text=caption,
                reply_to_message_id=msg_id,
                thread_id=thread_id,
                reply_markup=buttons,
            )

    except Exception as e:
        logger.error("Error generating draft: %s", e)
        _send_reply(f"⚠️ Gagal menyusun draft: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_publish_draft(draft_id: str, msg_id: int, thread_id: int):
    drafts = _load_drafts()
    draft = None
    if draft_id and draft_id in drafts:
        draft = drafts.get(draft_id)
    elif drafts:
        draft_id, draft = list(drafts.items())[-1]

    if not draft:
        _send_reply("⚠️ Tidak ada draft aktif yang menunggu konfirmasi.", reply_to_message_id=msg_id, thread_id=thread_id)
        return

    topic = draft.get("topic", "")
    content = draft.get("content", "")
    image_url = draft.get("image_url", "")
    pillar = draft.get("pillar", "breaking_trend")

    _send_reply(f"🚀 Mempublikasikan draft ke Threads (@pochita9887)...", reply_to_message_id=msg_id, thread_id=thread_id)

    try:
        from src.publish.threads import post_threads, post_thread_chain
        from src.organic.db import log_organic_post

        parts = draft.get("parts") or []
        bonus_reply = draft.get("bonus_reply") or ""
        structure = "chain" if len(parts) > 1 else draft.get("post_structure", "single")

        if structure == "chain":
            threads_result = post_thread_chain(
                parts, image_url=image_url or None, bonus_reply=bonus_reply
            )
            threads_id = threads_result.get("post_id", "")
        else:
            threads_result = post_threads(
                hook_text=content,
                reply_text=None,
                vibe_image_url=image_url or None,
                product_image_url=None,
            )
            threads_id = threads_result.get("post_id", "")

            if not threads_id and image_url:
                time.sleep(2)
                threads_result = post_threads(
                    hook_text=content,
                    reply_text=None,
                    vibe_image_url=None,
                    product_image_url=None,
                )
                threads_id = threads_result.get("post_id", "")

        if threads_id:
            log_organic_post(
                source="manual_telegram_approved",
                pillar=pillar,
                topic=topic,
                content=content,
                image_url=image_url,
                threads_id=threads_id,
                post_structure=structure,
            )
            drafts.pop(draft_id, None)
            _save_drafts(drafts)

            post_url = f"https://www.threads.net/@pochita9887/post/{threads_id}"
            success_msg = (
                f"✅ <b>Postingan Berhasil Terbit di Threads!</b>\n\n"
                f"📌 <b>Topik:</b> <i>{topic}</i>\n"
                f"🆔 <b>Threads ID:</b> <code>{threads_id}</code>\n"
                f"🔗 <a href=\"{post_url}\">Buka di Threads</a>"
            )
            _send_reply(success_msg, reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            _send_reply(f"❌ Gagal memposting ke Threads: {threads_result.get('error', 'Unknown error')}", reply_to_message_id=msg_id, thread_id=thread_id)

    except Exception as e:
        logger.error("Error publishing draft: %s", e)
        _send_reply(f"⚠️ Error saat publikasi: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_cancel_draft(draft_id: str, msg_id: int, thread_id: int):
    drafts = _load_drafts()
    if draft_id and draft_id in drafts:
        drafts.pop(draft_id, None)
        _save_drafts(drafts)
    elif drafts:
        drafts.clear()
        _save_drafts(drafts)

    _send_reply("❌ <b>Draft Dibatalkan.</b> Tidak ada konten yang dikirim ke Threads.", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_chain(args: str, msg_id: int, thread_id: int):
    """Manual 4-part chain draft. Empty args lets the planner pick the topic."""
    from src.organic.generator import CHAIN_PILLARS

    if args:
        tagged_p, clean_topic = extract_persona_tag(args)
        pillar = _infer_pillar(clean_topic)
        if pillar not in CHAIN_PILLARS:
            _send_reply(
                f"⚠️ Topik ini masuk pillar <code>{pillar}</code>, dan chain hanya untuk "
                f"<code>tech_gadget_ai</code> atau <code>market_finance_stocks</code>.\n"
                f"Pakai <code>/post_text</code> atau <code>/post_photo</code> untuk topik ini.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return
        _create_and_preview_draft(
            clean_topic, pillar, "with_image", msg_id, thread_id,
            persona=tagged_p, post_structure="chain",
        )
        return

    try:
        from src.organic.planner import plan_single_post
        plan = plan_single_post()
        if plan["pillar"] not in CHAIN_PILLARS:
            _send_reply(
                f"⚠️ Slot berikutnya jatuh ke pillar <code>{plan['pillar']}</code>, bukan pillar chain.\n"
                f"Kirim <code>/chain &lt;topik&gt;</code> untuk memaksa topik tech/market.",
                reply_to_message_id=msg_id,
                thread_id=thread_id,
            )
            return
        _create_and_preview_draft(
            plan["topic"], plan["pillar"], plan.get("post_format", "with_image"),
            msg_id, thread_id, post_structure="chain",
        )
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_post(msg_id: int, thread_id: int):
    try:
        from src.organic.planner import plan_single_post
        plan = plan_single_post()
        topic = plan["topic"]
        pillar = plan["pillar"]
        post_format = plan.get("post_format", "with_image")
        post_structure = plan.get("post_structure", "single")
        _create_and_preview_draft(topic, pillar, post_format, msg_id, thread_id,
                                  post_structure=post_structure)
    except Exception as e:
        _send_reply(f"⚠️ Error: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_topic(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply("Format: <code>/topic &lt;topik atau berita&gt;</code>\nContoh: <code>/topic [julid] kenaikan suku bunga The Fed</code>", reply_to_message_id=msg_id, thread_id=thread_id)
        return
    tagged_p, clean_topic = extract_persona_tag(args)
    pillar = _infer_pillar(clean_topic)
    _create_and_preview_draft(clean_topic, pillar, "with_image", msg_id, thread_id, persona=tagged_p)


def _cmd_post_text(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply("Format: <code>/post_text &lt;topik atau isu&gt;</code>\nContoh: <code>/post_text [nerd] pergerakan IHSG sesi 1</code>", reply_to_message_id=msg_id, thread_id=thread_id)
        return
    tagged_p, clean_topic = extract_persona_tag(args)
    pillar = _infer_pillar(clean_topic)
    _create_and_preview_draft(clean_topic, pillar, "text_only", msg_id, thread_id, persona=tagged_p)


def _cmd_post_photo(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply("Format: <code>/post_photo &lt;topik atau isu&gt;</code>\nContoh: <code>/post_photo [angry] kebakaran hutan kalimantan</code>", reply_to_message_id=msg_id, thread_id=thread_id)
        return
    tagged_p, clean_topic = extract_persona_tag(args)
    pillar = _infer_pillar(clean_topic)
    _create_and_preview_draft(clean_topic, pillar, "with_image", msg_id, thread_id, persona=tagged_p)



def _create_affiliate_draft_preview(
    aff_url: str,
    target_thread_url: str = None,
    msg_id: int = None,
    thread_id: int = None,
):
    """
    Generate an affiliate comment draft preview for ONE qualifying thread.
    Allows manual confirmation before actually posting as a reply.
    """
    try:
        from src.scraper.product_extractor import extract_product_metadata
        from src.affiliate.comment_engine import (
            get_our_posts,
            get_post_metrics,
            generate_affiliate_comment,
            extract_thread_id_from_url,
        )

        _send_reply(
            f"🔍 <b>Menganalisis Link Produk & Mencari Thread Terbaik...</b>\n"
            f"🛒 <code>{aff_url[:75]}</code>",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )

        # 1. Extract product
        product = extract_product_metadata(aff_url)
        from src.affiliate.comment_engine import _sanitize_product_name
        prod_name = _sanitize_product_name(product.get("name") or "Produk Rekomendasi")
        prod_price = product.get("price") or ""
        prod_img = product.get("image_url") or ""

        target_post = None

        # 2. Check if specific thread was provided
        if target_thread_url:
            media_id = extract_thread_id_from_url(target_thread_url)
            if not media_id:
                _send_reply(f"⚠️ URL Thread tidak valid: {target_thread_url}", reply_to_message_id=msg_id, thread_id=thread_id)
                return
            m = get_post_metrics(media_id)
            post_text = "Postingan pilihan"
            try:
                import os, requests
                r = requests.get(
                    f"https://graph.threads.net/v1.0/{media_id}",
                    params={"fields": "id,text", "access_token": os.getenv("THREADS_ACCESS_TOKEN")},
                    timeout=10,
                )
                if r.ok:
                    post_text = r.json().get("text", "")
            except Exception:
                pass
            target_post = {
                "id": media_id,
                "text": post_text,
                "views": m.get("views", 0),
                "likes": m.get("likes", 0),
                "replies": m.get("replies", 0),
            }
        else:
            # 3. Find our recent posts and select the SINGLE BEST one (views >= 100 or likes >= 20 or replies >= 10)
            posts = get_our_posts(limit=25)
            if not posts:
                _send_reply("⚠️ Tidak dapat mengambil daftar postingan Threads.", reply_to_message_id=msg_id, thread_id=thread_id)
                return

            bencana_keywords = ["banjir", "gempa", "kebakaran", "longsor", "tsunami", "korban", "meninggal", "duka"]
            from src.affiliate.comment_engine import (
                post_qualifies,
                organic_replies,
                post_age_minutes,
            )

            qualifying = []
            all_scored = []

            for p in posts:
                pid = p["id"]
                p_text = p.get("text", "")
                if any(kw in p_text.lower() for kw in bencana_keywords):
                    continue

                m = get_post_metrics(pid)
                views = m.get("views", 0)
                likes = m.get("likes", 0)
                replies_cnt = m.get("replies", 0)
                scored_p = {**p, "views": views, "likes": likes, "replies": replies_cnt}
                all_scored.append(scored_p)

                # Same AND gate as the daemon: the manual path must never
                # approve a link the automatic one would hold back.
                ok, reason = post_qualifies(
                    views=views,
                    likes=likes,
                    organic_reply_count=len(organic_replies(pid)),
                    age_minutes=post_age_minutes(p),
                )
                scored_p["_gate_reason"] = reason
                if ok:
                    qualifying.append(scored_p)

            if not qualifying:
                from src.affiliate.queue_manager import enqueue
                row_id = enqueue(aff_url)
                top_p = max(all_scored, key=lambda x: (x.get("views", 0), x.get("likes", 0))) if all_scored else None
                top_views = top_p.get("views", 0) if top_p else 0
                top_reason = top_p.get("_gate_reason", "diskusi belum hidup") if top_p else "diskusi belum hidup"
                _send_reply(
                    f"⏳ <b>Belum ada postingan yang lolos Gate 3.</b>\n\n"
                    f"Gate butuh <b>≥5 balasan org, umur post ≥45m, dan views ≥250 / likes ≥20</b> secara bersamaan.\n"
                    f"Postingan terbaik saat ini baru meraih <b>{top_views} views</b> — ditahan karena <i>{top_reason}</i>.\n\n"
                    f"📌 Link disimpan di <b>Antrian Affiliate</b> (ID: #{row_id}).\n"
                    f"🤖 Komentar otomatis dipasang begitu syaratnya terpenuhi!",
                    reply_to_message_id=msg_id,
                    thread_id=thread_id,
                )
                return

            # Select ONLY ONE: Filter out already commented posts, sort by likes desc, then views desc
            from src.affiliate.comment_engine import get_commented_post_ids
            already_commented = get_commented_post_ids()
            uncommented = [p for p in qualifying if p["id"] not in already_commented]
            if not uncommented:
                # Fallback if every qualifying post has been used
                uncommented = qualifying

            uncommented.sort(key=lambda x: (x.get("likes", 0), x.get("views", 0), x.get("replies", 0)), reverse=True)
            target_post = uncommented[0]
            candidate_ids = [p["id"] for p in uncommented]

        pid = target_post["id"]
        post_text = target_post.get("text", "")
        views = target_post.get("views", 0)
        likes = target_post.get("likes", 0)
        replies_cnt = target_post.get("replies", 0)
        if not target_thread_url and 'candidate_ids' not in locals():
            candidate_ids = [pid]

        # 4. Generate contextual comment
        comment = generate_affiliate_comment(post_text, product)
        if not comment:
            _send_reply("⚠️ Gagal menyusun komentar affiliate dengan LLM.", reply_to_message_id=msg_id, thread_id=thread_id)
            return

        # 5. Save Draft
        draft_id = f"aff_{int(time.time())}"
        drafts = _load_drafts()
        drafts[draft_id] = {
            "id": draft_id,
            "type": "affiliate",
            "target_post_id": pid,
            "target_post_text": post_text,
            "target_views": views,
            "target_likes": likes,
            "target_replies": replies_cnt,
            "candidate_post_ids": candidate_ids if not target_thread_url else [pid],
            "candidate_index": 0,
            "product_name": prod_name,
            "product_price": prod_price,
            "product_image": prod_img,
            "affiliate_url": aff_url,
            "comment": comment,
            "created_at": time.time(),
        }
        _save_drafts(drafts)

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Pasang Komentar", "callback_data": f"th:pub_aff:{draft_id}"},
                    {"text": "🔄 Ganti Thread", "callback_data": f"th:reroll_aff:{draft_id}"},
                ],
                [
                    {"text": "❌ Batalkan", "callback_data": f"th:cancel_aff:{draft_id}"},
                ]
            ]
        }

        post_url = f"https://www.threads.net/@pochita9887/post/{pid}"
        price_tag = f" — <b>{prod_price}</b>" if prod_price else ""
        caption = (
            f"🛍 <b>[PREVIEW DRAFT KOMENTAR AFFILIATE]</b>\n\n"
            f"🎯 <b>Target Thread (1 Post Terbaik):</b>\n"
            f"🆔 <code>{pid}</code> (👁 <b>{views}</b> views · ❤️ <b>{likes}</b> likes · 💬 <b>{replies_cnt}</b> replies)\n"
            f"💬 <i>\"{post_text[:110]}...\"</i>\n"
            f"🔗 <a href=\"{post_url}\">Lihat Thread</a>\n\n"
            f"📦 <b>Produk:</b> {prod_name}{price_tag}\n\n"
            f"💬 <b>Usulan Komentar:</b>\n"
            f"<i>\"{comment}\"</i>\n\n"
            f"⚠️ <b>Konfirmasi:</b> Pasang komentar ini ke thread pilihan di atas?"
        )

        _send_reply(
            text=caption,
            reply_to_message_id=msg_id,
            thread_id=thread_id,
            reply_markup=buttons,
        )

    except Exception as e:
        logger.error("Error creating affiliate draft: %s", e)
        _send_reply(f"⚠️ Gagal memproses affiliate: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_publish_affiliate_draft(draft_id: str, msg_id: int, thread_id: int):
    drafts = _load_drafts()
    draft = None
    if draft_id and draft_id in drafts:
        draft = drafts.get(draft_id)
    elif drafts:
        for did, d in reversed(list(drafts.items())):
            if d.get("type") == "affiliate":
                draft_id, draft = did, d
                break

    if not draft or draft.get("type") != "affiliate":
        _send_reply("⚠️ Tidak ada draft komentar affiliate yang menunggu konfirmasi.", reply_to_message_id=msg_id, thread_id=thread_id)
        return

    target_pid = draft.get("target_post_id")
    comment = draft.get("comment")

    _send_reply(f"🚀 Memposting komentar affiliate ke Thread <code>{target_pid}</code>...", reply_to_message_id=msg_id, thread_id=thread_id)

    try:
        from src.affiliate.comment_engine import post_reply_to_thread, record_affiliate_comment
        reply_id = post_reply_to_thread(target_pid, comment)
        if reply_id:
            record_affiliate_comment(
                post_id=target_pid,
                reply_id=reply_id,
                affiliate_url=draft.get("affiliate_url", ""),
                product_name=draft.get("product_name", ""),
            )
            drafts.pop(draft_id, None)
            _save_drafts(drafts)

            post_url = f"https://www.threads.net/@pochita9887/post/{target_pid}"
            success_msg = (
                f"✅ <b>Komentar Affiliate Berhasil Diposting!</b>\n\n"
                f"🎯 <b>Target Post:</b> <code>{target_pid}</code>\n"
                f"💬 <b>Komentar:</b>\n<i>\"{comment}\"</i>\n\n"
                f"🆔 <b>Reply ID:</b> <code>{reply_id}</code>\n"
                f"🔗 <a href=\"{post_url}\">Lihat di Threads</a>"
            )
            _send_reply(success_msg, reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            _send_reply("❌ Gagal memposting balasan komentar ke Threads API.", reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as e:
        logger.error("Error publishing affiliate reply: %s", e)
        _send_reply(f"⚠️ Error saat publikasi komentar: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_cancel_affiliate_draft(draft_id: str, msg_id: int, thread_id: int):
    drafts = _load_drafts()
    if draft_id and draft_id in drafts:
        drafts.pop(draft_id, None)
        _save_drafts(drafts)
    else:
        drafts = {k: v for k, v in drafts.items() if v.get("type") != "affiliate"}
        _save_drafts(drafts)

    _send_reply("❌ <b>Draft Komentar Affiliate Dibatalkan.</b> Tidak ada komentar yang dipasang ke Threads.", reply_to_message_id=msg_id, thread_id=thread_id)


def _handle_reroll_affiliate_draft(draft_id: str, msg_id: int, thread_id: int):
    drafts = _load_drafts()
    draft = None
    if draft_id and draft_id in drafts:
        draft = drafts.get(draft_id)
    elif drafts:
        for did, d in reversed(list(drafts.items())):
            if d.get("type") == "affiliate":
                draft_id, draft = did, d
                break

    if not draft or draft.get("type") != "affiliate":
        _send_reply("⚠️ Tidak ada draft komentar affiliate aktif untuk diganti thread.", reply_to_message_id=msg_id, thread_id=thread_id)
        return

    candidate_ids = draft.get("candidate_post_ids", [])
    if len(candidate_ids) <= 1:
        _send_reply("⚠️ Hanya ada 1 thread yang memenuhi kriteria dan belum pernah dikomentari.", reply_to_message_id=msg_id, thread_id=thread_id)
        return

    curr_idx = draft.get("candidate_index", 0)
    next_idx = (curr_idx + 1) % len(candidate_ids)
    next_pid = candidate_ids[next_idx]

    _send_reply(f"🔄 <b>Mengganti Target Thread ({next_idx + 1}/{len(candidate_ids)})...</b>", reply_to_message_id=msg_id, thread_id=thread_id)

    try:
        from src.affiliate.comment_engine import get_post_metrics, generate_affiliate_comment
        import os, requests

        # Fetch post text
        post_text = "Postingan pilihan"
        r = requests.get(
            f"https://graph.threads.net/v1.0/{next_pid}",
            params={"fields": "id,text", "access_token": os.getenv("THREADS_ACCESS_TOKEN")},
            timeout=10,
        )
        if r.ok:
            post_text = r.json().get("text", "")

        m = get_post_metrics(next_pid)
        views = m.get("views", 0)
        likes = m.get("likes", 0)
        replies_cnt = m.get("replies", 0)

        product = {
            "name": draft.get("product_name", ""),
            "price": draft.get("product_price", ""),
            "image_url": draft.get("product_image", ""),
            "affiliate_url": draft.get("affiliate_url", ""),
        }

        new_comment = generate_affiliate_comment(post_text, product)

        draft["target_post_id"] = next_pid
        draft["target_post_text"] = post_text
        draft["target_views"] = views
        draft["target_likes"] = likes
        draft["target_replies"] = replies_cnt
        draft["comment"] = new_comment
        draft["candidate_index"] = next_idx
        _save_drafts(drafts)

        buttons = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Pasang Komentar", "callback_data": f"th:pub_aff:{draft_id}"},
                    {"text": "🔄 Ganti Thread", "callback_data": f"th:reroll_aff:{draft_id}"},
                ],
                [
                    {"text": "❌ Batalkan", "callback_data": f"th:cancel_aff:{draft_id}"},
                ]
            ]
        }

        post_url = f"https://www.threads.net/@pochita9887/post/{next_pid}"
        prod_name = draft.get("product_name")
        prod_price = draft.get("product_price")
        price_tag = f" — <b>{prod_price}</b>" if prod_price else ""
        caption = (
            f"🛍 <b>[PREVIEW DRAFT KOMENTAR AFFILIATE (Opsi {next_idx + 1}/{len(candidate_ids)})]</b>\n\n"
            f"🎯 <b>Target Thread Baru:</b>\n"
            f"🆔 <code>{next_pid}</code> (👁 <b>{views}</b> views · ❤️ <b>{likes}</b> likes · 💬 <b>{replies_cnt}</b> replies)\n"
            f"💬 <i>\"{post_text[:110]}...\"</i>\n"
            f"🔗 <a href=\"{post_url}\">Lihat Thread</a>\n\n"
            f"📦 <b>Produk:</b> {prod_name}{price_tag}\n\n"
            f"💬 <b>Usulan Komentar:</b>\n"
            f"<i>\"{new_comment}\"</i>\n\n"
            f"⚠️ <b>Konfirmasi:</b> Pasang komentar ini ke thread pilihan di atas?"
        )

        _send_reply(
            text=caption,
            reply_to_message_id=msg_id,
            thread_id=thread_id,
            reply_markup=buttons,
        )
    except Exception as e:
        logger.error("Error rerolling affiliate draft: %s", e)
        _send_reply(f"⚠️ Gagal mengganti target thread: {e}", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_affiliate(args: str, msg_id: int, thread_id: int):
    if not args:
        _send_reply(
            "Format: <code>/affiliate &lt;link produk&gt; [link thread / ID thread]</code>\n\n"
            "Contoh:\n"
            "• Auto thread terbaik: <code>/affiliate https://s.shopee.co.id/xxx</code>\n"
            "• Direct thread: <code>/affiliate https://s.shopee.co.id/xxx 18131653009761783</code>",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
        return

    # Extract affiliate URL and optional thread URL/ID
    found_aff = []
    for pat in AFFILIATE_PATTERNS:
        found_aff.extend(re.findall(pat, args))

    found_th = []
    for pat in THREADS_PATTERNS:
        found_th.extend(re.findall(pat, args))
    if not found_th:
        num_ids = re.findall(r'\b\d{15,20}\b', args)
        if num_ids:
            found_th.extend(num_ids)

    aff_url = found_aff[0] if found_aff else args.split()[0]
    target_thread = found_th[0] if found_th else None

    _create_affiliate_draft_preview(aff_url=aff_url, target_thread_url=target_thread, msg_id=msg_id, thread_id=thread_id)


def _handle_affiliate_urls(aff_url: str, found_threads_urls: list[str], msg_id: int, thread_id: int):
    """Handle affiliate URLs directly posted in conversation."""
    target_thread = found_threads_urls[0] if found_threads_urls else None
    _create_affiliate_draft_preview(aff_url=aff_url, target_thread_url=target_thread, msg_id=msg_id, thread_id=thread_id)


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
            "Kamu adalah Apex — Social Media Growth Specialist & AI Operator untuk akun Threads Indonesia (@pochita9887).\n\n"
            "PERSONA & GAYA BICARA:\n"
            "- Suara autentik netizen Threads Indonesia: hidup, tajam, cerdas, berbobot, tapi tetap santai (nggak kaku seperti humas korporat, juga nggak alay).\n"
            "- Berani punya opini atau 'hot take' yang logis — bukan cuma merangkum berita netral yang membosankan.\n"
            "- Hindari klise murahan: JANGAN bawa-bawa gosip artis, infotainment, drama selebgram, atau lelucon basi KRL Manggarai/kosan.\n"
            "- Gaya tulisan netizen organik: kalimat pendek, punchy, ada jeda baris, minim jargon akademis, bahasa sehari-hari yang relatable.\n\n"
            "4 PILAR KONTEN UTAMA:\n"
            "  1. Bencana Alam, Cuaca Ekstrem & BMKG (info cepat, empatik, berbasis data riil).\n"
            "  2. Sepak Bola (Timnas Indonesia, Premier League, UCL — fokus analisis taktik & dinamika panas, bukan provokasi buta).\n"
            "  3. Teknologi, Gadget & Tren AI (dampak AI ke dunia kerja, tools baru, realita tech layoffs vs produktivitas).\n"
            "  4. Pasar Keuangan, Saham & Ekonomi (IHSG, pergerakan rupiah, emiten lokal, sentimen The Fed, kripto makro).\n\n"
            "MONETISASI AFFILIATE:\n"
            "- Strategi: biarkan post organik meledak dulu (threshold ≥20 likes atau ≥10 reply), baru daemon menempelkan komentar affiliate kontekstual secara halus (seamless, bukan spam hard-selling).\n\n"
            f"STATUS AKUN HARI INI:\n"
            f"- Postingan organik aktif: {post_count}/12\n"
            f"- Antrean link affiliate: {len(pending)} link pending\n"
            f"- 3 post terakhir:\n{recent_summary or '  (belum ada)'}\n\n"
            "ATURAN RESPON:\n"
            "- Jawab to the point, jangan pakai pembuka basa-basi ('Tentu!', 'Halo!', 'Menarik sekali pertanyaannya').\n"
            "- Kalau user minta draft konten, SELALU berikan 2-3 opsi variasi berbeda (misal: Opsi 1 = Kontroversial/Hot Take, Opsi 2 = Analisis/Data, Opsi 3 = Reaksi Spontan/Curhat Profesional).\n"
            "- Tiap draft harus siap copas langsung ke Threads: sudah termasuk format jeda baris yang rapi.\n"
            "- Kalau user minta posting, beri tahu mereka bisa langsung kirim: /post_text <topik> atau /post_photo <topik>."
        )

        from src.brief.generator import _llm
        raw = _llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ], temperature=0.7, max_tokens=1500)

        reply_text = _clean_text_for_telegram(raw)
        if not reply_text:
            reply_text = "Siap! Ada ide konten atau link affiliate yang mau di-generate? Ketik /draft atau langsung kirim tautannya."
        _send_reply(reply_text, reply_to_message_id=msg_id, thread_id=thread_id)

    except Exception as e:
        logger.error("Chat agent error in Threads topic: %s", e)
        _send_reply(f"⚠️ Maaf, ada kendala saat memproses pesanmu: {e}", reply_to_message_id=msg_id, thread_id=thread_id)
