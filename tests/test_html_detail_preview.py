import importlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault("feedparser", importlib.import_module("tests.feedparser_stub"))

from src.models.spider_rule import SpiderRuleOutput
from src.services.rule_generator import RuleGeneratorService


class HtmlDetailPreviewTests(unittest.TestCase):
    def make_service(self):
        return RuleGeneratorService(Mock())

    def make_rule(
        self,
        detail_strategy="detail_preferred",
        skip_detail=False,
        url="https://example.com/news",
        list_container="ul.news-list",
    ):
        return SpiderRuleOutput(
            rule_id="rule_html_preview_1",
            task_id="task_html_preview_1",
            task_name="HTML Detail Preview",
            url=url,
            source_type="html",
            list_container=list_container,
            item_selector="li",
            field_selectors={
                "title": "a::text",
                "url": "a::attr(href)",
            },
            detail_strategy=detail_strategy,
            skip_detail=skip_detail,
        )

    @patch("src.spiders.dynamic_spider.create_dynamic_spider_from_rule")
    def test_build_html_detail_preview_marks_warning_when_detail_body_missing(
        self,
        mock_create_spider,
    ):
        service = self.make_service()
        spider = Mock()
        spider.fetch_list.return_value = [
            {
                "title": "文章 A",
                "url": "https://example.com/a",
                "date": "2026-03-30",
                "body_text": "这是列表页摘要",
            }
        ]
        spider.fetch_detail.return_value = {
            "title": "文章 A",
            "url": "https://example.com/a",
            "body_text": "",
            "raw_markdown": "",
            "attachments": [],
            "images": [],
        }
        mock_create_spider.return_value = spider

        detail_samples, required, passed, message = service._build_html_detail_preview(
            self.make_rule(detail_strategy="detail_preferred"),
            max_items=2,
        )

        self.assertTrue(required)
        self.assertFalse(passed)
        self.assertIn("详情预览未通过", message)
        self.assertEqual(detail_samples[0]["status"], "warning")
        self.assertEqual(detail_samples[0]["body_source"], "list_fallback")

    @patch("src.spiders.dynamic_spider.create_dynamic_spider_from_rule")
    def test_build_html_detail_preview_skips_requirement_for_list_only(
        self,
        mock_create_spider,
    ):
        service = self.make_service()
        spider = Mock()
        spider.fetch_list.return_value = [
            {
                "title": "文章 B",
                "url": "https://example.com/b",
                "date": "2026-03-30",
                "body_text": "列表正文可直接使用",
            }
        ]
        mock_create_spider.return_value = spider

        detail_samples, required, passed, message = service._build_html_detail_preview(
            self.make_rule(detail_strategy="list_only", skip_detail=True),
            max_items=2,
        )

        self.assertFalse(required)
        self.assertTrue(passed)
        self.assertIn("无需详情预览", message)
        self.assertEqual(detail_samples[0]["status"], "passed")
        self.assertEqual(detail_samples[0]["body_source"], "list_only")

    @patch.object(RuleGeneratorService, "_extract_main_content_region")
    @patch.object(RuleGeneratorService, "_fetch_html_content")
    @patch.object(RuleGeneratorService, "_build_html_detail_preview")
    def test_build_rule_preview_bundle_includes_page_summary_and_test_snapshot(
        self,
        mock_detail_preview,
        mock_fetch_html,
        mock_extract_region,
    ):
        service = self.make_service()
        mock_fetch_html.return_value = """
        <html>
          <head>
            <title>示例新闻页</title>
            <meta name="description" content="页面摘要说明" />
          </head>
          <body>
            <main>
              <h1>头条标题</h1>
              <ul class="list-gl">
                <li><a href="/a">文章 A</a></li>
              </ul>
              <div class="wp_articlecontent">正文区域</div>
            </main>
          </body>
        </html>
        """
        mock_extract_region.return_value = {
            "text_content": "正文摘录",
            "detected_regions": [
                {
                    "tag": "main",
                    "id": "content",
                    "class": "news-list",
                    "link_count": 4,
                    "text_length": 120,
                }
            ],
        }
        mock_detail_preview.return_value = (
            [{"title": "文章 A", "status": "passed"}],
            True,
            True,
            "详情预览通过",
        )

        bundle = service.build_rule_preview_bundle(
            self.make_rule(
                url="https://design.sztu.edu.cn/xydt/tzgg.htm",
                list_container="ul.list-gl",
            ),
            max_items=1,
        )

        self.assertEqual(bundle["sample_data"][0]["title"], "文章 A")
        self.assertEqual(bundle["page_summary"]["title"], "示例新闻页")
        self.assertEqual(bundle["page_summary"]["heading_outline"][0]["text"], "头条标题")
        self.assertEqual(
            bundle["page_summary"]["site_profile"]["id"],
            "college_department",
        )
        self.assertEqual(
            bundle["page_summary"]["template_recommendations"][0]["id"],
            "sztu_standard_ul_list",
        )
        self.assertEqual(bundle["test_snapshot"]["sample_count"], 1)
        self.assertTrue(bundle["test_snapshot"]["detail_preview_passed"])


if __name__ == "__main__":
    unittest.main()
