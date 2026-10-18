"""
Organic Content Generator for Persona Rara Kinanti (@rarakinanti_)
Generates authentic Indonesian netizen takes without any affiliate links or promotional text.
Tailors tone to current time of day (WIB) and selected content pillar.
"""

import json
import re
from datetime import datetime, timezone, timedelta
from src.brief.generator import _llm
from src.image.vibe_search import search_vibe_image
from src.logger import get_logger
from src.organic.personas import get_persona

log = get_logger("organic_generator")

PILLARS = [
    "breaking_disaster",      # Bencana alam, cuaca ekstrem BMKG, musibah, krisis darurat
    "football_sports",        # Sepak bola Timnas, Liga 1, EPL, UCL, bursa transfer
    "tech_gadget_ai",         # Teknologi, AI, smartphone flagship, inovasi digital, chip
    "market_finance_stocks",  # IHSG, bursa saham IDX, Wall Street, S&P 500, dividen, makroekonomi
]


def _get_wib_context() -> tuple[int, str, str, int]:
    """Returns current WIB hour, mood descriptor, formatted date string, and year."""
    wib = timezone(timedelta(hours=7))
    now = datetime.now(wib)
    hour = now.hour
    date_str = now.strftime("%A, %d %B %Y")
    year = now.year

    if 6 <= hour < 10:
        mood = "pagi hari: ringkasan bursa global/Wall Street semalam, kabar gempa/cuaca BMKG pagi, update hasil pertandingan bola dini hari"
    elif 10 <= hour < 14:
        mood = "siang jam kerja: perdagangan IHSG sesi 1, kabar bencana aktual, update industri teknologi dan AI"
    elif 14 <= hour < 18:
        mood = "sore penutupan pasar: closing IHSG, kabar breaking news nasional, transfer sepak bola"
    elif 18 <= hour < 21:
        mood = "malam prime time: analisis laga sepak bola, diskusi tren teknologi terbaru, kabar mitigasi bencana terkini"
    else:
        mood = "malam bursa AS: pembukaan bursa saham Wall Street (S&P 500/Nasdaq), perkembangan rilis model AI global, jadwal bola larut malam"

    return hour, mood, date_str, year


CHAIN_PART_COUNT = 4

# Chains are only worth the extra reading for pillars where a longer argument
# holds attention (money and gadget decisions). Bencana never gets a chain:
# urgency wants one post, not a four-part essay.
CHAIN_PILLARS = ("tech_gadget_ai", "market_finance_stocks")

# The chain contract. Every clause exists to buy one more scroll: an unresolved
# opening, a fact that contradicts the obvious reading, a turn onto the reader,
# and a close precise enough that answering it requires typing.
CHAIN_RULES = """
STRUKTUR THREAD CHAIN — WAJIB TEPAT {n} ELEMEN DI `parts`, URUTAN INI:

PART 1 — HOOK (open loop):
- Buka dengan klaim tajam atau anomali. DILARANG memberi kesimpulan, hasil akhir, atau jawaban.
- Janji struktur dengan angka ganjil spesifik: "ada 3 alasannya, dan yang ketiga ...".
- ANGKA HANYA BOLEH DIPAKAI KALAU ADA DI SUMBER BERITA. Kalau tidak ada sumbernya, tulis versi tanpa angka. DILARANG SEKALI-KALI MENGARANG ANGKA, persentase, atau nama lembaga.

PART 2 — KONTRADIKSI / DATA:
- Satu angka atau fakta dari sumber yang membantah dugaan umum, lalu tunjukkan apa artinya.
- Tutup dengan celah baru, bukan penjelasan tuntas.

PART 3 — IMPLIKASI KE PEMBACA:
- Pindah dari topik ke pembaca: "Buat lo yang ..., ini ...".
- Sebutkan perasaan atau konsekuensi nyata (saldo turun, nunggu gajian, tanggungan), bukan abstraksi.

PART 4 — HOT TAKE + PEMICU KOMENTAR:
- Opini tajam milik sendiri yang bisa dibantah, lalu SATU pemicu dari dua jenis ini saja:
  (a) prediksi biner + stakes — "Tahun ini tutup di atas atau bawah 7.000?"
  (b) minta pengalaman sempit — "Yang udah pake seri ini, awet berapa bulan?"
- DILARANG pemicu klise: "Menurut kalian?", "Gimana menurut kalian?", "Tim mana?", "Ada yang ngerasain?"
- Pemicu HARUS spesifik ke isi thread, bukan pertanyaan yang bisa ditempel di post mana pun.

Setiap part: 1-3 kalimat pendek, ada jeda baris, satu ide saja.

bonus_reply (satu kalimat, opsional tapi disarankan):
- Satu potongan yang sengaja disimpan: angka minor, temuan minor, atau prediksi yang belum divalidasi.
- Boleh mengundang sanggahan. Boleh spesulatif tapi jujur ("kabar yang beredar", "kalau bener").
- BUKAN pengulangan part 4, BUKAN ajakan komentar, BUKAN kesimpulan utama.
- BUKAN promosi, BUKAN link, BUKAN nama produk.
""".replace("{n}", str(CHAIN_PART_COUNT))


