"""tests/v2/test_affiliate_verified.py — Phase 5 verified affiliate system.

Acceptance criteria covered:
  #7  unknown/untracked product links cannot be labelled commission-ready
  #8  no affiliate comment on unrelated posts, regardless of engagement
  #17 product links, attribution, and disclosures validated before any live post
"""
from __future__ import annotations

import pytest

from src.commerce.catalog import (
    UNVERIFIED, VERIFIED, EXPIRED, REVOKED,
    assess_link, catalog_summary, commission_ready_products,
    expire_stale_products, get_product, import_product, is_search_url,
    list_products, record_promotion, get_promotions, revoke_product,
    verify_product, has_tracking_params, looks_like_affiliate_url,
)
from src.commerce.disclosure import (
    DISCLOSURE_SHORT, DISCLOSURE_TEXT, check_disclosure, ensure_disclosure,
    has_disclosure, strip_disclosure,
)
from src.commerce.relevance import (
    RelevanceVerdict, check_relevance, relevance_is_independent_of_engagement,
)
from src.core.models import Product
from src.core.storage import migrate


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "v2.db"
    migrate(path)
    return path


def _product(**kw) -> Product:
    base = dict(
        name="Power Bank 10000mAh", category="power_bank",
        source_platform="tiktok", source_url="",
        original_affiliate_url="", verified_affiliate_url="",
        tracking_status=UNVERIFIED, evidence_url="",
    )
    base.update(kw)
    return Product(**base)


REAL_LINK = "https://vt.tokopedia.com/t/ZS9U2RGPH37jF-mCKQh/?af_id=12345"
SEARCH_LINK = "https://www.tiktok.com/search?q=power+bank"
BARE_LINK = "https://tokopedia.com/product/power-bank-10000"


# ── Link assessment: the fabricated-URL fix ─────────────────────────────────
class TestLinkAssessment:
    def test_search_url_is_detected(self):
        assert is_search_url(SEARCH_LINK) is True
        assert is_search_url("https://shopee.co.id/search?keyword=x") is True
        assert is_search_url(REAL_LINK) is False

    def test_tracking_params_detected(self):
        assert has_tracking_params(REAL_LINK) is True
        assert has_tracking_params(BARE_LINK) is False

    def test_search_url_is_never_acceptable(self):
        a = assess_link(SEARCH_LINK)
        assert a.verdict == "search_url"
        assert "search page" in a.reason

    def test_bare_url_is_not_acceptable(self):
        a = assess_link(BARE_LINK)
        assert a.verdict == "no_tracking"

    def test_tracking_url_is_acceptable_but_unverified(self):
        a = assess_link(REAL_LINK)
        assert a.verdict == "acceptable"
        # "acceptable" is NOT "verified" — it only means worth checking.
        assert "operator verification" in a.reason

    def test_empty_url(self):
        a = assess_link("")
        assert a.verdict == "empty"

    def test_looks_like_affiliate_is_only_a_hint(self):
        assert looks_like_affiliate_url(REAL_LINK) is True
        assert looks_like_affiliate_url(SEARCH_LINK) is False
        assert looks_like_affiliate_url(BARE_LINK) is False


