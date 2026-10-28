# V2 — Product Specification

**Version:** 1.0 · **Date:** 2026-10-29 · **Branch:** `feature/v2-persona-first`

---

## 1. What This System Is

A local-first content and affiliate automation studio for one Threads account.
It writes, evaluates, schedules, and publishes posts in a single consistent
persona, and attaches affiliate links only when a product is verified and
genuinely relevant.

It is **not** a news aggregator, a mass-engagement bot, or a link spammer.

## 2. The Persona

**Teman Tongkrongan** (`teman_tongkrongan_v1`) — an Indonesian everyday tech
companion.

- **Voice:** conversational `aku/kalian`, short varied rhythms, observational,
  warm, lightly funny, practical.
- **Audience:** Indonesian adults who care about useful tech, reasonably priced
  gadgets, home/desk setups, everyday practical problems, and smarter purchases.
- **Account promise:** *"Ngobrolin kebiasaan kecil, teknologi sehari-hari, dan
  barang yang beneran ada gunanya — tanpa maksa beli."*
- **Hard rules:** never claim to have bought, used, or tested something without
  verified evidence. Never state a number, price, or spec without a source.
  Never use engagement-bait closers, clickbait, or hashtags.

One persona, versioned. Variation happens on the *surface* (format, opening,
length, mood, goal), never on the *identity*.

## 3. Content Pillars (30-post trial)

| Pillar | Count | Purpose |
|---|---|---|
| `relatable` | 10 | observational humour and daily behaviour |
| `practical_tips` | 8 | genuinely useful, checkable, often no-purchase solutions |
| `opinions` | 6 | honest trade-offs and debates without ragebait |
| `discoveries` | 3 | useful item categories, tech features, tools |
| `affiliate` | 3 | disclosure-labelled, source-verified recommendations only |

An affiliate slot with no verified product **downgrades to organic content**.
It never becomes a fabricated link.

## 4. Volume

- **1 principal post per day.**
- **Optional 2–3 extras per week**, only after the main slot is healthy.
- **Quiet hours** configurable, default 23:00–06:00 WIB.
- **No mandatory filler.** A slot with no acceptable draft is skipped.

## 5. The Quality Gate

Every draft — scheduled, manual, or Telegram-approved — passes the same gate.

**Scoring (out of 100):** persona voice 20 · audience relevance 20 · natural
writing 20 · specific useful content 20 · evidential correctness 15 · novelty 5.

| Score | Decision |
|---|---|
| ≥ 85 and zero vetoes | `APPROVED_AUTO` |
| 70–84 | `NEEDS_REVIEW` |
| < 70 | `REVISE_ONCE` (one bounded revision) |

**Hard vetoes** reject outright regardless of score:

`unverified_numeric_claim` · `fabricated_firsthand_usage` ·
`unknown_source_time_sensitive` · `incompatible_product` ·
`uncertain_affiliate_tracking` · `no_disclosure` · `near_duplicate` ·
`excessive_repetition` · `unsafe_sensitive_framing` · `unknown_image_rights` ·
`stale_invalid_draft` · `unauthorized_operator_action` · `generic_filler` ·
`clickbait_pattern` · `engagement_bait_closer` · `hashtag_present` ·
`empty_draft` · `critic_unavailable`

## 6. Affiliate Rules

1. A product enters the catalog as `UNVERIFIED`.
2. Only an operator-supplied link from the official affiliate dashboard, with
   provenance and an expiry, can become `VERIFIED`.
3. `VERIFIED` + non-empty URL + approved reviewer = **commission-ready**.
   Nothing else is.
4. A marketplace **search URL is never** an affiliate link.
5. Relevance is decided by product/problem overlap plus buying intent.
   **Engagement (views, likes, replies) is not an input.**
6. Every commercial post and reply carries a disclosure. Missing = veto.

## 7. Reliability Contract

- Publication is a state machine with a stable `operation_id`.
- A timeout after submit yields `UNKNOWN_OUTCOME`, reconciled by API query —
  **never** by blind republish.
- Atomic queue claim via `UPDATE … WHERE state='QUEUED'`.
- Leases expire so a crashed worker does not stall the queue.
- `429 / 5xx / 401 / 403 / timeout` never delete a post. Only an authoritative
  `404` tombstones one, and the row is kept.

## 8. Operator Control (Telegram)

Mutation commands require a numeric user ID on `TELEGRAM_OPERATOR_IDS`. Chat
membership is not authorization. Inline buttons use opaque, single-use,
15-minute tokens bound to the issuing operator.

## 9. Non-Goals

No web dashboard. No auto-replying to strangers. No fake reviews, testimonials,
or earnings claims. No automatic posting of unverified links. No rewrite of the
working TikTok/Clipper video pipelines.

## 10. Success Definition

A person reading 20 successive posts perceives one consistent character with
varied topics and useful ideas — not a templated news bot or an affiliate spam
feed. Monetisation is measured by actual affiliate attribution, never inferred
from engagement.
