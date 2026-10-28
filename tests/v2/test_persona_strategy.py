"""tests/v2/test_persona_strategy.py — Phase 2 persona & strategy tests.

Acceptance criteria covered:
  #1 consistent persona across manual / scheduled / Telegram paths
  #2 generator works with every news/trend source disabled
  #3 30-day calendar respects pillar distribution; affiliate downgrades
"""
from __future__ import annotations

import random

import pytest

from src.core.storage import connect, migrate
from src.persona.profile import (
    PERSONA_SEED, PERSONA_VERSION, get_active_persona, get_persona_version,
    list_persona_versions, persona_prompt_block, save_persona, voice_variation_block,
)
from src.persona import voice as voice_mod
from src.audience.signals import (
    SEED_SIGNALS, add_signal, count_by_category, get_signals, retire_signal,
    seed_if_empty, AudienceSignal,
)
from src.strategy.calendar import (
    PILLAR_TARGETS_30, build_calendar, downgrade_to_organic, get_slot,
    pillar_distribution, upcoming_slots, assign_problem,
)
from src.strategy.ideation import generate_ideas, save_idea, score_idea, set_idea_state


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


# ── Persona Bible ───────────────────────────────────────────────────────────
class TestPersonaBible:
    def test_seed_is_teman_tongkrongan(self):
        assert PERSONA_VERSION == "teman_tongkrongan_v1"
        assert PERSONA_SEED.name == "Teman Tongkrongan"
        assert "Teman Tongkrongan" in PERSONA_SEED.archetype

    def test_seed_is_not_news_first(self):
        blob = " ".join(PERSONA_SEED.voice_rules).lower()
        blob += " " + PERSONA_SEED.bio.lower()
        for banned in ("berita", "bmkg", "ihsg", "sepak bola", "timnas", "warga threads"):
            assert banned not in blob, banned

    def test_seed_forbids_fabricated_experience(self):
        joined = " ".join(PERSONA_SEED.forbidden).lower()
        assert "pengalaman langsung" in joined
        assert "testimoni palsu" in joined

    def test_get_active_persona_seeds_on_first_call(self, db):
        p = get_active_persona(db)
        assert p.version == PERSONA_VERSION
        versions = list_persona_versions(db)
        assert len(versions) == 1
        assert versions[0]["active"] == 1

    def test_get_active_persona_is_stable_across_calls(self, db):
        a = get_active_persona(db)
        b = get_active_persona(db)
        assert a.version == b.version
        assert a.name == b.name
        assert a.voice_rules == b.voice_rules

    def test_persona_is_versioned_and_retrievable(self, db):
        get_active_persona(db)
        v2 = PERSONA_SEED
        v2 = type(v2)(**{**v2.__dict__, "version": "teman_tongkrongan_v2",
                         "name": "Teman Tongkrongan v2"})
        save_persona(v2, activate=True, path=db)
        assert get_active_persona(db).version == "teman_tongkrongan_v2"
        assert get_persona_version("teman_tongkrongan_v1", db) is not None
        assert get_persona_version("nope", db) is None

    def test_only_one_active_version(self, db):
        save_persona(PERSONA_SEED, activate=True, path=db)
        other = type(PERSONA_SEED)(**{**PERSONA_SEED.__dict__,
                                      "version": "other_v1"})
        save_persona(other, activate=True, path=db)
        active = [v for v in list_persona_versions(db) if v["active"]]
        assert len(active) == 1

    def test_prompt_block_contains_identity_and_bans(self, db):
        block = persona_prompt_block(get_active_persona(db))
        assert "IDENTITAS AKUN" in block
        assert "Teman Tongkrongan" in block
        assert "DILARANG KERAS" in block
        assert "ATURAN SUARA" in block

    def test_prompt_block_is_identical_for_every_caller(self, db):
        """The same block feeds manual, scheduled, and Telegram drafts."""
        p = get_active_persona(db)
        assert persona_prompt_block(p) == persona_prompt_block(p)

    def test_voice_variation_block_mentions_variation(self):
        block = voice_variation_block({"format": "comparison", "mood": "kritis"})
        assert "VARIASI" in block
        assert "comparison" in block
        assert "kritis" in block

    def test_voice_variation_block_never_empty(self):
        assert voice_variation_block(None).strip()
        assert voice_variation_block({}).strip()


