import sys
import types
import unittest
from types import SimpleNamespace

from tests import feedparser_stub

sys.modules["feedparser"] = types.SimpleNamespace(parse=feedparser_stub.parse)

from src.spiders.rss_spider import RssSpider


FULL_FEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Full Feed</title>
  <entry>
    <title>Structured Entry</title>
    <link href="https://example.com/posts/1" />
    <updated>2026-03-29T08:00:00Z</updated>
    <content type="html"><![CDATA[
      <h2>Overview</h2>
      <p>Hello <a href="/docs/start">world</a>.</p>
      <ul><li>alpha</li><li>beta</li></ul>
      <blockquote>quoted text</blockquote>
      <img src="https://example.com/images/body.jpg" alt="Body image" />
      <p><a href="https://cdn.example.com/poster.png">Poster</a></p>
      <hr />
    ]]></content>
  </entry>
</feed>
"""


SUMMARY_FEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>Summary Feed</title>
    <item>
      <title>Summary Only</title>
      <link>https://example.com/posts/2</link>
      <pubDate>Sun, 29 Mar 2026 10:00:00 GMT</pubDate>
      <description><![CDATA[
        <p>Short summary with <a href="/guide">guide</a>.</p>
      ]]></description>
    </item>
  </channel>
</rss>
"""


IMAGE_FEED_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Image Feed</title>
    <item>
      <title>Image Heavy</title>
      <link>https://example.com/posts/3</link>
      <pubDate>Sun, 29 Mar 2026 11:00:00 GMT</pubDate>
      <description><![CDATA[<p>tiny</p>]]></description>
      <media:thumbnail url="https://img.example.com/cover.jpg" />
      <enclosure url="https://img.example.com/gallery-1.jpg" type="image/jpeg" />
    </item>
  </channel>
</rss>
"""


GBK_FEED_XML = """<?xml version="1.0" encoding="gbk"?>
<rss version="2.0">
  <channel>
    <title>乱码测试</title>
    <item>
      <title>中文标题正常解析</title>
      <link>https://example.com/posts/4</link>
      <pubDate>Sun, 29 Mar 2026 12:00:00 GMT</pubDate>
      <description><![CDATA[<p>这是一段中文摘要。</p>]]></description>
    </item>
  </channel>
</rss>
"""


class SampleRssSpider(RssSpider):
    def __init__(self, feed_bytes: bytes, **rule_overrides):
        rule = {
            "rule_id": "rule_test",
            "task_id": "task_test",
            "task_name": "Test Feed",
            "task_purpose": "Test Purpose",
            "url": "https://example.com/feed.xml",
            "source_type": "rss",
            "enabled": True,
        }
        rule.update(rule_overrides)
        super().__init__(rule)
        self._feed_bytes = feed_bytes

    def _safe_get(self, url: str, *args, **kwargs):
        return SimpleNamespace(
            content=self._feed_bytes,
            status_code=200,
            text=self._feed_bytes.decode("utf-8", errors="ignore"),
        )

    def _extract_detail_page_content(self, url: str):
        return None


class RssSampleFeedTests(unittest.TestCase):
    def test_full_feed_builds_blocks_and_classifies_images(self):
        spider = SampleRssSpider(FULL_FEED_XML.encode("utf-8"))
        articles = spider.fetch_list(limit=1)
        self.assertEqual(len(articles), 1)

        article = articles[0]
        block_types = [block.get("type") for block in article.get("content_blocks", [])]
        categories = {asset.get("category") for asset in article.get("image_assets", [])}

        self.assertIn("title", block_types)
        self.assertIn("list", block_types)
        self.assertIn("quote", block_types)
        self.assertIn("image", block_types)
        self.assertIn("divider", block_types)
        self.assertIn("body", categories)
        self.assertIn("external", categories)
        self.assertIn("[world](https://example.com/docs/start)", article["body_text"])

    def test_summary_feed_keeps_absolute_links(self):
        spider = SampleRssSpider(SUMMARY_FEED_XML.encode("utf-8"))
        articles = spider.fetch_list(limit=1)
        self.assertEqual(len(articles), 1)

        article = articles[0]
        self.assertIn("[guide](https://example.com/guide)", article["body_text"])
        self.assertTrue(article["content_blocks"])
        self.assertEqual(article["content_blocks"][0]["type"], "paragraph")

    def test_image_feed_classifies_cover_and_attachment_images(self):
        spider = SampleRssSpider(IMAGE_FEED_XML.encode("utf-8"))
        articles = spider.fetch_list(limit=1)
        self.assertEqual(len(articles), 1)

        article = articles[0]
        categories = {asset.get("category") for asset in article.get("image_assets", [])}

        self.assertIn("cover", categories)
        self.assertIn("attachment", categories)
        self.assertIn("### 图片", article["body_text"])

    def test_gbk_feed_decodes_without_garbled_text(self):
        spider = SampleRssSpider(GBK_FEED_XML.encode("gbk"))
        articles = spider.fetch_list(limit=1)
        self.assertEqual(len(articles), 1)

        article = articles[0]
        self.assertEqual(article["title"], "中文标题正常解析")
        self.assertIn("这是一段中文摘要", article["body_text"])


if __name__ == "__main__":
    unittest.main()
