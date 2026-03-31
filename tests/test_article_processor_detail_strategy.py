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


if __name__ == "__main__":
    unittest.main()
