import unittest

from src.llm_service import LLMService


class LLMServiceTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.service = LLMService(api_key="", base_url="https://example.com/v1")

    def test_rss_manual_timeout_scales_with_content_length(self):
        short_timeout = self.service._resolve_request_timeout(
            raw_text="x" * 1000,
            target_label="RSS 摘要生成",
            priority="manual",
        )
        long_timeout = self.service._resolve_request_timeout(
            raw_text="x" * 18000,
            target_label="RSS 摘要生成",
            priority="manual",
        )

        self.assertGreaterEqual(short_timeout, 105.0)
        self.assertGreater(long_timeout, short_timeout)
        self.assertLessEqual(long_timeout, 210.0)

    def test_default_batch_timeout_keeps_lower_baseline(self):
        timeout = self.service._resolve_request_timeout(
            raw_text="简短正文",
            target_label="公文",
            priority="batch",
        )
        self.assertEqual(timeout, 60.0)


if __name__ == "__main__":
    unittest.main()
