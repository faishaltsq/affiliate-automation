"""tests/test_affiliate_threshold.py — Test affiliate post qualification by views and engagement.

Rewritten for Gate 3: AND logic (organic replies >= 5 AND age >= 45m AND
views >= 250 OR likes >= 20). The old cases encoded the retired OR logic where
views alone earned a link.
"""

from datetime import datetime, timezone, timedelta

from unittest.mock import patch, MagicMock
import pytest

from src.affiliate.comment_engine import (
    check_and_comment_our_posts,
    get_post_metrics,
    post_qualifies,
    organic_replies,
    pick_affiliate_reply_target,
    ORGANIC_REPLIES_MIN,
)

OWN = "pochita9887"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """The engine sleeps 10s between replies; tests must not inherit that."""
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _replies(count: int, prefix: str = "org", minutes_ago: float = 30.0) -> list:
    """`count` replies from strangers, oldest first."""
    return [
        {
            "id": f"{prefix}_{i}",
            "text": f"diskusi menarik nomor {i}",
            "timestamp": _iso(minutes_ago + i),
            "username": f"stranger{i}",
        }
        for i in range(count)
    ]


def _with_replies(reply_map):
    """Patch get_post_replies so each post id returns its own reply list."""
    return patch(
        "src.affiliate.comment_engine.get_post_replies",
        side_effect=lambda pid: reply_map.get(pid, []),
    )


def test_get_post_metrics_includes_views():
    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.json.return_value = {
        "data": [
            {"name": "views", "values": [{"value": 150}]},
            {"name": "likes", "values": [{"value": 5}]},
            {"name": "replies", "values": [{"value": 2}]},
        ]
    }

    with patch("requests.get", return_value=fake_response):
        metrics = get_post_metrics("12345")
        assert metrics["views"] == 150
        assert metrics["likes"] == 5
        assert metrics["replies"] == 2


def test_qualifying_by_views_threshold():
    """Real reach plus a live discussion passes; reach alone no longer does."""
    sample_posts = [
        {
            "id": "post_1",
            "text": "Tips teknologi AI terbaru untuk produktivitas",
            "timestamp": _iso(90),
        },
        {
            "id": "post_2",
            "text": "Update pasar saham dan IHSG hari ini",
            "timestamp": _iso(90),
        },
    ]

    def mock_metrics(pid):
        if pid == "post_1":
            return {"views": 260, "likes": 2, "replies": 10}
        return {"views": 40, "likes": 0, "replies": 0}

    mock_product = {
        "name": "Keyboard Mechanical RGB",
        "url": "https://id.shp.ee/sample123",
        "price": "Rp 250.000",
        "category": "Gadget & Tech",
    }

    with patch("src.affiliate.comment_engine.extract_product_metadata", return_value=mock_product), \
         patch("src.affiliate.comment_engine.get_our_posts", return_value=sample_posts), \
         patch("src.affiliate.comment_engine.get_commented_post_ids", return_value=set()), \
         patch("src.affiliate.comment_engine.get_post_metrics", side_effect=mock_metrics), \
         _with_replies({"post_1": _replies(10), "post_2": []}), \
         patch("src.affiliate.comment_engine.generate_affiliate_comment", return_value="Spill keyboard yang enak buat ngetik ini: https://id.shp.ee/sample123"), \
         patch("src.affiliate.comment_engine.post_reply_to_thread", return_value="reply_999"), \
         patch("src.affiliate.comment_engine.record_affiliate_comment"):

        results = check_and_comment_our_posts(
            affiliate_url="https://id.shp.ee/sample123",
            views_threshold=250,
            likes_threshold=20,
            replies_threshold=10,
        )

        assert len(results) == 1
        res = results[0]
        assert res["status"] == "commented"
        assert res["post_id"] == "post_1"
        assert res["views"] == 260
        assert res["reply_id"] == "reply_999"


def test_below_views_threshold_saved_to_queue():
    sample_posts = [
        {
            "id": "post_low",
            "text": "Berita singkat cuaca cerah berawan",
            "timestamp": _iso(90),
        },
    ]

    with patch("src.affiliate.comment_engine.extract_product_metadata", return_value={"name": "Barang"}), \
         patch("src.affiliate.comment_engine.get_our_posts", return_value=sample_posts), \
         patch("src.affiliate.comment_engine.get_post_metrics", return_value={"views": 85, "likes": 3, "replies": 1}), \
         _with_replies({"post_low": _replies(6)}):

        results = check_and_comment_our_posts(
            affiliate_url="https://id.shp.ee/sample123",
            views_threshold=100,
            likes_threshold=20,
            replies_threshold=10,
        )

        assert len(results) == 1
        assert results[0]["status"] == "below_threshold"


# ── Gate 3 legs, in isolation ─────────────────────────────────────────────────

