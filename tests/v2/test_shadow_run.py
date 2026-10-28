"""tests/v2/test_shadow_run.py — Phase 7 offline shadow run guarantees.

The shadow run is the acceptance gate for the whole V2 content pipeline. These
tests pin its contract so a regression cannot quietly turn it into something
that touches the network or publishes.
"""
from __future__ import annotations

import json

import pytest

from src.core.storage import migrate
from src.shadow_run import (
    PILLARS, generate_fixtures, generate_negative_controls, run,
)


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


class TestShadowRun:
    def test_produces_requested_fixture_count(self, db):
        fixtures = generate_fixtures(count=30, path=db)
        assert len(fixtures) == 30

    def test_covers_all_five_pillars(self, db):
        fixtures = generate_fixtures(count=30, path=db)
        seen = {f.pillar for f in fixtures}
        assert seen == set(PILLARS)

    def test_is_reproducible(self, db):
        """Same seed -> identical fixtures. That is what makes it reviewable."""
        a = generate_fixtures(count=30, seed=1234, path=db)
        b = generate_fixtures(count=30, seed=1234, path=db)
        assert [f.content for f in a] == [f.content for f in b]
        assert [f.verdict for f in a] == [f.verdict for f in b]

    def test_different_seeds_differ(self, db):
        a = generate_fixtures(count=30, seed=1, path=db)
        b = generate_fixtures(count=30, seed=2, path=db)
        assert [f.variation for f in a] != [f.variation for f in b]

    def test_no_external_calls(self, db):
        """The run must not import a network client at all."""
        import sys

        before = set(sys.modules)
        run(count=10, path=db)
        new = {m for m in set(sys.modules) - before}
        for banned in ("requests", "urllib.request", "http.client"):
            assert banned not in new, f"shadow run pulled in {banned}"

    def test_report_records_no_live_publishing(self, db):
        report = run(count=10, path=db)
        assert report["live_publishing"] is False
        assert report["external_calls"] == 0
        assert report["mode"] == "offline_shadow"

    def test_report_has_pillar_and_verdict_distribution(self, db):
        report = run(count=30, path=db)
        assert sum(report["pillar_distribution"].values()) == 30
        assert sum(report["verdict_distribution"].values()) == 30

    def test_persona_is_recorded(self, db):
        report = run(count=10, path=db)
        assert report["persona"] == "teman_tongkrongan_v1"

    def test_fixtures_are_json_serialisable(self, db):
        report = run(count=10, path=db)
        text = json.dumps(report, ensure_ascii=False)
        assert "fixtures" in text

    def test_affiliate_fixtures_carry_disclosure(self, db):
        fixtures = generate_fixtures(count=30, path=db)
        aff = [f for f in fixtures if f.pillar == "affiliate"]
        assert aff
        for f in aff:
            assert "afiliasi" in f.content.lower(), f.content[:60]

    def test_no_fixture_contains_hashtags(self, db):
        for f in generate_fixtures(count=30, path=db):
            assert "#" not in f.content, f.content[:60]

    def test_no_fixture_claims_firsthand_experience(self, db):
        from src.content.verifier import has_fabricated_firsthand

        for f in generate_fixtures(count=30, path=db):
            assert has_fabricated_firsthand(f.content) is False, f.content[:60]


class TestNegativeControls:
    def test_every_control_is_rejected(self, db):
        controls = generate_negative_controls(path=db)
        assert controls
        for c in controls:
            assert c["rejected"] is True, (
                f"control '{c['control']}' passed the gate: {c['content'][:60]}"
            )

    def test_controls_cover_the_mandatory_veto_classes(self, db):
        controls = generate_negative_controls(path=db)
        names = {c["control"] for c in controls}
        for required in ("fabricated_firsthand", "unsourced_number",
                         "generic_filler", "engagement_bait", "clickbait",
                         "hashtag", "no_disclosure"):
            assert required in names, required

    def test_each_control_names_a_veto_code(self, db):
        for c in generate_negative_controls(path=db):
            assert c["vetoes"], c["control"]

    def test_fabricated_firsthand_is_caught(self, db):
        controls = {c["control"]: c for c in generate_negative_controls(path=db)}
        assert "fabricated_firsthand_usage" in controls["fabricated_firsthand"]["vetoes"]

    def test_missing_disclosure_is_caught(self, db):
        controls = {c["control"]: c for c in generate_negative_controls(path=db)}
        assert "no_disclosure" in controls["no_disclosure"]["vetoes"]

    def test_engagement_bait_is_caught(self, db):
        controls = {c["control"]: c for c in generate_negative_controls(path=db)}
        assert "engagement_bait_closer" in controls["engagement_bait"]["vetoes"]


class TestShadowRunReport:
    def test_report_flags_failing_controls(self, db):
        report = run(count=10, path=db)
        assert report["negative_controls"]["all_rejected"] is True

    def test_report_is_deterministic(self, db):
        a = run(count=10, seed=99, path=db)
        b = run(count=10, seed=99, path=db)
        assert a["verdict_distribution"] == b["verdict_distribution"]
        assert [f["content"] for f in a["fixtures"]] == \
               [f["content"] for f in b["fixtures"]]
