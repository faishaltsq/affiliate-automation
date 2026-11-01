# Affiliate Automation V2 — 30-Draft Live Evaluation Report

- **Timestamp**: 2026-10-30 17:39:57 UTC
- **Model**: Production tig/step-5-unrestricted (No Mocking)
- **Total Drafts Evaluated**: 28/30
- **Average Quality Score**: 54.4/100
- **Total Duration**: 590.6s

## Summary Metrics

### Verdict Distribution
| Verdict | Count | Percentage |
| --- | --- | --- |
| `APPROVED_AUTO` | 9 | 32.1% |
| `NEEDS_REVIEW` | 6 | 21.4% |
| `REJECTED` | 9 | 32.1% |
| `REVISE_ONCE` | 4 | 14.3% |

### Format & Structure Distribution
| Structure | Format | Count |
| --- | --- | --- |
| Single Post | `comparison` | 11 |
| Single Post | `multi_paragraph` | 4 |
| Single Post | `short_paragraph` | 5 |
| Single Post | `single_snappy` | 8 |

## Detailed Draft Records

### Draft #1 — [OPINIONS] comparison (Score: 0.0 | `REJECTED`)
- **Problem**: Diskon besar kadang cuma mengembalikan harga normal
- **Angle**: trade-off yang jarang dibahas jujur
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `kontras`
- **Content Hash**: `b6fba604c37f7947...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Harapan: diskon besar berarti hemat. Kenyataan: kadang itu cuma harga normal yang dinaikkan dulu biar kelihatan murah. Yang berguna: catat harga barang beberapa minggu sebelum beli, biar kebuka mana diskon asli dan mana cuma trik.

---

### Draft #2: FAILED/SKIPPED (opinions)
- Error: write_draft returned None

### Draft #2 — [OPINIONS] multi_paragraph (Score: 77.0 | `NEEDS_REVIEW`)
- **Problem**: Nggak semua orang butuh flagship; banyak yang butuh baterai awet
- **Angle**: pendapat yang tidak populer tapi masuk akal
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `anekdot_singkat`
- **Content Hash**: `b0083fb748671efc...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> Orang pulang kerja sambil buru-buru cari power bank tuh pemandangan yang biasa. Padahal kebanyakan dari mereka cuma butuh HP yang awet sampai malam. Flagship memang canggih, tapi kalau tiap sore harus cari colokan, tenangnya hari hilang begitu saja.

---

### Draft #3 — [OPINIONS] single_snappy (Score: 94.0 | `APPROVED_AUTO`)
- **Problem**: Review yang terlalu rapi dan positif itu tanda bahaya, bukan jaminan
- **Angle**: klaim marketing vs realita pemakaian
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `anekdot_singkat`
- **Content Hash**: `486c1d4cde31162e...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Buka review, komentarnya positif semua dan fotonya bagus semua. Itu etalase. Yang beneran berguna, cari cerita kekurangan kecil setelah dipakai harian.

---

### Draft #4 — [OPINIONS] comparison (Score: 84.0 | `NEEDS_REVIEW`)
- **Problem**: Spesifikasi tinggi di atas kertas sering nggak terasa di pemakaian harian
- **Angle**: pendapat yang tidak populer tapi masuk akal
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pernyataan_lugas`
- **Content Hash**: `74e68b307216750e...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> HP dengan spek tinggi di atas kertas sering nggak kerasa bedanya di pemakaian harian. Yang bikin nyaman tuh justru hal sepele: gerakan mulus, layar enak dilihat, dan gak panes. Jebakan terbesar tuh ngejar angka di brosur, padahal yang penting pengalaman pakainya.

---

### Draft #5 — [OPINIONS] comparison (Score: 59.0 | `REVISE_ONCE`)
- **Problem**: Gadget murah yang awet sering lebih masuk akal daripada mahal yang sekali drop rusak
- **Angle**: pendapat yang tidak populer tapi masuk akal
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pernyataan_lugas`
- **Content Hash**: `26b2d97643974fd2...`
- **Publication Pre-Validation**: `eligible=False` (verdict_revise_once)

**Content:**
> Gadget mahal yang sekali jatuh langsung rusak itu sering cuma gaya doang. Aku lebih pilih yang murah tapi beneran tahan dipake harian. Trade-off-nya jelas: fitur seadanya, tapi kalau kepleset dari meja masih aman.

