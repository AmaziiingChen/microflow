import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.modules.setdefault("feedparser", importlib.import_module("tests.feedparser_stub"))
sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))

from src.core.article_processor import ArticleContext, ArticleProcessor


class ArticleProcessorDetailStrategyTests(unittest.TestCase):
    def setUp(self):
        self.processor = ArticleProcessor(Mock(), Mock())

    def tearDown(self):
        self.processor.shutdown(wait=False)

    def test_list_only_strategy_uses_list_payload_without_fetching_detail(self):
        spider = SimpleNamespace(
            detail_strategy="list_only",
            skip_detail=True,
            field_selectors={"summary": ".summary"},
            fetch_detail=Mock(side_effect=AssertionError("should not fetch detail")),
        )
        ctx = ArticleContext(
            url="https://example.com/a",
            title="列表页文章",
            date="2026-03-30",
            source_name="HTML 源",
            raw_text="这是列表页正文",
            source_type="html",
        )

        detail, error = self.processor._resolve_detail_payload(
            spider,
            ctx,
            source_type="html",
            detail_strategy="list_only",
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["body_text"], "这是列表页正文")
        self.assertEqual(detail["raw_markdown"], "这是列表页正文")
        spider.fetch_detail.assert_not_called()

    def test_detail_preferred_strategy_keeps_detail_body_even_when_list_has_text(self):
        spider = SimpleNamespace(
            detail_strategy="detail_preferred",
            skip_detail=False,
            field_selectors={},
            fetch_detail=Mock(
                return_value={
                    "title": "详情页文章",
                    "url": "https://example.com/a",
                    "body_text": "这是详情页正文",
                    "body_html": "<p>这是详情页正文</p>",
                    "attachments": [],
                    "images": [],
                    "content_blocks": [],
                    "image_assets": [],
                    "exact_time": "2026-03-30 09:00",
                }
            ),
        )
        ctx = ArticleContext(
            url="https://example.com/a",
            title="详情页文章",
            date="2026-03-30",
            source_name="HTML 源",
            raw_text="这是列表页摘要",
            source_type="html",
        )

        detail, error = self.processor._resolve_detail_payload(
            spider,
            ctx,
            source_type="html",
            detail_strategy="detail_preferred",
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["body_text"], "这是详情页正文")
        self.assertEqual(detail["exact_time"], "2026-03-30 09:00")
        spider.fetch_detail.assert_called_once_with("https://example.com/a")

    def test_hybrid_strategy_merges_list_fallback_into_detail(self):
        spider = SimpleNamespace(
            detail_strategy="hybrid",
            skip_detail=False,
            field_selectors={"summary": ".summary"},
            fetch_detail=Mock(
                return_value={
                    "title": "混合文章",
                    "url": "https://example.com/a",
                    "body_text": "",
                    "body_html": "",
                    "attachments": [
                        {"name": "详情附件", "url": "https://example.com/detail.pdf"}
                    ],
                    "images": [],
                    "content_blocks": [],
                    "image_assets": [],
                    "exact_time": "",
                }
            ),
        )
        ctx = ArticleContext(
            url="https://example.com/a",
            title="混合文章",
            date="2026-03-30",
            source_name="HTML 源",
            raw_text="这是列表页补充正文",
            attachments=[
                {"name": "列表附件", "url": "https://example.com/list.pdf"}
            ],
            source_type="html",
        )

        detail, error = self.processor._resolve_detail_payload(
            spider,
            ctx,
            source_type="html",
            detail_strategy="hybrid",
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["body_text"], "这是列表页补充正文")
        self.assertEqual(detail["exact_time"], "2026-03-30")
        self.assertEqual(len(detail["attachments"]), 2)
        self.assertEqual(
            [item["url"] for item in detail["attachments"]],
            ["https://example.com/detail.pdf", "https://example.com/list.pdf"],
        )

    def test_list_only_strategy_keeps_structured_list_payload(self):
        spider = SimpleNamespace(
            detail_strategy="list_only",
            skip_detail=True,
            field_selectors={"summary": ".summary"},
            fetch_detail=Mock(side_effect=AssertionError("should not fetch detail")),
        )
        ctx = ArticleContext(
            url="https://example.com/structured",
            title="结构化列表文章",
            date="2026-03-30",
            source_name="HTML 源",
            raw_text="结构化正文",
            body_html="<div><h3>会议安排</h3><p>请按时参加。</p></div>",
            raw_markdown="### 会议安排\n\n请按时参加。",
            attachments=[
                {"name": "会议纪要", "url": "https://example.com/agenda.pdf"}
            ],
            content_blocks=[{"type": "heading", "level": 3, "text": "会议安排"}],
            image_assets=[
                {
                    "url": "https://example.com/cover.jpg",
                    "category": "body",
                    "caption": "封面图",
                }
            ],
            images=["https://example.com/cover.jpg"],
            source_type="html",
        )

        detail, error = self.processor._resolve_detail_payload(
            spider,
            ctx,
            source_type="html",
            detail_strategy="list_only",
        )

        self.assertEqual(error, "")
        self.assertIsNotNone(detail)
        self.assertEqual(detail["body_html"], "<div><h3>会议安排</h3><p>请按时参加。</p></div>")
        self.assertEqual(detail["raw_markdown"], "### 会议安排\n\n请按时参加。")
        self.assertEqual(detail["images"], ["https://example.com/cover.jpg"])
        self.assertEqual(detail["attachments"][0]["url"], "https://example.com/agenda.pdf")