def test_gate_blocks_high_reach_with_no_discussion():
    """3000 views, zero organic replies → HOLD. Reach is not permission."""
    ok, reason = post_qualifies(
        views=3000, likes=200, organic_reply_count=0, age_minutes=120.0
    )
    assert ok is False
    assert "organic replies" in reason


def test_gate_blocks_young_post_even_with_discussion():
    """Fresh post with a buzzing comment section still waits out its window."""
    ok, reason = post_qualifies(
        views=900, likes=90, organic_reply_count=20, age_minutes=10.0
    )
    assert ok is False
    assert "age" in reason


def test_gate_blocks_no_reach_despite_discussion():
    ok, reason = post_qualifies(
        views=60, likes=3, organic_reply_count=12, age_minutes=200.0
    )
    assert ok is False
    assert "views" in reason


def test_gate_passes_on_likes_when_views_short():
    """A small but dense thread earns its link on the likes leg."""
    ok, reason = post_qualifies(
        views=120, likes=25, organic_reply_count=8, age_minutes=60.0
    )
    assert ok is True
    assert reason == "ok"


def test_gate_skips_age_leg_when_timestamp_missing():
    """An unparseable timestamp must not hold the post forever."""
    ok, _ = post_qualifies(views=400, likes=10, organic_reply_count=7, age_minutes=None)
    assert ok is True


def test_gate_defaults_match_contract():
    assert ORGANIC_REPLIES_MIN == 5


def test_gate_boundary_is_inclusive():
    """Exactly at every threshold → passes; one under any → fails."""
    assert post_qualifies(views=250, likes=20, organic_reply_count=5, age_minutes=45.0)[0]
    assert not post_qualifies(views=250, likes=20, organic_reply_count=4, age_minutes=45.0)[0]
    assert not post_qualifies(views=250, likes=20, organic_reply_count=5, age_minutes=44.9)[0]


# ── Own replies never count ──────────────────────────────────────────────────

def test_organic_replies_drops_own_chain_parts():
    """A 4-part chain plus the bonus self-reply is 5 replies of self-talk."""
    own_chain = [
        {
            "id": f"chain_{i}",
            "text": f"bagian {i}",
            "timestamp": _iso(5 * i),
            "username": OWN,
        }
        for i in range(5)
    ]
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=own_chain):
        assert organic_replies("post_1") == []


def test_gate_does_not_open_on_self_talk():
    """Self-reply inflation must never buy a link placement."""
    post = [{"id": "post_self", "text": "Rilis flagship baru bawa AI on-device", "timestamp": _iso(120)}]
    own_chain = [
        {"id": f"c_{i}", "text": f"bagian {i}", "timestamp": _iso(6 * i), "username": OWN}
        for i in range(9)
    ]
    with patch("src.affiliate.comment_engine.extract_product_metadata", return_value={"name": "Gadget"}), \
         patch("src.affiliate.comment_engine.get_our_posts", return_value=post), \
         patch("src.affiliate.comment_engine.get_post_metrics",
               return_value={"views": 800, "likes": 40, "replies": 9}), \
         _with_replies({"post_self": own_chain}), \
         patch("src.affiliate.comment_engine.post_reply_to_thread") as mock_reply:
        results = check_and_comment_our_posts(affiliate_url="https://id.shp.ee/x")

    assert results[0]["status"] == "below_threshold"
    mock_reply.assert_not_called()


def test_mixed_case_username_also_filtered():
    replies = [{"id": "r1", "text": "halo", "timestamp": _iso(5), "username": "Pochita9887"}]
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=replies):
        assert organic_replies("post_1") == []


def test_chain_parts_do_not_double_count_as_discussion():
    """4 chain parts + 1 bonus self-reply + 1 real reply = 1 organic."""
    replies = (
        [
            {"id": f"self_{i}", "text": f"bagian {i}", "timestamp": _iso(6 * i), "username": OWN}
            for i in range(5)
        ]
        + [{"id": "real", "text": "setuju", "timestamp": _iso(25), "username": "fan"}]
    )
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=replies):
        assert len(organic_replies("post_1")) == 1


# ── Where the link lands ─────────────────────────────────────────────────────

def test_affiliate_answers_buying_intent_comment_not_root():
    """A comment asking where to buy outranks every other reply."""
    replies = [
        {"id": "r_first", "text": "keren banget", "timestamp": _iso(40), "username": "a"},
        {"id": "r_ask", "text": "ada yang tau beli di mana?", "timestamp": _iso(12), "username": "b"},
        {"id": "r_late", "text": "setuju sih", "timestamp": _iso(3), "username": "c"},
    ]
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=replies):
        assert pick_affiliate_reply_target("post_1") == "r_ask"


def test_affiliate_falls_back_to_oldest_comment_without_intent():
    replies = [
        {"id": "r_new", "text": "wah menarik", "timestamp": _iso(2), "username": "a"},
        {"id": "r_old", "text": "nah ini penting", "timestamp": _iso(30), "username": "b"},
    ]
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=replies):
        assert pick_affiliate_reply_target("post_1") == "r_old"


