import json
import re
from src.brief.generator import _llm
from src.trends.live_trends import get_live_id_trends
from src.image.vibe_search import search_vibe_image
from src.logger import get_logger
from src.guardrails import repair_json

log = get_logger("social_copy")


def generate_social_bundle(product: dict, trend_hint: str = "") -> dict:
    """
    Generates viral Indonesian netizen copy using the "Pure Viral Bait + Numpang Lapak Spill" strategy.
    The main thread does NOT have to match the product — it focuses 100% on engagement & virality.
    The first reply transitions smoothly to the affiliate link.
    """
    title = product.get("name", "Produk Rekomendasi")
    desc = product.get("description", "")
    price = product.get("price", "")
    url = product.get("affiliate_url", "")

    # Resolve trending topic
    if not trend_hint:
        try:
            live = get_live_id_trends()
            if live:
                trend_hint = f"Topik viral saat ini: {', '.join(live[:4])}"
        except Exception:
            pass

    trend_guidance = (
        f"Konteks trend spesifik yang bisa kamu gunakan: '{trend_hint}'"
        if trend_hint
        else "Pilih salah satu topik yang selalu ramai diperdebatkan netizen Indonesia: drama kantor/WFO, kelakuan absurd di KRL Manggarai, perdebatan receh viral, atau cuaca ekstrem."
    )

    prompt = f"""Kamu adalah Rara Kinanti (@rarakinanti_), cewek 22 tahun di Jakarta. Fresh grad setahun kerja kantoran, ngekos di Tebet, commute KRL Manggarai tiap hari. Jiwa masih maba tapi badan udah budak korporat. Overthinker malam hari, pekerja lecek pagi hari. Demen ngopi, self reward boncos, dan nyari printilan penyelamat hidup.

KEPRIBADIAN RARA:
- Relatable & gak jaim. Sering ngeluh tapi tetep dijalanin (resigned acceptance).
- Nada: sarkas tapi manis, kayak kakak kosan yang sering curhat di dapur.
- Sering pakai: istg, cape bgt, demi apapun, ga paham lagi, pov lu lagi di fase, sender mau curhat, pliss, wkwk, literally, boncos, racun, worth it parah, rekening menangis, nangis bgt, meleyot
- Emoji favorit: 😭 🥲 ✨ 💀 ☕ 🙏 (pakai secukupnya, ga lebay)

VOICE & GAYA NULIS RARA:
1. HURUF KECIL semua (lowercase aesthetic). Ga pernah nulis huruf kapital rapi kayak artikel formal.
2. Kalimat pendek, potong-potong — kayak lagi ngetik cepet di hp sambil di KRL.
3. POLA KALIMAT KHAS RARA:
   - 'istg hari ini panas bgt sampe rasanya jalan dari stasiun ke kantor = workout 💀'
   - 'pov: lu lagi di fase mau resign tapi rekening belum siap wkwkwk sender relate'
   - 'ga paham lagi sama orang yg bisa produktif jam 1 malem, gue mah udah rebahan dari jam 9'
   - 'self reward katanya, boncos katanya 🥲'
   - 'demi apapun, krl manggarai hari ini kek sardin kalengan. gue cuma bisa pasrah berdiri 40 menit'
   - 'kalian pernah ga sih ngerasa gaji itu cuma numpang lewat doang di rekening'
4. TOPIK YANG RARA SERING BAHAS:
   - Commute KRL Manggarai (desek-desekan, AC mati, stasiun transit chaos)
   - Dilema WFO (pengen resign, lembur tanpa uang lembur, AC kantor anget)
   - Keuangan anak muda (gaji numpang lewat, self reward boncos, checkout shopee jam 1 malem)
   - Cuaca panas Jakarta (BMKG 37-38°C, aspal meleleh, keringetan lecek sampe kantor)
   - Quarter life crisis dikemas lucu
5. JANGAN PERNAH sebut produk, brand, atau link di Postingan Utama! Ini murni curhat/keluh kesah Rara yang bikin orang gatal pengen reply.

CARA RARA SPILL PRODUK DI REPLY:
- JANGAN PERNAH bilang 'mumpung rame' atau 'numpang lapak' — itu awkward kalau ga ada engagement.
- Buka natural kayak lanjutan curhat: 'btw ini bukan iklan tp emg worth it parah...' atau 'oke gue spill penyelamat hidup gue belakangan ini pliss...' atau 'ga nyangka barang receh ini yg bikin survive...' atau 'nah karena pada relate, gue rekomendasiin ini deh...'
- Spill produk santai kayak Rara beneran pake sendiri. Cerita personal, bukan promosi.
- WAJIB sertakan link afiliasi di akhir reply.

Info Produk (HANYA untuk balasan/reply):
- Nama Produk: {title}
- Fitur: {desc}
- Estimasi Harga: {price or 'Cek promo di link'}
- Link: {url}

{trend_guidance}

Format output HARUS JSON valid:
{{
  "hook_viral_1": "Tweet utama 1: Curhat/menfess lokal Indo yang super relatable (huruf kecil semua, gaya X). Tanpa link & tanpa sebut produk!",
  "hook_viral_2": "Tweet utama 2: Sudut pandang topik lain yang juga viral & bikin gatal komen. Tanpa link & tanpa sebut produk!",
  "vibe_image_query": "English visual aesthetic keywords for Pinterest (contoh: 'krl commuter line crowded aesthetic' atau 'jakarta street sunny hot aesthetic' atau 'office overtime tired aesthetic')",
  "threads_reply": "Balasan pertama: sambungan curhat natural (misal: 'btw penyelamat hidup gue belakangan ini...') + spill produk santai + link.",
  "facebook_post": "Post Facebook santai ala cerita netizen tentang pengalaman pakai produk ini + link."
}}

Hanya output JSON valid. Tanpa markdown ticks."""

    log.info(f"Generating viral bait + numpang lapak bundle for: '{title}'")
    resp_text = _llm([{"role": "user", "content": prompt}], temperature=0.8, max_tokens=1500)

    clean_json = repair_json(resp_text)
    try:
        bundle = json.loads(clean_json)
        required_keys = ["hook_viral_1", "threads_reply", "facebook_post"]
        for k in required_keys:
            if k not in bundle or not bundle[k]:
                raise ValueError(f"Missing key in JSON: {k}")
    except Exception as e:
        log.warning(f"Failed to parse LLM JSON: {e}. Using fallback viral bait template.")
        bundle = {
            "hook_viral_1": "ga paham lagi sama orang yang rela desek-desekan di krl manggarai jam 8 pagi demi sampai kantor tepat waktu padahal ujungnya lembur tanpa uang lembur 😭",
            "hook_viral_2": "pov lu lagi di fase wfo lecek abis transit manggarai, keringetan sampe baju tembel, terus nyampe kantor ac nya anget. cape bgt sumpah",
            "vibe_image_query": "krl manggarai penuh sesak",
            "threads_reply": f"btw ini bukan iklan tp emg worth it parah, gue pake sendiri dan ngebantu bgt: {title}. anginnya kenceng parah buat harga segini. cek sini deh: {url}",
            "facebook_post": f"Cerita sedikit, dari kemarin sering banget pusing kalau cuaca lagi panas atau ruangan pengap. Akhirnya nemu ini dan ngebantu banget: {url}",
        }

    hook_1 = bundle.get("hook_viral_1", "")
    hook_2 = bundle.get("hook_viral_2", "")
    th_reply = bundle.get("threads_reply", "")
    vibe_query = bundle.get("vibe_image_query", "krl manggarai penuh sesak")

    bundle["threads_main"] = hook_1

    # Search authentic relatable vibe photo for the main post
    vibe_img_url = search_vibe_image(vibe_query)
    bundle["vibe_image_url"] = vibe_img_url
    bundle["product_image_url"] = product.get("image_url", "")

    # Format Telegram Showcase Cards
    bundle["telegram_main_caption"] = (
        f"🔥 <b>POST UTAMA (Viral Bait - Threads/X)</b>\n"
        f"<i>(Gunakan foto suasana ini di postingan utama feed)</i>\n\n"
        f"📌 <b>Opsi 1:</b>\n<i>{hook_1}</i>\n\n"
        f"🎯 <b>Opsi 2:</b>\n<i>{hook_2}</i>"
    )

    bundle["telegram_reply_caption"] = (
        f"💬 <b>KOMEN PERTAMA (Numpang Lapak + Spill Link)</b>\n"
        f"<i>(Gunakan foto produk ini di reply/balasan pertama)</i>\n\n"
        f"<i>{th_reply}</i>\n\n"
        f"💰 <b>Harga:</b> {price or 'Cek promo di link'}\n"
        f"🛒 <b>Beli di sini:</b> {url}"
    )

    bundle["telegram_caption"] = (
        f"🔥 <b>STRATEGI: VIRAL BAIT + NUMPANG LAPAK</b>\n\n"
        f"🧵 <b>POST UTAMA (Foto Suasana):</b>\n<i>{hook_1}</i>\n\n"
        f"💬 <b>KOMEN PERTAMA (Foto Produk):</b>\n<i>{th_reply}</i>\n\n"
        f"🛒 <b>Link:</b> {url}"
    )

    return bundle
