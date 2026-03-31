import importlib
import sys
import threading
import unittest
import types
from unittest.mock import patch

sys.modules.setdefault("feedparser", importlib.import_module("tests.feedparser_stub"))
sys.modules.setdefault("webview", types.SimpleNamespace(windows=[]))

from src.core.article_processor import ArticleContext
from src.core.scheduler import SpiderScheduler


class FakeDB:
    def __init__(self, existing_count=0, latest_cursor=None):
        self.existing_count = existing_count
        self.latest_cursor = latest_cursor
        self.frontiers = {}

    def get_article_count_by_source(self, _source_name):
        return self.existing_count

    def get_latest_article_cursor_by_source(self, _source_name):
        return self.latest_cursor

    def get_crawl_frontier_url(self, source_name, section_name=None):
        return self.frontiers.get((source_name, str(section_name or "").strip()), "")

    def upsert_crawl_frontier(
        self, source_name, section_name=None, frontier_url="", frontier_cursor=""
    ):
        _ = frontier_cursor
        key = (source_name, str(section_name or "").strip())
        self.frontiers[key] = frontier_url
        return True


class DummyProcessor:
    def __init__(self):
        self.submitted = []
        self.skip_existing_urls = set()
        self.url_checks = []

    def create_context(self, article, source_name, section_name=None):
        return ArticleContext(
            url=article.get("url", ""),
            title=article.get("title", ""),
            date=article.get("date", ""),
            source_name=source_name,
            section_name=section_name,
            raw_text=article.get("body_text", ""),
            category=article.get("category", ""),
            department=source_name,
        )

    def should_skip_by_title(self, _title):
        return False, ""

    def should_skip_by_url(self, url, _is_manual):
        self.url_checks.append(url)
        return url in self.skip_existing_urls

    def should_skip_by_date(self, _date, _mode, _today_str):
        return False

    def submit(self, spider, ctx, mode, today_str, is_manual):
        self.submitted.append(
            {
                "source": spider.SOURCE_NAME,
                "title": ctx.title,
                "url": ctx.url,
                "date": ctx.date,
                "detail": ctx.detail,
                "mode": mode,
                "today_str": today_str,
                "is_manual": is_manual,
            }
        )
        return True

    def get_stats(self):
        return {}

    def get_queue_size(self):
        return 0


class DummySpider:
    def __init__(self, source_name, section_articles, detail_map=None):
        self.SOURCE_NAME = source_name
        self.SECTIONS = {name: name for name in section_articles}
        self._section_articles = section_articles
        self._detail_map = detail_map or {}
        self.fetch_kwargs = []

    def fetch_list(self, page_num=1, section_name=None, limit=None, **_kwargs):
        _ = page_num
        self.fetch_kwargs.append(
            {
                "section_name": section_name,
                "limit": limit,
                **_kwargs,
            }
        )
        articles = list(self._section_articles.get(section_name, []))
        if limit is not None:
            return articles[:limit]
        return articles

    def fetch_detail(self, url):
        return self._detail_map.get(url)


