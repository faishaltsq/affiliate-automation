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

log = get_logger("organic_generator")

PILLARS = [
    "breaking_trend",      # Viral news, memes, drama netizen
    "relatable_struggle",  # Commute KRL Manggarai, WFO drama, AC kantor mati, burnout
    "hot_take_debate",     # Debat receh makanan, kebiasaan unik, lifestyle
    "pop_culture_meme",    # K-pop, film lokal/bioskop, konser, viral TikTok sound
    "menfess_qna",         # Pertanyaan mancing engagement (drop playlist, spill)
    "lifestyle_dump",      # Foto aesthetic + one-line thought santai
]


def _get_wib_context() -> tuple[int, str]:
    """Returns current WIB hour and mood descriptor."""
    wib = timezone(timedelta(hours=7))
    now = datetime.now(wib)
    hour = now.hour

    if 6 <= hour < 10:
        mood = "pagi rush hour: lagi commute KRL Manggarai yang chaos, ngantuk parah, butuh kopi, lecek sebelum nyampe kantor"
    elif 10 <= hour < 14:
        mood = "siang lemes: mikirin makan siang apa, boncos gofood, meeting ga guna, ac kantor anget, pengen tidur siang"
    elif 14 <= hour < 18:
        mood = "sore menjelang pulang: energi udah 1%, ngitung menit jam pulang, siap-siap perang di stasiun transit"
    elif 18 <= hour < 21:
        mood = "malam santai/self reward: baru nyampe kosan, leha-leha, makan batagor/seblak, scrolling timeline"
    elif 21 <= hour <= 23:
        mood = "malam overthinking: quarter life crisis, mikirin karir dan gaji numpang lewat, rebahan di kasur kamar kos"
    else:
        mood = "tengah malam/insomnia: belum bisa tidur, scroll hp sambil mikirin hal-hal random di hidup"

    return hour, mood


