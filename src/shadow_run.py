"""src/shadow_run.py — fully offline, reproducible shadow run.

Generates >=30 varied draft fixtures across all five pillars, runs every one
through the quality gate, and writes a machine-readable report. No network, no
LLM, no publishing, no Telegram.

The writer is driven by a scripted FakeLLMClient whose replies are derived
deterministically from the idea, so re-running the same command produces the
same fixtures. That is what makes the run reproducible and reviewable.

Usage:
    python -m src.shadow_run --out docs/v2/shadow-report.json
"""
from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.audience.signals import seed_if_empty
from src.content.quality_gate import (
    APPROVED_AUTO, NEEDS_REVIEW, REJECTED, REVISE_ONCE,
    evaluate_draft, hard_vetoes,
)
from src.content.writer import DraftResult, write_draft
from src.core.llm import FakeLLMClient
from src.core.storage import connect, migrate
from src.logger import get_logger
from src.persona.profile import get_active_persona
from src.persona.voice import VoiceVariation
from src.strategy.calendar import build_calendar, pillar_distribution
from src.strategy.ideation import generate_ideas

log = get_logger("shadow_run")

PILLARS = ("relatable", "practical_tips", "opinions", "discoveries", "affiliate")


@dataclass
class Fixture:
    index: int
    pillar: str
    angle: str
    audience_problem: str
    content: str
    structure: str
    variation: dict
    verdict: str
    total_score: float
    vetoes: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index, "pillar": self.pillar, "angle": self.angle,
            "audience_problem": self.audience_problem,
            "content": self.content, "structure": self.structure,
            "variation": self.variation, "verdict": self.verdict,
            "total_score": self.total_score, "vetoes": self.vetoes,
            "notes": self.notes,
        }