def test_affiliate_falls_back_to_root_when_no_comments():
    with patch("src.affiliate.comment_engine.get_post_replies", return_value=[]):
        assert pick_affiliate_reply_target("post_root") == "post_root"


def test_nested_reply_target_is_used_not_root():
    """The gate must hand the nested target to the reply API, never the root."""
    post = [{"id": "post_1", "text": "AI gadget murah mulai menarik", "timestamp": _iso(90)}]
    replies = [
        {"id": "r_plain", "text": "ok", "timestamp": _iso(30), "username": "a"},
        {"id": "r_intent", "text": "worth gak ya? butuh buat kerja", "timestamp": _iso(20), "username": "b"},
    ] + _replies(5)

    with patch("src.affiliate.comment_engine.extract_product_metadata", return_value={"name": "Gadget"}), \
         patch("src.affiliate.comment_engine.get_our_posts", return_value=post), \
         patch("src.affiliate.comment_engine.get_post_metrics",
               return_value={"views": 500, "likes": 12, "replies": 7}), \
         _with_replies({"post_1": replies}), \
         patch("src.affiliate.comment_engine.generate_affiliate_comment",
               return_value="ini jawabannya: https://id.shp.ee/x"), \
         patch("src.affiliate.comment_engine.post_reply_to_thread", return_value="reply_x") as mock_reply, \
         patch("src.affiliate.comment_engine.record_affiliate_comment"):
        results = check_and_comment_our_posts(affiliate_url="https://id.shp.ee/x")

    assert results[0]["status"] == "commented"
    mock_reply.assert_called_once_with("r_intent", "ini jawabannya: https://id.shp.ee/x")


@patch("src.affiliate.comment_engine.post_reply_to_thread")
@patch("src.affiliate.comment_engine.get_post_metrics")
@patch("src.affiliate.comment_engine.get_post_replies", return_value=[])
@patch("src.affiliate.comment_engine.get_commented_post_ids", return_value=set())
@patch("src.affiliate.comment_engine.get_our_posts")
def test_bencana_post_is_skipped_before_the_gate(
    mock_posts, mock_commented, mock_replies, mock_metrics, mock_reply
):
    """A disaster post is dropped before any metrics or replies call."""
    mock_posts.return_value = [
        {"id": "post_banjir",
         "text": "Banjir rob rob Jakarta utara merendam ribuan rumah warga",
         "timestamp": _iso(600)},
        {"id": "post_gempa",
         "text": "Gempa Gempa M 6.1 guncang Sulawesi, korban dilaporkan berjatuhan",
         "timestamp": _iso(600)},
    ]

    results = check_and_comment_our_posts(affiliate_url="https://id.shp.ee/x")

    mock_metrics.assert_not_called()
    mock_replies.assert_not_called()
    mock_reply.assert_not_called()
    assert results == []


# ── Posts that never get a link ───────────────────────────────────────────────

def test_bencana_post_never_gets_affiliate():
    post = [{"id": "post_b",
             "text": "Banjir bandang menerjang, korban dilaporkan meninggal",
             "timestamp": _iso(90)}]
    with patch("src.affiliate.comment_engine.extract_product_metadata", return_value={"name": "Gadget"}), \
         patch("src.affiliate.comment_engine.get_our_posts", return_value=post), \
         patch("src.affiliate.comment_engine.get_post_metrics",
               return_value={"views": 5000, "likes": 300, "replies": 90}), \
         _with_replies({"post_b": _replies(90)}), \
         patch("src.affiliate.comment_engine.post_reply_to_thread") as mock_reply:
        results = check_and_comment_our_posts(affiliate_url="https://id.shp.ee/x")

    mock_reply.assert_not_called()
    assert all(r["status"] != "commented" for r in results)


    @patch("src.affiliate.comment_engine.post_reply_to_thread")
    @patch("src.affiliate.comment_engine.get_post_metrics")
    @patch("src.affiliate.comment_engine.get_post_replies", return_value=[])
    @patch("src.affiliate.comment_engine.get_commented_post_ids", return_value=set())
    @patch("src.affiliate.comment_engine.get_our_posts")
    def test_bencana_post_is_skipped_before_the_gate(
        self, mock_posts, mock_commented, mock_replies, mock_metrics, mock_reply
    ):
        """A disaster post never even gets measured, and never gets a link."""
        mock_posts.return_value = [
            {"id": "post_banjir",
             "text": "Banjir rob rob Jakarta utara merendam ribuan rumah warga",
             "timestamp": _iso(600)},
            {"id": "post_gempa",
             "text": "Gempa Gempa M 6.1 guncang Sulawesi, korban berjatuhan",
             "timestamp": _iso(600)},
        ]

        results = check_and_comment_our_posts(affiliate_url=URL)

        mock_metrics.assert_not_called()
        mock_replies.assert_not_called()
        mock_reply.assert_not_called()
        self.assertEqual(results, [])
