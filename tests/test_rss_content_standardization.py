import unittest

from src.utils.rss_content import markdown_to_blocks, normalize_rss_content


class RssContentStandardizationTests(unittest.TestCase):
    def test_normalize_rss_content_converts_bare_links_to_markdown(self):
        normalized = normalize_rss_content(
            """
            <div>
              <p>请访问 https://example.com/posts/alpha?x=1 获取详情。</p>
              <p>另见 <a href="https://example.com/docs/start">说明文档</a>。</p>
            </div>
            """,
            base_url="https://example.com/feed.xml",
        )

        self.assertIn(
            "[example.com/posts/alpha](https://example.com/posts/alpha?x=1)",
            normalized["markdown"],
        )
        self.assertIn(
            "[说明文档](https://example.com/docs/start)",
            normalized["markdown"],
        )

    def test_markdown_to_blocks_keeps_anchor_and_inline_segments(self):
        blocks = markdown_to_blocks(
            "### 章节标题\n\n"
            "正文含有 [链接](https://example.com) 与 **重点**。\n\n"
            "- 第一项\n- 第二项\n"
        )

        self.assertEqual(blocks[0]["type"], "title")
        self.assertEqual(blocks[0]["anchor_id"], "章节标题")
        self.assertTrue(blocks[0]["segments"])

        paragraph = blocks[1]
        self.assertEqual(paragraph["type"], "paragraph")
        segment_types = [segment["type"] for segment in paragraph["segments"]]
        self.assertIn("link", segment_types)
        self.assertIn("strong", segment_types)

        list_block = blocks[2]
        self.assertEqual(list_block["type"], "list")
        self.assertEqual(len(list_block["item_blocks"]), 2)
        self.assertEqual(list_block["item_blocks"][0]["text"], "第一项")


if __name__ == "__main__":
    unittest.main()
