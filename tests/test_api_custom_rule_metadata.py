import sys
import threading
import types
import unittest
from unittest.mock import Mock

from tests import feedparser_stub

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault(
    "feedparser", types.SimpleNamespace(parse=feedparser_stub.parse)
)

from src import api as api_module


class ApiCustomRuleMetadataTests(unittest.TestCase):
    def make_api(self, rules):
        api = api_module.Api.__new__(api_module.Api)
        api._rules_manager = Mock()
        api._rules_manager.load_custom_rules.return_value = rules
        api._summary_lock = threading.Lock()
        api._active_summary_tokens = {}
        api._active_summary_events = {}
        return api

    def test_get_custom_spider_rules_returns_catalog_and_health_summary(self):
        api = self.make_api(
            [
                {
                    "rule_id": "rss_1",
                    "task_name": "RSS One",
                    "source_type": "rss",
                    "health": {"status": "healthy"},
                },
                {
                    "rule_id": "rss_2",
                    "task_name": "RSS Two",
                    "source_type": "rss",
                    "health": {
                        "status": "error",
                        "consecutive_failures": 3,
                        "last_error_message": "timeout",
                        "last_checked_at": "2026-03-29T18:00:00",
                    },
                },
                {
                    "rule_id": "html_1",
                    "task_name": "HTML One",
                    "source_type": "html",
                },
            ]
        )

        result = api.get_custom_spider_rules()

        self.assertEqual(result["status"], "success")
        self.assertIn("rss_strategy_catalog", result)
        self.assertEqual(result["rss_health_summary"]["total_rules"], 2)
        self.assertEqual(result["rss_health_summary"]["healthy_count"], 1)
        self.assertEqual(result["rss_health_summary"]["error_count"], 1)
        self.assertEqual(result["rss_health_summary"]["attention_count"], 1)
        self.assertEqual(
            result["rss_health_summary"]["attention_rules"][0]["task_name"],
            "RSS Two",
        )


if __name__ == "__main__":
    unittest.main()