_FALLBACK_CONTENT = "Isu {topic} ini punya implikasi nyata kalau dicermati secara objektif. Dampaknya ke depan bakal signifikan."

# A figure the post may not contain unless the source states it. Covers
# percentages, plain integers, thousands separators and decimal points — the
# shapes an LLM reaches for when it wants to sound precise. Digit runs glued
# to letters are excluded: "3D", "4K", "5G" are product designations, not
# statistics, and deleting them leaves "modeling D" behind.
_FIGURE_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?:\s?%|persen|ribu juta|miliar|triliun|ribu)?(?![A-Za-z0-9])",
    re.I,
)
# A bare year is a horizon marker, not a statistic. The chain's comment trigger
# is a binary prediction against a year ("sebelum atau sesudah 2028?"), and
# stripping it would remove the one mechanic the whole structure is built on.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def _figure_tokens(text: str) -> set:
    """Returns the numeric strings written into a post, normalised."""
    if not text:
        return set()
    out = set()
    for m in _FIGURE_RE.finditer(text):
        raw = m.group(0)
        core = re.sub(r"[^\d]", "", raw)
        if not core:
            continue
        # A bare year is a horizon marker, not a statistic — the chain's
        # comment trigger is a binary prediction against a year, and blocking
        # it would delete the mechanic the structure is built on.
        if _YEAR_RE.match(raw.strip()):
            continue
        # Normalise so "40%", "40 %" and "40 persen" collapse to one token.
        out.add(re.sub(r"^0+(?=\d)", "", core).lstrip("0") or "0")
    return out


def _sourced_tokens(topic: str, source_summary: str) -> set:
    """Figures the headline or the feed description actually supports."""
    return _figure_tokens(f"{topic} {source_summary or ''}")


def _strip_unsourced_numbers(
    content: str, parts: list, bonus_reply: str, topic: str, source_summary: str
) -> tuple:
    """Returns (content, parts, bonus_reply, fabricated).

    fabricated is the set of numeric tokens the post used that the source never
    stated. When non-empty the caller regenerates; the cleaned text it returns
    is the safe fallback if that second attempt also fails.
    """
    allowed = _sourced_tokens(topic, source_summary)
    combined = " \n\n ".join([content or ""] + [bonus_reply or ""]) + " \n ".join(parts or [])
    fabricated = _figure_tokens(combined) - allowed
    if not fabricated:
        return content, parts, bonus_reply, set()

    def _sub(m):
        core = re.sub(r"[^\d]", "", m.group(0))
        norm = re.sub(r"^0+(?=\d)", "", core).lstrip("0") or "0"
        return "" if norm in fabricated else m.group(0)

    def _clean(s: str) -> str:
        if not s:
            return s
        s = re.sub(_FIGURE_RE.pattern, _sub, s, flags=re.I)
        s = re.sub(r"[ \t]{2,}", " ", s)
        # Deleting "70%" from "pangkas konsep 70%." would otherwise leave
        # "konsep ." — a gap that reads as a typo on a live account.
        s = re.sub(r"\s+([,.;:!?])", r"\1", s)
        return s.strip()

    parts = [_clean(p) for p in (parts or [])]
    content = _clean(content) if content else ""
    bonus_reply = _clean(bonus_reply) if bonus_reply else ""
    return content, parts, bonus_reply, fabricated