def generate_organic_post(topic: str, pillar: str = "breaking_trend") -> dict:
    """
    Generates an authentic organic Threads post in Rara Kinanti's voice.
    No products, no affiliate links, no promotional hashtags.
    """
    hour, mood = _get_wib_context()

    prompt = f"""Kamu adalah akun netizen Indonesia yang suka ngomongin berita dan isu yang lagi terjadi. Bukan jurnalis, bukan portal berita — cuma orang biasa yang mantau timeline dan punya pendapat.

CARA NULIS:
- Bahasa Indonesia santai, kayak ngobrol biasa. Ga formal, ga alay.
- Kapitalisasi natural — ga harus lowercase semua, ga harus rapi juga.
- Fokus ke SUBSTANSI topik yang dibahas. Jangan jadiin soal diri sendiri atau kehidupan pribadi.
- JANGAN pernah sebut KRL, Manggarai, kosan, kantor, commute, kecuali topiknya emang soal itu.
- JANGAN pakai slang berlebihan (istg, literally, chaos, pov, cape bgt).
- JANGAN pakai hashtag.
- JANGAN sebut produk, brand, jualan, atau link.
- Emoji: boleh 0 atau 1. Ga wajib.

STRATEGI KONTEN YANG PERFORM BAGUS DI THREADS:
Pilih SALAH SATU pendekatan ini berdasarkan topiknya, dan VARIASIKAN — jangan selalu pilih pertanyaan:

1. TAKE TAJAM / OPINI SINGKAT — satu atau dua kalimat yang bikin orang penasaran atau mau debat. TIDAK diakhiri tanda tanya.
   Contoh: "inflasi bahan pokok naik terus tapi yang paling banyak ngomong soal ini justru yang paling jauh dari dampaknya."

2. REAKSI SINGKAT + INSIGHT — 2 kalimat: reaksi jujur, lanjut dengan insight relevan. TIDAK harus diakhiri tanda tanya.
   Contoh: "banjir bandang di padang parah banget. drainase kota sama tata ruang emang PR menahun yang ga pernah tuntas tiap musim hujan."

3. ANGLE YANG GA DIBAHAS ORANG — sorotin sudut yang biasanya luput dari diskusi mainstream. Statement, bukan pertanyaan.
   Contoh: "yang menarik dari kasus ini bukan siapa yang salah, tapi kenapa sistemnya bisa biarin ini terjadi berulang."

4. PERTANYAAN TERBUKA — gunakan HANYA kalau topiknya genuinely debatable dan belum ada jawaban jelas. Jangan terlalu sering.
   Contoh: "Ezra Walian jarang dapet menit bermain di timnas, padahal potensinya lumayan. sistem permainan kita yang belum cocok, atau emang ada faktor lain?"

5. KONTEN BENCANA / MUSIBAH — WAJIB sebutkan:
   - Lokasi spesifik (kota, kecamatan, provinsi)
   - Kondisi terkini yang kamu tau dari konteks topik (korban, pengungsi, kerusakan, status evakuasi)
   - Sumber info kalau memungkinkan (BNPB, BMKG, Pemda, media tertentu)
   - Ajakan simpati yang tulus (doa/donasi/spread awareness), bukan sekadar "sedih banget"
   Contoh: "Banjir bandang di Kota Padang, Sumbar hari ini parah banget. BNPB laporin ratusan rumah terendam dan beberapa warga masih dievakuasi. semoga bantuan cepat sampai dan semua selamat."

PENTING: Mayoritas postingan harus berupa STATEMENT / OPINI, bukan pertanyaan. Pertanyaan boleh muncul maksimal 1 dari 4 postingan.

KONTEKS:
- Jam: {hour:02d}:00 WIB
- Topik: "{topic}"
- Jenis konten: {pillar}

TUGAS:
Tulis 1 postingan Threads soal topik di atas, pilih pendekatan yang paling cocok. Fokus biar orang yang baca pengen like, reply, atau setuju — bukan sekadar baca dan scroll lanjut.

Sertakan juga `image_query`: kata kunci pencarian foto ASLI / DOKUMENTASI BERITA.
- Untuk berita Indonesia / kasus kriminal / perundungan / bencana / politik / kasus viral / figur publik: WAJIB GUNAKAN BAHASA INDONESIA yang spesifik sesuai peristiwanya (contoh: 'kasus perundungan pemalang polisi', 'konferensi pers polda jateng', 'banjir bandang padang bnpb', 'sidang mk sengketa'). JANGAN gunakan kata kunci generik bahasa Inggris seperti 'classroom' atau 'people talking'. Kita butuh foto jurnalisme / pers / pelaku / TKP asli.
- Untuk topik umum/lifestyle/relatable: kata kunci visual yang nyata dan autentik.

Format output JSON:
{{
  "content": "postingan threads",
  "image_query": "kata kunci spesifik foto berita asli / dokumentasi"
}}
Hanya output JSON. Tanpa markdown ticks.
"""

    log.info(f"Generating organic post for topic: '{topic}' (pillar={pillar})")
    resp_text = _llm([{"role": "user", "content": prompt}], temperature=0.85, max_tokens=600)

    # Clean potential markdown ticks
    clean_json = re.sub(r"^```json\s*", "", resp_text.strip(), flags=re.I)
    clean_json = re.sub(r"^```\s*", "", clean_json.strip())
    clean_json = re.sub(r"\s*```$", "", clean_json.strip())

    try:
        data = json.loads(clean_json)
        content = data.get("content", "").strip()
        vibe_query = data.get("image_query", data.get("vibe_image_query", "")).strip()
        if not content:
            raise ValueError("Empty content from LLM")
    except Exception as e:
        log.warning(f"Failed to parse LLM JSON for organic post: {e}. Using fallback.")
        content = f"lagi rame banget ngebahas isu {topic} hari ini di timeline. ada banyak sudut pandang menarik, menurut kalian gimana?"
        vibe_query = f"{topic} berita indonesia"

    if not vibe_query:
        vibe_query = f"{topic} berita"

    # Search authentic news / real-life image
    image_url = search_vibe_image(vibe_query, raw_topic=topic)

    return {
        "topic": topic,
        "pillar": pillar,
        "content": content,
        "vibe_image_query": vibe_query,
        "image_url": image_url,
    }


if __name__ == "__main__":
    res = generate_organic_post("cuaca panas jakarta bmkg", "relatable_struggle")
    print("Content:", res["content"])
    print("Image query:", res["vibe_image_query"])
    print("Image URL:", res["image_url"])
