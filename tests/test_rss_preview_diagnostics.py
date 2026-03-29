import unittest

from src.utils.rss_preview import analyze_rss_preview_content, markdown_to_preview_text


class RssPreviewDiagnosticsTests(unittest.TestCase):
    def test_markdown_to_preview_text_keeps_readable_labels(self):
        preview_text = markdown_to_preview_text(
            "# 标题\n\n正文含有[链接](https://example.com)和![配图](https://img.example.com/a.jpg)"
        )

        self.assertIn("标题", preview_text)
        self.assertIn("正文含有链接和配图", preview_text)
        self.assertNotIn("https://example.com", preview_text)

    def test_detects_missing_body(self):
        diagnostics = analyze_rss_preview_content("", image_count=0, attachment_count=0)

        self.assertFalse(diagnostics["has_body"])
        self.assertEqual(diagnostics["preview_status"], "empty")
        self.assertEqual(diagnostics["preview_warnings"][0]["code"], "body_missing")

    def test_detects_short_image_heavy_samples(self):
        diagnostics = analyze_rss_preview_content(
            "图文快讯",
            image_count=4,
            attachment_count=4,
        )

        warning_codes = {warning["code"] for warning in diagnostics["preview_warnings"]}
        self.assertTrue(diagnostics["has_body"])
        self.assertTrue(diagnostics["is_body_short"])
        self.assertEqual(diagnostics["preview_status"], "warning")
        self.assertIn("body_short", warning_codes)
        self.assertIn("image_heavy", warning_codes)

    def test_returns_ready_for_normal_length_content(self):
        diagnostics = analyze_rss_preview_content(
            (
                "这是一个较完整的正文样本，包含多段说明和上下文，用来验证预览态不会误报。"
                "它的长度已经超过短文本阈值，因此应该被判定为可以直接预览的结构化结果。"
            ),
            image_count=1,
            attachment_count=1,
        )

        self.assertTrue(diagnostics["has_body"])
        self.assertFalse(diagnostics["preview_warnings"])
        self.assertEqual(diagnostics["preview_status"], "ready")


if __name__ == "__main__":
    unittest.main()