def _regenerate_without_numbers(
    topic, pillar, post_format, post_style, persona, post_structure,
    is_chain, source_summary,
) -> tuple:
    """One clean second attempt at the same slot.

    Re-enters generate_organic_post with _retry_done set so the guard cannot
    fire again — a model that fabricates twice must still yield a post, and
    this time every figure has been purged from the material it is given.
    """
    try:
        out = generate_organic_post(
            topic=topic,
            pillar=pillar,
            post_format=post_format,
            post_style=post_style,
            persona=persona,
            post_structure=post_structure,
            source_summary=source_summary,
            _retry_done=True,
        )
        return out.get("content", ""), out.get("parts") or [], out.get("bonus_reply", "")
    except Exception as e:
        log.warning(f"Number-free regeneration failed: {e}")
        return "", [], ""


def _normalize_chain(raw) -> tuple:
    """Validates the chain the LLM produced. Returns (parts, error).

    parts is exactly CHAIN_PART_COUNT non-empty strings, or an empty list when
    the model produced something unusable. A chain that arrives short or as one
    blob defeats the entire structure, so the caller falls back to a single post
    instead of publishing a broken thread to a live account.
    """
    if not isinstance(raw, list):
        return [], f"expected a list, got {type(raw).__name__}"
    parts = [str(p).strip() for p in raw if str(p or "").strip()]
    if len(parts) < CHAIN_PART_COUNT:
        return [], f"only {len(parts)} usable parts (need {CHAIN_PART_COUNT})"
    return parts[:CHAIN_PART_COUNT], ""