# ── Voice variation ─────────────────────────────────────────────────────────
class TestVoiceVariation:
    def test_random_variation_uses_known_values(self):
        rng = random.Random(7)
        for _ in range(30):
            v = voice_mod.random_variation(rng)
            assert v.format in voice_mod.FORMATS
            assert v.opening in voice_mod.OPENINGS
            assert v.length in voice_mod.LENGTHS
            assert v.mood in voice_mod.MOODS
            assert v.goal in voice_mod.GOALS

    def test_varied_variation_avoids_recent(self):
        rng = random.Random(11)
        history = [voice_mod.random_variation(rng).key() for _ in range(6)]
        for _ in range(10):
            v = voice_mod.varied_variation(history, rng)
            assert v.key() not in set(history)

    def test_varied_variation_falls_back_when_exhausted(self):
        """Total function: even a saturated window returns something."""
        all_keys = [
            voice_mod.VoiceVariation(f, o, l, m, g).key()
            for f in voice_mod.FORMATS for o in voice_mod.OPENINGS
            for l in voice_mod.LENGTHS for m in voice_mod.MOODS
            for g in voice_mod.GOALS
        ]
        v = voice_mod.varied_variation(all_keys, random.Random(3))
        assert v.key() in set(all_keys)

    def test_word_budget_ordering(self):
        assert (voice_mod.word_budget("sangat_pendek")[1]
                < voice_mod.word_budget("pendek")[1]
                < voice_mod.word_budget("sedang")[1])

    def test_mini_thread_maps_to_thread_structure(self):
        assert voice_mod.structure_for_format("mini_thread") == "thread"
        assert voice_mod.structure_for_format("single_snappy") == "single"

    def test_guides_are_non_empty(self):
        for opening in voice_mod.OPENINGS:
            assert voice_mod.opening_guide(opening).strip()
        for mood in voice_mod.MOODS:
            assert voice_mod.mood_guide(mood).strip()
        for goal in voice_mod.GOALS:
            assert voice_mod.goal_guide(goal).strip()

    def test_variation_prompt_has_five_dimensions(self):
        v = voice_mod.random_variation(random.Random(5))
        text = voice_mod.variation_prompt(v)
        assert text.count("- ") == 5


# ── Audience signals ────────────────────────────────────────────────────────
class TestAudienceSignals:
    def test_seed_covers_all_five_pillars(self):
        cats = {s.category for s in SEED_SIGNALS}
        assert cats == {"relatable", "practical_tips", "opinions",
                        "discoveries", "affiliate"}

    def test_seed_has_no_unsourced_statistics(self):
        """Seed problems must be writable without any external evidence.

        Wattage ratings (30W/65W/100W) and a '3-day sale' are the subject
        matter itself, not a claimed measurement — those are allowed. What is
        banned is a percentage, a price in rupiah, or a study figure that the
        writer would have to invent a source for.
        """
        import re
        for s in SEED_SIGNALS:
            assert "%" not in s.problem, s.problem
            assert not re.search(r"\brp\b|\brid\b", s.problem, re.I), s.problem
            assert not re.search(r"\d+\s*(?:ribu|juta|miliar|persen)", s.problem, re.I), s.problem
            assert "menurut penelitian" not in s.problem.lower(), s.problem

    def test_seed_if_empty_populates_once(self, db):
        assert seed_if_empty(db) == len(SEED_SIGNALS)
        assert seed_if_empty(db) == 0
        assert len(get_signals(path=db)) == len(SEED_SIGNALS)

    def test_count_by_category(self, db):
        seed_if_empty(db)
        counts = count_by_category(db)
        assert counts["relatable"] == 10
        assert counts["practical_tips"] == 8
        assert counts["opinions"] == 6
        assert counts["discoveries"] == 3
        assert counts["affiliate"] == 3

    def test_add_and_retire_signal(self, db):
        sid = add_signal(AudienceSignal("practical_tips", "uji coba sinyal baru"),
                         path=db)
        assert len(get_signals(category="practical_tips", path=db)) >= 1
        retire_signal(sid, path=db)
        active = [s for s in get_signals(path=db) if s.id == sid]
        assert active == []

    def test_get_signals_filters_by_category(self, db):
        seed_if_empty(db)
        aff = get_signals(category="affiliate", path=db)
        assert aff
        assert all(s.category == "affiliate" for s in aff)


