import unittest

from src.utils.article_identity import (
    build_stable_article_fingerprint,
    canonicalize_article_url,
)


class ArticleIdentityTests(unittest.TestCase):
    def test_canonicalize_article_url_strips_tracking_params(self):
        url = (
            "https://Example.com/news/detail?id=42&utm_source=wechat"
            "&fbclid=abc123&spm=feed&from=timeline"
        )

        normalized = canonicalize_article_url(url)

        self.assertEqual(normalized, "https://example.com/news/detail?id=42")

    def test_canonicalize_article_url_keeps_virtual_fragment(self):
        url = "https://example.com/news/list.htm#item-abcdef123456"

        normalized = canonicalize_article_url(url)

        self.assertEqual(normalized, url)

    def test_stable_fingerprint_ignores_field_order(self):
        first = build_stable_article_fingerprint(
            source_name="自定义网页源",
            page_url="https://example.com/news",
            title="标题 A",
            date="2026-03-30",
            fields={"title": "标题 A", "date": "2026-03-30", "summary": "正文"},
        )
        second = build_stable_article_fingerprint(
            source_name="自定义网页源",
            page_url="https://example.com/news",
            title="标题 A",
            date="2026-03-30",
            fields={"summary": "正文", "date": "2026-03-30", "title": "标题 A"},
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
