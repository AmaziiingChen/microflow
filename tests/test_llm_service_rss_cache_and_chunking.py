import unittest
from unittest.mock import Mock, patch

from src.llm_service import LLMService


class LLMServiceRssCacheAndChunkingTests(unittest.TestCase):
    def setUp(self):
        self.service = LLMService(api_key="", base_url="https://example.com/v1")

    def test_rss_formatting_ignores_cache_when_global_cache_disabled(self):
        cached_markdown = "### 已缓存排版正文"
        fake_db = Mock()
        fake_db.get_ai_result_cache.return_value = {
            "result_text": cached_markdown,
        }

        with (
            patch("src.llm_service._get_database", return_value=fake_db),
            patch.object(
                self.service,
                "_generate_with_retry",
                return_value="### 最新排版正文",
            ),
        ):
            result = self.service.format_rss_article(
                "Cached RSS",
                "原始正文",
                custom_prompt="排版提示词",
                priority="manual",
            )

        self.assertEqual(result, "### 最新排版正文")
        fake_db.get_ai_result_cache.assert_not_called()

    def test_rss_formatting_can_bypass_cache_when_requested(self):
        fake_db = Mock()
        fake_db.get_ai_result_cache.return_value = {
            "result_text": "### 已缓存排版正文"
        }

        with (
            patch("src.llm_service._get_database", return_value=fake_db),
            patch.object(self.service, "_read_cached_result") as read_cache_mock,
            patch.object(self.service, "_write_cached_result") as write_cache_mock,
            patch.object(
                self.service,
                "_generate_with_retry",
                return_value="### 最新排版正文",
            ) as generate_mock,
        ):
            result = self.service.format_rss_article(
                "Cached RSS",
                "原始正文",
                custom_prompt="排版提示词",
                priority="manual",
                use_cache=False,
            )

        self.assertEqual(result, "### 最新排版正文")
        read_cache_mock.assert_not_called()
        generate_mock.assert_called_once()
        write_cache_mock.assert_not_called()

    def test_long_rss_summary_uses_chunk_pipeline(self):
        with (
            patch.object(self.service, "_should_use_chunked_rss_summary", return_value=True),
            patch.object(
                self.service,
                "_split_rss_markdown_chunks",
                return_value=["第一段正文", "第二段正文"],
            ),
            patch.object(self.service, "_read_cached_result", return_value=None),
            patch.object(self.service, "_write_cached_result"),
            patch.object(self.service, "_generate_with_retry") as generate_mock,
        ):
            generate_mock.side_effect = [
                "### 第一段提炼\n- 要点 A",
                "### 第二段提炼\n- 要点 B",
                "【主题】【对象】【动作】\n### 总结\n- 汇总结果",
            ]

            result = self.service.summarize_rss_article(
                "Long RSS",
                "x" * 9000,
                custom_prompt="长文提示词",
                priority="manual",
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tags"], ["主题", "对象", "动作"])
        self.assertIn("汇总结果", result["summary"])
        self.assertEqual(generate_mock.call_count, 3)


if __name__ == "__main__":
    unittest.main()