---

### Draft #1 — [PRACTICAL_TIPS] short_paragraph (Score: 72.0 | `NEEDS_REVIEW`)
- **Problem**: Bingung pilih power bank: capacity nyata vs angka di kemasan
- **Angle**: kesalahan umum dan cara hindarinya
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `anekdot_singkat`
- **Content Hash**: `71e9e4047d2f2da5...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> Pegang power bank baru, senyum dulu lihat kotaknya. Tapi jangan cuma lihat yang paling besar di depan; cek kapasitas output yang tercantum lebih kecil. Baca bagian output sebelum beli, bukan kapasitas sel.

---

### Draft #2 — [PRACTICAL_TIPS] comparison (Score: 0.0 | `REJECTED`)
- **Problem**: Nggak tahu cara cek apakah charger benar-benar support fast charging
- **Angle**: langkah konkret yang bisa dicek hari ini
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pernyataan_lugas`
- **Content Hash**: `ca981ad576ca5d05...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Charger yang support ngecas cepat biasanya langsung bikin HP nunjukin indikasi khusus pas nyolok. Kalau nggak ada perubahan apa-apa, besar kemungkinan cuma ngecas biasa. Coba bandingin dua charger berbeda di HP yang sama sambil lihat notifikasi di status bar. Yang beneran ngecas cepat biasanya keliatan beda dari ngecas standar. Cek apakah muncul penanda tambahan pas pakai charger yang diragukan.

---

### Draft #3 — [PRACTICAL_TIPS] comparison (Score: 86.0 | `APPROVED_AUTO`)
- **Problem**: Nggak paham bedanya adaptor 30W, 65W, dan 100W untuk kebutuhan sendiri
- **Angle**: urutan prioritas kalau budget terbatas
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pernyataan_lugas`
- **Content Hash**: `a4f4892070a95455...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Jebakan paling umum: mikir makin besar daya makin baik. Padahal kalau HP atau perangkat nggak dukung, tenaga ekstra cuma bikin boros kantong. Prioritas pertama: sesuaikan dengan kebutuhan harian, bukan gengsi angka di kemasan.

---

### Draft #4 — [PRACTICAL_TIPS] comparison (Score: 86.0 | `APPROVED_AUTO`)
- **Problem**: Kabel USB-C murah sering nggak bisa transfer data, cuma charging
- **Angle**: cara cek sebelum beli / sebelum rusak
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `kontras`
- **Content Hash**: `e18968f8c70fccf1...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Harapannya kabel murah bisa transfer file juga. Kenyataannya, banyak yang cuma ngecas. Kalau nemu yang langsung kedeteksi di laptop, itu tandanya bagus. Sebelum beli banyak, coba satu dulu. Lebih baik gitu daripada stok yang nggak kepake.

---

### Draft #5 — [PRACTICAL_TIPS] single_snappy (Score: 0.0 | `REJECTED`)
- **Problem**: Nggak tahu cara backup otomatis sebelum HP rusak
- **Angle**: urutan prioritas kalau budget terbatas
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pertanyaan_jujur`
- **Content Hash**: `82fe135c12fac53a...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Pernah mikir, kalau HP tiba-tiba mati total, data yang paling susah diganti itu yang mana?

Kalau budget buat backup lagi tipis, urutannya gini: pakai dulu fitur backup bawaan pabrikan yang biasanya gratis buat kontak dan pesan. Lanjut backup foto ke laptop lewat kabel, itu juga gratis. Kalau masih ada sisa, baru pikirkan langganan cloud eksternal.

Cek sekarang: buka pengaturan ponsel, cari menu backup, lihat kapan terakhir kali proses itu jalan.

---