# ── Catalog: only VERIFIED is commission-ready ──────────────────────────────
class TestCatalog:
    def test_import_creates_unverified(self, db):
        pid, a = import_product("Power Bank", REAL_LINK, path=db)
        p = get_product(pid, path=db)
        assert p.tracking_status == UNVERIFIED
        assert p.reviewer_status == "pending"
        assert p.is_commission_ready is False

    def test_import_rejects_search_url(self, db):
        with pytest.raises(ValueError) as ei:
            import_product("Power Bank", SEARCH_LINK, path=db)
        assert "search URL" in str(ei.value)

    def test_import_rejects_empty_url(self, db):
        with pytest.raises(ValueError):
            import_product("Power Bank", "", path=db)

    def test_verify_requires_tracking_url(self, db):
        pid, _ = import_product("Power Bank", BARE_LINK, path=db)
        assert verify_product(pid, BARE_LINK, path=db) is False
        assert get_product(pid, path=db).tracking_status == UNVERIFIED

    def test_verify_rejects_search_url(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        assert verify_product(pid, SEARCH_LINK, path=db) is False

    def test_verify_marks_commission_ready(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        assert verify_product(pid, REAL_LINK, evidence_url="https://dash.example",
                              path=db) is True
        p = get_product(pid, path=db)
        assert p.tracking_status == VERIFIED
        assert p.reviewer_status == "approved"
        assert p.is_commission_ready is True

    def test_unverified_is_never_commission_ready(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        assert get_product(pid, path=db).is_commission_ready is False

    def test_revoked_is_not_commission_ready(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        verify_product(pid, REAL_LINK, path=db)
        revoke_product(pid, "platform withdrew the campaign", path=db)
        p = get_product(pid, path=db)
        assert p.tracking_status == REVOKED
        assert p.is_commission_ready is False

    def test_expired_is_not_commission_ready(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        verify_product(pid, REAL_LINK, expires_at="2000-01-01 00:00:00", path=db)
        expire_stale_products(path=db)
        p = get_product(pid, path=db)
        assert p.tracking_status == EXPIRED
        assert p.is_commission_ready is False

    def test_future_expiry_stays_verified(self, db):
        pid, _ = import_product("Power Bank", REAL_LINK, path=db)
        verify_product(pid, REAL_LINK, expires_at="2099-01-01 00:00:00", path=db)
        expire_stale_products(path=db)
        assert get_product(pid, path=db).tracking_status == VERIFIED

    def test_commission_ready_list_only_returns_verified(self, db):
        pid1, _ = import_product("A", REAL_LINK, path=db)
        pid2, _ = import_product("B", REAL_LINK + "&x=1", path=db)
        verify_product(pid1, REAL_LINK, path=db)
        ready = commission_ready_products(path=db)
        assert [p.id for p in ready] == [pid1]
        assert all(p.is_commission_ready for p in ready)

    def test_empty_verified_url_is_not_ready(self, db):
        """A VERIFIED row with no URL must not pass the readiness check."""
        p = _product(tracking_status=VERIFIED, verified_affiliate_url="",
                     reviewer_status="approved")
        assert p.is_commission_ready is False

    def test_pending_reviewer_is_not_ready(self, db):
        p = _product(tracking_status=VERIFIED,
                     verified_affiliate_url=REAL_LINK,
                     reviewer_status="pending")
        assert p.is_commission_ready is False

    def test_list_products_filters(self, db):
        import_product("A", REAL_LINK, category="power_bank", path=db)
        import_product("B", REAL_LINK + "&y=2", category="cable", path=db)
        assert len(list_products(category="power_bank", path=db)) == 1
        assert len(list_products(tracking_state=UNVERIFIED, path=db)) == 2

    def test_catalog_summary(self, db):
        pid, _ = import_product("A", REAL_LINK, path=db)
        verify_product(pid, REAL_LINK, path=db)
        import_product("B", REAL_LINK + "&z=3", path=db)
        s = catalog_summary(path=db)
        assert s["total"] == 2
        assert s["by_state"][VERIFIED] == 1
        assert s["by_state"][UNVERIFIED] == 1
        assert s["commission_ready"] == 1

    def test_promotion_records_disclosure(self, db):
        pid, _ = import_product("A", REAL_LINK, path=db)
        verify_product(pid, REAL_LINK, path=db)
        prid = record_promotion(pid, "post_123", evidence=REAL_LINK,
                                disclosed=True, status="published", path=db)
        proms = get_promotions("post_123", path=db)
        assert len(proms) == 1
        assert proms[0]["disclosed"] == 1
        assert proms[0]["status"] == "published"


# ── Relevance: engagement is not a factor ───────────────────────────────────
class TestRelevance:
    def test_matching_product_is_relevant(self, db):
        p = _product()
        v = check_relevance(
            p,
            post_text="Gue lagi cari power bank yang kapasitasnya nggak bohong.",
            audience_problem="Bingung pilih power bank: capacity nyata vs angka di kemasan",
            pillar="affiliate",
        )
        assert v.relevant is True
        assert v.matched_terms

    def test_unrelated_product_is_blocked(self, db):
        p = _product(name="Kaus Rajut Premium", category="fashion")
        v = check_relevance(
            p,
            post_text="Banjir rendam tiga wilayah di Aceh pagi ini.",
            audience_problem="Banjir rendam 3 wilayah Aceh",
            pillar="affiliate",
        )
        assert v.relevant is False
        assert "sensitive_topic" in v.blocked_by

    def test_sensitive_topic_blocks_even_matching_product(self, db):
        """Acceptance #8: a disaster post never gets a link, ever."""
        p = _product()
        v = check_relevance(
            p,
            post_text="Banjir bandang bikin banyak orang kehabisan daya "
                      "karena listrik mati, power bank jadi penting.",
            audience_problem="Banjir dan listrik mati",
            pillar="affiliate",
        )
        assert v.relevant is False
        assert "sensitive_topic" in v.blocked_by

    @pytest.mark.parametrize("pillar", [
        "relatable", "opinions", "breaking_disaster", "football_sports",
    ])
    def test_non_commercial_pillar_blocks(self, pillar, db):
        p = _product()
        v = check_relevance(
            p,
            post_text="Gue butuh power bank baru nih.",
            audience_problem="Butuh power bank",
            pillar=pillar,
        )
        assert v.relevant is False
        assert "pillar_not_commercial" in v.blocked_by

    def test_no_buying_intent_blocks(self, db):
        p = _product()
        v = check_relevance(
            p,
            post_text="Power bank di meja gue udah tiga tahun dipakai.",
            audience_problem="Power bank lama masih dipakai",
            pillar="affiliate",
        )
        assert v.relevant is False
        assert "no_buying_intent" in v.blocked_by

    def test_no_context_blocks(self, db):
        p = _product()
        v = check_relevance(p, post_text="", audience_problem="", pillar="affiliate")
        assert v.relevant is False
        assert "no_context" in v.blocked_by

    def test_no_term_overlap_blocks(self, db):
        p = _product(name="Tas Kanvas", category="bag")
        v = check_relevance(
            p,
            post_text="Gue mau beli headphone buat dengerin podcast.",
            audience_problem="Butuh headphone",
            pillar="affiliate",
        )
        assert v.relevant is False
        assert "no_term_overlap" in v.blocked_by

    def test_relevance_ignores_engagement_entirely(self, db):
        """The signature has no views/likes/replies parameter at all."""
        import inspect

        sig = inspect.signature(check_relevance)
        params = set(sig.parameters)
        for banned in ("views", "likes", "replies", "engagement", "reach"):
            assert banned not in params, banned
        assert relevance_is_independent_of_engagement() is True

    def test_high_engagement_does_not_rescue_an_unrelated_product(self, db):
        """Even a viral post gets nothing if the product does not fit."""
        p = _product(name="Blender Portable", category="kitchen")
        v = check_relevance(
            p,
            post_text="Thread ini lagi rame banget, banyak yang bahas "
                      "headphone murah.",
            audience_problem="Diskusi headphone murah",
            pillar="affiliate",
        )
        assert v.relevant is False


# ── Disclosure ──────────────────────────────────────────────────────────────
class TestDisclosure:
    def test_clean_post_has_no_disclosure(self):
        assert has_disclosure("Power bank murah sering bohong.") is False

    def test_disclosure_text_is_detected(self):
        assert has_disclosure(DISCLOSURE_TEXT) is True
        assert has_disclosure(DISCLOSURE_SHORT) is True

    def test_hashtag_disclosure_is_detected(self):
        assert has_disclosure("Rekomendasi power bank #afiliasi") is True

    def test_ensure_disclosure_appends(self):
        out = ensure_disclosure("Power bank ini worth it.")
        assert "afiliasi" in out.lower()
        assert out.startswith("Power bank ini worth it.")

    def test_ensure_disclosure_is_idempotent(self):
        once = ensure_disclosure("Power bank ini worth it.")
        twice = ensure_disclosure(once)
        assert once == twice
        assert twice.lower().count("afiliasi") == 1

    def test_ensure_disclosure_short_form(self):
        out = ensure_disclosure("Cek ini.", short=True)
        assert out.endswith(DISCLOSURE_SHORT)

    def test_check_disclosure_blocks_undisclosed_commercial(self):
        v = check_disclosure("Beli power bank ini ya.", is_commercial=True)
        assert v.present is False

    def test_check_disclosure_passes_with_label(self):
        v = check_disclosure(
            "Beli power bank ini ya.\n\n" + DISCLOSURE_TEXT, is_commercial=True
        )
        assert v.present is True

    def test_check_disclosure_ignores_non_commercial(self):
        v = check_disclosure("Hujan deras hari ini.", is_commercial=False)
        assert v.present is True

    def test_strip_disclosure_removes_label(self):
        text = "Power bank ini worth it.\n\n" + DISCLOSURE_TEXT
        out = strip_disclosure(text)
        assert "afiliasi" not in out.lower()
        assert "worth it" in out

    def test_strip_then_ensure_roundtrip(self):
        text = "Power bank ini worth it."
        labelled = ensure_disclosure(text)
        stripped = strip_disclosure(labelled)
        assert "worth it" in stripped
        assert "afiliasi" not in stripped.lower()


# ── The legacy fabricated-URL builders must not be used by V2 ───────────────
class TestLegacyAffiliateURLsAreRetired:
    def test_v2_never_calls_the_legacy_builders(self, db):
        """V2 must not import or use the synthetic URL builders."""
        import sys

        before = set(sys.modules)
        import_product("A", REAL_LINK, path=db)
        new = {m for m in set(sys.modules) - before}
        assert not any("affiliate.tiktok" in m for m in new)

    def test_legacy_builders_still_exist_for_the_running_daemon(self):
        """The old module is preserved (the daemon imports it), but V2 does not."""
        from src.affiliate.tiktok import (
            build_tiktok_search_url, build_tokopedia_affiliate_url,
        )

        # They still work — the legacy pipeline depends on them.
        assert build_tiktok_search_url("power bank").startswith(
            "https://www.tiktok.com/search"
        )
        # And V2's catalog refuses BOTH, which is the whole point: neither is
        # a commission-trackable product link.
        assert assess_link(build_tiktok_search_url("x")).verdict == "search_url"
        assert assess_link(
            build_tokopedia_affiliate_url("x")
        ).verdict == "search_url"
