"""src/commerce/catalog.py — verified product catalog with link provenance.

V1's `src/affiliate/tiktok.py` built two kinds of link that looked like
affiliate links but were not:

    build_tiktok_search_url()   -> https://www.tiktok.com/search?q=...
    build_tokopedia_affiliate_url() -> ...&af_id={TIKTOK_AFFILIATE_ID}

Neither proves commission attribution. A TikTok search URL is a search page.
An `af_id` parameter is a guess. Both were stored and later posted as though
they were commission-trackable.

V2's catalog has four link states and only one of them is publishable:

    UNVERIFIED  — imported, not yet checked
    VERIFIED    — an operator supplied the exact link from the official
                  affiliate dashboard, with provenance and an expiry
    EXPIRED     — past its expiry
    REVOKED     — withdrawn by the operator or the platform

`is_commission_ready` requires VERIFIED + a non-empty verified URL + an
approved reviewer status. Nothing else counts. Acceptance criterion #7.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.clock import parse_sqlite_utc, sqlite_utc, utc_now
from src.core.models import (
    EXPIRED, REVOKED, UNVERIFIED, VERIFIED, Product,
)
from src.core.storage import connect, transaction
from src.logger import get_logger

log = get_logger("commerce_catalog")

# Hosts that can never be a commission-tracked affiliate link on their own.
_SEARCH_HOSTS = (
    "tiktok.com/search",
    "tokopedia.com/search",
    "shopee.co.id/search",
    "lazada.co.id/catalog",
    "bukalapak.com/s",
    "google.com/search",
)

# Query parameters that indicate a real tracking link.
_TRACKING_PARAMS = (
    "af_id", "aff_id", "affiliate_id", "sub_id", "subid", "click_id",
    "utm_source", "utm_medium", "utm_campaign", "irclickid", "ttclid",
    "gclid", "fbclid", "aff_click_id",
)


def is_search_url(url: str) -> bool:
    """True when the URL is a marketplace search page, not a product link."""
    low = (url or "").lower()
    return any(host in low for host in _SEARCH_HOSTS)


def has_tracking_params(url: str) -> bool:
    low = (url or "").lower()
    return any(f"{p}=" in low or f"&{p}=" in low for p in _TRACKING_PARAMS)


def looks_like_affiliate_url(url: str) -> bool:
    """A weak heuristic. It is NOT proof of attribution.

    Used only to warn an operator that a link may be worth verifying — never
    to mark something VERIFIED.
    """
    if not url or is_search_url(url):
        return False
    return has_tracking_params(url)


@dataclass
class LinkAssessment:
    url: str
    is_search_url: bool
    has_tracking: bool
    verdict: str          # acceptable | search_url | no_tracking | empty
    reason: str


def assess_link(url: str) -> LinkAssessment:
    """Classify a candidate affiliate URL before it can enter the catalog."""
    if not url or not url.strip():
        return LinkAssessment(url or "", False, False, "empty",
                              "no URL supplied")
    if is_search_url(url):
        return LinkAssessment(
            url, True, has_tracking_params(url), "search_url",
            "this is a marketplace search page, not a commission-tracked "
            "product link",
        )
    if not has_tracking_params(url):
        return LinkAssessment(
            url, False, False, "no_tracking",
            "no tracking parameters — cannot prove commission attribution",
        )
    return LinkAssessment(url, False, True, "acceptable",
                          "carries tracking parameters; still requires "
                          "operator verification")


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            source_platform TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            original_affiliate_url TEXT DEFAULT '',
            verified_url TEXT DEFAULT '',
            affiliate_url TEXT DEFAULT '',
            tracking_state TEXT NOT NULL DEFAULT 'UNVERIFIED',
            checked_at TIMESTAMP,
            expires_at TIMESTAMP,
            price_if_verified REAL,
            restrictions TEXT DEFAULT '',
            reviewer_status TEXT NOT NULL DEFAULT 'pending',
            evidence_url TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _row_to_product(row) -> Product:
    return Product(
        id=int(row["id"]), name=row["name"], category=row["category"] or "",
        source_platform=row["source_platform"] or "",
        source_url=row["source_url"] or "",
        original_affiliate_url=row["original_affiliate_url"] or "",
        verified_affiliate_url=row["verified_url"] or "",
        tracking_status=row["tracking_state"] or UNVERIFIED,
        evidence_url=row["evidence_url"] or "",
        price_if_verified=row["price_if_verified"],
        checked_at=row["checked_at"], expires_at=row["expires_at"],
        restrictions=row["restrictions"] or "",
        reviewer_status=row["reviewer_status"] or "pending",
    )


def import_product(
    name: str,
    affiliate_url: str,
    source_platform: str = "",
    source_url: str = "",
    category: str = "",
    evidence_url: str = "",
    restrictions: str = "",
    expires_at: Optional[str] = None,
    source: str = "operator",
    path=None,
) -> tuple[int, LinkAssessment]:
    """Import a product as UNVERIFIED. Never auto-verifies.

    The URL is assessed on the way in so a search URL is rejected at import
    time rather than discovered later at publish time.
    """
    assessment = assess_link(affiliate_url)
    if assessment.verdict == "empty":
        raise ValueError("cannot import a product without an affiliate URL")
    if assessment.verdict == "search_url":
        raise ValueError(
            "refusing to import a marketplace search URL as an affiliate link: "
            f"{affiliate_url}"
        )

    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            cur = conn.execute(
                """
                INSERT INTO products
                    (source, name, category, source_platform, source_url,
                     original_affiliate_url, verified_url, affiliate_url,
                     tracking_state, evidence_url, restrictions, expires_at,
                     checked_at, reviewer_status)
                VALUES (?, ?, ?, ?, ?, ?, '', ?, 'UNVERIFIED', ?, ?, ?, ?, 'pending')
                """,
                (
                    source, name, category, source_platform, source_url,
                    affiliate_url, affiliate_url, evidence_url, restrictions,
                    expires_at, sqlite_utc(),
                ),
            )
            return int(cur.lastrowid), assessment


def verify_product(
    product_id: int,
    verified_url: str,
    evidence_url: str = "",
    expires_at: Optional[str] = None,
    price: Optional[float] = None,
    reviewer: str = "operator",
    path=None,
) -> bool:
    """Mark a product VERIFIED. Requires a real, tracking-carrying URL."""
    assessment = assess_link(verified_url)
    if assessment.verdict != "acceptable":
        log.warning(
            "Refusing to verify product %s: %s", product_id, assessment.reason
        )
        return False

    with connect(path) as conn:
        with transaction(conn):
            _ensure_table(conn)
            cur = conn.execute(
                """
                UPDATE products
                SET verified_url = ?, affiliate_url = ?, tracking_state = 'VERIFIED',
                    evidence_url = ?, expires_at = ?, price_if_verified = ?,
                    checked_at = ?, reviewer_status = 'approved'
                WHERE id = ?
                """,
                (
                    verified_url, verified_url, evidence_url, expires_at,
                    price, sqlite_utc(), product_id,
                ),
            )
            return cur.rowcount == 1


def revoke_product(product_id: int, reason: str = "", path=None) -> bool:
    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                "UPDATE products SET tracking_state = 'REVOKED', "
                "restrictions = ?, checked_at = ? WHERE id = ?",
                (reason, sqlite_utc(), product_id),
            )
            return cur.rowcount == 1


def expire_stale_products(path=None) -> int:
    """Move VERIFIED products past their expiry into EXPIRED."""
    now_s = sqlite_utc(utc_now())
    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                "UPDATE products SET tracking_state = 'EXPIRED' "
                "WHERE tracking_state = 'VERIFIED' AND expires_at IS NOT NULL "
                "AND expires_at != '' AND expires_at < ?",
                (now_s,),
            )
            return int(cur.rowcount)


def get_product(product_id: int, path=None) -> Optional[Product]:
    with connect(path) as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    return _row_to_product(row) if row else None


def list_products(
    tracking_state: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 100,
    path=None,
) -> list[Product]:
    sql = "SELECT * FROM products WHERE 1=1"
    params: list = []
    if tracking_state:
        sql += " AND tracking_state = ?"
        params.append(tracking_state)
    if category:
        sql += " AND category = ?"
        params.append(category)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_product(r) for r in rows]


def commission_ready_products(path=None) -> list[Product]:
    """Only products that may legally and technically carry a link."""
    expire_stale_products(path=path)
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM products WHERE tracking_state = 'VERIFIED' "
            "AND reviewer_status = 'approved' AND verified_url != '' "
            "ORDER BY id DESC"
        ).fetchall()
    return [_row_to_product(r) for r in rows]


def catalog_summary(path=None) -> dict:
    expire_stale_products(path=path)
    with connect(path) as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT tracking_state, COUNT(*) AS n FROM products "
            "GROUP BY tracking_state"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM products").fetchone()["n"]
    return {
        "total": int(total),
        "by_state": {r["tracking_state"]: int(r["n"]) for r in rows},
        "commission_ready": len(commission_ready_products(path=path)),
    }


def record_promotion(
    product_id: int,
    post_id: str,
    evidence: str = "",
    disclosed: bool = False,
    status: str = "planned",
    path=None,
) -> int:
    with connect(path) as conn:
        with transaction(conn):
            cur = conn.execute(
                """
                INSERT INTO promotions
                    (product_id, post_id, evidence, disclosed, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (product_id, str(post_id), evidence, 1 if disclosed else 0, status),
            )
            return int(cur.lastrowid)


def get_promotions(post_id: Optional[str] = None, path=None) -> list[dict]:
    sql = "SELECT * FROM promotions"
    params: list = []
    if post_id:
        sql += " WHERE post_id = ?"
        params.append(str(post_id))
    sql += " ORDER BY id DESC"
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
