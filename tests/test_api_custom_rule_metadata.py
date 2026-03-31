import sys
import tempfile
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


class ApiCustomRuleMetadataTests(unittest.TestCase):
    def make_api(self, rules):
        api = api_module.Api.__new__(api_module.Api)
        api._rules_manager = Mock()
        api._rules_manager.load_custom_rules.return_value = rules
        api._rules_manager.save_custom_rule.return_value = True
        api._summary_lock = threading.Lock()
        api._active_summary_tokens = {}
        api._active_summary_events = {}
        api._get_effective_sources = Mock(return_value=["公文通"])
        api._resolve_article_source_type = Mock(return_value="html")
        api._resolve_article_ai_config = Mock(
            return_value={
                "source_type": "html",
                "enable_ai_formatting": False,
                "enable_ai_summary": False,
                "formatting_prompt": "",
                "summary_prompt": "",
            }
        )
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
                        "status_detail": "network_error",
                        "consecutive_failures": 3,
                        "last_error_message": "timeout",
                        "last_checked_at": "2026-03-29T18:00:00",
                        "last_success_at": "2026-03-28T18:00:00",
                        "last_failure_at": "2026-03-29T18:00:00",
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
        self.assertEqual(
            result["rss_health_summary"]["attention_rules"][0]["status_detail"],
            "network_error",
        )
        self.assertEqual(result["html_health_summary"]["total_rules"], 1)
        self.assertEqual(result["html_health_summary"]["healthy_count"], 0)
        self.assertEqual(result["html_health_summary"]["error_count"], 0)

    def test_get_custom_spider_rules_returns_html_health_summary(self):
        api = self.make_api(
            [
                {
                    "rule_id": "html_1",
                    "task_name": "HTML One",
                    "source_type": "html",
                    "health": {"status": "healthy"},
                },
                {
                    "rule_id": "html_2",
                    "task_name": "HTML Two",
                    "source_type": "html",
                    "health": {
                        "status": "error",
                        "status_detail": "selector_error",
                        "consecutive_failures": 2,
                        "last_error_message": "未找到列表容器",
                        "last_checked_at": "2026-03-30T18:00:00",
                        "last_failure_at": "2026-03-30T18:00:00",
                    },
                },
            ]
        )

        result = api.get_custom_spider_rules()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["html_health_summary"]["total_rules"], 2)
        self.assertEqual(result["html_health_summary"]["healthy_count"], 1)
        self.assertEqual(result["html_health_summary"]["error_count"], 1)
        self.assertEqual(result["html_health_summary"]["attention_count"], 1)
        self.assertEqual(
            result["html_health_summary"]["attention_rules"][0]["task_name"],
            "HTML Two",
        )
        self.assertEqual(
            result["html_health_summary"]["attention_rules"][0]["status_detail"],
            "selector_error",
        )

    def test_get_custom_spider_rules_includes_alerting_healthy_html_rule(self):
        api = self.make_api(
            [
                {
                    "rule_id": "html_healthy_warn",
                    "task_name": "HTML 预警源",
                    "source_type": "html",
                    "health": {
                        "status": "healthy",
                        "status_detail": "field_drift",
                        "is_alerting": True,
                        "field_alerts": [
                            {
                                "field": "date",
                                "current_rate": 0.0,
                                "baseline_rate": 1.0,
                            }
                        ],
                    },
                }
            ]
        )

        result = api.get_custom_spider_rules()

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["html_health_summary"]["total_rules"], 1)
        self.assertEqual(result["html_health_summary"]["healthy_count"], 1)
        self.assertEqual(result["html_health_summary"]["attention_count"], 1)
        self.assertEqual(
            result["html_health_summary"]["attention_rules"][0]["status_detail"],
            "field_drift",
        )

    def test_get_custom_spider_rule_versions_returns_history_payload(self):
        api = self.make_api([])
        api._rules_manager.get_rule_by_id.return_value = {
            "rule_id": "html_1",
            "task_name": "HTML One",
            "source_type": "html",
            "updated_at": "2026-03-30T21:00:00",
        }
        api._rules_manager.get_rule_versions.return_value = [
            {
                "version_id": "ver_1",
                "saved_at": "2026-03-30T20:00:00",
                "reason": "save",
                "snapshot": {"task_name": "HTML Zero"},
            }
        ]

        result = api.get_custom_spider_rule_versions("html_1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rule_id"], "html_1")
        self.assertEqual(result["task_name"], "HTML One")
        self.assertEqual(result["versions"][0]["version_id"], "ver_1")
        self.assertEqual(result["current_snapshot"]["source_type"], "html")

    def test_rollback_custom_spider_rule_version_returns_updated_rule(self):
        api = self.make_api([])
        api._rules_manager.rollback_rule_to_version.return_value = {
            "rule_id": "html_1",
            "task_name": "HTML One",
            "version_history": [],
        }

        result = api.rollback_custom_spider_rule_version("html_1", "ver_1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["rule_id"], "html_1")
        self.assertEqual(result["version_id"], "ver_1")
        self.assertEqual(result["rule"]["task_name"], "HTML One")
        api._rules_manager.rollback_rule_to_version.assert_called_once_with(
            "html_1", "ver_1"
        )

    def test_export_custom_spider_rules_writes_json_file(self):
        api = self.make_api([])
        api._rules_manager.build_rules_export_payload.return_value = {
            "format": "microflow_custom_rules_export",
            "rules": [{"rule_id": "html_1"}],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            target_path = f"{temp_dir}/rules_export.json"
            window = Mock()
            window.create_file_dialog.return_value = [target_path]
            fake_webview = types.SimpleNamespace(
                windows=[window],
                SAVE_DIALOG="save",
            )
            with patch.object(api_module, "webview", fake_webview):
                result = api.export_custom_spider_rules()

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["exported_count"], 1)
            with open(target_path, "r", encoding="utf-8") as f:
                exported = f.read()
            self.assertIn("microflow_custom_rules_export", exported)

    def test_import_custom_spider_rules_reads_json_file(self):
        api = self.make_api([])
        api._rules_manager.import_rules_payload.return_value = {
            "status": "success",
            "message": "成功导入 1 条规则",
            "imported_count": 1,
            "added_count": 1,
            "updated_count": 0,
            "skipped_count": 0,
            "errors": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = f"{temp_dir}/rules_import.json"
            with open(source_path, "w", encoding="utf-8") as f:
                f.write('{"format":"microflow_custom_rules_export","rules":[{"rule_id":"html_1"}]}')

            window = Mock()
            window.create_file_dialog.return_value = [source_path]
            fake_webview = types.SimpleNamespace(
                windows=[window],
                OPEN_DIALOG="open",
            )
            with patch.object(api_module, "webview", fake_webview):
                result = api.import_custom_spider_rules()

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["imported_count"], 1)

    def test_get_history_paged_uses_compact_query_payload(self):
        api = self.make_api([])

        with patch.object(api_module, "db") as mock_db:
            mock_db.get_articles_paged.return_value = [
                {
                    "id": 1,
                    "title": "测试文章",
                    "url": "https://example.com/article",
                    "summary": "摘要",
                    "source_name": "公文通",
                }
            ]

            result = api.get_history_paged(page=2, page_size=10)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"][0]["has_full_content"], False)
        mock_db.get_articles_paged.assert_called_once_with(
            limit=10,
            offset=10,
            source_name=None,
            source_names=["公文通"],
            favorites_only=False,
            include_content=False,
        )

    def test_get_article_detail_returns_full_payload_marker(self):
        api = self.make_api([])

        with patch.object(api_module, "db") as mock_db:
            mock_db.get_article_by_id.return_value = {
                "id": 7,
                "title": "详情文章",
                "url": "https://example.com/detail",
                "summary": "【通知】详情摘要",
                "raw_text": "原文正文",
                "raw_markdown": "原文正文",
                "enhanced_markdown": "",
                "ai_summary": "",
                "ai_tags": "",
                "source_type": "html",
            }

            result = api.get_article_detail(7)

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["data"]["has_full_content"])
        self.assertEqual(result["data"]["raw_markdown"], "原文正文")

    def test_search_articles_uses_compact_payload_for_keyword_search(self):
        api = self.make_api([])

        with patch.object(api_module, "db") as mock_db:
            mock_db.search_articles.return_value = [
                {
                    "id": 3,
                    "title": "搜索文章",
                    "url": "https://example.com/search",
                    "summary": "搜索摘要",
                    "source_name": "公文通",
                    "has_full_content": False,
                }
            ]

            result = api.search_articles("关键词")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"][0]["has_full_content"], False)
        mock_db.search_articles.assert_called_once_with(
            "关键词",
            limit=50,
            source_names=["公文通"],
            favorites_only=False,
            include_content=False,
        )

    def test_confirm_and_save_rule_normalizes_html_request_config(self):
        api = self.make_api([])
        api._rules_manager.save_custom_rule.return_value = True

        result = api.confirm_and_save_rule(
            {
                "rule_id": "html_rule_1",
                "task_id": "task_html_1",
                "task_name": "HTML 测试源",
                "url": "https://example.com/list",
                "source_type": "html",
                "list_container": " ul.news-list ",
                "item_selector": " li ",
                "field_selectors": {
                    "title": " a.title::text ",
                    "url": " a::attr(href) ",
                    "date": " .date::text ",
                },
                "request_method": "POST",
                "request_body": '{"page":1}',
                "request_headers": "X-Test: demo",
                "cookie_string": "Cookie: sid=1; uid=2",
                "pagination_mode": "url_template",
                "page_url_template": "https://example.com/list?page={page}",
                "body_field": "date",
            }
        )

        self.assertEqual(result["status"], "success")
        saved_rule = api._rules_manager.save_custom_rule.call_args[0][0]
        self.assertEqual(saved_rule["request_method"], "post")
        self.assertEqual(saved_rule["cookie_string"], "sid=1; uid=2")
        self.assertEqual(
            saved_rule["request_headers"]["Content-Type"],
            "application/json; charset=UTF-8",
        )
        self.assertEqual(saved_rule["field_selectors"]["title"], "a.title::text")

    def test_confirm_and_save_rule_rejects_invalid_html_payload(self):
        api = self.make_api([])

        result = api.confirm_and_save_rule(
            {
                "rule_id": "html_rule_2",
                "task_id": "task_html_2",
                "task_name": "HTML 缺失字段",
                "url": "https://example.com/list",
                "source_type": "html",
                "list_container": "ul.news-list",
                "item_selector": "li",
                "field_selectors": {
                    "title": "a.title::text",
                },
            }
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("title 与 url", result["message"])

    def test_generate_custom_spider_rule_returns_detail_preview_metadata(self):
        api = self.make_api([])
        api._rule_generator = Mock()
        generated_rule = Mock()
        generated_rule.model_dump.return_value = {
            "rule_id": "rule_html_preview_1",
            "task_id": "task_html_preview_1",
            "task_name": "HTML Preview",
        }
        api._rule_generator.generate_and_test_rule.return_value = types.SimpleNamespace(
            success=True,
            rule=generated_rule,
            sample_data=[{"title": "样本 A"}],
            detail_samples=[
                {
                    "title": "详情样本 A",
                    "status": "warning",
                    "status_label": "仅回退列表正文",
                }
            ],
            detail_preview_required=True,
            detail_preview_passed=False,
            detail_preview_message="详情预览未通过",
            page_summary={"title": "Example Page"},
            test_snapshot={"sample_count": 1},
        )

        result = api.generate_custom_spider_rule(
            url="https://example.com/news",
            task_id="task_html_preview_1",
            task_name="HTML Preview",
            target_fields=["title", "url"],
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["detail_samples"][0]["status"], "warning")
        self.assertTrue(result["detail_preview_required"])
        self.assertFalse(result["detail_preview_passed"])
        self.assertEqual(result["detail_preview_message"], "详情预览未通过")
        self.assertEqual(result["page_summary"]["title"], "Example Page")
        self.assertEqual(result["test_snapshot"]["sample_count"], 1)
        self.assertEqual(result["rule"]["page_summary"]["title"], "Example Page")

    def test_generate_custom_spider_rule_uses_existing_rule_recovery_context(self):
        api = self.make_api([])
        api._rule_generator = Mock()
        api._rules_manager.get_rule_by_id.return_value = {
            "rule_id": "html_recover_1",
            "task_id": "task_html_recover_1",
            "task_name": "HTML Recover",
            "url": "https://example.com/news",
            "source_type": "html",
            "field_selectors": {"title": "a::text"},
            "health": {
                "status_detail": "list_container_drift",
                "last_error_message": "未找到列表容器: ul.news-list",
                "last_known_good_snapshot": {
                    "list_container": "ul.news-list",
                    "item_selector": "li",
                    "field_selectors": {"title": "a::text", "url": "a::attr(href)"},
                },
            },
        }
        generated_rule = Mock()
        generated_rule.model_dump.return_value = {
            "rule_id": "html_recover_1",
            "task_id": "task_html_recover_1",
            "task_name": "HTML Recover",
        }
        api._rule_generator.generate_and_test_rule.return_value = types.SimpleNamespace(
            success=True,
            rule=generated_rule,
            sample_data=[{"title": "样本 A"}],
            detail_samples=[],
            detail_preview_required=False,
            detail_preview_passed=True,
            detail_preview_message="",
            recovery_applied=True,
            recovery_message="已基于历史健康快照和失败上下文辅助重生成。",
        )

        result = api.generate_custom_spider_rule(
            url="https://example.com/news",
            task_id="task_html_recover_1",
            task_name="HTML Recover",
            target_fields=["title", "url"],
            existing_rule_id="html_recover_1",
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["recovery_applied"])
        self.assertIn("辅助重生成", result["recovery_message"])
        _, kwargs = api._rule_generator.generate_and_test_rule.call_args
        self.assertEqual(kwargs["recovery_context"]["existing_rule_id"], "html_recover_1")
        self.assertEqual(
            kwargs["recovery_context"]["health"]["status_detail"],
            "list_container_drift",
        )

    def test_generate_custom_spider_rule_passes_request_headers_and_cookie(self):
        api = self.make_api([])
        api._rule_generator = Mock()
        generated_rule = Mock()
        generated_rule.model_dump.return_value = {
            "rule_id": "html_http_1",
            "task_id": "task_html_http_1",
            "task_name": "HTML HTTP",
        }
        api._rule_generator.generate_and_test_rule.return_value = types.SimpleNamespace(
            success=True,
            rule=generated_rule,
            sample_data=[],
            detail_samples=[],
            detail_preview_required=False,
            detail_preview_passed=True,
            detail_preview_message="",
        )

        result = api.generate_custom_spider_rule(
            url="https://example.com/protected",
            task_id="task_html_http_1",
            task_name="HTML HTTP",
            target_fields=["title", "url"],
            request_method="post",
            request_body='{"page":1,"size":20}',
            request_headers={
                "Referer": "https://portal.example.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookie_string="sid=abc; token=xyz",
        )

        self.assertEqual(result["status"], "success")
        _, kwargs = api._rule_generator.generate_and_test_rule.call_args
        self.assertEqual(
            kwargs["request_headers"],
            {
                "Referer": "https://portal.example.com",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self.assertEqual(kwargs["request_method"], "post")
        self.assertEqual(kwargs["request_body"], '{"page":1,"size":20}')
        self.assertEqual(kwargs["cookie_string"], "sid=abc; token=xyz")

    def test_test_custom_spider_rule_returns_detail_preview_bundle(self):
        api = self.make_api([])
        api._rule_generator = Mock()
        api._rule_generator.build_rule_preview_bundle.return_value = {
            "sample_data": [{"title": "样本 A"}],
            "detail_samples": [
                {
                    "title": "详情样本 A",
                    "status": "passed",
                    "status_label": "详情正文命中",
                }
            ],
            "detail_preview_required": True,
            "detail_preview_passed": True,
            "detail_preview_message": "详情预览通过",
            "page_summary": {"title": "Preview Page"},
            "test_snapshot": {"sample_count": 1},
        }

        rule_dict = {
            "rule_id": "html_rule_preview_test",
            "task_id": "task_preview_test",
            "task_name": "HTML Preview Test",
            "url": "https://example.com/news",
            "source_type": "html",
            "list_container": "ul.news-list",
            "item_selector": "li",
            "field_selectors": {
                "title": "a::text",
                "url": "a::attr(href)",
            },
        }

        result = api.test_custom_spider_rule(rule_dict)

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["detail_samples"][0]["status"], "passed")
        self.assertTrue(result["detail_preview_required"])
        self.assertTrue(result["detail_preview_passed"])
        self.assertEqual(result["detail_preview_message"], "详情预览通过")
        self.assertEqual(result["page_summary"]["title"], "Preview Page")
        self.assertEqual(result["test_snapshot"]["sample_count"], 1)

    @patch("src.spiders.dynamic_spider.create_dynamic_spider_from_rule")
    def test_manual_retest_custom_spider_rule_updates_health_on_success(
        self, mock_create_spider
    ):
        api = self.make_api([])
        saved_rule = {
            "rule_id": "html_1",
            "task_name": "HTML One",
            "url": "https://example.com/news",
            "source_type": "html",
        }
        refreshed_rule = {
            **saved_rule,
            "health": {
                "status": "healthy",
                "status_detail": "healthy",
                "last_fetched_count": 2,
            },
        }
        api._rules_manager.get_rule_by_id.side_effect = [saved_rule, refreshed_rule]
        api._rules_manager.update_rule_health.return_value = True

        spider = Mock()
        spider.fetch_list.return_value = [{"title": "A"}, {"title": "B"}]
        spider.last_fetch_status = "healthy"
        spider.last_fetch_error = ""
        spider.last_fetched_count = 2
        mock_create_spider.return_value = spider

        result = api.manual_retest_custom_spider_rule("html_1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["count"], 2)
        api._rules_manager.update_rule_health.assert_called_once_with(
            "html_1",
            status="healthy",
            error_message="",
            fetched_count=2,
            field_hit_stats=None,
        )
        self.assertEqual(result["health"]["status"], "healthy")

    @patch("src.spiders.dynamic_spider.create_dynamic_spider_from_rule")
    def test_manual_retest_custom_spider_rule_updates_health_on_failure(
        self, mock_create_spider
    ):
        api = self.make_api([])
        saved_rule = {
            "rule_id": "html_2",
            "task_name": "HTML Two",
            "url": "https://example.com/news",
            "source_type": "html",
        }
        refreshed_rule = {
            **saved_rule,
            "health": {
                "status": "error",
                "status_detail": "selector_error",
                "last_error_message": "未找到列表容器: ul.news-list",
            },
        }
        api._rules_manager.get_rule_by_id.side_effect = [saved_rule, refreshed_rule]
        api._rules_manager.update_rule_health.return_value = True

        spider = Mock()
        spider.fetch_list.return_value = []
        spider.last_fetch_status = "error"
        spider.last_fetch_error = "未找到列表容器: ul.news-list"
        spider.last_fetched_count = 0
        mock_create_spider.return_value = spider

        result = api.manual_retest_custom_spider_rule("html_2")

        self.assertEqual(result["status"], "error")
        self.assertIn("未找到列表容器", result["message"])
        api._rules_manager.update_rule_health.assert_called_once_with(
            "html_2",
            status="error",
            error_message="未找到列表容器: ul.news-list",
            fetched_count=0,
            field_hit_stats=None,
        )
        self.assertEqual(result["health"]["status_detail"], "selector_error")


if __name__ == "__main__":
    unittest.main()