class ArticleProcessorAiFallbackTests(unittest.TestCase):
    def setUp(self):
        self.llm = Mock()
        self.db = Mock()
        self.db.check_if_new_or_updated.return_value = (True, "new")
        self.db.insert_or_update_article_sync = Mock(return_value=True)
        self.db.get_article_by_url = Mock(
            return_value={"id": 321, "is_favorite": 0}
        )
        self.config_service = Mock()
        self.config_service.get.return_value = "TestModel"
        self.processor = ArticleProcessor(
            self.llm,
            self.db,
            config_service=self.config_service,
        )

    def tearDown(self):
        self.processor.shutdown(wait=False)

    def test_ai_failure_falls_back_to_original_content_and_still_inserts(self):
        spider = SimpleNamespace(
            detail_strategy="detail_preferred",
            skip_detail=False,
            field_selectors={},
            fetch_detail=Mock(
                return_value={
                    "title": "音乐学院文章",
                    "url": "https://example.com/music",
                    "body_text": "这是足够长的原文正文内容，用来验证回退入库逻辑。",
                    "body_html": "<p>这是足够长的原文正文内容，用来验证回退入库逻辑。</p>",
                    "raw_markdown": "### 正文\n\n这是足够长的原文正文内容，用来验证回退入库逻辑。",
                    "attachments": [],
                    "images": [],
                    "content_blocks": [],
                    "image_assets": [],
                    "exact_time": "2026-03-30 09:00",
                }
            ),
        )
        self.llm.summarize_article.return_value = "⚠️ 请求过于频繁"

        ctx = ArticleContext(
            url="https://example.com/music",
            title="音乐学院文章",
            date="2026-03-30",
            source_name="音乐学院",
            source_type="html",
        )

        success, reason, article_data = self.processor.process(
            spider=spider,
            ctx=ctx,
            mode="continuous",
            today_str="2026-03-30",
            is_manual=False,
        )

        self.assertTrue(success)
        self.assertEqual(reason, "ai_failed_fallback")
        self.db.insert_or_update_article_sync.assert_called_once()
        self.assertEqual(
            self.db.insert_or_update_article_sync.call_args.kwargs["summary"],
            "### 正文\n\n这是足够长的原文正文内容，用来验证回退入库逻辑。",
        )
        self.assertIsNotNone(article_data)
        self.assertEqual(article_data["id"], 321)
        self.assertTrue(article_data["ai_summary_failed"])
        self.assertEqual(article_data["ai_failure_reason"], "ai_failed")
        self.assertEqual(article_data["ai_failure_message"], "请求过于频繁")

    def test_image_rich_article_uses_structured_markdown_text_for_ai_summary(self):
        spider = SimpleNamespace(
            detail_strategy="detail_preferred",
            skip_detail=False,
            field_selectors={},
            fetch_detail=Mock(
                return_value={
                    "title": "图文文章",
                    "url": "https://example.com/rich-media",
                    "body_text": "",
                    "body_html": '<div><img src="https://example.com/a.jpg" /><p>近日，音乐学院收到感谢信。</p></div>',
                    "raw_markdown": "![图1](https://example.com/a.jpg)\n\n### 正文\n\n近日，音乐学院收到感谢信，并完成了闭幕式演出任务。",
                    "attachments": [],
                    "images": ["https://example.com/a.jpg"],
                    "content_blocks": [],
                    "image_assets": [
                        {"url": "https://example.com/a.jpg", "category": "body"}
                    ],
                    "exact_time": "2026-03-30 09:00",
                }
            ),
        )
        self.llm.summarize_article.return_value = "【通知】【音乐学院】\n\n### 核心内容\n- 成功完成演出任务。"

        ctx = ArticleContext(
            url="https://example.com/rich-media",
            title="图文文章",
            date="2026-03-30",
            source_name="音乐学院",
            source_type="html",
        )

        success, reason, article_data = self.processor.process(
            spider=spider,
            ctx=ctx,
            mode="continuous",
            today_str="2026-03-30",
            is_manual=False,
        )

        self.assertTrue(success)
        self.assertEqual(reason, "new")
        self.llm.summarize_article.assert_called_once()
        llm_raw_text = self.llm.summarize_article.call_args.args[1]
        self.assertIn("近日，音乐学院收到感谢信", llm_raw_text)
        self.assertNotIn("![图1]", llm_raw_text)
        self.assertEqual(
            self.db.insert_or_update_article_sync.call_args.kwargs["summary"],
            "【通知】【音乐学院】\n\n### 核心内容\n- 成功完成演出任务。",
        )
        self.assertEqual(
            self.db.insert_or_update_article_sync.call_args.kwargs["raw_content"],
            llm_raw_text,
        )
        self.assertIsNotNone(article_data)
        self.assertEqual(article_data["id"], 321)
        self.assertFalse(article_data["ai_summary_failed"])


if __name__ == "__main__":
    unittest.main()
