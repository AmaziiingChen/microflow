import sys
import tempfile
import unittest
from pathlib import Path
import types

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))

from src.services import custom_spider_rules_manager as manager_module
from src.services.custom_spider_rules_manager import CustomSpiderRulesManager


class CustomRuleHealthTests(unittest.TestCase):
    def setUp(self):
        CustomSpiderRulesManager._instance = None
        manager_module._rules_manager_instance = None
        self.temp_dir = tempfile.TemporaryDirectory()
        self.rules_path = Path(self.temp_dir.name) / "rules.json"
        self.manager = CustomSpiderRulesManager(self.rules_path)
        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_rss_1",
                    "task_id": "task_rss_1",
                    "task_name": "RSS 测试源",
                    "url": "https://example.com/feed.xml",
                    "source_type": "rss",
                    "enabled": True,
                }
            )
        )

    def tearDown(self):
        self.temp_dir.cleanup()
        CustomSpiderRulesManager._instance = None
        manager_module._rules_manager_instance = None

    def test_rule_health_tracks_failures_and_recovery(self):
        self.assertTrue(
            self.manager.update_rule_health(
                "rule_rss_1",
                status="error",
                error_message="timeout",
                fetched_count=0,
            )
        )

        failed_rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(failed_rule["health"]["status"], "error")
        self.assertEqual(failed_rule["health"]["consecutive_failures"], 1)
        self.assertEqual(failed_rule["health"]["last_error_message"], "timeout")

        self.assertTrue(
            self.manager.update_rule_health(
                "rule_rss_1",
                status="healthy",
                fetched_count=5,
            )
        )

        recovered_rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(recovered_rule["health"]["status"], "healthy")
        self.assertEqual(recovered_rule["health"]["consecutive_failures"], 0)
        self.assertEqual(recovered_rule["health"]["last_fetched_count"], 5)
        self.assertEqual(recovered_rule["health"]["last_error_message"], "")

    def test_save_custom_rule_preserves_existing_health(self):
        self.manager.update_rule_health(
            "rule_rss_1",
            status="error",
            error_message="network error",
            fetched_count=0,
        )

        self.manager.save_custom_rule(
            {
                "rule_id": "rule_rss_1",
                "task_id": "task_rss_1",
                "task_name": "RSS 测试源",
                "task_purpose": "更新后的说明",
                "url": "https://example.com/feed.xml",
                "source_type": "rss",
                "enabled": True,
            }
        )

        rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(rule["task_purpose"], "更新后的说明")
        self.assertEqual(rule["health"]["status"], "error")
        self.assertEqual(rule["health"]["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