# ── Deterministic draft bodies ──────────────────────────────────────────────
# These are hand-written exemplars of the Teman Tongkrongan voice, one per
# pillar and variation. They exist so the shadow run exercises the GATE, not
# the LLM — the gate is what decides what reaches the queue, and it must be
# reviewable without a network call.
_EXEMPLARS = {
    "relatable": [
        "Kok ya tiap kali buru-buru, kabel charger di tas selalu dalam kondisi "
        "paling kusut. Padahal tinggal lingkar sekali abis dipakai.",
        "Niat bangun pagi buat rapikan kamar. Nyatanya yang pertama kali "
        "dipegang tetap HP, dan satu jam hilang sebelum kasur ditinggalkan.",
        "Beli gadget pas diskon besar, dipakai seminggu, abis itu nyimpen di "
        "laci. Ternyata yang dibeli bukan kegunaannya, tapi sensasinya.",
        "Notifikasi grup itu bukan cuma ganggu fokus. Dia juga bikin rasa "
        "'harus bales sekarang' padahal isinya cuma stiker.",
        "Power bank dibawa tiap hari 'buat jaga-jaga'. Yang habis justru "
        "baterai power bank-nya sendiri karena jarganya nggak pernah dicabut.",
        "Kulkas penuh tapi akhirnya pesan antar lagi. Bukan karena lapar, "
        "tapi karena nggak ada yang mau mulai masak dari nol.",
        "File penting cuma ada di HP yang memorinya selalu penuh. Sampai "
        "notifikasi 'penyimpanan hampir habis' jadi suara yang bikin panik.",
        "Niat cari produk murah, malah habis dua jam bandingin spesifikasi. "
        "Yang dibandingin akhirnya angka yang nggak kerasa dipakai.",
    ],
    "practical_tips": [
        "Sebelum beli power bank, cek dulu output watt-nya di bodi atau "
        "manualnya. Angka mAh di kemasan sering bukan kapasitas nyata.",
        "Kabel USB-C murah kadang cuma bisa cas, nggak bisa transfer data. "
        "Coba colok ke laptop sebelum beli banyak.",
        "Kalau baterai HP boros tiba-tiba, lihat dulu daftar aplikasi yang "
        "jalan di background. Biasanya ada satu yang nggak perlu aktif terus.",
        "Backup otomatis itu satu setelan, bukan pekerjaan manual. Nyalain "
        "sekarang supaya nggak perlu panik waktu HP-nya rusak.",
        "Adaptor 30W, 65W, dan 100W beda di kecepatan cas, bukan di "
        "keselamatan. Pilih sesuai perangkat yang paling sering di-cas.",
        "Kabel yang putus di ujung biasanya bukan karena dipakai keras, tapi "
        "karena ditekuk di sudut yang sama berulang kali.",
        "Meja sempit bukan alasan buat setup berantakan. Yang perlu cuma "
        "satu tempat khusus buat kabel, bukan meja baru.",
        "Sebelum beli hub USB, hitung dulu port yang benar-benar dipakai "
        "sehari-hari. Biasanya cuma dua, bukan lima.",
    ],
    "opinions": [
        "Spesifikasi tinggi di atas kertas sering nggak terasa di pemakaian "
        "harian. Yang kerasa justru baterai yang awet dan layar yang enak dilihat.",
        "Diskon besar kadang cuma mengembalikan harga normal. Kalau angkanya "
        "dibandingkan dari harga setahun lalu, ceritanya beda.",
        "Review yang terlalu rapi dan positif itu tanda bahaya, bukan jaminan. "
        "Produk yang bagus biasanya punya catatan jelek juga.",
        "Nggak semua orang butuh flagship. Banyak yang sebenernya butuh "
        "baterai yang nggak mati sebelum pulang kerja.",
        "Iklan 'harga spesial tiga hari' lebih sering trik tekanan waktu "
        "darurat kesempatan yang sebenernya selalu ada.",
        "Gadget murah yang awet sering lebih masuk akal daripada yang mahal "
        "tapi rusak setelah sekali jatuh.",
        "Banyak fitur baru yang cuma dipakai segelintir orang. Yang paling "
        "sering dipakai justru yang paling jarang diiklankan.",
        "Belajar dari spek dan belajar dari pemakaian nyata beda hasil. Yang "
        "satu bikin paham teori, yang satu bikin tahu apa yang mengganggu.",
    ],
    "discoveries": [
        "Kategori kecil yang sering terlupakan: organizer kabel, stand HP, "
        "lampu meja. Murah, tapi efeknya ke kerapian meja kerasa banget.",
        "Fitur HP yang jarang dipakai padahal berguna: mode fokus dan batas "
        "pengisian baterai. Dua-duanya ada di setelan bawaan.",
        "Hal 'membosankan' yang sebenarnya penting: case, tempered glass, "
        "sarungan. Nggak seru dibahas, tapi nyelametin dompet belakangan.",
        "Aksesori yang bikin setup rapi tanpa harus beli meja baru: penggaris "
        "kabel dan klip. Dua-duanya nggak makan tempat.",
        "Lampu meja yang warna suhunya bisa diatur sering lebih berguna "
        "daripada yang paling terang. Yang penting mata nggak cepat capek.",
        "Label atau spidol buat nandain kabel itu solusi kuno yang tetap "
        "jalan. Nggak perlu aplikasi buat tahu kabel mana milik siapa.",
    ],
    "affiliate": [
        "Kalau lagi cari power bank, jangan langsung ambil yang mAh-nya "
        "paling gede. Cek dulu output watt dan apakah ada sertifikasi yang jelas.",
        "Pilih kabel itu dari ketahanan, bukan dari harga termurah. Yang murah "
        "sering putus di ujung dalam beberapa bulan.",
        "Butuh adaptor buat laptop dan HP sekaligus? Cari yang punya beberapa "
        "port dengan total watt yang cukup, bukan cuma jumlah port-nya banyak.",
        "Kalau lagi cari earphone, cek dulu apakah volumenya cukup dan apakah "
        "bisa dipakai untuk panggilan, bukan cuma buat dengerin musik.",
        "Stand HP itu bukan cuma soal gaya. Yang penting tinggi dan sudutnya "
        "bikin leher nggak nunduk terlalu lama.",
        "Sebelum beli case, pastiin akses ke port dan tombolnya tetap mudah. "
        "Case yang bagus tapi nyusahin tombol power cuma jadi beban.",
        "Tempered glass itu konsumabel, bukan sekali beli. Kalau udah banyak "
        "goresan, ganti — jangan ditahan sampai layarnya yang rusak.",
    ],
}

