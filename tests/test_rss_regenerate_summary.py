import sys
import threading
import types
import unittest
from unittest.mock import Mock, patch

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


if __name__ == "__main__":
    unittest.main()
