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
        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 测试源",
                    "url": "https://example.com/list",
                    "source_type": "html",
                    "list_container": "ul.news-list",
                    "item_selector": "li",
                    "field_selectors": {
                        "title": "a::text",
                        "url": "a::attr(href)",
                        "date": ".date::text",
                    },
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
        self.assertEqual(failed_rule["health"]["status_detail"], "network_error")
        self.assertEqual(failed_rule["health"]["consecutive_failures"], 1)
        self.assertEqual(failed_rule["health"]["last_error_message"], "timeout")
        self.assertFalse(failed_rule["health"]["is_alerting"])

        self.assertTrue(
            self.manager.update_rule_health(
                "rule_rss_1",
                status="healthy",
                fetched_count=5,
            )
        )

        recovered_rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(recovered_rule["health"]["status"], "healthy")
        self.assertEqual(recovered_rule["health"]["status_detail"], "healthy")
        self.assertEqual(recovered_rule["health"]["consecutive_failures"], 0)
        self.assertEqual(recovered_rule["health"]["last_fetched_count"], 5)
        self.assertEqual(recovered_rule["health"]["last_error_message"], "")
        self.assertFalse(recovered_rule["health"]["is_alerting"])

    def test_rule_health_classifies_selector_failures(self):
        self.assertTrue(
            self.manager.update_rule_health(
                "rule_rss_1",
                status="error",
                error_message="未找到列表容器: ul.news-list",
                fetched_count=0,
            )
        )

        rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(rule["health"]["status_detail"], "list_container_drift")

        self.assertTrue(
            self.manager.update_rule_health(
                "rule_rss_1",
                status="error",
                error_message="未找到列表项: li",
                fetched_count=0,
            )
        )

        rule = self.manager.get_rule_by_id("rule_rss_1")
        self.assertEqual(rule["health"]["status_detail"], "item_selector_drift")
        self.assertEqual(rule["health"]["consecutive_failures"], 2)
        self.assertTrue(rule["health"]["is_alerting"])

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

    def test_save_custom_rule_records_version_history_snapshot(self):
        original_rule = self.manager.get_rule_by_id("rule_html_1")
        self.assertIsNotNone(original_rule)
        self.assertEqual(original_rule.get("version_history"), [])

        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 测试源",
                    "task_purpose": "更新后的用途",
                    "url": "https://example.com/list",
                    "source_type": "html",
                    "list_container": "div.news-list",
                    "item_selector": "article",
                    "field_selectors": {
                        "title": "h2::text",
                        "url": "a::attr(href)",
                        "date": "time::text",
                    },
                    "enabled": True,
                }
            )
        )

        updated_rule = self.manager.get_rule_by_id("rule_html_1")
        self.assertEqual(updated_rule["task_purpose"], "更新后的用途")
        self.assertEqual(len(updated_rule["version_history"]), 1)
        first_version = updated_rule["version_history"][0]
        self.assertEqual(first_version["reason"], "save")
        self.assertEqual(first_version["snapshot"]["list_container"], "ul.news-list")
        self.assertEqual(first_version["snapshot"]["item_selector"], "li")
        self.assertNotIn("task_purpose", first_version["snapshot"])

    def test_rollback_rule_to_version_restores_previous_snapshot(self):
        self.manager.update_rule_health(
            "rule_html_1",
            status="error",
            error_message="selector drift",
            fetched_count=0,
        )
        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 测试源 v2",
                    "task_purpose": "第二版",
                    "url": "https://example.com/list-v2",
                    "source_type": "html",
                    "list_container": "div.news-list",
                    "item_selector": "article",
                    "field_selectors": {
                        "title": "h2::text",
                        "url": "a::attr(href)",
                        "date": "time::text",
                    },
                    "enabled": True,
                }
            )
        )

        updated_rule = self.manager.get_rule_by_id("rule_html_1")
        target_version_id = updated_rule["version_history"][0]["version_id"]
        rolled_back_rule = self.manager.rollback_rule_to_version(
            "rule_html_1", target_version_id
        )

        self.assertIsNotNone(rolled_back_rule)
        self.assertEqual(rolled_back_rule["task_name"], "HTML 测试源")
        self.assertEqual(rolled_back_rule["url"], "https://example.com/list")
        self.assertEqual(rolled_back_rule["list_container"], "ul.news-list")
        self.assertEqual(rolled_back_rule["health"]["status"], "error")
        self.assertEqual(len(rolled_back_rule["version_history"]), 2)
        self.assertEqual(rolled_back_rule["version_history"][0]["reason"], "rollback")
        self.assertEqual(
            rolled_back_rule["version_history"][0]["snapshot"]["task_name"],
            "HTML 测试源 v2",
        )

    def test_get_rule_versions_returns_versions_in_reverse_chronological_order(self):
        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 测试源 v2",
                    "url": "https://example.com/list-v2",
                    "source_type": "html",
                    "list_container": "div.news-list",
                    "item_selector": "article",
                    "field_selectors": {
                        "title": "h2::text",
                        "url": "a::attr(href)",
                        "date": "time::text",
                    },
                    "enabled": True,
                }
            )
        )
        self.assertTrue(
            self.manager.save_custom_rule(
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 测试源 v3",
                    "url": "https://example.com/list-v3",
                    "source_type": "html",
                    "list_container": "section.news-list",
                    "item_selector": "article",
                    "field_selectors": {
                        "title": "h3::text",
                        "url": "a::attr(href)",
                        "date": "time::text",
                    },
                    "enabled": True,
                }
            )
        )

        versions = self.manager.get_rule_versions("rule_html_1")
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["snapshot"]["task_name"], "HTML 测试源 v2")
        self.assertEqual(versions[1]["snapshot"]["task_name"], "HTML 测试源")

    def test_build_rules_export_payload_supports_filtering(self):
        payload = self.manager.build_rules_export_payload(["rule_html_1"])
        self.assertEqual(payload["format"], "microflow_custom_rules_export")
        self.assertEqual(payload["rule_count"], 1)
        self.assertEqual(payload["rules"][0]["rule_id"], "rule_html_1")

    def test_import_rules_payload_updates_rule_without_creating_new_version(self):
        payload = {
            "format": "microflow_custom_rules_export",
            "version": "1.0",
            "rules": [
                {
                    "rule_id": "rule_html_1",
                    "task_id": "task_html_1",
                    "task_name": "HTML 导入版",
                    "url": "https://example.com/imported",
                    "source_type": "html",
                    "list_container": "section.news-list",
                    "item_selector": "article",
                    "field_selectors": {
                        "title": "h2::text",
                        "url": "a::attr(href)",
                    },
                    "version_history": [
                        {
                            "version_id": "ver_imported_1",
                            "saved_at": "2026-03-30T10:00:00",
                            "reason": "save",
                            "snapshot": {
                                "rule_id": "rule_html_1",
                                "task_id": "task_html_1",
                                "task_name": "HTML 历史版",
                                "url": "https://example.com/older",
                                "source_type": "html",
                                "list_container": "ul.news-list",
                                "item_selector": "li",
                                "field_selectors": {
                                    "title": "a::text",
                                    "url": "a::attr(href)",
                                },
                            },
                        }
                    ],
                    "page_summary": {"title": "示例页面"},
                    "test_snapshot": {"sample_count": 2},
                }
            ],
        }

        result = self.manager.import_rules_payload(payload)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["updated_count"], 1)
        imported_rule = self.manager.get_rule_by_id("rule_html_1")
        self.assertEqual(imported_rule["task_name"], "HTML 导入版")
        self.assertEqual(imported_rule["page_summary"]["title"], "示例页面")
        self.assertEqual(imported_rule["test_snapshot"]["sample_count"], 2)
        self.assertEqual(len(imported_rule["version_history"]), 1)
        self.assertEqual(
            imported_rule["version_history"][0]["version_id"],
            "ver_imported_1",
        )

    def test_rule_health_detects_field_drift_and_keeps_last_good_snapshot(self):
        self.assertTrue(
            self.manager.update_rule_health(
                "rule_html_1",
                status="healthy",
                fetched_count=5,
                field_hit_stats={
                    "title": {"hit_count": 5, "total_count": 5, "hit_rate": 1.0},
                    "url": {"hit_count": 5, "total_count": 5, "hit_rate": 1.0},
                    "date": {"hit_count": 5, "total_count": 5, "hit_rate": 1.0},
                },
            )
        )

        self.assertTrue(
            self.manager.update_rule_health(
                "rule_html_1",
                status="healthy",
                fetched_count=5,
                field_hit_stats={
                    "title": {"hit_count": 5, "total_count": 5, "hit_rate": 1.0},
                    "url": {"hit_count": 5, "total_count": 5, "hit_rate": 1.0},
                    "date": {"hit_count": 0, "total_count": 5, "hit_rate": 0.0},
                },
            )
        )

        rule = self.manager.get_rule_by_id("rule_html_1")
        self.assertEqual(rule["health"]["status"], "healthy")
        self.assertEqual(rule["health"]["status_detail"], "field_drift")
        self.assertTrue(rule["health"]["is_alerting"])
        self.assertEqual(rule["health"]["field_alerts"][0]["field"], "date")
        self.assertEqual(
            rule["health"]["last_known_good_snapshot"]["field_selectors"]["date"],
            ".date::text",
        )

    def test_rule_health_alerts_when_rule_stays_empty_too_long(self):
        for _ in range(3):
            self.assertTrue(
                self.manager.update_rule_health(
                    "rule_html_1",
                    status="empty",
                    fetched_count=0,
                )
            )

        rule = self.manager.get_rule_by_id("rule_html_1")
        self.assertEqual(rule["health"]["status"], "empty")
        self.assertEqual(rule["health"]["status_detail"], "stale_empty")
        self.assertEqual(rule["health"]["consecutive_empties"], 3)
        self.assertTrue(rule["health"]["is_alerting"])


if __name__ == "__main__":
    unittest.main()
