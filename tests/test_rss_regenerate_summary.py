import sys
import threading
import types
import unittest
from unittest.mock import ANY, Mock, patch

from tests import feedparser_stub

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault(
    "feedparser", types.SimpleNamespace(parse=feedparser_stub.parse)
)

from src import api as api_module


class RssRegenerateSummaryTests(unittest.TestCase):
    def make_api(self, ai_config):
        api = api_module.Api.__new__(api_module.Api)
        api._llm = Mock()
        api._summary_lock = threading.Lock()
        api._active_summary_tokens = {}
        api._active_summary_events = {}
        api._resolve_article_ai_config = Mock(return_value=ai_config)
        api.validate_ai_prerequisites = Mock(return_value={"status": "success"})
        return api

    def test_rss_formatting_only_does_not_generate_summary(self):
        article = {
            "id": 1,
            "title": "RSS Article",
            "raw_text": "Raw body content for rss article.",
            "source_type": "rss",
            "enhanced_markdown": "",
        }
        api = self.make_api(
            {
                "source_type": "rss",
                "enable_ai_formatting": True,
                "enable_ai_summary": False,
                "formatting_prompt": "format prompt",
                "summary_prompt": "summary prompt",
            }
        )
        api._llm.format_rss_article.return_value = "## Formatted body"
        api._llm.summarize_rss_article.side_effect = AssertionError(
            "formatting-only path should not call summarize_rss_article"
        )

        fake_db = Mock()
        fake_db.get_article_by_id.return_value = article
        fake_db.update_rss_ai_content.return_value = True

        with patch.object(api_module, "db", fake_db):
            result = api.regenerate_summary(article["id"], skip_ai_precheck=False)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_kind"], "rss_formatting")
        self.assertEqual(result["ai_summary"], "")
        self.assertEqual(result["ai_tags"], [])
        self.assertEqual(result["enhanced_markdown"], "## Formatted body")
        api._llm.format_rss_article.assert_called_once_with(
            article["title"],
            article["raw_text"],
            "format prompt",
            priority="manual",
            cancel_event=ANY,
            use_cache=False,
        )
        api.validate_ai_prerequisites.assert_called_once()
        fake_db.update_rss_ai_content.assert_called_once_with(
            article["id"],
            "## Formatted body",
            "",
            [],
        )

    def test_rss_summary_only_resets_enhanced_markdown_to_raw(self):
        article = {
            "id": 2,
            "title": "Summary RSS",
            "raw_text": "Original raw markdown body.",
            "source_type": "rss",
            "enhanced_markdown": "old enhanced body",
        }
        api = self.make_api(
            {
                "source_type": "rss",
                "enable_ai_formatting": False,
                "enable_ai_summary": True,
                "formatting_prompt": "",
                "summary_prompt": "summary prompt",
            }
        )
        api._llm.summarize_rss_article.return_value = {
            "status": "success",
            "summary": "Summary body",
            "tags": ["重点"],
        }

        fake_db = Mock()
        fake_db.get_article_by_id.return_value = article
        fake_db.update_rss_ai_content.return_value = True

        with patch.object(api_module, "db", fake_db):
            result = api.regenerate_summary(article["id"], skip_ai_precheck=True)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_kind"], "rss_summary")
        self.assertEqual(result["enhanced_markdown"], article["raw_text"])
        self.assertEqual(result["ai_summary"], "Summary body")
        self.assertEqual(result["ai_tags"], ["重点"])
        api._llm.summarize_rss_article.assert_called_once_with(
            article["title"],
            article["raw_text"],
            "summary prompt",
            priority="manual",
            cancel_event=ANY,
            use_cache=False,
        )
        fake_db.update_rss_ai_content.assert_called_once_with(
            article["id"],
            article["raw_text"],
            "Summary body",
            ["重点"],
        )

    def test_rss_reset_clears_ai_outputs_without_calling_llm(self):
        article = {
            "id": 3,
            "title": "Reset RSS",
            "raw_text": "Reset me to raw markdown.",
            "source_type": "rss",
            "enhanced_markdown": "legacy enhanced",
            "ai_summary": "legacy summary",
            "ai_tags": '["旧标签"]',
        }
        api = self.make_api(
            {
                "source_type": "rss",
                "enable_ai_formatting": False,
                "enable_ai_summary": False,
                "formatting_prompt": "",
                "summary_prompt": "",
            }
        )
        api._llm.format_rss_article.side_effect = AssertionError(
            "reset path should not call format_rss_article"
        )
        api._llm.summarize_rss_article.side_effect = AssertionError(
            "reset path should not call summarize_rss_article"
        )

        fake_db = Mock()
        fake_db.get_article_by_id.return_value = article
        fake_db.update_rss_ai_content.return_value = True

        with patch.object(api_module, "db", fake_db):
            result = api.regenerate_summary(article["id"], skip_ai_precheck=False)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result_kind"], "rss_reset")
        self.assertEqual(result["enhanced_markdown"], article["raw_text"])
        self.assertEqual(result["ai_summary"], "")
        self.assertEqual(result["ai_tags"], [])
        api.validate_ai_prerequisites.assert_not_called()
        fake_db.update_rss_ai_content.assert_called_once_with(
            article["id"],
            article["raw_text"],
            "",
            [],
        )


