"""src/persona/reference_library.py — Versioned Editorial Reference Library.

Provides canonical positive and negative editorial reference examples for the
Teman Tongkrongan account, implementing the Sol-inspired conversational voice:
- Relaxed, intelligent, confident, warm, observational.
- Clear points, natural Indonesian rhythm, understated humor.
- Strict anti-patterns for AI clichés, forced humor, personification, and fake engagement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

REFERENCE_LIBRARY_VERSION = "sol_conversational_v1"


@dataclass(frozen=True)
class ReferenceExample:
    id: str
    category: str
    tone: str
    content: str
    why: str
    is_positive: bool
    version: str = REFERENCE_LIBRARY_VERSION
    parts: Optional[list[str]] = None


# ── Canonical Positive Examples ──────────────────────────────────────────────
POSITIVE_EXAMPLES: list[ReferenceExample] = [
    ReferenceExample(
        id="pos_observational_witty",
        category="relatable",
        tone="observational_witty",
        content=(
            "Niat beli tablet buat produktivitas pas kerja di luar, ujung-ujungnya "
            "cuma jadi layar kedua buat nonton sambil scroll HP di kasur. "
            "Ada kenyamanan aneh waktu sadar kita nggak sendirian ngalamin ini."
        ),
        why=(
            "Irama alami percakapan kontemporer, humor understated tanpa lelucon receh, "
            "tidak memaksa kata ganti mekanis, langsung ke observasi manusiawi."
        ),
        is_positive=True,
    ),
    ReferenceExample(
        id="pos_crisp_practical",
        category="practical_tips",
        tone="crisp_practical",
        content=(
            "Port Type-C kerasa longgar bukan selalu berarti konektornya rusak. "
            "Seringnya cuma serat kain dari saku celana yang memadat di dalem. "
            "Coba bersihin pelan-pelan pakai tusuk gigi kayu kering pas HP mati. "
            "Biasanya langsung seret lagi tanpa perlu ganti port."
        ),
        why=(
            "Langsung ke inti masalah teknis harian. Panjang mengikuti substansi (44 kata). "
            "Solusi konkret, aman, dan masuk akal tanpa menggurui."
        ),
        is_positive=True,
    ),
    ReferenceExample(
        id="pos_confident_analytical",
        category="opinions",
        tone="confident_analytical",
        content=(
            "Label RAM 16GB di HP murah itu hampir selalu 8GB fisik plus 8GB virtual swap. "
            "Di atas kertas kelihatan gahar, tapi di pemakaian harian memori virtual "
            "jauh lebih lambat dibanding RAM asli. "
            "Jangan bayar ekstra cuma buat angka marketing yang jarang kerasa fungsinya."
        ),
        why=(
            "Percaya diri tanpa arogan. Membedah trik spesifikasi secara santai dan cerdas. "
            "Sikap kritis yang mendidik pembaca tanpa nada marah-marah."
        ),
        is_positive=True,
    ),
    ReferenceExample(
        id="pos_thoughtful_calm",
        category="thoughtful_observations",
        tone="thoughtful_calm",
        content=(
            "Layar 120Hz emang bikin navigasi mulus banget. Tapi kalau lagi di luar "
            "seharian tanpa colokan, nurunin ke 60Hz bisa nambah waktu nyala hampir dua jam. "
            "Mulus itu enak, tapi HP tetap nyala pas butuh pesan taksi jauh lebih penting."
        ),
        why=(
            "Trade-off realistis tanpa kata-kata klise. Menyampaikan prioritas praktis "
            "secara hangat dan membumi."
        ),
        is_positive=True,
    ),
    ReferenceExample(
        id="pos_curious_discovery",
        category="discoveries",
        tone="curious_discovery",
        content=(
            "Banyak yang belum sadar colokan USB-C di monitor kantor sebenarnya bisa "
            "ngirim video sekaligus ngisi daya laptop lewat satu kabel aja. "
            "Syaratnya cuma satu: kabelnya harus support DisplayPort Alt Mode dan "
            "Power Delivery, bukan kabel charger polosan bawaan HP."
        ),
        why=(
            "Fakta teknis akurat dikemas kasual. Membantu orang memanfaatkan apa yang "
            "sudah ada di meja mereka tanpa jargon rumit."
        ),
        is_positive=True,
    ),
    ReferenceExample(
        id="pos_transparent_recommendation",
        category="affiliate",
        tone="transparent_recommendation",
        content=(
            "Adaptor GaN 65W dua port itu investasi paling masuk akal kalau sering bawa "
            "laptop dan HP barengan. Bobotnya sepertiga charger laptop lama, dan satu colokan "
            "di kafe udah cukup buat dua perangkat. "
            "Pastikan port utamanya support minimal 45W biar laptop nggak ngeluh slow charging."
        ),
        why=(
            "Rekomendasi jujur dengan batasan teknis yang jelas. "
            "Fokus pada utilitas nyata, bukan jualan agresif."
        ),
        is_positive=True,
    ),
]


# ── Canonical Negative Examples ──────────────────────────────────────────────
NEGATIVE_EXAMPLES: list[ReferenceExample] = [
    ReferenceExample(
        id="neg_rhetorical_question_opener",
        category="discoveries",
        tone="rhetorical_question_opener",
        content=(
            "Kenapa masih sering pasang aplikasi tambahan cuma buat berbagi koneksi internet "
            "dari HP ke laptop? Padahal tethering bawaan sudah ada di menu pengaturan."
        ),
        why=(
            "GAGAL: Membuka dengan pertanyaan retoris yang menunda poin. Pertanyaannya bukan "
            "pertanyaan sungguhan, cuma pembuka gaya iklan. Poinnya ('tethering sudah bawaan') "
            "bisa jadi kalimat pertama tanpa kehilangan apa pun."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_three_part_charger_thread",
        category="practical_tips",
        tone="artificial_padding_personification",
        content=(
            "Part 1: Kabel charger di tas tuh niatnya biar praktis. Kenyataannya, dia lebih sering balik rumah dalam keadaan kusut dan ujungnya udah mulai rusak.\n"
            "Part 2: Putusnya bukan karena kabelnya nakal. Biasanya karena sehari-hari nyelip sama barang lain yang lebih keras.\n"
            "Part 3: Pas ngecek isi tas, sekalian liat ujung kabel. Kalau udah mulai keliatan ada benang tembaga, itu tandanya dia butuh sedikit perhatian."
        ),
        parts=[
            "Kabel charger di tas tuh niatnya biar praktis. Kenyataannya, dia lebih sering balik rumah dalam keadaan kusut dan ujungnya udah mulai rusak.",
            "Putusnya bukan karena kabelnya nakal. Biasanya karena sehari-hari nyelip sama barang lain yang lebih keras.",
            "Pas ngecek isi tas, sekalian liat ujung kabel. Kalau udah mulai keliatan ada benang tembaga, itu tandanya dia butuh sedikit perhatian.",
        ],
        why=(
            "GAGAL TOTAL: (1) Observasi sepele dipaksakan jadi 3 bagian thread (artificial thread padding). "
            "(2) Personifikasi konyol: kabel dibilang 'nakal' dan 'butuh perhatian'. "
            "(3) Klise AI: 'niatnya... kenyataannya...'. "
            "(4) Saran berbahaya: tembaga terbuka cuma dibilang butuh perhatian, padahal risiko korsleting."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_typo_and_repetitive_powerbank",
        category="practical_tips",
        tone="typo_and_repetitive_wrap",
        content=(
            "Kapasitas power bank di kotak tuh bukan jaminan berapa banyak daya yang benar-benar masuk ke HP. "
            "Sebagnya hilang jadi panas dan proses konversi. Kesalahan umum: langsung tergiur yang paling gede tanpa mikir pemakaian nyata sehari-hari. "
            "Padahal kalau nggak sering keluar rumah, bawa yang terlalu besar cuma bikin tas makin berat. Mending pilih yang ukurannya wajar dan pas di saku. "
            "Kapasitas besar nggak ada gunanya kalau power bank-nya sendiri sering ditinggal di rumah."
        ),
        why=(
            "GAGAL: (1) Typo nyata ('Sebagnya'). (2) Struktur berulang dan kalimat penutup "
            "merupakan kesimpulan klise yang mengulang apa yang sudah dikatakan sebelumnya. "
            "(3) Format membosankan tanpa ritme percakapan dinamis."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_forced_personification",
        category="relatable",
        tone="forced_personification",
        content=(
            "Laptop kamu lagi menjerit kepanasan karena kipasnya capek meniup debu yang menumpuk. "
            "Jangan biarkan dia menderita sendirian di atas meja kerjamu yang berantakan."
        ),
        why=(
            "GAGAL: Personifikasi berlebihan benda mati ('menjerit', 'capek', 'menderita'). "
            "Barang elektronik tidak memiliki emosi atau kelelahan. Nada kekanak-kanakan."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_overexplaining_obvious",
        category="practical_tips",
        tone="textbook_definition",
        content=(
            "Charger adalah perangkat elektronik yang berfungsi mengalirkan arus listrik bolak-balik "
            "ke dalam baterai smartphone agar dayanya terisi kembali secara optimal untuk menunjang aktivitas."
        ),
        why=(
            "GAGAL: Menjelaskan hal mendasar yang sudah dipahami semua orang seperti definisi buku teks. "
            "Bukan obrolan kawan santai, tetapi tugas sekolah kaku."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_ai_motivational_conclusion",
        category="opinions",
        tone="motivational_cliche",
        content=(
            "Setiap perangkat punya kelebihan dan kekurangan masing-masing. "
            "Gunakan teknologi dengan bijak agar harimu semakin produktif dan bermakna. "
            "Semangat menjalani aktivitas hari ini ya kawan-kawan!"
        ),
        why=(
            "GAGAL: Penutup motivasi generik khas AI/LinkedIn. Sangat klise, hambar, "
            "dan menghilangkan karakter santai cerdas akun."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_repetitive_joke_structure",
        category="relatable",
        tone="formulaic_meme",
        content=(
            "Hanya ada dua tipe orang di dunia ini: tipe yang kabelnya selalu rapi di organizer, "
            "dan tipe yang kabelnya kusut mirip benang layangan. Kalian tim yang mana nih?"
        ),
        why=(
            "GAGAL: Format lelucon template basi ('hanya ada dua tipe orang') dan ditutup "
            "dengan pertanyaan engagement bait murahan ('kalian tim mana nih')."
        ),
        is_positive=False,
    ),
    ReferenceExample(
        id="neg_artificially_casual_slang",
        category="relatable",
        tone="tryhard_slang",
        content=(
            "Jujurly ini worth it parah sih, literally no debat no kecap, beneran bikin shock therapy "
            "banget gais, fix no counter!"
        ),
        why=(
            "GAGAL: Bahasa gaul dipaksakan dan bertumpuk (slang salad). Terdengar dibuat-buat, "
            "bukan gaya kawan cerdas yang santai dan artikulatif."
        ),
        is_positive=False,
    ),
]


def get_reference_examples(is_positive: Optional[bool] = None) -> list[ReferenceExample]:
    """Retrieve filtered positive or negative reference examples."""
    if is_positive is True:
        return list(POSITIVE_EXAMPLES)
    if is_positive is False:
        return list(NEGATIVE_EXAMPLES)
    return list(POSITIVE_EXAMPLES) + list(NEGATIVE_EXAMPLES)


def get_editorial_reference_prompt(version: str = REFERENCE_LIBRARY_VERSION) -> str:
    """Format canonical reference examples for injection into prompts."""
    pos_lines = []
    for ex in POSITIVE_EXAMPLES[:4]:
        pos_lines.append(
            f"Contoh Positif ({ex.tone}):\n"
            f'"{ex.content}"\n'
            f"Alasan Baik: {ex.why}\n"
        )

    neg_lines = []
    for ex in NEGATIVE_EXAMPLES[:4]:
        neg_lines.append(
            f"Contoh Buruk/Dilarang ({ex.tone}):\n"
            f'"{ex.content}"\n'
            f"Alasan Dilarang: {ex.why}\n"
        )

    return (
        f"STANDAR EDITORIAL CONVERSATIONAL ({version}):\n\n"
        "--- ACUAN GAYA POSITIF ---\n"
        + "\n".join(pos_lines)
        + "\n--- POLA BURUK YANG WAJIB DIHINDARI ---\n"
        + "\n".join(neg_lines)
    )