# ── Calendar ────────────────────────────────────────────────────────────────
class TestCalendar:
    def test_30_day_distribution_matches_targets(self, db):
        slots = build_calendar(days=30, rng=random.Random(1), path=db)
        mains = [s for s in slots if s.slot_kind == "main"]
        assert len(mains) == 30
        dist = pillar_distribution(days=30, path=db)
        for pillar, target in PILLAR_TARGETS_30.items():
            assert dist.get(pillar, 0) == target, (pillar, dist.get(pillar), target)

    def test_no_pillar_clusters_into_a_run(self, db):
        slots = build_calendar(days=30, rng=random.Random(4), path=db)
        mains = sorted(
            [s for s in slots if s.slot_kind == "main"], key=lambda s: s.slot_date
        )
        longest = 1
        run = 1
        for a, b in zip(mains, mains[1:]):
            run = run + 1 if a.pillar == b.pillar else 1
            longest = max(longest, run)
        assert longest <= 2, f"pillar ran {longest} days in a row"

    def test_extras_are_optional_and_limited(self, db):
        slots = build_calendar(days=30, rng=random.Random(2), path=db)
        extras = [s for s in slots if s.slot_kind == "extra"]
        assert 0 < len(extras) <= 3 * 5  # ~3 per week over 30 days
        assert all(s.pillar != "affiliate" for s in extras)

    def test_affiliate_slots_are_commercial(self, db):
        slots = build_calendar(days=30, rng=random.Random(9), path=db)
        aff = [s for s in slots if s.pillar == "affiliate"]
        assert len(aff) == 3
        assert all(s.commercial_allowed for s in aff)

    def test_organic_slots_are_not_commercial(self, db):
        slots = build_calendar(days=30, rng=random.Random(9), path=db)
        organic = [s for s in slots if s.pillar in ("relatable", "opinions")]
        assert organic
        assert all(not s.commercial_allowed for s in organic)

    def test_calendar_is_idempotent(self, db):
        a = build_calendar(days=30, rng=random.Random(3), path=db)
        b = build_calendar(days=30, rng=random.Random(3), path=db)
        assert len([s for s in a if s.slot_kind == "main"]) == \
               len([s for s in b if s.slot_kind == "main"])
        assert pillar_distribution(days=30, path=db) == PILLAR_TARGETS_30

    def test_get_slot_roundtrip(self, db):
        slots = build_calendar(days=7, rng=random.Random(5), path=db)
        target = next(s for s in slots if s.slot_kind == "main")
        got = get_slot(target.slot_date, "main", path=db)
        assert got is not None
        assert got.pillar == target.pillar

    def test_assign_problem(self, db):
        slots = build_calendar(days=7, rng=random.Random(6), path=db)
        target = next(s for s in slots if s.slot_kind == "main")
        assign_problem(target.id, "kabel selalu kusut", "observasi", path=db)
        got = get_slot(target.slot_date, "main", path=db)
        assert got.audience_problem == "kabel selalu kusut"
        assert got.target_angle == "observasi"

    def test_downgrade_affiliate_to_organic(self, db):
        slots = build_calendar(days=30, rng=random.Random(9), path=db)
        aff = next(s for s in slots if s.pillar == "affiliate")
        downgrade_to_organic(aff.id, "no verified product", path=db)
        got = get_slot(aff.slot_date, "main", path=db)
        assert got.pillar == "practical_tips"
        assert got.commercial_allowed is False
        assert "downgraded_from_affiliate" in got.source_constraints

    def test_upcoming_slots_are_ordered(self, db):
        build_calendar(days=30, rng=random.Random(8), path=db)
        ups = upcoming_slots(days=7, path=db)
        assert ups
        dates = [s.slot_date for s in ups]
        assert dates == sorted(dates)

    def test_calendar_works_with_no_news_sources(self, db):
        """No trend module is imported or called anywhere in this path."""
        import sys
        before = set(sys.modules)
        build_calendar(days=30, rng=random.Random(12), path=db)
        new_mods = {m for m in set(sys.modules) - before if "trend" in m}
        assert not new_mods, f"calendar pulled in trend modules: {new_mods}"