class HtmlRegenerateSummaryFallbackTests(unittest.TestCase):
    def make_api(self):
        api = api_module.Api.__new__(api_module.Api)
        api._llm = Mock()
        api._article_processor = Mock()
        api._summary_lock = threading.Lock()
        api._active_summary_tokens = {}
        api._active_summary_events = {}
        api._resolve_article_ai_config = Mock(
            return_value={
                "source_type": "html",
                "enable_ai_formatting": False,
                "enable_ai_summary": True,
                "formatting_prompt": "",
                "summary_prompt": "",
            }
        )
        api.validate_ai_prerequisites = Mock(return_value={"status": "success"})
        api._is_system_content_article = Mock(return_value=False)
        api._get_annotation_view_modes_to_reset_on_regeneration = Mock(return_value=[])
        api._clear_article_annotations_for_view_modes = Mock(return_value=0)
        api._build_article_content_payload = Mock(
            return_value={"summary": "", "ai_summary": "", "result_kind": "default"}
        )
        return api

    def test_html_regenerate_recovers_text_from_raw_markdown(self):
        article = {
            "id": 11,
            "title": "图文文章",
            "raw_text": "[纯图片内容]",
            "raw_markdown": "![图1](https://example.com/a.jpg)\n\n### 正文\n\n这是一篇图文文章，仍然包含足够的可总结正文。",
            "source_type": "html",
            "summary": "",
        }
        api = self.make_api()
        api._llm.summarize_article.return_value = "【通知】\n\n### 摘要\n- 已恢复正文并完成总结。"

        fake_db = Mock()
        fake_db.get_article_by_id.return_value = article
        fake_db.update_summary.return_value = True

        with patch.object(api_module, "db", fake_db):
            result = api.regenerate_summary(article["id"], skip_ai_precheck=True)

        self.assertEqual(result["status"], "success")
        api._llm.summarize_article.assert_called_once()
        llm_raw_text = api._llm.summarize_article.call_args.args[1]
        self.assertIn("这是一篇图文文章", llm_raw_text)
        self.assertNotIn("![图1]", llm_raw_text)
        fake_db.update_summary.assert_called_once_with(
            article["id"],
            "【通知】\n\n### 摘要\n- 已恢复正文并完成总结。",
        )

    def test_html_regenerate_clears_residual_cancel_flags_before_regenerating(self):
        article = {
            "id": 12,
            "title": "普通文章",
            "raw_text": "这是一篇用于验证手动重新生成的正文内容，长度已经足够触发 AI 总结。",
            "raw_markdown": "",
            "source_type": "html",
            "summary": "",
        }
        api = self.make_api()
        api._llm.summarize_article.return_value = "【通知】\n\n### 摘要\n- 已重新生成。"

        fake_db = Mock()
        fake_db.get_article_by_id.return_value = article
        fake_db.update_summary.return_value = True

        with patch.object(api_module, "db", fake_db):
            result = api.regenerate_summary(article["id"], skip_ai_precheck=True)

        self.assertEqual(result["status"], "success")
        api._article_processor.clear_cancel.assert_called_once()
        api._llm.clear_cancel.assert_called_once()
        api._llm.summarize_article.assert_called_once()


if __name__ == "__main__":
    unittest.main()
