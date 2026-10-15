"""tiktok_agent.py — Conversational AI Operator for TikTok Automation.

Handles intent parsing, natural language commands, strategy consultation,
and background execution of video search, render, and upload for TikTok.
"""

import json
import logging
import re
import threading
from typing import Optional
import requests

from src.config import (
    TELEGRAM_BOT_TOKEN,
    TG_AFFILIATE_CHAT_ID,
    TG_TIKTOK_THREAD_ID,
    LLM_API_KEY,
    LLM_MODEL,
    OPENROUTER_URL,
)
from src.tiktok.config import load_config, save_config
from src.tiktok.state import VideoStateDB
from src.tiktok.pipeline import run_pipeline

logger = logging.getLogger("bot.tiktok_agent")
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# Lock to ensure only one video render/upload pipeline runs at a time
_PIPELINE_LOCK = threading.Lock()


def _clean_text_for_telegram(text: str) -> str:
    """Strip internal reasoning/think tags and clean text."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    text = re.sub(r"<reasoning>.*?</reasoning>", "", text, flags=re.DOTALL)
    return text.strip()


def _send_reply(text: str, reply_to_message_id: Optional[int] = None, thread_id: Optional[int] = None):
    """Send formatted HTML message to TikTok Telegram topic with plain text fallback."""
    target_thread = thread_id if thread_id is not None else TG_TIKTOK_THREAD_ID
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

    try:
        r = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        if not r.ok and ("can't parse entities" in r.text.lower() or "bad request" in r.text.lower()):
            payload.pop("parse_mode", None)
            requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    except Exception as exc:
        logger.error("Failed to send message to TikTok topic: %s", exc)


def handle_tiktok_message(msg: dict, text: str, msg_id: int, thread_id: int):
    """Main router for messages inside TikTok topic."""
    text_lower = text.lower().strip()

    # 1. Direct Slash Commands
    if text.startswith("/"):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        args = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/status", "/tiktok_status"):
            _cmd_status(msg_id, thread_id)
            return
        elif cmd in ("/upload", "/post"):
            _trigger_pipeline_async(dry_run=False, msg_id=msg_id, thread_id=thread_id, keyword=args or None)
            return
        elif cmd in ("/dryrun", "/test"):
            _trigger_pipeline_async(dry_run=True, msg_id=msg_id, thread_id=thread_id, keyword=args or None)
            return
        elif cmd in ("/keywords", "/keyword"):
            _cmd_keywords(args, msg_id, thread_id)
            return
        elif cmd in ("/help", "/start"):
            _cmd_help(msg_id, thread_id)
            return
        else:
            # Fallback to conversational handler even for unknown slash commands
            pass

    # 2. Natural Language Intent Detection (Agentic Control)
    intent, intent_arg, fill_mode = _detect_intent(text_lower, text)

    if intent == "upload":
        _trigger_pipeline_async(
            dry_run=False, msg_id=msg_id, thread_id=thread_id, keyword=intent_arg, fill_mode=fill_mode
        )
        return
    elif intent == "dryrun":
        _trigger_pipeline_async(
            dry_run=True, msg_id=msg_id, thread_id=thread_id, keyword=intent_arg, fill_mode=fill_mode
        )
        return
    elif intent == "status":
        _cmd_status(msg_id, thread_id)
        return
    elif intent == "keywords":
        _cmd_keywords(intent_arg or "", msg_id, thread_id)
        return

    # 3. Conversational AI Strategy Mode (Chat with TikTok Director)
    _chat_with_tiktok_strategist(text, msg_id, thread_id)


def _detect_intent(text_lower: str, original_text: str) -> tuple[Optional[str], Optional[str], bool]:
    """Detect if the user's conversational message intends to execute an action.

    Returns (intent, intent_arg, fill_mode).
    fill_mode=True when user wants full-screen vertical video (no blur bars).
    """
    cleaned = text_lower.strip()

    # Detect fill_mode (full screen vertical) keywords
    fill_keywords = ["full", "penuh", "full screen", "fullscreen", "full hp", "full layar", "full di hp", "tanpa blur", "no blur", "crop penuh"]
    fill_mode = any(k in cleaned for k in fill_keywords)

    # 1. Quick acceptance (agree to upload default/preset)
    if cleaned in ("bebas", "terserah", "acak", "random", "apa aja", "apa saja", "lanjut", "gas", "gass", "oke"):
        return "upload", None, fill_mode

    # 2. Dry run triggers
    if any(k in cleaned for k in ["dry run", "dryrun", "tes render", "test render", "render aja", "tanpa upload"]):
        m = re.search(r"(?:dry\s*run|tes render)\s*(?:tentang|niche|kategori)?\s*(.*)", cleaned)
        kw = m.group(1).strip() if m and m.group(1) else None
        if kw in ("dulu", "dong", "ya", ""):
            kw = None
        return "dryrun", kw, fill_mode

    # 3. Status triggers
    if any(k in cleaned for k in [
        "cek status", "status tiktok", "video apa aja yang udah", "berapa video hari ini",
        "video yang terupload", "riwayat upload", "history upload", "rekap upload", "rekap video",
        "jadwal", "scheduler", "kapan upload", "jam berapa upload", "jadwal fyp", "slot fyp"
    ]):
        return "status", None, False

    # 4. Keyword triggers
    if any(k in cleaned for k in ["daftar keyword", "list keyword", "kata kunci pencarian"]):
        return "keywords", None, False
    if cleaned.startswith("tambah keyword") or cleaned.startswith("tambahkan keyword"):
        kw = original_text.split(maxsplit=2)[-1].strip()
        return "keywords", f"add {kw}", False

    # 5. Natural Language Upload Triggers
    patterns = [
        r"(?:tolong\s+)?(?:cari(?:kan)?|minta|mau|ambil(?:kan)?)\s+video\s+(.*?)(?:\s+(?:dan|lalu|terus)\s+(?:upload|unggah|post))?$",
        r"(?:tolong\s+)?(?:upload|unggah|post|gas|jalanin)\s+(?:1\s+)?(?:konten|video)\s*(.*?)$",
        r"(?:cari(?:kan)?\s+dan\s+upload)\s+(.*)$",
        r"video\s+(.*?)\s+(?:dong|nih|ya)$",
    ]

    for pat in patterns:
        m = re.match(pat, cleaned)
        if m:
            kw = m.group(1).strip() if m.lastindex and m.group(1) else None
            if kw:
                for filler in [" dan upload", " lalu upload", " terus upload", " dong", " nih", " ya", " dulu", " sekarang", " aja", " saja"]:
                    if kw.endswith(filler):
                        kw = kw[:-len(filler)].strip()
                for prep in ["tentang ", "niche ", "kategori "]:
                    if kw.startswith(prep):
                        kw = kw[len(prep):].strip()
            if kw in ("", "dong", "nih", "ya", "dulu", "sekarang", "baru", "1"):
                kw = None
            return "upload", kw, fill_mode

    return None, None, False


def _cmd_status(msg_id: int, thread_id: int):
    """Format and send TikTok agent status card."""
    cfg = load_config()
    db = VideoStateDB(cfg["db_path"])
    stats = db.get_stats(days=7)
    recent = db.get_recent_uploads(4)
    db.close()

    total_uploaded = stats["total_uploaded"]
    last_run = stats.get("last_run")
    last_run_str = f"{last_run['run_at'][:19]} (uploaded: {last_run['videos_uploaded']})" if last_run else "Belum ada log"

    recent_list_str = ""
    for r in recent:
        clean_title = r["title"][:45]
        recent_list_str += f"• <code>{r['uploaded_at'][:16]}</code> — <b>{clean_title}</b>\n"

    from src.tiktok.scheduler import get_status_str as sched_status_str
    sched_status = sched_status_str()

    q_count = db.get_queued_count()
    source_mode = cfg.get("source_mode", "shorts")
    mode_desc = "🌲 Outdoor Boys Multi-Part Highlight" if source_mode == "outdoorboys" else "🎬 YouTube Shorts FYP"

    msg = (
        "📊 <b>Status TikTok Automation Agent</b>\n\n"
        f"🎯 <b>Mode Sumber:</b> {mode_desc}\n"
        f"📦 <b>Antrean Cuplikan Part:</b> {q_count} klip siap upload\n"
        f"🎬 <b>Total Video Terunggah:</b> {total_uploaded}\n"
        f"⏰ <b>Scheduler:</b> {sched_status}\n"
        f"⏱ <b>Durasi Klip Aktif:</b> {cfg.get('clip_duration_seconds', 75)} detik\n"
        f"📐 <b>Format Render:</b> 1080x1920 (9:16 Lanczos + Header Part + Unsharp)\n"
        f"📺 <b>Dual Upload:</b> TikTok + YouTube Shorts\n"
        f"🔄 <b>Terakhir Dijalankan:</b> {last_run_str}\n\n"
        f"<b>4 Video Terakhir:</b>\n{recent_list_str or '• Belum ada video'}\n\n"
        f"💡 <i>Ketik 'upload 1 video baru' atau kirim /upload untuk eksekusi langsung.</i>"
    )
    _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_keywords(args: str, msg_id: int, thread_id: int):
    """View or update search keywords."""
    cfg = load_config()
    keywords = cfg.get("keywords", [])

    if not args:
        kw_list = "\n".join([f"• {k}" for k in keywords])
        msg = (
            "🔑 <b>Daftar Keyword Pencarian YouTube 4K:</b>\n\n"
            f"{kw_list}\n\n"
            "<i>Untuk menambah: <code>/keywords add &lt;nama keyword&gt;</code></i>\n"
            "<i>Untuk menghapus: <code>/keywords remove &lt;nama keyword&gt;</code></i>"
        )
        _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)
        return

    parts = args.split(maxsplit=1)
    subcmd = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    if subcmd == "add" and target:
        if target not in keywords:
            keywords.append(target)
            cfg["keywords"] = keywords
            save_config(cfg)
            _send_reply(f"✅ Keyword berhasil ditambahkan: <b>{target}</b>", reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            _send_reply(f"ℹ️ Keyword sudah ada di daftar: <b>{target}</b>", reply_to_message_id=msg_id, thread_id=thread_id)
    elif subcmd in ("remove", "delete") and target:
        if target in keywords:
            keywords.remove(target)
            cfg["keywords"] = keywords
            save_config(cfg)
            _send_reply(f"🗑 Keyword berhasil dihapus: <b>{target}</b>", reply_to_message_id=msg_id, thread_id=thread_id)
        else:
            _send_reply(f"⚠️ Keyword tidak ditemukan di daftar: <b>{target}</b>", reply_to_message_id=msg_id, thread_id=thread_id)
    else:
        _send_reply("Format: <code>/keywords add &lt;keyword&gt;</code> atau <code>/keywords remove &lt;keyword&gt;</code>", reply_to_message_id=msg_id, thread_id=thread_id)


def _cmd_help(msg_id: int, thread_id: int):
    """Show help guide for TikTok topic."""
    msg = (
        "🎬 <b>Panduan Kontrol TikTok Automation Agent:</b>\n\n"
        "Kamu bisa ngobrol santai seperti dengan asisten produksi sungguhan:\n\n"
        "• <b>Instruksi Langsung:</b>\n"
        "  - <i>\"Upload 1 video mancing dong\"</i>\n"
        "  - <i>\"Cari video pabrik satisfying full dan upload\"</i>\n"
        "  - <i>\"Coba dry run tes render video kayu\"</i>\n"
        "  - <i>\"Cek status upload hari ini\"</i>\n\n"
        "• <b>Platform Upload:</b>\n"
        "  - Setiap video otomatis diupload ke <b>TikTok</b> DAN <b>YouTube Shorts</b>\n"
        "  - Tambah kata <i>\"full\"</i> untuk mode center crop (penuh di HP)\n\n"
        "• <b>Diskusi &amp; Curation:</b>\n"
        "  - <i>\"Rekomendasi hook teks buat konten bangunan\"</i>\n"
        "  - <i>\"Gimana cara optimasi video 6 menit di TikTok?\"</i>\n\n"
        "• <b>Slash Commands:</b>\n"
        "  - <code>/upload [keyword]</code> : Eksekusi download, render &amp; upload ke TikTok + YouTube.\n"
        "  - <code>/dryrun [keyword]</code> : Tes render tanpa upload.\n"
        "  - <code>/status</code> : Cek rekap &amp; statistik video.\n"
        "  - <code>/keywords</code> : Kelola kata kunci pencarian."
    )
    _send_reply(msg, reply_to_message_id=msg_id, thread_id=thread_id)


def _trigger_pipeline_async(
    dry_run: bool, msg_id: int, thread_id: int, keyword: Optional[str] = None, fill_mode: bool = False
):
    """Run the TikTok pipeline in a detached thread so Telegram poller never freezes."""
    if _PIPELINE_LOCK.locked():
        _send_reply(
            "⏳ <b>Pipeline Sedang Berjalan!</b>\n"
            "Harap tunggu hingga proses render dan upload video saat ini selesai.",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
        return

    mode_text = "DRY RUN (Download & Render Saja)" if dry_run else "PRODUKSI (Download, Render & Upload TikTok)"
    fill_text = " | 📱 Full Screen (Center Crop)" if fill_mode else ""
    kw_text = f"Topik/Keyword: <i>{keyword}</i>" if keyword else "Keyword: <i>Preset Otomatis</i>"
    _send_reply(
        f"⚡ <b>Siap! Memulai TikTok Pipeline ({mode_text}{fill_text})</b>\n\n"
        f"🔍 {kw_text}\n"
        f"⏱ <i>Menganalisis chapters YouTube, auto-skip intro, dan merender vertikal 9:16...</i>",
        reply_to_message_id=msg_id,
        thread_id=thread_id,
    )

    def worker():
        with _PIPELINE_LOCK:
            try:
                def progress_cb(text: str):
                    # Notify significant steps back to Telegram topic
                    if any(key in text for key in ["Melompati intro", "Merender", "Mengunggah", "Sukses", "Shorts"]):
                        _send_reply(text, thread_id=thread_id)

                res = run_pipeline(
                    dry_run=dry_run,
                    count=1,
                    keyword_override=keyword,
                    progress_callback=progress_cb,
                    fill_mode=fill_mode,
                    upload_youtube=True,
                )

                if res.get("success"):
                    videos = res.get("videos", [])
                    v = videos[0] if videos else {}
                    title = v.get("title", "Video Konten")
                    channel = v.get("channel", "Official Creator")

                    if dry_run:
                        _send_reply(
                            f"🧪 <b>Dry Run Selesai dengan Sukses!</b>\n\n"
                            f"📌 <b>Judul:</b> {title}\n"
                            f"👤 <b>Channel:</b> {channel}\n"
                            f"📁 <b>Output Render:</b> <code>{v.get('path','')}</code>\n\n"
                            f"<i>Visual telah dirender 9:16 Lanczos + Unsharp CRF 18 siap upload.</i>",
                            thread_id=thread_id,
                        )
                    else:
                        yt_url = v.get("youtube_url", "")
                        yt_line = f"\n📺 <b>YouTube Shorts:</b> <a href='{yt_url}'>{yt_url}</a>" if yt_url else "\n📺 <b>YouTube Shorts:</b> <i>Upload gagal atau dinonaktifkan</i>"
                        _send_reply(
                            f"🎉 <b>Video Sukses Terunggah!</b>\n\n"
                            f"📌 <b>Judul:</b> {title}\n"
                            f"👤 <b>Channel:</b> {channel}\n"
                            f"✨ <b>Kualitas:</b> 1080x1920 HD (BT.709 High 4.2)\n"
                            f"🎵 <b>TikTok:</b> <i>Live di feed @pochitalkshop</i>"
                            f"{yt_line}",
                            thread_id=thread_id,
                        )
                else:
                    errors_str = "; ".join(res.get("errors", [])) or "Kandidat video tidak ditemukan"
                    _send_reply(
                        f"⚠️ <b>Pipeline Selesai dengan Catatan:</b>\n{errors_str}",
                        thread_id=thread_id,
                    )
            except Exception as e:
                logger.exception("Pipeline worker failed: %s", e)
                _send_reply(
                    f"❌ <b>Terjadi Kendala saat Menjalankan Pipeline:</b>\n<code>{e}</code>",
                    thread_id=thread_id,
                )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()


def _chat_with_tiktok_strategist(user_message: str, msg_id: int, thread_id: int):
    """Conversational AI mode: Answer questions as a seasoned TikTok Video Director & Operator."""
    cfg = load_config()
    db = VideoStateDB(cfg["db_path"])
    stats = db.get_stats(days=7)
    recent = db.get_recent_uploads(3)
    db.close()

    recent_summary = ""
    for r in recent:
        recent_summary += f"- {r.get('title')[:50]} (uploaded: {r.get('uploaded_at')[:16]})\n"

    system_prompt = (
        "Kamu adalah Apex — TikTok Video Director & Content Automation Operator.\n\n"
        "KARAKTER & GAYA:\n"
        "- Berbicara santai, tegas, penuh wawasan — seperti kreator konten senior yang ngobrol dengan timnya.\n"
        "- Nggak bertele-tele. Kalau ada jawaban 3 kalimat, kasih 3 kalimat. Kalau butuh penjelasan, tulis rapi dengan poin.\n"
        "- Gunakan bahasa Indonesia campur sedikit Inggris teknis (natural, bukan kaku).\n"
        "- Bisa bercanda ringan tapi tetap substantif — jangan terlalu formal atau terlalu norak.\n\n"
        "KEAHLIAN:\n"
        "- Algoritma FYP TikTok: retensi 3 detik pertama, completion rate, visual hook, pacing ritme kerja & ASMR.\n"
        "- Produksi vertikal: 9:16 blurred pillarbox Lanczos, BT.709 color grading, safe zone teks mobile, CRF 18 quality.\n"
        "- Curation niche: restorasi pisau (TysyTube), restorasi mobil barn find (WD Detailing), outdoor/mancing (Outdoor Boys), bushcraft (advoko MAKES), satisfying cleaning (ASMR rug/pressure wash), pertukangan (Bertram Craft).\n"
        "- Auto-skip intro YouTube lewat chapter detection, lalu potong 6 menit aksi inti terbaik.\n\n"
        f"STATUS BOT SAAT INI:\n"
        f"- Total video live di TikTok: {stats['total_uploaded']}\n"
        f"- 3 terakhir diupload:\n{recent_summary or '  (belum ada)'}\n\n"
        "ATURAN KOMUNIKASI:\n"
        "- Jawab langsung tanpa basa-basi pembuka ('Tentu!', 'Halo!', dll).\n"
        "- Jangan pernah tulis tag <think>, reasoning, atau langkah internal — langsung ke substansi.\n"
        "- Kalau user minta ide hook/judul/caption, kasih 2-3 opsi konkret siap pakai.\n"
        "- Kalau user minta eksekusi (upload, render, dry run), ingatkan cukup bilang 'upload video [topik]' atau ketik /upload.\n"
        "- Kalau evaluasi performa video, berikan analisis faktual berdasarkan niche dan format yang sedang jalan."
    )

    try:
        from src.brief.generator import _llm
        raw_content = _llm([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ], temperature=0.7, max_tokens=1500)

        reply_text = _clean_text_for_telegram(raw_content)

        if not reply_text:
            reply_text = (
                "Halo! Aku siap bantu kurasi dan otomatisasi konten TikTok kamu.\n\n"
                "Kamu bisa minta langsung:\n"
                "• <i>'cari video orang mancing dan upload'</i>\n"
                "• <i>'coba dry run tes render video kayu'</i>\n"
                "• Atau kirim <code>/upload</code>"
            )
        _send_reply(reply_text, reply_to_message_id=msg_id, thread_id=thread_id)
    except Exception as exc:
        logger.error("LLM chat error in TikTok topic: %s", exc)
        _send_reply(
            "Halo! Aku Apex, operator TikTok video kamu. "
            "Ketik <code>/upload</code> untuk memproses video baru, atau <code>/status</code> untuk cek rekap.",
            reply_to_message_id=msg_id,
            thread_id=thread_id,
        )