### Draft #6 — [PRACTICAL_TIPS] single_snappy (Score: 0.0 | `REJECTED`)
- **Problem**: Susah bedain mana harga wajar dan mana harga yang kena markup
- **Angle**: kesalahan umum dan cara hindarinya
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `f3c911d191b459be...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Kesalahan umum: ngira harga udah wajar cuma karena barang lagi banyak dibahas. Padahal dibahas bukan berarti langka. Coba amati dulu pola harganya sebelum memutuskan.

---

### Draft #1 — [PRACTICAL_TIPS] short_paragraph (Score: 67.0 | `REVISE_ONCE`)
- **Problem**: Bingung pilih power bank: capacity nyata vs angka di kemasan
- **Angle**: kesalahan umum dan cara hindarinya
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `kontras`
- **Content Hash**: `03999e040b027679...`
- **Publication Pre-Validation**: `eligible=False` (verdict_revise_once)

**Content:**
> Banyak yang beli power bank gara-gara label di kotak keliatan besar, terus berharap bisa ngecas HP berhari-hari. Padahal yang masuk ke HP selalu lebih kecil. Salah satu penyebabnya: ngisi daya pas power bank-nya juga lagi low.

---

### Draft #2 — [PRACTICAL_TIPS] multi_paragraph (Score: 0.0 | `REJECTED`)
- **Problem**: Nggak tahu cara cek apakah charger benar-benar support fast charging
- **Angle**: langkah konkret yang bisa dicek hari ini
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pernyataan_lugas`
- **Content Hash**: `4339b0f1dfc010e2...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Banyak charger yang diiklankan cepat, tapi pas dipakai HP nggak nunjukin tanda apa-apa. Cara paling gampang: colok charger ke HP, lalu lihat layar kunci atau status bar. Kalau ada teks atau ikon khusus charging cepat, biasanya adapter dan kabelnya memang support. Tapi kalau nggak muncul, jangan buru-buru salahin HP. Coba ganti kabelnya dengan yang lebih tebal atau asli, karena sering kali kabel yang jadi masalah meski adapternya oke. Perhatikan juga kalau charger cepat panas berlebihan, itu bisa tanda arus listriknya nggak stabil. Jadi sebelum beli charger baru, cek dulu tanda di layar HP kalian.

---

### Draft #3 — [PRACTICAL_TIPS] short_paragraph (Score: 0.0 | `REJECTED`)
- **Problem**: Nggak tahu cara backup otomatis sebelum HP rusak
- **Angle**: urutan prioritas kalau budget terbatas
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `anekdot_singkat`
- **Content Hash**: `631aeadca0b76b91...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Suasana di tempat servis HP sering sama: orang datang bawa layar retak, tapi yang paling ditanyain bukan biaya perbaikan. Foto dan video yang belum tersalin biasanya yang bikin panik. Kalau lagi hemat, backup otomatis nggak perlu langsung bayar. Mulai dari materi yang nggak bisa dibuat ulang, seperti galeri kamera. Baru kontak, chat, atau catatan penting. Banyak HP punya layanan bawaan yang cukup diatur sekali, lalu bekerja sendiri. Cek dulu setelan bawaan sebelum mikir langganan tambahan. Hari ini, buka pengaturan backup HP dan pastikan folder kamera sudah masuk daftar yang disinkronkan.

---

### Draft #4 — [PRACTICAL_TIPS] single_snappy (Score: 0.0 | `REJECTED`)
- **Problem**: Bingung cara cek garansi resmi sebelum beli gadget bekas
- **Angle**: cara cek sebelum beli / sebelum rusak
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pertanyaan_jujur`
- **Content Hash**: `6fa7b50068a8d424...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Pernah kepikiran nggak sih, pas mau beli gadget bekas, garansi resminya masih aktif atau nggak? Banyak yang cuma lihat kondisi fisik, padahal status garansi bisa dicek dari nomor seri atau IMEI di situs resmi merek masing-masing sebelum bayar. Kalau ternyata udah habis, seharusnya harga jualnya lebih masuk akal. Cek dulu, jangan sampai bayar mahal tapi garansinya udah nggak ada.

---

