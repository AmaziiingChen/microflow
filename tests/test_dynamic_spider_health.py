import importlib
from types import SimpleNamespace
import sys
import unittest
from bs4 import BeautifulSoup

sys.modules.setdefault("feedparser", importlib.import_module("tests.feedparser_stub"))

from src.spiders.dynamic_spider import DynamicSpider


class DynamicSpiderHealthTests(unittest.TestCase):
    def make_rule(self):
        return {
            "rule_id": "html_rule_1",
            "task_id": "task_html_1",
            "task_name": "HTML 测试规则",
            "task_purpose": "通知公告",
            "url": "https://example.com/news",
            "list_container": "ul.news-list",
            "item_selector": "li",
            "field_selectors": {
                "title": "a::text",
                "url": "a::attr(href)",
                "date": ".date::text",
            },
            "source_type": "html",
            "enabled": True,
        }

    def test_fetch_list_marks_rule_healthy_on_success(self):
        spider = DynamicSpider(self.make_rule())
        spider._safe_get = lambda _url, **_kwargs: SimpleNamespace(
            text="""
            <html>
              <body>
                <ul class="news-list">
                  <li>
                    <a href="/article-1">第一条通知</a>
                    <span class="date">2026-03-30</span>
                  </li>
                </ul>
              </body>
            </html>
            """
        )

        articles = spider.fetch_list(limit=5)

        self.assertEqual(len(articles), 1)
        self.assertEqual(spider.last_fetch_status, "healthy")
        self.assertEqual(spider.last_fetched_count, 1)
        self.assertEqual(spider.last_fetch_error, "")

    def test_fetch_list_marks_rule_error_when_container_missing(self):
        spider = DynamicSpider(self.make_rule())
        spider._safe_get = lambda _url, **_kwargs: SimpleNamespace(
            text="<html><body><div>empty</div></body></html>"
        )

        articles = spider.fetch_list(limit=5)

        self.assertEqual(articles, [])
        self.assertEqual(spider.last_fetch_status, "error")
        self.assertEqual(spider.last_fetched_count, 0)
        self.assertIn("未找到列表容器", spider.last_fetch_error)

    def test_fetch_detail_uses_custom_detail_selectors(self):
        rule = {
            **self.make_rule(),
            "detail_body_selector": ".article-body",
            "detail_time_selector": ".meta time::attr(datetime)",
            "detail_attachment_selector": ".article-attachments",
            "detail_image_selector": ".article-gallery",
        }
        spider = DynamicSpider(rule)
        spider._safe_get = lambda _url, **_kwargs: SimpleNamespace(
            text="""
            <html>
              <head>
                <title>示例详情 - 深圳技术大学</title>
              </head>
              <body>
                <div class="meta">
                  <time datetime="2026/03/30 08:45">2026年3月30日 08:45</time>
                </div>
                <div class="article-body">
                  <h2>会议安排</h2>
                  <p>请相关老师按时参加。</p>
                  <table>
                    <tr><th>事项</th><th>地点</th></tr>
                    <tr><td>专题会</td><td>C3-804</td></tr>
                  </table>
                </div>
                <div class="article-attachments">
                  <a href="/files/notice.pdf">会议通知附件</a>
                </div>
                <div class="article-gallery">
                  <figure>
                    <img src="/images/cover.jpg" alt="封面图" />
                    <figcaption>图注说明</figcaption>
                  </figure>
                </div>
              </body>
            </html>
            """
        )

        detail = spider.fetch_detail("https://example.com/detail/1")

        self.assertIsNotNone(detail)
        self.assertEqual(detail["title"], "示例详情")
        self.assertIn("会议安排", detail["body_text"])
        self.assertIn("请相关老师按时参加。", detail["body_text"])
        self.assertEqual(detail["exact_time"], "2026-03-30 08:45")
        self.assertEqual(len(detail["attachments"]), 1)
        self.assertEqual(
            detail["attachments"][0]["url"],
            "https://example.com/files/notice.pdf",
        )
        self.assertEqual(detail["attachments"][0]["name"], "会议通知附件")
        self.assertEqual(detail["images"], ["https://example.com/images/cover.jpg"])
        self.assertEqual(len(detail["image_assets"]), 1)
        self.assertEqual(detail["image_assets"][0]["caption"], "图注说明")
        self.assertIn("## 会议安排", detail["raw_markdown"])
        self.assertIn("<table", detail["raw_markdown"])
        self.assertTrue(detail["body_html"])
        self.assertTrue(detail["raw_markdown"])

    def test_extract_article_body_field_preserves_structured_markdown(self):
        rule = {
            **self.make_rule(),
            "field_selectors": {
                "title": "a::text",
                "url": "a::attr(href)",
                "date": ".date::text",
                "summary": ".summary::text",
            },
            "body_field": "summary",
            "detail_strategy": "list_only",
        }
        spider = DynamicSpider(rule)
        item = BeautifulSoup(
            """
            <li>
              <a href="/article-structured">结构化通知</a>
              <span class="date">2026-03-30</span>
              <div class="summary">
                <h3>一、会议安排</h3>
                <p>请<strong>相关老师</strong>按时参加，并关注<em>材料提交</em>。</p>
                <blockquote>所有材料需提前一天提交。</blockquote>
                <ul>
                  <li>准备议程</li>
                  <li>确认会场</li>
                </ul>
                <figure>
                  <img src="/images/inline.jpg" alt="议程图" />
                  <figcaption>会议议程图</figcaption>
                </figure>
                <a href="/files/notice.pdf">会议通知附件</a>
              </div>
            </li>
            """,
            "html.parser",
        ).select_one("li")

        article = spider._extract_article_from_item(item, index=0, page_url=spider.target_url)

        self.assertIsNotNone(article)
        self.assertIn("请相关老师按时参加，并关注材料提交。", article["body_text"])
        self.assertIn("所有材料需提前一天提交。", article["body_text"])
        self.assertNotIn("请 相关老师", article["body_text"])
        self.assertIn("### 一、会议安排", article["raw_markdown"])
        self.assertIn("**相关老师**", article["raw_markdown"])
        self.assertIn("*材料提交*", article["raw_markdown"])
        self.assertIn("> 所有材料需提前一天提交。", article["raw_markdown"])
        self.assertIn("- 准备议程", article["raw_markdown"])
        self.assertEqual(article["images"], ["https://example.com/images/inline.jpg"])
        self.assertEqual(len(article["image_assets"]), 1)
        self.assertEqual(article["image_assets"][0]["caption"], "会议议程图")
        self.assertEqual(
            article["attachments"][0]["url"],
            "https://example.com/files/notice.pdf",
        )

    def test_fetch_detail_collects_inline_attachments_without_attachment_selector(self):
        rule = {
            **self.make_rule(),
            "detail_body_selector": ".article-body",
        }
        spider = DynamicSpider(rule)
        spider._safe_get = lambda _url, **_kwargs: SimpleNamespace(
            text="""
            <html>
              <body>
                <div class="article-body">
                  <h2>下载专区</h2>
                  <p><a href="/files/guide.docx">操作指引</a></p>
                </div>
              </body>
            </html>
            """
        )

        detail = spider.fetch_detail("https://example.com/detail/2")

        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["attachments"]), 1)
        self.assertEqual(
            detail["attachments"][0]["url"],
            "https://example.com/files/guide.docx",
        )

    def test_fetch_detail_merges_same_origin_iframe_content(self):
        rule = {
            **self.make_rule(),
            "detail_body_selector": ".article-body",
        }
        spider = DynamicSpider(rule)
        pages = {
            "https://example.com/detail/iframe": """
                <html>
                  <body>
                    <div class="article-body">
                      <p>这是主页面导语。</p>
                      <iframe src="/embedded/detail-body.html"></iframe>
                    </div>
                  </body>
                </html>
            """,
            "https://example.com/embedded/detail-body.html": """
                <html>
                  <body>
                    <div class="article-content">
                      <h3>补充说明</h3>
                      <p>这是 iframe 内的正文内容。</p>
                      <p><a href="/files/iframe.pdf">iframe 附件</a></p>
                    </div>
                  </body>
                </html>
            """,
        }
        spider._safe_get = lambda url, **_kwargs: SimpleNamespace(text=pages[url])

        detail = spider.fetch_detail("https://example.com/detail/iframe")

        self.assertIsNotNone(detail)
        self.assertIn("这是主页面导语。", detail["body_text"])
        self.assertIn("这是 iframe 内的正文内容。", detail["body_text"])
        self.assertIn("### 补充说明", detail["raw_markdown"])
        self.assertEqual(len(detail["attachments"]), 1)
        self.assertEqual(
            detail["attachments"][0]["url"],
            "https://example.com/files/iframe.pdf",
        )

    def test_extract_article_normalizes_tracking_url(self):
        spider = DynamicSpider(self.make_rule())
        item = BeautifulSoup(
            """
            <li>
              <a href="/article-1?id=42&utm_source=wechat&fbclid=abc123">第一条通知</a>
              <span class="date">2026-03-30</span>
            </li>
            """,
            "html.parser",
        ).select_one("li")

        article = spider._extract_article_from_item(item, index=0, page_url=spider.target_url)

        self.assertIsNotNone(article)
        self.assertEqual(
            article["url"],
            "https://example.com/article-1?id=42",
        )

    def test_extract_article_virtual_url_is_stable_without_real_link(self):
        rule = {
            **self.make_rule(),
            "field_selectors": {
                "title": ".title::text",
                "date": ".date::text",
                "summary": ".summary::text",
            },
        }
        spider = DynamicSpider(rule)
        item = BeautifulSoup(
            """
            <li>
              <span class="title">无链接文章</span>
              <span class="date">2026-03-30</span>
              <p class="summary">这里是正文摘要</p>
            </li>
            """,
            "html.parser",
        ).select_one("li")

        first = spider._extract_article_from_item(item, index=0, page_url=spider.target_url)
        second = spider._extract_article_from_item(item, index=7, page_url=spider.target_url)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["url"], second["url"])
        self.assertIn("#item-", first["url"])

    def test_fetch_list_supports_next_link_pagination_with_max_pages_guard(self):
        rule = {
            **self.make_rule(),
            "pagination_mode": "next_link",
            "next_page_selector": "a.next::attr(href)",
            "max_pages": 2,
        }
        spider = DynamicSpider(rule)
        requested_urls = []
        pages = {
            "https://example.com/news": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-1">第一页通知</a><span class="date">2026-03-30</span></li>
                  </ul>
                  <a class="next" href="/news?page=2">下一页</a>
                </body></html>
            """,
            "https://example.com/news?page=2": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-2">第二页通知</a><span class="date">2026-03-29</span></li>
                  </ul>
                  <a class="next" href="/news?page=3">下一页</a>
                </body></html>
            """,
            "https://example.com/news?page=3": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-3">第三页通知</a><span class="date">2026-03-28</span></li>
                  </ul>
                </body></html>
            """,
        }

        spider._safe_get = lambda url, **_kwargs: (
            requested_urls.append(url) or SimpleNamespace(text=pages[url])
        )

        articles = spider.fetch_list(limit=10)

        self.assertEqual([item["title"] for item in articles], ["第一页通知", "第二页通知"])
        self.assertEqual(
            requested_urls,
            ["https://example.com/news", "https://example.com/news?page=2"],
        )

    def test_fetch_list_supports_url_template_pagination(self):
        rule = {
            **self.make_rule(),
            "pagination_mode": "url_template",
            "page_url_template": "https://example.com/news?page={page}",
            "page_start": 2,
            "max_pages": 3,
        }
        spider = DynamicSpider(rule)
        requested_urls = []
        pages = {
            "https://example.com/news": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-1">第一页通知</a><span class="date">2026-03-30</span></li>
                  </ul>
                </body></html>
            """,
            "https://example.com/news?page=2": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-2">第二页通知</a><span class="date">2026-03-29</span></li>
                  </ul>
                </body></html>
            """,
            "https://example.com/news?page=3": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-3">第三页通知</a><span class="date">2026-03-28</span></li>
                  </ul>
                </body></html>
            """,
        }

        spider._safe_get = lambda url, **_kwargs: (
            requested_urls.append(url) or SimpleNamespace(text=pages[url])
        )

        articles = spider.fetch_list(limit=10)

        self.assertEqual(
            [item["title"] for item in articles],
            ["第一页通知", "第二页通知", "第三页通知"],
        )
        self.assertEqual(
            requested_urls,
            [
                "https://example.com/news",
                "https://example.com/news?page=2",
                "https://example.com/news?page=3",
            ],
        )

    def test_fetch_list_supports_load_more_pagination(self):
        rule = {
            **self.make_rule(),
            "pagination_mode": "load_more",
            "load_more_selector": "button.load-more",
            "max_pages": 2,
        }
        spider = DynamicSpider(rule)
        requested_urls = []
        pages = {
            "https://example.com/news": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-1">第一页通知</a><span class="date">2026-03-30</span></li>
                  </ul>
                  <button class="load-more" data-url="/news?page=2">加载更多</button>
                </body></html>
            """,
            "https://example.com/news?page=2": """
                <html><body>
                  <ul class="news-list">
                    <li><a href="/article-2">第二页通知</a><span class="date">2026-03-29</span></li>
                  </ul>
                </body></html>
            """,
        }

        spider._safe_get = lambda url, **_kwargs: (
            requested_urls.append(url) or SimpleNamespace(text=pages[url])
        )

        articles = spider.fetch_list(limit=10)

        self.assertEqual(
            [item["title"] for item in articles],
            ["第一页通知", "第二页通知"],
        )
        self.assertEqual(
            requested_urls,
            ["https://example.com/news", "https://example.com/news?page=2"],
        )


if __name__ == "__main__":
    unittest.main()
