import unittest
from unittest.mock import patch, MagicMock
from src.bot.threads_agent import (
    _create_affiliate_draft_preview,
    _handle_publish_affiliate_draft,
    _handle_cancel_affiliate_draft,
    _load_drafts,
    _save_drafts,
)


class TestThreadsAffiliatePreview(unittest.TestCase):
    def setUp(self):
        # Reset drafts
        _save_drafts({})

    def tearDown(self):
        _save_drafts({})

    @patch("src.bot.threads_agent._send_reply")
    @patch("src.affiliate.comment_engine.generate_affiliate_comment")
    @patch("src.scraper.product_extractor.extract_product_metadata")
    @patch("src.affiliate.comment_engine.get_commented_post_ids", return_value=set())
    @patch("src.affiliate.comment_engine.get_post_replies")
    @patch("src.affiliate.comment_engine.get_post_metrics")
    @patch("src.affiliate.comment_engine.get_our_posts")
    def test_create_affiliate_draft_preview_success(
        self, mock_posts, mock_metrics, mock_replies, mock_commented, mock_extract, mock_gen_comment, mock_send
    ):
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

        mock_posts.return_value = [
            {"id": "post_100", "text": "Diskusi tren teknologi AI dan dampaknya bagi industri.", "timestamp": old},
            {"id": "post_50", "text": "Bahas sepak bola Liga Inggris semalam.", "timestamp": old},
        ]
        # post_100 clears every Gate 3 leg: 5 organic replies, 2h old, 300 views.
        # post_50 clears none.
        mock_metrics.side_effect = lambda pid: (
            {"views": 300, "likes": 5, "replies": 6} if pid == "post_100"
            else {"views": 40, "likes": 2, "replies": 1}
        )
        mock_replies.side_effect = lambda pid: (
            [{"id": f"r{i}", "text": "menarik", "timestamp": old, "username": f"user{i}"} for i in range(6)]
            if pid == "post_100" else []
        )
        mock_extract.return_value = {
            "name": "HELENA Top Longsleeve Knit Premium",
            "price": "Rp 89.000",
            "image_url": "https://img.com/pic.jpg",
            "affiliate_url": "https://vt.tokopedia.com/t/sample/",
        }
        mock_gen_comment.return_value = "Nih ada promo baju longsleeve knit adem: https://vt.tokopedia.com/t/sample/"

        _create_affiliate_draft_preview(
            aff_url="https://vt.tokopedia.com/t/sample/",
            target_thread_url=None,
            msg_id=123,
            thread_id=9,
        )

        drafts = _load_drafts()
        self.assertEqual(len(drafts), 1)
        draft = list(drafts.values())[0]
        self.assertEqual(draft["type"], "affiliate")
        self.assertEqual(draft["target_post_id"], "post_100")
        self.assertEqual(draft["target_views"], 300)
        self.assertEqual(draft["product_name"], "HELENA Top Longsleeve Knit Premium")
        self.assertIn("Nih ada promo", draft["comment"])

        # Check that confirmation buttons were sent
        calls = mock_send.call_args_list
        preview_call = calls[-1]
        reply_markup = preview_call.kwargs.get("reply_markup")
        self.assertIsNotNone(reply_markup)
        btn_texts = [b["text"] for row in reply_markup["inline_keyboard"] for b in row]
        self.assertIn("🚀 Pasang Komentar", btn_texts)
        self.assertIn("❌ Batalkan", btn_texts)

    @patch("src.bot.threads_agent._send_reply")
    @patch("src.affiliate.comment_engine.generate_affiliate_comment")
    @patch("src.affiliate.comment_engine.get_commented_post_ids", return_value=set())
    @patch("src.affiliate.comment_engine.get_post_replies")
    @patch("src.affiliate.comment_engine.get_post_metrics")
    @patch("src.affiliate.comment_engine.get_our_posts")
    @patch("src.affiliate.queue_manager.enqueue", return_value=77)
    def test_preview_holds_when_discussion_is_dead(
        self, mock_enqueue, mock_posts, mock_metrics, mock_replies, mock_commented,
        mock_gen_comment, mock_send
    ):
        """300 views but no replies → Gate 3 holds it and the link goes to queue."""
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        mock_posts.return_value = [
            {"id": "post_ghost", "text": "Rilis chip AI generasi baru", "timestamp": old},
        ]
        mock_metrics.return_value = {"views": 3000, "likes": 100, "replies": 0}
        mock_replies.return_value = []

        _create_affiliate_draft_preview(
            aff_url="https://vt.tokopedia.com/t/sample/",
            target_thread_url=None,
            msg_id=123,
            thread_id=9,
        )

        self.assertEqual(_load_drafts(), {})
        mock_enqueue.assert_called_once_with("https://vt.tokopedia.com/t/sample/")
        mock_gen_comment.assert_not_called()
        sent = mock_send.call_args_list[-1].args[0]
        self.assertIn("Belum ada postingan yang lolos Gate 3", sent)

    @patch("src.bot.threads_agent._send_reply")
    @patch("src.affiliate.comment_engine.record_affiliate_comment")
    @patch("src.affiliate.comment_engine.post_reply_to_thread")
    def test_publish_affiliate_draft(self, mock_reply, mock_record, mock_send):
        draft_id = "aff_test_1"
        drafts = {
            draft_id: {
                "id": draft_id,
                "type": "affiliate",
                "target_post_id": "post_100",
                "product_name": "HELENA Top",
                "comment": "Nih ada promo: https://url",
                "affiliate_url": "https://url",
            }
        }
        _save_drafts(drafts)
        mock_reply.return_value = "reply_999"

        _handle_publish_affiliate_draft(draft_id, msg_id=123, thread_id=9)

        mock_reply.assert_called_once_with("post_100", "Nih ada promo: https://url")
        mock_record.assert_called_once_with(
            post_id="post_100",
            reply_id="reply_999",
            affiliate_url="https://url",
            product_name="HELENA Top",
        )
        self.assertEqual(_load_drafts(), {})

    @patch("src.bot.threads_agent._send_reply")
    @patch("src.affiliate.comment_engine.generate_affiliate_comment")
    @patch("src.affiliate.comment_engine.get_post_metrics")
    @patch("requests.get")
    def test_reroll_affiliate_draft(self, mock_get, mock_metrics, mock_gen_comment, mock_send):
        from src.bot.threads_agent import _handle_reroll_affiliate_draft

        draft_id = "aff_test_reroll"
        drafts = {
            draft_id: {
                "id": draft_id,
                "type": "affiliate",
                "target_post_id": "post_100",
                "target_views": 300,
                "candidate_post_ids": ["post_100", "post_200", "post_300"],
                "candidate_index": 0,
                "product_name": "Knit Top",
                "product_price": "Rp 50.000",
                "product_image": "",
                "affiliate_url": "https://vt.tokopedia.com/t/test/",
                "comment": "Old comment",
            }
        }
        _save_drafts(drafts)

        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"id": "post_200", "text": "Post tentang saham"}
        mock_metrics.return_value = {"views": 250, "likes": 10, "replies": 3}
        mock_gen_comment.return_value = "New rerolled comment"

        _handle_reroll_affiliate_draft(draft_id, msg_id=123, thread_id=9)

        saved = _load_drafts()[draft_id]
        self.assertEqual(saved["target_post_id"], "post_200")
        self.assertEqual(saved["candidate_index"], 1)
        self.assertEqual(saved["comment"], "New rerolled comment")

    def test_affiliate_patterns_coverage(self):
        from src.bot.threads_agent import AFFILIATE_PATTERNS
        import re

        test_urls = [
            "https://vt.tokopedia.com/t/ZS9U26G1xknoL-1N3nI/",
            "https://tokopedia.link/abcd123",
            "https://www.tokopedia.com/shop/product/123",
            "https://vt.tiktok.com/ZS9U2xxxx/",
            "https://www.tiktok.com/@user/video/123",
            "https://shopee.co.id/product/123/456",
            "https://id.shp.ee/abcde",
            "https://s.shopee.co.id/xyz",
            "https://shope.ee/test",
            "https://s.lazada.co.id/s.123",
            "https://www.blibli.com/p/item/123",
        ]

        for url in test_urls:
            matched = any(re.findall(pat, url) for pat in AFFILIATE_PATTERNS)
            self.assertTrue(matched, f"URL should match affiliate patterns: {url}")

    @patch("src.bot.threads_agent._create_affiliate_draft_preview")
    def test_cmd_affiliate_direct_thread(self, mock_create):
        from src.bot.threads_agent import _cmd_affiliate
        # Test affiliate with direct numeric thread ID
        _cmd_affiliate("https://s.shopee.co.id/xyz 18131653009761783", msg_id=1, thread_id=9)
        mock_create.assert_called_once_with(
            aff_url="https://s.shopee.co.id/xyz",
            target_thread_url="18131653009761783",
            msg_id=1,
            thread_id=9,
        )