### Draft #5 — [PRACTICAL_TIPS] short_paragraph (Score: 87.0 | `APPROVED_AUTO`)
- **Problem**: Susah bedain mana harga wajar dan mana harga yang kena markup
- **Angle**: langkah konkret yang bisa dicek hari ini
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `543d2dfda6f17a4b...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Logika 'makin mahal makin bagus' sering dipakai buat nutup harga yang emang sengaja dinaikin. Cara ceknya gampang: liat dulu apakah yang ditawarkan itu fungsi baru yang kalian butuh, atau cuma kosmetik biar keliatan beda. Kalau cuma ganti nama dan warna, biasanya versi lama harganya lebih masuk akal. Hari ini, coba bandingin dua produk yang setara di toko online, terus pilih yang fungsinya beneran ngaruh ke pemakaian sehari-hari.

---

### Draft #6 — [PRACTICAL_TIPS] comparison (Score: 84.0 | `NEEDS_REVIEW`)
- **Problem**: Nggak paham bedanya adaptor 30W, 65W, dan 100W untuk kebutuhan sendiri
- **Angle**: urutan prioritas kalau budget terbatas
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pertanyaan_jujur`
- **Content Hash**: `879e225e6e887bd9...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> Kenapa adaptor ngecas ada yang kecil, ada yang gede? Kalau uang terbatas, lihat dulu perangkat terberat kalian. Daya rendah cukup buat HP. Daya menengah lebih fleksibel buat HP dan tablet. Daya tinggi sebenarnya buat laptop, bukan gaya-gayaan. Beli sesuai kebutuhan, jangan tergiur ukuran besar.

---

### Draft #1 — [DISCOVERIES] comparison (Score: 77.0 | `NEEDS_REVIEW`)
- **Problem**: Aksesori yang bikin setup rapi tanpa beli meja baru
- **Angle**: alat sederhana yang menghemat waktu
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `299acf2915ee6e16...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> Kebiasaan ngeliat meja penuh kabel biasanya bikin kalian mikir butuh meja baru. Padahal, tempat simpan kecil di bawah meja sama penjepit kabel sering cukup. Jebakannya: ngira masalahnya ruang, padahal cuma butuh aksesori sederhana yang bikin hemat waktu.

---

### Draft #2 — [DISCOVERIES] single_snappy (Score: 85.0 | `APPROVED_AUTO`)
- **Problem**: Fitur HP yang jarang dipakai padahal berguna: mode fokus, batas pengisian daya
- **Angle**: kategori kecil yang sering terlupakan
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `langsung_ke_inti`
- **Content Hash**: `3aa89cf294fe9a5b...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Mode fokus sama batas pengisian daya tuh fitur HP yang jarang disentuh, padahal dua-duanya berguna. Satu ngatur biar notif nggak ganggu pas kalian lagi butuh konsentrasi, satu lagi ngatur biar pengisian nggak jalan terus sampai penuh tiap malam. Repotnya cuma di awal: harus cari di pengaturan dulu, bukan langsung kelihatan di layar utama. Tapi sekali diatur, rasanya HP-nya lebih masuk akal dipakai sehari-hari, bukan cuma nyala terus ngikutin semua notif.

---

### Draft #3 — [DISCOVERIES] comparison (Score: 0.0 | `REJECTED`)
- **Problem**: Kategori kecil yang sering terlupakan: organizer kabel, stand HP, lampu meja
- **Angle**: hal 'membosankan' yang sebenarnya penting
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `kontras`
- **Content Hash**: `89f582b94c26bd01...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Organizer kabel, stand HP, sama lampu meja biasanya ada di urutan terakhir kalau lagi beli perlengkapan. Padahal setelah dipakai, justru mereka yang bikin hari lebih enak: nggak ada lagi kabel berserakan pas buru-buru, HP punya tempat stabil buat meeting, dan meja tetap terang pas malam. Barang yang kelihatan membosankan di etalase seringnya paling praktis dipakai sehari-hari.

---

### Draft #22: FAILED/SKIPPED (discoveries)
- Error: write_draft returned None

### Draft #4 — [DISCOVERIES] single_snappy (Score: 62.0 | `REVISE_ONCE`)
- **Problem**: Fitur HP yang jarang dipakai padahal berguna: mode fokus, batas pengisian daya
- **Angle**: kategori kecil yang sering terlupakan
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pertanyaan_jujur`
- **Content Hash**: `b6f94697dc1fbdc5...`
- **Publication Pre-Validation**: `eligible=False` (verdict_revise_once)

**Content:**
> Pernah nggak sih HP kalian punya mode fokus sama batas pengisian daya tapi jarang disentuh? Dua fitur kecil ini sebenernya berguna buat ngatur notifikasi dan ngatur kapan cas berhenti. Trade-off-nya cuma harus diingat buat nyalain sendiri, karena nggak jalan otomatis.

---

### Draft #5 — [DISCOVERIES] multi_paragraph (Score: 91.0 | `APPROVED_AUTO`)
- **Problem**: Kategori kecil yang sering terlupakan: organizer kabel, stand HP, lampu meja
- **Angle**: fitur yang ada tapi jarang dipakai
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `5fe7f0a687e1c613...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Stand HP di meja sering cuma jadi penahan HP pas makan.

