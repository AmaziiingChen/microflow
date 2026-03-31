import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))
sys.modules.setdefault("feedparser", importlib.import_module("tests.feedparser_stub"))

from src.services.rule_generator import RuleGeneratorService
from src.spiders.dynamic_spider import DynamicSpider
from src.utils.browser_render import (
    HtmlFetchResult,
    _serialize_playwright_page,
    fetch_html_with_strategy,
)


class BrowserRenderFallbackTests(unittest.TestCase):
    def test_serialize_playwright_page_prefers_shadow_dom_snapshot(self):
        page = Mock()
        page.evaluate.return_value = (
            '<!DOCTYPE html><html><body><x-card>'
            '<div data-microflow-shadow-root="open"><p>shadow body</p></div>'
            "</x-card></body></html>"
        )

        html = _serialize_playwright_page(page)

        self.assertIn('data-microflow-shadow-root="open"', html)
        self.assertIn("shadow body", html)
        page.content.assert_not_called()

    def test_serialize_playwright_page_falls_back_to_page_content(self):
        page = Mock()
        page.evaluate.side_effect = RuntimeError("shadow snapshot failed")
        page.content.return_value = "<html><body>fallback body</body></html>"

        html = _serialize_playwright_page(page)

        self.assertEqual(html, "<html><body>fallback body</body></html>")
        page.content.assert_called_once()

    def test_requests_first_falls_back_to_browser(self):
        call_order = []

        def fake_requests(*_args, **_kwargs):
            call_order.append("requests")
            return HtmlFetchResult(
                success=False,
                engine="requests",
                error_message="403 Forbidden",
            )

        def fake_browser(*_args, **_kwargs):
            call_order.append("browser")
            return HtmlFetchResult(
                success=True,
                html="<html>browser ok</html>",
                engine="browser",
                status_code=200,
            )

        with patch("src.utils.browser_render._fetch_html_via_requests", side_effect=fake_requests):
            with patch(
                "src.utils.browser_render._render_html_in_browser",
                side_effect=fake_browser,
            ):
                result = fetch_html_with_strategy(
                    "https://example.com",
                    strategy="requests_first",
                )

        self.assertTrue(result.success)
        self.assertEqual(result.engine, "browser")
        self.assertEqual(call_order, ["requests", "browser"])

    def test_browser_first_falls_back_to_requests(self):
        call_order = []

        def fake_browser(*_args, **_kwargs):
            call_order.append("browser")
            return HtmlFetchResult(
                success=False,
                engine="browser",
                error_message="浏览器超时",
            )

        def fake_requests(*_args, **_kwargs):
            call_order.append("requests")
            return HtmlFetchResult(
                success=True,
                html="<html>requests ok</html>",
                engine="requests",
                status_code=200,
            )

        with patch(
            "src.utils.browser_render._render_html_in_browser",
            side_effect=fake_browser,
        ):
            with patch("src.utils.browser_render._fetch_html_via_requests", side_effect=fake_requests):
                result = fetch_html_with_strategy(
                    "https://example.com",
                    strategy="browser_first",
                )

        self.assertTrue(result.success)
        self.assertEqual(result.engine, "requests")
        self.assertEqual(call_order, ["browser", "requests"])

    def test_post_request_is_forwarded_to_requests_engine(self):
        with patch("src.utils.browser_render._fetch_html_via_requests") as mock_fetch_requests:
            mock_fetch_requests.return_value = HtmlFetchResult(
                success=True,
                html="<html>post ok</html>",
                engine="requests",
                status_code=200,
            )
            result = fetch_html_with_strategy(
                "https://example.com/search",
                strategy="requests_only",
                request_method="post",
                request_body='{"page":1,"size":20}',
            )

        self.assertTrue(result.success)
        self.assertEqual(
            mock_fetch_requests.call_args.kwargs["request_method"],
            "post",
        )
        self.assertEqual(
            mock_fetch_requests.call_args.kwargs["request_body"],
            '{"page":1,"size":20}',
        )


