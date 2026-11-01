"""src/strategy/ideation.py — three creative ideas per slot.

V1 reached for a headline. V2 asks: given this audience problem, what are three
genuinely different ways to talk about it? Ideas are ranked by audience
relevance, novelty, persona fit, and schedule balance — then the writer takes
the best one.

Every idea is generated from the local audience signal catalog. No news feed,
no trend API, no network. That is what makes the system work with all external
sources disabled.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from src.audience.signals import AudienceSignal, get_signals, seed_if_empty
from src.core.models import ContentIdea, IDEA
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("strategy_ideation")

# Angle templates per pillar. Each one is a different *way to talk about* a
# problem, not a different problem — that is what keeps 3 ideas distinct.
ANGLE_TEMPLATES = {
    "relatable": [
        "observasi kebiasaan yang jarang disadari",
        "cerita singkat situasi yang biasa kejadian",
        "kontras antara niat dan kenyataan",
        "satu detail kecil yang bikin kesel",
    ],
    "practical_tips": [
        "langkah konkret yang bisa dicek hari ini",
        "cara cek sebelum beli / sebelum rusak",
        "kesalahan umum dan cara hindarinya",
        "urutan prioritas kalau budget terbatas",
    ],
    "opinions": [
        "trade-off yang jarang dibahas jujur",
        "klaim marketing vs realita pemakaian",
        "pendapat yang tidak populer tapi masuk akal",
        "kapan sesuatu yang mahal memang worth it",
    ],
    "discoveries": [
        "kategori kecil yang sering terlupakan",
        "fitur yang ada tapi jarang dipakai",
        "alat sederhana yang menghemat waktu",
        "hal 'membosankan' yang sebenarnya penting",
    ],
    "affiliate": [
        "panduan memilih sesuai kebutuhan, bukan harga termurah",
        "apa yang harus diverifikasi sebelum klik link",
        "kapan TIDAK perlu beli yang baru",
    ],
}


@dataclass
class IdeaCandidate:
    pillar: str
    angle: str
    audience_problem: str
    provenance: str = ""
    signal_id: Optional[int] = None
    rank_score: float = 0.0
    novelty: float = 0.0
    relevance: float = 0.0
    persona_fit: float = 0.0
    balance: float = 0.0
    observation: str = ""
    delivery_hint: str = ""
    id: Optional[int] = None

    def to_idea(self) -> ContentIdea:
        return ContentIdea(
            pillar=self.pillar,
            angle=self.angle,
            audience_problem=self.audience_problem,
            source_ids=[self.signal_id] if self.signal_id else [],
            rank_score=self.rank_score,
            state=IDEA,
            id=self.id,
        )


def _recent_angles(limit: int = 20, path=None) -> set[str]:
    with connect(path) as conn:
        try:
            rows = conn.execute(
                "SELECT angle FROM content_ideas ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except Exception:
            return set()
    return {r["angle"] for r in rows}


def _recent_problems(limit: int = 20, path=None) -> set[str]:
    with connect(path) as conn:
        try:
            rows = conn.execute(
                "SELECT audience_problem FROM content_ideas "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        except Exception:
            return set()
    return {r["audience_problem"] for r in rows if r["audience_problem"]}

def _tokenize(text: str) -> set[str]:
    import re
    words = re.findall(r"\w+", text.lower())
    stops = {"yang", "dan", "di", "ke", "dari", "ini", "itu", "untuk", "pada", "adalah", "bisa", "ada"}
    return {w for w in words if len(w) > 2 and w not in stops}


def jaccard_similarity(text_a: str, text_b: str) -> float:
    set_a = _tokenize(text_a)
    set_b = _tokenize(text_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def is_duplicate_premise(candidate_text: str, recent_premises: list[str], threshold: float = 0.55) -> bool:
    """Check if candidate premise is too similar to any premise from recent days."""
    for past in recent_premises:
        if jaccard_similarity(candidate_text, past) >= threshold:
            return True
    return False


def _recent_premises_14_days(days: int = 14, path=None) -> list[str]:
    """Retrieve premises used in ideas/drafts within the last N days."""
    with connect(path) as conn:
        try:
            rows = conn.execute(
                """
                SELECT audience_problem FROM content_ideas 
                WHERE created_at >= datetime('now', ?) AND audience_problem != ''
                UNION
                SELECT angle FROM content_ideas 
                WHERE created_at >= datetime('now', ?) AND angle != ''
                """,
                (f"-{days} days", f"-{days} days"),
            ).fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []


def filter_premise(premise: str, pillar: str = "") -> tuple[bool, str]:
    """Pre-generation rejection gate.

    Rejects ideas before writing if they are mundane, forced, or lack a clear point.
    Returns (True, "") or (False, reason_code).
    """
    if not premise or len(premise.strip()) < 12:
        return False, "empty_premise"

    text = premise.lower()

    # 1. Unsafe practical advice
    unsafe_patterns = [
        "tembaga keliatan", "tembaga terbuka", "kabel terkelupas",
        "ditusuk jarum", "dicuci air", "masih aman dipakai",
    ]
    if any(p in text for p in unsafe_patterns):
        return False, "unsafe_practical_advice"

    # 2. Forced metaphor / anthropomorphism
    forced_patterns = [
        "nakal", "ngambek", "punya perasaan", "butuh pelukan",
        "dia lelah", "dia marah", "kabel berontak",
    ]
    if any(p in text for p in forced_patterns):
        return False, "forced_metaphor"

    # 3. Generic AI clichés
    cliche_patterns = [
        "niat praktis kenyataannya", "butuh sedikit perhatian",
        "menjadi saksi bisu", "di era serba digital",
    ]
    if any(p in text for p in cliche_patterns):
        return False, "generic_ai_voice"

    # 4. Mundane observation with no clear point / technical angle
    if text.strip() in [
        "kabel charger yang selalu kusut di tas dan putus di ujung",
        "kabel kusut di tas",
    ]:
        return False, "trivial_observation"

    return True, ""
def score_idea(
    signal: AudienceSignal,
    angle: str,
    used_angles: set[str],
    used_problems: set[str],
    pillar_counts: dict[str, int],
) -> tuple[float, dict[str, float]]:
    """Score out of 100. Weights are heuristics, not engagement predictors.

    The balance term is deliberately heavy: without it the persona-fit term
    alone collapses every run onto practical_tips and relatable, and the
    opinions / discoveries / affiliate pillars never get written at all.
    """
    # Audience relevance: how confident we are this is a real, shared problem.
    relevance = signal.confidence * 20.0

    # Novelty: unseen angle and unseen problem both earn points.
    novelty = 0.0
    if angle not in used_angles:
        novelty += 10.0
    if signal.problem not in used_problems:
        novelty += 10.0

    # Persona fit: the persona is practical and observational. Practical tips
    # and relatable observations are the natural centre of gravity; affiliate
    # ideas must earn their place through verification, not volume.
    persona_fit = {
        "practical_tips": 20.0,
        "relatable": 19.0,
        "opinions": 17.0,
        "discoveries": 16.0,
        "affiliate": 12.0,
    }.get(signal.category, 15.0)

    # Schedule balance: a pillar that is behind its 30-day target gets a push.
    # Scaled so a completely unwritten pillar outranks a well-served one.
    from src.strategy.calendar import PILLAR_TARGETS_30
    target = PILLAR_TARGETS_30.get(signal.category, 1)
    current = pillar_counts.get(signal.category, 0)
    deficit = max(0.0, (target - current) / max(1, target))
    balance = deficit * 40.0

    total = relevance + novelty + persona_fit + balance
    return total, {
        "relevance": round(relevance, 2),
        "novelty": round(novelty, 2),
        "persona_fit": round(persona_fit, 2),
        "balance": round(balance, 2),
    }


def generate_ideas(
    pillar: Optional[str] = None,
    count: int = 3,
    rng: Optional[random.Random] = None,
    path=None,
) -> list[IdeaCandidate]:
    """Produce `count` distinct, ranked ideas.

    Distinctness is enforced by using a different angle template and a
    different audience signal for each idea, so the writer never receives
    three paraphrases of the same post.
    """
    r = rng or random.Random()
    seed_if_empty(path)

    signals = get_signals(category=pillar, limit=200, path=path)
    if not signals:
        log.warning("No audience signals available for pillar=%s", pillar)
        return []

    used_angles = _recent_angles(path=path)
    used_problems = _recent_problems(path=path)
    recent_14d = _recent_premises_14_days(days=14, path=path)
    from src.strategy.calendar import pillar_distribution
    pillar_counts = pillar_distribution(path=path)

    # Filter out signals that fail pre-generation rejection gate or 14-day duplicate check
    valid_signals = []
    for s in signals:
        ok, reason = filter_premise(s.problem, pillar=s.category)
        if not ok:
            continue
        if is_duplicate_premise(s.problem, recent_14d):
            continue
        valid_signals.append(s)

    if valid_signals:
        signals = valid_signals
    # Pick a spread of signals: rotate through the list so the same pillar is
    # not always chosen. Sorting purely by confidence collapses every run onto
    # whichever pillar happens to carry the highest-confidence seeds.
    r.shuffle(signals)
    signals.sort(key=lambda s: -s.confidence)

    # Round-robin across pillars first, then by confidence inside each pillar.
    # This is what lets repeated runs cover all five pillars instead of
    # hammering the single highest-confidence category.
    by_pillar: dict[str, list[AudienceSignal]] = {}
    for s in signals:
        by_pillar.setdefault(s.category, []).append(s)
    ordered: list[AudienceSignal] = []
    pillar_order = list(by_pillar.keys())
    r.shuffle(pillar_order)
    idx = 0
    while any(by_pillar.values()):
        pillar = pillar_order[idx % len(pillar_order)]
        bucket = by_pillar.get(pillar) or []
        if bucket:
            ordered.append(bucket.pop(0))
        idx += 1

    chosen_signals = ordered[: max(count * 4, count)]
    candidates: list[IdeaCandidate] = []
    used_angles_local: set[str] = set()

    for signal in chosen_signals:
        templates = list(ANGLE_TEMPLATES.get(signal.category, ANGLE_TEMPLATES["relatable"]))
        r.shuffle(templates)
        for angle in templates:
            if angle in used_angles_local:
                continue
            total, parts = score_idea(
                signal, angle, used_angles | used_angles_local,
                used_problems, pillar_counts,
            )
            obs = f"{signal.problem} ({angle})"
            hint = "single_snappy" if ("observasi" in angle or len(signal.problem) < 40) else "short_paragraph"
            candidates.append(IdeaCandidate(
                pillar=signal.category,
                angle=angle,
                provenance=getattr(signal, "provenance", ""),
                audience_problem=signal.problem,
                signal_id=signal.id,
                rank_score=total,
                novelty=parts["novelty"],
                relevance=parts["relevance"],
                persona_fit=parts["persona_fit"],
                balance=parts["balance"],
                observation=obs,
                delivery_hint=hint,
            ))
            used_angles_local.add(angle)
            break
        if len(candidates) >= count * 3:
            break

    candidates.sort(key=lambda c: -c.rank_score)

    # Rank alone will not diversify: persona-fit keeps practical_tips and
    # relatable a few points ahead, so a pure top-N slice returns the same two
    # pillars forever and the opinions/discoveries/affiliate pillars never get
    # written. Take the best idea per pillar first, then fill the remainder by
    # score. The result is still ranked — it just cannot be mono-pillar.
    best_per_pillar: dict[str, IdeaCandidate] = {}
    for c in candidates:
        best_per_pillar.setdefault(c.pillar, c)
    picks: list[IdeaCandidate] = []
    for pillar in sorted(best_per_pillar, key=lambda p: -best_per_pillar[p].rank_score):
        picks.append(best_per_pillar[pillar])
        if len(picks) >= count:
            break
    if len(picks) < count:
        chosen_ids = {id(p) for p in picks}
        for c in candidates:
            if id(c) in chosen_ids:
                continue
            picks.append(c)
            if len(picks) >= count:
                break
    picks.sort(key=lambda c: -c.rank_score)
    return picks


def save_idea(candidate: IdeaCandidate, planned_date: Optional[str] = None, path=None) -> int:
    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                "INSERT INTO content_ideas "
                "(pillar, angle, provenance, audience_problem, source_ids_json, idea_state, planned_date, rank_score) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate.pillar, candidate.angle,
                    candidate.provenance, candidate.audience_problem,
                    str([candidate.signal_id] if candidate.signal_id else []),
                    IDEA, planned_date, candidate.rank_score,
                ),
            )
            return int(cur.lastrowid)


def get_idea(idea_id: int, path=None) -> Optional[ContentIdea]:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM content_ideas WHERE id = ?", (idea_id,)
        ).fetchone()
    if row is None:
        return None
    return ContentIdea(
        pillar=row["pillar"], angle=row["angle"],
        audience_problem="", source_ids=[], planned_date=row["planned_date"],
        rank_score=row["rank_score"], state=row["idea_state"], id=row["id"],
        created_at=row["created_at"],
    )


def set_idea_state(idea_id: int, state: str, path=None) -> None:
    with connect(path) as conn:
        with transaction(conn):
            conn.execute(
                "UPDATE content_ideas SET idea_state = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (state, idea_id),
            )
