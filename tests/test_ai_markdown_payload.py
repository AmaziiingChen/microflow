import unittest

from src.utils.ai_markdown import build_tag_items, resolve_article_summary_payload


class ResolveArticleSummaryPayloadTests(unittest.TestCase):
    def test_prefers_new_ai_summary_and_tags(self):
        payload = resolve_article_summary_payload(
            {
                "summary": "【旧标签】\n旧正文",
                "ai_summary": "新的摘要正文",
                "ai_tags": '["重点", "进展"]',
                "enhanced_markdown": "# 完整正文",
            }
        )

        self.assertEqual(payload["tags"], ["重点", "进展"])
        self.assertEqual(payload["summary_body"], "新的摘要正文")
        self.assertEqual(
            payload["summary_markdown"],
            "【旧标签】\n旧正文",
        )
        self.assertEqual(payload["preview_markdown"], "新的摘要正文")

    def test_falls_back_to_legacy_summary_body(self):
        payload = resolve_article_summary_payload(
            {
                "summary": "【资讯】\n这是兼容摘要",
                "enhanced_markdown": "## 正文排版",
            }
        )

        self.assertEqual(payload["tags"], ["资讯"])
        self.assertEqual(payload["summary_body"], "这是兼容摘要")
        self.assertEqual(payload["preview_markdown"], "这是兼容摘要")

    def test_falls_back_to_enhanced_markdown_when_summary_missing(self):
        payload = resolve_article_summary_payload(
            {
                "enhanced_markdown": "# 标题\n\n正文段落",
                "raw_markdown": "原始正文",
            }
        )

        self.assertEqual(payload["tags"], [])
        self.assertEqual(payload["summary_body"], "")
        self.assertEqual(payload["preview_markdown"], "# 标题\n\n正文段落")
        self.assertEqual(payload["summary_markdown"], "# 标题\n\n正文段落")

    def test_builds_tag_items_with_priority(self):
        items = build_tag_items(["重点", "进展", "对象"])

        self.assertEqual(items[0]["text"], "重点")
        self.assertEqual(items[0]["priority"], 1)
        self.assertTrue(items[0]["is_primary"])
        self.assertEqual(items[2]["priority"], 3)

    def test_payload_exposes_tag_items(self):
        payload = resolve_article_summary_payload(
            {
                "ai_summary": "新的摘要正文",
                "ai_tags": '["重点", "进展"]',
            }
        )

        self.assertEqual(payload["tag_items"][0]["text"], "重点")
        self.assertEqual(payload["tag_items"][1]["priority"], 2)


if __name__ == "__main__":
    unittest.main()