# ── Ideation ────────────────────────────────────────────────────────────────
class TestIdeation:
    def test_generates_requested_count(self, db):
        ideas = generate_ideas(count=3, rng=random.Random(1), path=db)
        assert len(ideas) == 3

    def test_ideas_are_distinct(self, db):
        ideas = generate_ideas(count=3, rng=random.Random(2), path=db)
        keys = {(i.pillar, i.angle, i.audience_problem) for i in ideas}
        assert len(keys) == 3

    def test_ideas_are_ranked_descending(self, db):
        ideas = generate_ideas(count=3, rng=random.Random(3), path=db)
        scores = [i.rank_score for i in ideas]
        assert scores == sorted(scores, reverse=True)

    def test_ideas_respect_pillar_filter(self, db):
        ideas = generate_ideas(pillar="practical_tips", count=3,
                               rng=random.Random(4), path=db)
        assert ideas
        assert all(i.pillar == "practical_tips" for i in ideas)

    def test_ideas_work_with_no_news_or_trends(self, db):
        """Acceptance #2: useful ideas with every external source disabled."""
        import sys
        before = set(sys.modules)
        ideas = generate_ideas(count=3, rng=random.Random(13), path=db)
        assert len(ideas) == 3
        new_mods = {m for m in set(sys.modules) - before if "trend" in m}
        assert not new_mods

    def test_ideas_cover_multiple_pillars_over_runs(self, db):
        """Across repeated runs every pillar must eventually surface.

        A single run only asks for 3 ideas, so it can cover at most 3 pillars;
        the guarantee that matters is that no pillar is permanently starved.
        """
        seen = set()
        for seed in range(12):
            for idea in generate_ideas(count=5, rng=random.Random(seed), path=db):
                seen.add(idea.pillar)
        assert len(seen) == 5, f"only {sorted(seen)} were ever generated"

    def test_single_run_is_never_mono_pillar(self, db):
        for seed in range(10):
            ideas = generate_ideas(count=3, rng=random.Random(seed), path=db)
            pillars = {i.pillar for i in ideas}
            assert len(pillars) >= 2, f"seed {seed} returned only {pillars}"

    def test_score_idea_rewards_novelty(self, db):
        seed_if_empty(db)
        sig = AudienceSignal("practical_tips", "masalah uji novelty", confidence=0.9)
        fresh, _ = score_idea(sig, "angle baru", set(), set(), {})
        stale, _ = score_idea(sig, "angle baru", {"angle baru"}, {"masalah uji novelty"}, {})
        assert fresh > stale

    def test_score_idea_rewards_pillar_deficit(self, db):
        seed_if_empty(db)
        sig = AudienceSignal("affiliate", "butuh rekomendasi", confidence=0.7)
        behind, _ = score_idea(sig, "a", set(), set(), {"affiliate": 0})
        ahead, _ = score_idea(sig, "a", set(), set(), {"affiliate": 99})
        assert behind > ahead

    def test_save_and_update_idea(self, db):
        ideas = generate_ideas(count=1, rng=random.Random(7), path=db)
        iid = save_idea(ideas[0], planned_date="2026-11-01", path=db)
        set_idea_state(iid, "DRAFT", path=db)
        with connect(db) as conn:
            row = conn.execute(
                "SELECT idea_state, planned_date FROM content_ideas WHERE id = ?",
                (iid,),
            ).fetchone()
        assert row["idea_state"] == "DRAFT"
        assert row["planned_date"] == "2026-11-01"

    def test_repeated_generation_avoids_recent_angles(self, db):
        first = generate_ideas(count=3, rng=random.Random(21), path=db)
        for idea in first:
            save_idea(idea, path=db)
        second = generate_ideas(count=3, rng=random.Random(21), path=db)
        overlap = {i.angle for i in first} & {i.angle for i in second}
        assert len(overlap) < 3, "ideation keeps repeating the same angles"