class DynamicSpiderFetchStrategyTests(unittest.TestCase):
    def make_rule(self):
        return {
            "rule_id": "html_rule_browser_1",
            "task_id": "task_html_browser_1",
            "task_name": "HTML 浏览器兜底测试",
            "task_purpose": "通知公告",
            "url": "https://example.com/news",
            "list_container": "ul.news-list",
            "item_selector": "li",
            "field_selectors": {
                "title": "a::text",
                "url": "a::attr(href)",
            },
            "source_type": "html",
            "fetch_strategy": "browser_first",
            "enabled": True,
        }

    @patch("src.spiders.dynamic_spider.time.sleep", return_value=None)
    @patch("src.spiders.dynamic_spider.fetch_html_with_strategy")
    def test_fetch_list_surfaces_transport_error_from_fetch_strategy(
        self,
        mock_fetch_html,
        _mock_sleep,
    ):
        mock_fetch_html.return_value = HtmlFetchResult(
            success=False,
            engine="browser",
            error_message="站点要求浏览器验证，requests 抓取失败",
        )
        spider = DynamicSpider(self.make_rule())

        articles = spider.fetch_list(limit=5)

        self.assertEqual(articles, [])
        self.assertEqual(spider.last_fetch_status, "error")
        self.assertIn("站点要求浏览器验证", spider.last_fetch_error)
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["strategy"],
            "browser_first",
        )

    @patch("src.spiders.dynamic_spider.time.sleep", return_value=None)
    @patch("src.spiders.dynamic_spider.fetch_html_with_strategy")
    def test_fetch_list_passes_custom_headers_and_cookies(
        self,
        mock_fetch_html,
        _mock_sleep,
    ):
        mock_fetch_html.return_value = HtmlFetchResult(
            success=True,
            engine="requests",
            html="""
            <html>
              <body>
                <ul class="news-list">
                  <li><a href="/article-1">第一条</a></li>
                </ul>
              </body>
            </html>
            """,
            status_code=200,
        )
        rule = self.make_rule()
        rule["fetch_strategy"] = "requests_only"
        rule["request_method"] = "post"
        rule["request_body"] = "page=1&size=20"
        rule["request_headers"] = {
            "Referer": "https://portal.example.com",
            "X-Requested-With": "XMLHttpRequest",
        }
        rule["cookie_string"] = "sid=abc; token=xyz"
        spider = DynamicSpider(rule)

        spider.fetch_list(limit=1)

        kwargs = mock_fetch_html.call_args.kwargs
        self.assertEqual(kwargs["strategy"], "requests_only")
        self.assertEqual(
            kwargs["browser_headers"]["Referer"],
            "https://portal.example.com",
        )
        self.assertEqual(
            kwargs["browser_headers"]["X-Requested-With"],
            "XMLHttpRequest",
        )
        self.assertEqual(kwargs["cookies"]["sid"], "abc")
        self.assertEqual(kwargs["cookies"]["token"], "xyz")
        self.assertEqual(kwargs["request_method"], "post")
        self.assertEqual(kwargs["request_body"], "page=1&size=20")
        self.assertEqual(
            kwargs["headers"]["Referer"],
            "https://portal.example.com",
        )


class RuleGeneratorFetchStrategyTests(unittest.TestCase):
    def make_service(self):
        return RuleGeneratorService(Mock())

    @patch("src.services.rule_generator.fetch_html_with_strategy")
    def test_fetch_html_content_passes_strategy_and_persists_error(self, mock_fetch_html):
        mock_fetch_html.return_value = HtmlFetchResult(
            success=False,
            engine="browser",
            error_message="浏览器渲染超时",
        )
        service = self.make_service()

        html = service._fetch_html_content(
            "https://example.com/page",
            timeout=12,
            fetch_strategy="browser_first",
        )

        self.assertIsNone(html)
        self.assertEqual(service._last_html_fetch_error, "浏览器渲染超时")
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["strategy"],
            "browser_first",
        )
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["request_timeout_seconds"],
            12,
        )
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["browser_timeout_seconds"],
            20,
        )

    @patch("src.services.rule_generator.fetch_html_with_strategy")
    def test_fetch_html_content_passes_custom_headers_and_cookies(self, mock_fetch_html):
        mock_fetch_html.return_value = HtmlFetchResult(
            success=True,
            engine="requests",
            html="<html>ok</html>",
            status_code=200,
        )
        service = self.make_service()

        html = service._fetch_html_content(
            "https://example.com/protected",
            fetch_strategy="requests_only",
            request_method="post",
            request_body='{"page":1}',
            request_headers={
                "Referer": "https://portal.example.com",
                "X-Requested-With": "XMLHttpRequest",
            },
            cookie_string="sid=abc; token=xyz",
        )

        self.assertEqual(html, "<html>ok</html>")
        kwargs = mock_fetch_html.call_args.kwargs
        self.assertEqual(kwargs["strategy"], "requests_only")
        self.assertEqual(
            kwargs["browser_headers"],
            {
                "Referer": "https://portal.example.com",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        self.assertEqual(kwargs["cookies"], {"sid": "abc", "token": "xyz"})
        self.assertEqual(kwargs["request_method"], "post")
        self.assertEqual(kwargs["request_body"], '{"page":1}')
        self.assertEqual(
            kwargs["headers"]["Referer"],
            "https://portal.example.com",
        )

    @patch("src.services.rule_generator.fetch_html_with_strategy")
    def test_test_existing_rule_uses_saved_fetch_strategy(self, mock_fetch_html):
        mock_fetch_html.return_value = HtmlFetchResult(
            success=True,
            engine="browser",
            html="""
            <html>
              <body>
                <ul class="news-list">
                  <li><a href="/article-1">第一条</a></li>
                </ul>
              </body>
            </html>
            """,
            status_code=200,
        )
        service = self.make_service()
        rule = SimpleNamespace(
            url="https://example.com/news",
            list_container="ul.news-list",
            item_selector="li",
            field_selectors={"title": "a::text"},
            fetch_strategy="browser_only",
            request_method="post",
            request_body="page=1",
        )

        sample = service.test_existing_rule(rule, max_items=1)

        self.assertEqual(len(sample), 1)
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["strategy"],
            "browser_only",
        )
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["request_method"],
            "post",
        )
        self.assertEqual(
            mock_fetch_html.call_args.kwargs["request_body"],
            "page=1",
        )


if __name__ == "__main__":
    unittest.main()