Padahal engselnya yang jarang disentuh: diubah sudutnya, layar sejajar mata, leher nggak nunduk terus.

---

### Draft #1 — [RELATABLE] comparison (Score: 88.0 | `APPROVED_AUTO`)
- **Problem**: Kabel charger yang selalu kusut di tas dan leher konektornya gampang getas
- **Angle**: cerita singkat situasi yang biasa kejadian
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `langsung_ke_inti`
- **Content Hash**: `b249a4b2c2da2f71...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Kabel charger di tas beda sama power bank. Satu balik kusut, satu nyangkut di sudut. Yang rusak selalu bagian leher, bukan ujungnya.

---

### Draft #2 — [RELATABLE] comparison (Score: 74.0 | `NEEDS_REVIEW`)
- **Problem**: Meja kerja penuh kabel sampai mouse nggak muat gerak
- **Angle**: kontras antara niat dan kenyataan
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `langsung_ke_inti`
- **Content Hash**: `c618615fc64b95b4...`
- **Publication Pre-Validation**: `eligible=False` (needs_operator_approval)

**Content:**
> Kabel di meja sengaja ditaruh semua di belakang biar nggak mengganggu. Pas mouse mau dipakai, geraknya jadi sempit karena terlalu banyak kabel nyerubung di belakang monitor. Rapi di depan, sesak di belakang.

---

### Draft #3 — [RELATABLE] single_snappy (Score: 66.0 | `REVISE_ONCE`)
- **Problem**: Notifikasi grup bikin fokus buyar tiap lima menit
- **Angle**: satu detail kecil yang bikin kesel
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `langsung_ke_inti`
- **Content Hash**: `a97fa7554f9c69e8...`
- **Publication Pre-Validation**: `eligible=False` (verdict_revise_once)

**Content:**
> Notifikasi grup yang masuk terus-menerus bikin susah nge-fokus. Masalahnya bukan bunyinya, tapi refleks buka HP yang tiba-tiba muncul.

---

### Draft #4 — [RELATABLE] single_snappy (Score: 93.0 | `APPROVED_AUTO`)
- **Problem**: Niat bangun pagi buat rapikan kamar, akhirnya scroll HP di kasur
- **Angle**: observasi kebiasaan yang jarang disadari
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `8fa0d6537a752a86...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Bangun pagi niatnya mau rapikan kamar, tapi tangan udah megang HP duluan. Padahal yang bikin susah bangkit bukan cuma rasa kantuk. Kalau HP selalu ada di jangkauan, scroll lagi jadi hal paling gampang pas mata baru kebuka. Coba malam ini taruh HP jauh dari kasur. Biar pas bangun, yang pertama disentuh bukan layar.

---

### Draft #5 — [RELATABLE] multi_paragraph (Score: 91.0 | `APPROVED_AUTO`)
- **Problem**: Beli gadget baru karena diskon, dipakai dua minggu lalu disimpan
- **Angle**: cerita singkat situasi yang biasa kejadian
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `observasi`
- **Content Hash**: `66a1126097aeb380...`
- **Publication Pre-Validation**: `eligible=False` (review_required_pending_approval)

**Content:**
> Diskon bikin gadget terasa wajib punya. Awalnya semangat dipakai tiap hari, lalu pelan-pelan balik ke laci.

Bukan karena barangnya jelek. Tapi kebutuhan yang bikin kita beli ternyata udah nggak terlalu kerasa lagi.

---

### Draft #6 — [RELATABLE] short_paragraph (Score: 0.0 | `REJECTED`)
- **Problem**: Niat cari produk murah, malah habis dua jam bandingin spesifikasi
- **Angle**: observasi kebiasaan yang jarang disadari
- **Structure**: `single` (Parts: 0)
- **Opening Style**: `pertanyaan_jujur`
- **Content Hash**: `0fb029c2963b7eec...`
- **Publication Pre-Validation**: `eligible=False` (evaluation_has_vetoes: generic_ai_voice)
- **Vetoes**: `generic_ai_voice`

**Content:**
> Kenapa niat cuma cari yang murah, malah ketarik bandingin fitur yang nggak kita ngerti? Trade-off-nya sebenarnya di mana?

---