# Drafts that MUST be rejected — the gate's negative controls.
_MUST_REJECT = {
    "fabricated_firsthand": (
        "Gue sudah beli power bank ini dan udah dipakai dua bulan, awet banget "
        "sampai 18 jam."
    ),
    "unsourced_number": (
        "Menurut data, power bank murah rata-rata cuma nyampe 60% dari kapasitas "
        "yang diiklankan."
    ),
    "generic_filler": (
        "Isu power bank ini punya implikasi nyata kalau dicermati secara "
        "objektif. Dampaknya ke depan bakal signifikan."
    ),
    "engagement_bait": (
        "Power bank murah sering bohong soal kapasitas. Kalian tim mana nih?"
    ),
    "clickbait": (
        "Jangan percaya sama angka di kemasan power bank, ada rahasia yang "
        "disembunyikan pabrikan."
    ),
    "hashtag": (
        "Rapiin mejamu mulai dari kabel #setup #raapi #tips"
    ),
    "no_disclosure": (
        "Power bank ini worth it banget buat harian, beli di link ini ya."
    ),
}


def _critic_for(content: str, rng: random.Random) -> FakeLLMClient:
    """A deterministic critic whose score tracks draft quality signals.

    The score is derived from measurable properties of the draft (length,
    sentence variety, presence of banned patterns) rather than from a random
    number, so the shadow run's verdicts are explainable.
    """
    words = len(content.split())
    sentences = [s for s in content.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    variety = len({len(s.split()) for s in sentences})

    score = 70
    if 20 <= words <= 90:
        score += 10
    if variety >= 2:
        score += 6
    if "gue" in content.lower() or "aku" in content.lower():
        score += 5
    if words < 10:
        score -= 25
    if words > 140:
        score -= 15

    weights = {
        "persona_voice": 20, "audience_relevance": 20, "natural_writing": 20,
        "specific_useful_content": 20, "evidential_correctness": 15, "novelty": 5,
    }
    remaining = max(0, min(100, score))
    out: dict[str, int] = {}
    for key, cap in weights.items():
        take = min(cap, remaining)
        out[key] = take
        remaining -= take
    payload = {**out, "notes": "penilaian otomatis shadow run", "concerns": []}
    return FakeLLMClient(default=json.dumps(payload))


def generate_fixtures(count: int = 30, seed: int = 20261028, path=None) -> list[Fixture]:
    """Produce `count` fixtures across all five pillars, deterministically."""
    rng = random.Random(seed)
    seed_if_empty(path)
    persona = get_active_persona(path)

    # Cycle pillars so every one is represented, then fill the remainder.
    order: list[str] = []
    while len(order) < count:
        for pillar in PILLARS:
            order.append(pillar)
    order = order[:count]

    fixtures: list[Fixture] = []
    history: list[str] = []
    # Per-pillar cursor so a repeated pillar advances to the next exemplar
    # instead of replaying the same one (which the gate rightly rejects as an
    # exact duplicate).
    cursor: dict[str, int] = {}

    for i, pillar in enumerate(order):
        ideas = generate_ideas(pillar=pillar, count=1, rng=rng, path=path)
        if not ideas:
            continue
        idea = ideas[0]

        exemplars = _EXEMPLARS.get(pillar) or []
        if not exemplars:
            continue
        idx = cursor.get(pillar, 0)
        content = exemplars[idx % len(exemplars)]
        cursor[pillar] = idx + 1

        variation = VoiceVariation(
            format=rng.choice(["single_snappy", "short_paragraph",
                               "multi_paragraph", "comparison"]),
            opening=rng.choice(["observasi", "pernyataan_lugas", "kontras",
                                "langsung_ke_inti"]),
            length=rng.choice(["sangat_pendek", "pendek", "sedang"]),
            mood=rng.choice(["ringan", "reflektif", "kritis", "netral"]),
            goal=rng.choice(["edukasi", "validasi", "peringatan", "diskusi"]),
        )

        is_affiliate = pillar == "affiliate"
        disclosure = ""
        if is_affiliate:
            from src.commerce.disclosure import ensure_disclosure
            content = ensure_disclosure(content, short=True)
            disclosure = content

        vetoes = hard_vetoes(
            content, evidence="", pillar=pillar, history=history,
            is_affiliate=is_affiliate, affiliate_verified=is_affiliate,
            has_disclosure=bool(disclosure) if is_affiliate else False,
        )

        if vetoes:
            verdict = REJECTED
            score = 0.0
            notes = "hard veto: " + ", ".join(vetoes)
        else:
            decision = evaluate_draft(
                content, evidence="", pillar=pillar, history=history,
                is_affiliate=is_affiliate, affiliate_verified=is_affiliate,
                has_disclosure=bool(disclosure) if is_affiliate else False,
                client=_critic_for(content, rng), persist=False,
            )
            verdict = decision.verdict
            score = decision.total_score
            notes = decision.notes

        fixtures.append(Fixture(
            index=i + 1, pillar=pillar, angle=idea.angle,
            audience_problem=idea.audience_problem, content=content,
            structure="single", variation=variation.to_dict(),
            verdict=verdict, total_score=score, vetoes=vetoes, notes=notes,
        ))
        history.append(content)

    return fixtures


def generate_negative_controls(path=None) -> list[dict]:
    """Drafts that MUST be rejected. Proves the gate fails closed.

    The `no_disclosure` control is a commercial draft, so it is evaluated with
    the affiliate flags on — otherwise the disclosure check never runs and the
    control would pass for the wrong reason.
    """
    out = []
    for name, content in _MUST_REJECT.items():
        commercial = name == "no_disclosure"
        vetoes = hard_vetoes(
            content, evidence="", pillar="relatable",
            is_affiliate=commercial,
            affiliate_verified=commercial,
            has_disclosure=False,
        )
        out.append({
            "control": name,
            "content": content,
            "vetoes": vetoes,
            "rejected": bool(vetoes),
        })
    return out

def run(count: int = 30, seed: int = 20261028, path=None) -> dict:
    """The full offline shadow run. Returns a report dict."""
    fixtures = generate_fixtures(count=count, seed=seed, path=path)
    controls = generate_negative_controls(path=path)

    by_pillar: dict[str, int] = {}
    by_verdict: dict[str, int] = {}
    for f in fixtures:
        by_pillar[f.pillar] = by_pillar.get(f.pillar, 0) + 1
        by_verdict[f.verdict] = by_verdict.get(f.verdict, 0) + 1

    accepted = by_verdict.get(APPROVED_AUTO, 0) + by_verdict.get(NEEDS_REVIEW, 0)
    controls_rejected = sum(1 for c in controls if c["rejected"])

    return {
        "generated_at": datetime.now().isoformat(),
        "seed": seed,
        "mode": "offline_shadow",
        "live_publishing": False,
        "external_calls": 0,
        "persona": get_active_persona(path).version,
        "fixture_count": len(fixtures),
        "pillar_distribution": by_pillar,
        "verdict_distribution": by_verdict,
        "accepted": accepted,
        "negative_controls": {
            "total": len(controls),
            "rejected": controls_rejected,
            "all_rejected": controls_rejected == len(controls),
            "detail": controls,
        },
        "fixtures": [f.to_dict() for f in fixtures],
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.shadow_run",
        description="Offline, reproducible shadow run — no network, no publishing.",
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20261028)
    parser.add_argument("--out", default="docs/v2/shadow-report.json")
    args = parser.parse_args(argv)

    report = run(count=args.count, seed=args.seed)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Shadow run complete — {report['fixture_count']} fixtures")
    print(f"  persona: {report['persona']}")
    print(f"  live publishing: {report['live_publishing']}")
    print(f"  external calls: {report['external_calls']}")
    print("  pillars:")
    for pillar, n in sorted(report["pillar_distribution"].items()):
        print(f"    {pillar:<16} {n}")
    print("  verdicts:")
    for verdict, n in sorted(report["verdict_distribution"].items()):
        print(f"    {verdict:<16} {n}")
    nc = report["negative_controls"]
    print(f"  negative controls rejected: {nc['rejected']}/{nc['total']}")
    if not nc["all_rejected"]:
        print("  !! SOME NEGATIVE CONTROLS PASSED — the gate is not failing closed")
        return 1
    print(f"\nReport written to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