class SchedulerIncrementalGuardrailsTests(unittest.TestCase):
    def make_scheduler(self, processor):
        with patch.object(SpiderScheduler, "_init_spiders", lambda self: None):
            scheduler = SpiderScheduler(processor)
        scheduler._progress_lock = threading.Lock()
        scheduler._cancel_event.clear()
        scheduler.active_spiders = []
        return scheduler

    def test_cold_start_selects_global_newest_candidate(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "未来技术学院",
            {
                "新闻中心": [
                    {"title": "旧新闻", "url": "https://example.com/old", "date": "2025-07-11"}
                ],
                "行政通知": [
                    {"title": "最新通知", "url": "https://example.com/new", "date": "2026-03-27"}
                ],
            },
        )

        fake_db = FakeDB(existing_count=0)
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(len(processor.submitted), 1)
        self.assertEqual(processor.submitted[0]["title"], "最新通知")
        self.assertEqual(
            fake_db.frontiers[("未来技术学院", "行政通知")],
            "https://example.com/new",
        )

    def test_continuous_mode_recovers_gap_article_within_repair_window(self):
        processor = DummyProcessor()
        processor.skip_existing_urls = {"https://example.com/frontier"}
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "工程物理学院",
            {
                "新闻动态": [
                    {"title": "已存在前沿", "url": "https://example.com/frontier", "date": "2026-03-25"},
                    {"title": "中间缺口文章", "url": "https://example.com/gap", "date": "2026-03-24"},
                ]
            },
        )

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-25 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(
            processor.url_checks,
            ["https://example.com/frontier", "https://example.com/gap"],
        )
        self.assertEqual(
            [item["url"] for item in processor.submitted],
            ["https://example.com/gap"],
        )

    def test_continuous_mode_repair_window_bypasses_time_cutoff_for_gap_article(self):
        processor = DummyProcessor()
        processor.skip_existing_urls = {"https://example.com/frontier"}
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "公文通",
            {
                "通知公告": [
                    {"title": "最新已入库", "url": "https://example.com/frontier", "date": "2026-03-25"},
                    {"title": "被物理删除的旧文章", "url": "https://example.com/gap-old", "date": "2026-03-20"},
                ]
            },
        )

        fake_db = FakeDB(existing_count=20, latest_cursor="2026-03-25 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(
            [item["url"] for item in processor.submitted],
            ["https://example.com/gap-old"],
        )

    def test_manual_continuous_mode_repairs_gap_before_frontier_without_backfill(self):
        processor = DummyProcessor()
        existing_urls = [f"https://example.com/existing-{idx}" for idx in range(10)]
        frontier_url = "https://example.com/frontier-11"
        processor.skip_existing_urls = set(existing_urls + [frontier_url])
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "城市交通与物流学院",
            {
                "通知公告": [
                    {
                        "title": f"已存在文章 {idx}",
                        "url": existing_urls[idx],
                        "date": "2026-03-25",
                    }
                    for idx in range(10)
                ]
                + [
                    {
                        "title": "默认窗口外但边界内缺口",
                        "url": "https://example.com/gap-10",
                        "date": "2026-03-24",
                    },
                    {
                        "title": "手动修复边界",
                        "url": frontier_url,
                        "date": "2026-03-23",
                    },
                    {
                        "title": "边界外旧文章",
                        "url": "https://example.com/older-than-frontier",
                        "date": "2026-03-18",
                    }
                ]
            },
        )

        fake_db = FakeDB(existing_count=30, latest_cursor="2026-03-25 10:00:00")
        fake_db.frontiers[("城市交通与物流学院", "通知公告")] = frontier_url
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=True,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(spider.fetch_kwargs[0]["limit"], 50)
        self.assertEqual(
            [item["url"] for item in processor.submitted],
            ["https://example.com/gap-10"],
        )

    def test_continuous_mode_recovers_undated_gap_article_by_detail_time(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "城市交通与物流学院",
            {
                "通知公告": [
                    {"title": "无日期旧通知", "url": "https://example.com/undated-old", "date": ""}
                ]
            },
            detail_map={
                "https://example.com/undated-old": {
                    "title": "无日期旧通知",
                    "url": "https://example.com/undated-old",
                    "exact_time": "2026-03-20 08:00:00",
                }
            },
        )

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-25 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(
            [item["url"] for item in processor.submitted],
            ["https://example.com/undated-old"],
        )

    def test_continuous_mode_allows_same_day_newer_article_via_detail_time(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "新材料与新能源学院",
            {
                "学院动态": [
                    {"title": "同日更晚新文章", "url": "https://example.com/same-day-new", "date": "2026-03-25"}
                ]
            },
            detail_map={
                "https://example.com/same-day-new": {
                    "title": "同日更晚新文章",
                    "url": "https://example.com/same-day-new",
                    "exact_time": "2026-03-25 18:00:00",
                    "body_text": "detail body",
                    "attachments": [],
                }
            },
        )

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-25 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(len(processor.submitted), 1)
        self.assertEqual(
            processor.submitted[0]["detail"]["exact_time"],
            "2026-03-25 18:00:00",
        )

    def test_continuous_mode_allows_small_number_of_undated_articles_on_weak_date_sites(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "弱日期站点",
            {
                "通知公告": [
                    {"title": "无日期新通知 1", "url": "https://example.com/a", "date": ""},
                    {"title": "无日期新通知 2", "url": "https://example.com/b", "date": ""},
                    {"title": "无日期新通知 3", "url": "https://example.com/c", "date": ""},
                    {"title": "无日期旧通知 4", "url": "https://example.com/d", "date": ""},
                ]
            },
        )

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-25 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 3)
        self.assertEqual(
            [item["url"] for item in processor.submitted],
            [
                "https://example.com/a",
                "https://example.com/b",
                "https://example.com/c",
            ],
        )

    def test_dynamic_html_spider_uses_shallower_page_budget_in_incremental_mode(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "自定义网页源",
            {
                None: [
                    {"title": "第一页新内容", "url": "https://example.com/new", "date": "2026-03-30"}
                ]
            },
        )
        spider._is_dynamic = True
        spider._source_type = "html"
        spider.pagination_mode = "url_template"
        spider.max_pages = 5
        spider.incremental_max_pages = 2

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-29 10:00:00")
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(spider.fetch_kwargs[0]["page_budget"], 2)

    def test_dynamic_html_spider_uses_full_page_budget_in_cold_start(self):
        processor = DummyProcessor()
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "自定义网页源",
            {
                None: [
                    {"title": "冷启动内容", "url": "https://example.com/new", "date": "2026-03-30"}
                ]
            },
        )
        spider._is_dynamic = True
        spider._source_type = "html"
        spider.pagination_mode = "load_more"
        spider.max_pages = 4
        spider.incremental_max_pages = 1

        fake_db = FakeDB(existing_count=0)
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=False,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(spider.fetch_kwargs[0]["page_budget"], 4)

    def test_manual_dynamic_html_spider_uses_full_page_budget_for_deep_repair(self):
        processor = DummyProcessor()
        processor.skip_existing_urls = {
            "https://example.com/existing-frontier",
            "https://example.com/manual-frontier",
        }
        scheduler = self.make_scheduler(processor)
        spider = DummySpider(
            "自定义网页源",
            {
                None: [
                    {
                        "title": "已存在前沿",
                        "url": "https://example.com/existing-frontier",
                        "date": "2026-03-30",
                    },
                    {
                        "title": "历史缺口",
                        "url": "https://example.com/gap",
                        "date": "2026-03-29",
                    },
                    {
                        "title": "手动修复边界",
                        "url": "https://example.com/manual-frontier",
                        "date": "2026-03-28",
                    },
                ]
            },
        )
        spider._is_dynamic = True
        spider._source_type = "html"
        spider.pagination_mode = "load_more"
        spider.max_pages = 4
        spider.incremental_max_pages = 1

        fake_db = FakeDB(existing_count=5, latest_cursor="2026-03-29 10:00:00")
        fake_db.frontiers[("自定义网页源", "")] = "https://example.com/manual-frontier"
        with patch("src.core.scheduler.db", fake_db):
            _source_name, count, errors = scheduler._process_spider(
                spider=spider,
                mode="continuous",
                today_str="2026-03-30",
                is_manual=True,
            )

        self.assertEqual(errors, [])
        self.assertEqual(count, 1)
        self.assertEqual(spider.fetch_kwargs[0]["page_budget"], 4)
        self.assertEqual(spider.fetch_kwargs[0]["limit"], 50)


if __name__ == "__main__":
    unittest.main()