def generate_organic_post(
    topic: str,
    pillar: str = "breaking_disaster",
    post_format: str = "with_image",
    post_style: str = "snappy_take",
    persona: str = None,
    post_structure: str = "single",
    source_summary: str = "",
    _retry_done: bool = False,
) -> dict:
    """
    Generates an authentic organic Threads post in Netizen 'Warga Threads' voice or custom persona.
    Focuses exclusively on: Bencana, Sepak Bola, Teknologi/AI, dan Saham/IHSG.
    Supports both text-only threads and image-backed threads.

    post_structure:
      "single" — one short hot take (default, unchanged behaviour).
      "chain"  — 4-part thread built to be read to the end: hook with an open
                 loop, contradicting data, implication for the reader, then a hot
                 take ending in a specific comment trigger. A `bonus_reply`
                 carries the withheld fragment into the reply section.
    """
    hour, mood, date_str, year = _get_wib_context()
    persona_cfg = get_persona(persona)
    is_chain = post_structure == "chain"

    style_guide = ""
    if post_style == "morning_greeting":
        style_guide = (
            "GAYA: Sapaan pagi yang singkat & langsung sambung ke inti bahasan.\n"
            "Contoh pembuka: 'morning wargaa threadss, udah pada ngopi belom? ...'\n"
            "Setelah 1 sapaan pendek, LANGSUNG masuk ke substansi/opini tanpa kata pengantar 'lagi rame/lagi sliweran'."
        )
    elif post_style == "midday_banter":
        style_guide = (
            "GAYA: Celetukan siang jam istirahat. Langsung to the point ke sudut pandang kritis atau opini tajam."
        )
    elif post_style == "afternoon_wrap":
        style_guide = (
            "GAYA: Opini sore hari atau penutupan bursa. Langsung to the point ke evaluasi atau insight."
        )
    elif post_style == "night_scrolling":
        style_guide = (
            "GAYA: Opini reflektif malam hari. Langsung kupas inti isu atau analisis tanpa kata pengantar klise."
        )
    else:  # snappy_take
        style_guide = (
            "GAYA: TO THE POINT! Langsung ke substansi opini, argumen, reaksi tajam, atau fakta utama.\n"
            "Kalimat pertama langsung membedah isu tanpa basa-basi kata pembuka apa pun."
        )

    format_guide = ""
    if post_format == "text_only":
        format_guide = (
            "FORMAT: POSTINGAN TEKS POLOS (TANPA GAMBAR).\n"
            "Tulis postingan yang padat, renyah, berbobot, dan to the point."
        )
    else:
        format_guide = (
            "FORMAT: POSTINGAN DENGAN GAMBAR EDITORIAL (4:5).\n"
            "WAJIB sertakan `image_query` berupa deskripsi visual pemandangan / objek nyata di lapangan (3-6 kata bahasa Indonesia).\n"
            "STANDAR VISUAL TINGGI (KOMPOSISI & ANGLE BERKUALITAS):\n"
            "- PRIORITASKAN ANGLE DINAMIS & EMOSI KUAT: Cari foto dengan low-angle, medium close-up, aksi puncak peristiwa (climax action), ekspresi emosional tajam (teriak, fokus tegang, selebrasi, haru), dan subjek fokus tajam di tengah frame (shallow depth of field).\n"
            "- HINDARI FOTO DATAR / STATIS: Jangan cari foto orang duduk berjejer, pidato di podium, konferensi pers formal, plang nama gedung, atau landscape kosong tanpa interaksi manusia/aksi.\n"
            "- Contoh sepak bola -> image_query: 'selebrasi emosional pemain sepak bola gol lapangan' atau 'aksi duel perebutan bola pemain liga'\n"
            "- Contoh bencana alam -> image_query: 'evakuasi dramatis warga terjang arus banjir' atau 'kobaran api tebal kebakaran pemukiman warga'\n"
            "- Contoh pasar saham/IHSG -> image_query: 'ekspresi tegang pialang saham layar indeks merah bursa'\n"
            "- Contoh teknologi/AI -> image_query: 'close up interaksi tangan robotik chip ai futuristik'\n"
            "- HINDARI foto pasien di ranjang RS, ruang rawat, atau konferensi pers podium kecuali topiknya spesifik tentang itu."
        )

    persona_section = f"""
KARAKTER & TONE PERSONA KHUSUS:
Nama Persona: {persona_cfg.name} {persona_cfg.emoji}
{persona_cfg.prompt_guide}
"""

    # A chain stays scannable because each PART is short, not because the whole
    # thread is — the single-post ceiling would collapse 4 parts into one blob.
    if is_chain:
        length_rule = (
            "- DILARANG BERBELIT: tiap part maksimal 3 kalimat pendek dan 1 ide saja. "
            "Total tepat 4 part — JANGAN menggabungkannya jadi satu paragraf panjang!"
        )
        structure_rules = CHAIN_RULES
        output_schema = (
            'Format output JSON:\n'
            '{\n'
            f'  "parts": ["part 1", "part 2", "part 3", "part {CHAIN_PART_COUNT}"],\n'
            '  "bonus_reply": "satu kalimat bonus untuk kolom balasan (kosongkan jika tidak ada)",\n'
            '  "image_query": "kata kunci foto berita asli (kosongkan jika text_only)"\n'
            '}'
        )
    else:
        length_rule = (
            "- DILARANG PANJANG / BERTELE-TELE: Orang malas membaca tulisan panjang di Threads. Buat SANGAT SINGKAT, cukup 1 sampai 2 kalimat padat (maksimal 20-35 kata)!"
        )
        structure_rules = (
            "STRUKTUR KONTEN TO THE POINT (1-2 KALIMAT PADAT):\n"
            "- Panjang: Cukup 1-2 kalimat pendek (20-35 kata). Ringkas, tajam, nendang.\n"
            "- Bentuk: Pernyataan lugas, opini kritis berbobot, atau fakta keras.\n"
            "- HINDARI BERTANYA: Buat pernyataan atau hot take yang kuat, biarkan isi argumennya yang otomatis memancing orang mendebat atau setuju di kolom komentar!"
        )
        output_schema = (
            'Format output JSON:\n'
            '{\n'
            '  "content": "postingan threads",\n'
            '  "image_query": "kata kunci foto berita asli (kosongkan jika text_only)"\n'
            '}'
        )

    # Numbers can only come from a real source. Without this block the model
    # was told "use the figures from the source" while being handed nothing but
    # a headline, so it filled the gap with invented percentages.
    source_section = ""
    if source_summary.strip():
        source_section = (
            "SUMBER BERITA (deskripsi feed — INI SATU-SATUNYA tempat angka boleh diambil):\n"
            f"{source_summary.strip()}\n"
            "- Setiap angka, persentase, tanggal, dan nama lembaga di tulisan lo WAJIB ada di ringkasan di atas.\n"
            "- Kalau ringkasan TIDAK memuat angka, tulis tanpa angka sama sekali. JANGAN mengarang.\n"
        )
    else:
        source_section = (
            "SUMBER BERITA: (tidak ada ringkasan tersedia)\n"
            "- DILARANG memakai angka, persentase, atau statistik apa pun. Tulis versi tanpa angka.\n"
            "- DILARANG menyebut nama lembaga/instansi yang tidak muncul di judul topik.\n"
        )

    prompt = f"""Kamu adalah akun netizen Indonesia yang cerdas, to the point, santai, dan berwawasan luas (Warga Threads).
{persona_section}
FOKUS KONTEN HANYA 4 PILAR UTAMA:
1. Bencana Alam & Krisis Nasional (BMKG, BNPB, gempa, banjir bandang, cuaca ekstrem, mitigasi)
2. Sepak Bola & Olahraga (Timnas Indonesia, Liga 1, Premier League, Liga Champions, bursa transfer)
3. Teknologi, Gadget & AI (Generative AI, smartphone baru, chip, ekosistem digital, sains)
4. Pasar Saham & Ekonomi (IHSG, saham lokal IDX, Wall Street AS, S&P 500, Nasdaq, dividen, makroekonomi)

DILARANG KERAS (STRICT PROHIBITION):
- DILARANG KERAS SELALU MENUTUP DENGAN PERTANYAAN KLISE: Jangan pakai "Menurut kalian...?", "Gimana menurut kalian?", "Kalian tim mana?", "Ada yang ngerasain hal serupa?". Netizen bosan membaca format yang isinya pertanyaan terus-menerus!
{length_rule}
- WAJIB TO THE POINT: Kalimat pertama langsung menusuk ke subjek, opini tajam, celetukan kritis, atau fakta utama tanpa basa-basi!
- DILARANG KERAS kata pembuka klise: "lagi rame", "lagi sliweran", "dari tadi sliweran", "rame banget ngebahas", "scrolling timeline", "timeline hari ini rame", "nemu bahasan menarik".
- DILARANG membahas gosip artis, infotainment, drama selebriti, pacaran/cerai/selingkuh artis.
- DILARANG membawa topik KRL Manggarai, anak kos, atau keluhan pribadi klise.
- DILARANG menggunakan slang kaku (istg, literally, chaos, pov, cape bgt).
- DILARANG menggunakan hashtag (#).
- DILARANG promosi produk atau mencantumkan link afiliasi.

PANDUAN GAYA & FORMAT:
{style_guide}
{format_guide}

{structure_rules}

KONTEKS WAKTU REAL-TIME:
- Tanggal & Waktu: {date_str}, pukul {hour:02d}:00 WIB ({mood})
- Tahun: {year}
- PENTING: Seluruh konteks WAJIB berbasis fakta & perkembangan terkini tahun {year}. DILARANG KERAS mengutip data basi/usang dari tahun 2023 atau tahun-tahun sebelumnya seolah-olah terjadi hari ini.
- Topik: "{topic}"
- Pilar: {pillar}
- Style: {post_style}
- Format: {post_format}

{source_section}
{output_schema}
Hanya output JSON valid tanpa markdown ticks.
"""

    log.info(f"Generating organic post for topic: '{topic}' (pillar={pillar}, structure={post_structure})")
    # A chain is four posts plus a bonus reply — 600 tokens would cut it off
    # mid-sentence.
    max_tokens = 900 if is_chain else 600
    resp_text = _llm([{"role": "user", "content": prompt}], temperature=0.85, max_tokens=max_tokens)

    # Clean potential markdown ticks
    clean_json = re.sub(r"^```json\s*", "", resp_text.strip(), flags=re.I)
    clean_json = re.sub(r"^```\s*", "", clean_json.strip())
    clean_json = re.sub(r"\s*```$", "", clean_json.strip())

    parts: list = []
    bonus_reply = ""
    try:
        data = json.loads(clean_json)
        vibe_query = data.get("image_query", data.get("vibe_image_query", "")).strip()
        if is_chain:
            parts, chain_err = _normalize_chain(data.get("parts"))
            if not parts:
                raise ValueError(f"unusable chain: {chain_err}")
            bonus_reply = (data.get("bonus_reply") or "").strip()
            content = "\n\n".join(parts)
        else:
            content = data.get("content", "").strip()
            if not content:
                raise ValueError("Empty content from LLM")
    except Exception as e:
        if is_chain:
            # Publishing a half-formed chain to a live account costs more than
            # one wasted LLM call.
            log.warning(f"Chain generation failed ({e}). Falling back to a single post.")
            return generate_organic_post(
                topic=topic,
                pillar=pillar,
                post_format=post_format,
                post_style=post_style,
                persona=persona,
                post_structure="single",
                source_summary=source_summary,
            )
        log.warning(f"Failed to parse LLM JSON for organic post: {e}. Using fallback.")
        content = f"Isu {topic} ini punya implikasi nyata kalau dicermati secara objektif. Dampaknya ke depan bakal signifikan."
        vibe_query = f"{topic} berita indonesia"

    # The chain rule bans invented figures, but a rule in prose is not
    # enforcement. This is the enforcement: any figure the model wrote that the
    # source never stated gets stripped, and the whole post is regenerated
    # once. If the second attempt still fabricates, the caller gets a
    # number-free version rather than a made-up statistic.
    if not _retry_done:
        content, parts, bonus_reply, fabricated = _strip_unsourced_numbers(
            content, parts, bonus_reply, topic, source_summary
        )
        if fabricated:
            log.warning(
                f"Chain/post cited figures absent from the source "
                f"({', '.join(sorted(fabricated)[:5])}); "
                "regenerating without them."
            )
            content, parts, bonus_reply = _regenerate_without_numbers(
                topic, pillar, post_format, post_style, persona, post_structure,
                is_chain, source_summary,
            )
            if not content:
                # Second attempt was worse than the first. Salvage the first one
                # with the fabricated figures already removed.
                content = "\n\n".join(parts) if parts else _FALLBACK_CONTENT.format(topic=topic)
                bonus_reply = ""

    # The card carries the hook only: part 1 is what sells the scroll, and
    # printing all four parts onto one image turns the thread into a poster.
    card_summary = parts[0] if parts else content
    result_base = {
        "topic": topic,
        "pillar": pillar,
        "post_format": post_format,
        "post_style": post_style,
        "persona": persona_cfg.key,
        "post_structure": post_structure if parts else "single",
        "content": content,
        "parts": parts,
        "bonus_reply": bonus_reply,
    }

    image_url = None
    if post_format == "text_only":
        log.info(f"Generated TEXT-ONLY netizen post (skipping image search)")
        vibe_query = ""
    else:
        # Ground the photo search in the topic's real entities. The LLM's visual
        # query is atmospheric and can drift off-topic; the entity-derived query
        # keeps the photo attached to the actual news (karhutla → burning peatland,
        # IHSG halt → red trading board), so image and headline never disagree.
        from src.trends.multi_source import fetch_topic_image_query

        # Market stories must show the actual instrument, not a stock photo of a
        # trading floor shot years ago — the number in the headline (IHSG 5.185)
        # has to match the number on the card.
        chart_bytes = None
        try:
            from src.image.market_chart import render_market_chart, symbol_for_topic

            if symbol_for_topic(topic):
                chart_bytes = render_market_chart(topic)
        except Exception as e:
            log.warning(f"Market chart render failed, falling back to photo: {e}")

        entity_query = fetch_topic_image_query(topic, pillar)
        vibe_query = entity_query or vibe_query or f"{topic} berita"
        log.info(f"Image query grounded to topic entities: '{vibe_query}'")

        if chart_bytes:
            from src.image.card_generator import generate_editorial_card

            log.info("Using live market chart as card image")
            image_url = generate_editorial_card(
                photo_url="",
                headline=topic,
                summary=card_summary,
                pillar=pillar,
                source_hint="yahoo finance",
                photo_bytes=chart_bytes,
            )
            return {
                **result_base,
                "vibe_image_query": f"live chart: {topic}",
                "image_url": image_url,
            }

        # Fetch authentic real-life HD photo from news portals / internet
        raw_photo_url = search_vibe_image(vibe_query, raw_topic=topic)

        if raw_photo_url:
            from src.image.card_generator import generate_editorial_card
            # Renders clean editorial news card (Real HD photo on top + Dark template with text on bottom)
            image_url = generate_editorial_card(
                photo_url=raw_photo_url,
                headline=topic,
                summary=card_summary,
                pillar=pillar,
            )
        else:
            image_url = ""

    return {
        **result_base,
        "vibe_image_query": vibe_query,
        "image_url": image_url,
    }


if __name__ == "__main__":
    res = generate_organic_post("cuaca panas jakarta bmkg", "relatable_struggle")
    print("Content:", res["content"])
    print("Image query:", res["vibe_image_query"])
    print("Image URL:", res["image_url"])
