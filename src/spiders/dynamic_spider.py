"""
动态爬虫 - 基于用户自定义 CSS 选择器规则的通用爬虫

支持运行时加载用户通过 AI 生成的规则，无需硬编码选择器。
"""

import json
import logging
import re
import time
import random
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, FeatureNotFound
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.spiders.base_spider import BaseSpider, ArticleData
from src.utils.article_identity import (
    build_stable_article_fingerprint,
    canonicalize_article_url,
)
from src.utils.browser_render import fetch_html_with_strategy, normalize_fetch_strategy
from src.utils.html_rule_strategy import normalize_detail_strategy
from src.utils.http_rule_config import (
    normalize_request_headers,
    normalize_cookie_string,
    parse_cookie_string,
    normalize_request_method,
    normalize_request_body,
)

logger = logging.getLogger(__name__)


def _create_soup(html: str) -> BeautifulSoup:
    """优先使用 lxml，缺失时自动回退到内置 html.parser。"""
    try:
        return BeautifulSoup(html, 'lxml')
    except FeatureNotFound:
        logger.debug("lxml 解析器不可用，回退到 html.parser")
        return BeautifulSoup(html, 'html.parser')


def _normalize_selector(selector: str) -> str:
    """
    规范化 CSS 选择器，修复常见的 AI 生成错误

    将非法的 JS 属性名转换为合法的 BeautifulSoup4 CSS 选择器格式。

    Args:
        selector: 原始选择器字符串

    Returns:
        规范化后的选择器
    """
    if not selector:
        return selector

    selector = selector.strip()
    original = selector  # 保存原始值用于日志

    # 1. 纯 JS 属性名 -> 转换为 ::attr() 或 ::text
    js_text_props = ['textcontent', 'innertext', 'text']
    js_attr_props = {
        'href': '::attr(href)',
        'src': '::attr(src)',
    }

    selector_lower = selector.lower()

    # 处理纯 JS 文本属性
    if selector_lower in js_text_props:
        selector = '::text'
        logger.debug("选择器规范化: '%s' -> '%s'", original, selector)
        return selector

    # 处理纯 JS 链接/图片属性
    if selector_lower in js_attr_props:
        selector = js_attr_props[selector_lower]
        logger.debug("选择器规范化: '%s' -> '%s'", original, selector)
        return selector

    # 2. 处理带标签但用了 JS 属性的情况 (如 'a.href', 'span.textContent')
    if '.' in selector and '::' not in selector:
        parts = selector.rsplit('.', 1)
        if len(parts) == 2:
            tag_part, attr_part = parts
            attr_lower = attr_part.lower()

            if attr_lower in js_text_props:
                selector = f"{tag_part}::text"
                logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
                return selector

            if attr_lower in js_attr_props:
                selector = f"{tag_part}{js_attr_props[attr_lower]}"
                logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
                return selector

    return selector


def _extract_value_with_selector(item, selector: str, field_name: str) -> str:
    """
    从列表项中提取字段值

    支持三种选择器格式：
    1. 普通选择器：'span.date' -> 获取元素的文本
    2. 文本提取：'span.date::text' 或 '::text' -> 获取文本
    3. 属性提取：'a::attr(href)' 或 '::attr(href)' -> 获取属性值

    Args:
        item: BeautifulSoup 元素（列表项）
        selector: CSS 选择器（已规范化）
        field_name: 字段名（用于智能判断 URL 字段）

    Returns:
        提取的值字符串
    """
    from urllib.parse import urljoin

    # 解析选择器中的 ::text 或 ::attr() 后缀
    attr_name = None
    extract_text = False
    css_selector = selector

    if '::attr(' in selector:
        # 提取属性：'a::attr(href)' -> css_selector='a', attr_name='href'
        match = re.match(r'^(.+?)::attr\(([^)]+)\)$', selector)
        if match:
            css_selector = match.group(1).strip()
            attr_name = match.group(2).strip()
        else:
            # 纯属性提取：'::attr(href)' -> css_selector='', attr_name='href'
            match = re.match(r'^::attr\(([^)]+)\)$', selector)
            if match:
                css_selector = ''
                attr_name = match.group(1).strip()
    elif '::text' in selector:
        # 提取文本：'span.date::text' 或 '::text'
        css_selector = selector.replace('::text', '').strip()
        extract_text = True

    # 根据选择器类型确定目标元素
    if css_selector:
        # 有具体选择器，从子节点中查找
        element = item.select_one(css_selector)
        if not element:
            return ""
    else:
        # 纯 '::text' 或 '::attr(href)'，直接操作当前 item
        element = item

    # 提取值
    if attr_name:
        # 提取属性
        value = element.get(attr_name, '') or ''
        if isinstance(value, list):
            value = value[0] if value else ''
        return str(value).strip()
    elif extract_text:
        # 提取文本
        return element.get_text(strip=True)
    else:
        # 默认行为：根据字段名智能判断
        if field_name.lower() in ['url', 'link', 'href']:
            # 链接字段，优先取 href
            return element.get('href', '') or element.get_text(strip=True)
        else:
            # 其他字段，取文本
            return element.get_text(strip=True)


def _resolve_selected_node(item: Any, selector: str) -> Optional[Any]:
    """根据选择器解析出对应节点，供结构化正文提取复用。"""
    css_selector = selector

    if '::attr(' in selector:
        match = re.match(r'^(.+?)::attr\(([^)]+)\)$', selector)
        if match:
            css_selector = match.group(1).strip()
        else:
            return None
    elif '::text' in selector:
        css_selector = selector.replace('::text', '').strip()

    if css_selector:
        return item.select_one(css_selector)
    return item


def _coerce_positive_int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return parsed if parsed >= minimum else default


def _extract_url_from_node(
    node: Any,
    current_url: str,
    *,
    allow_onclick: bool = False,
) -> str:
    if not node:
        return ""

    for attr_name in (
        "href",
        "data-url",
        "data-href",
        "data-next",
        "data-next-url",
        "data-load-more",
        "data-page-url",
        "hx-get",
    ):
        raw_value = str(node.get(attr_name) or "").strip()
        if raw_value and not raw_value.startswith("javascript:"):
            return urljoin(current_url, raw_value)

    if allow_onclick:
        onclick = str(node.get("onclick") or "").strip()
        if onclick:
            match = re.search(
                r"""['"](?P<url>(?:https?:)?//[^'"]+|/[^'"]+|\.\.?/[^'"]+|[^'"]+\?(?:[^'"]+))['"]""",
                onclick,
            )
            if match:
                return urljoin(current_url, match.group("url"))

    return ""


def _looks_like_url_candidate(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    if lowered.startswith(("http://", "https://", "//", "/", "./", "../")):
        return True
    return any(marker in text for marker in ("?", ".htm", ".html", ".php", ".aspx"))


def _normalize_extracted_plain_text(value: str) -> str:
    """清理结构化正文转纯文本时多余的空格，尽量保留中英文可读性。"""
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if not cleaned:
        return ""

    cleaned = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", cleaned)
    cleaned = re.sub(r"\s+(?=[，。！？；：、）】》])", "", cleaned)
    cleaned = re.sub(r"(?<=[（【《])\s+", "", cleaned)
    return cleaned


class DynamicSpider(BaseSpider):
    """
    动态爬虫 - 基于用户自定义规则

    继承自 BaseSpider，使用用户通过 AI 生成的 CSS 选择器规则进行抓取。

    规则字典格式（rule_dict）:
    {
        "rule_id": "rule_abc123",
        "task_id": "task_001",
        "task_name": "科技新闻",
        "url": "https://example.com/news",
        "list_container": "ul.news-list",
        "item_selector": "li",
        "field_selectors": {
            "title": "a.title",
            "url": "a",
            "date": "span.date",
            "summary": "p.desc"
        },
        "require_ai_summary": true,
        "enabled": true
    }
    """

    # 字段别名映射（用于智能识别标题和链接）
    TITLE_ALIASES = ['title', 'name', '标题', '名称', 'headline', 'subject']
    URL_ALIASES = ['url', 'link', 'href', '链接', '地址', 'source']
    DATE_ALIASES = ['date', 'time', 'datetime', '日期', '时间', 'pubdate', 'publish_time']

    def __init__(self, rule_dict: Dict[str, Any]):
        """
        初始化动态爬虫

        Args:
            rule_dict: 规则字典，包含 CSS 选择器和元数据
        """
        super().__init__()

        # 保存规则
        self.rule_dict = rule_dict

        # 元数据
        self.rule_id = rule_dict.get('rule_id', 'unknown')
        self.task_id = rule_dict.get('task_id', 'unknown')
        self.task_name = rule_dict.get('task_name', '动态数据源')
        self.task_purpose = rule_dict.get('task_purpose', '')  # 任务目的/类别
        self.target_url = rule_dict.get('url', '')

        # 覆盖基类的 SOURCE_NAME（使用 task_name 作为来源名称）
        self.SOURCE_NAME = self.task_name
        self.BASE_URL = self.target_url

        # CSS 选择器
        self.list_container = rule_dict.get('list_container', '')
        self.item_selector = rule_dict.get('item_selector', '')
        self.field_selectors = rule_dict.get('field_selectors', {})
        self.fetch_strategy = normalize_fetch_strategy(
            rule_dict.get('fetch_strategy')
        )
        self.request_method = normalize_request_method(
            rule_dict.get('request_method')
        )
        self.request_body = normalize_request_body(
            rule_dict.get('request_body')
        )
        self.request_headers = normalize_request_headers(
            rule_dict.get('request_headers')
        )
        self.cookie_string = normalize_cookie_string(
            rule_dict.get('cookie_string')
        )
        self.request_cookies = parse_cookie_string(self.cookie_string)
        raw_pagination_mode = str(rule_dict.get('pagination_mode') or 'single').strip().lower()
        self.pagination_mode = (
            raw_pagination_mode
            if raw_pagination_mode in {'single', 'next_link', 'url_template', 'load_more'}
            else 'single'
        )
        self.next_page_selector = str(rule_dict.get('next_page_selector') or '').strip()
        self.page_url_template = str(rule_dict.get('page_url_template') or '').strip()
        self.page_start = _coerce_positive_int(rule_dict.get('page_start'), 2, minimum=1)
        self.incremental_max_pages = _coerce_positive_int(
            rule_dict.get('incremental_max_pages'),
            1,
            minimum=1,
        )
        self.load_more_selector = str(rule_dict.get('load_more_selector') or '').strip()
        default_max_pages = 3 if self.pagination_mode != 'single' else 1
        self.max_pages = _coerce_positive_int(
            rule_dict.get('max_pages'),
            default_max_pages,
            minimum=1,
        )

        # AI 摘要开关
        self.require_ai_summary = rule_dict.get('require_ai_summary', True)

        # 🌟 跳过详情页抓取（当列表页已包含所需全部字段时）
        self.detail_strategy = normalize_detail_strategy(
            rule_dict.get('detail_strategy'),
            skip_detail=bool(rule_dict.get('skip_detail', False)),
        )
        self.skip_detail = self.detail_strategy == 'list_only'

        # 🌟 正文来源字段（指定某个字段作为正文，而非拼接所有字段）
        self.body_field = rule_dict.get('body_field')

        # 🌟 详情页提取规则（仅 HTML 动态爬虫有效）
        self.detail_body_selector = str(rule_dict.get('detail_body_selector') or '').strip()
        self.detail_time_selector = str(rule_dict.get('detail_time_selector') or '').strip()
        self.detail_attachment_selector = str(rule_dict.get('detail_attachment_selector') or '').strip()
        self.detail_image_selector = str(rule_dict.get('detail_image_selector') or '').strip()

        # 🌟 专属 AI 提示词
        self.custom_summary_prompt = rule_dict.get('custom_summary_prompt', '')

        # 动态爬虫标记（用于 ArticleProcessor 识别）
        self._is_dynamic_spider = True
        self._rule_id = self.rule_id

        # 🌟 数据源类型标记（用于调度器识别爬虫类型）
        self._source_type = rule_dict.get('source_type', 'html')

        # 🌟 单次抓取最大条目数（可配置，覆盖默认值）
        self.max_items = rule_dict.get('max_items')

        # 🌟 健康状态（供调度器回写规则健康信息）
        self.last_fetch_status = "idle"
        self.last_fetch_error = ""
        self.last_fetched_count = 0
        self.last_field_hit_stats: Dict[str, Dict[str, Any]] = {}
        self._last_transport_error = ""
        self._browser_timeout_seconds = 20
        self._browser_wait_ms = 1200

        logger.info("🕷️ DynamicSpider 初始化: %s (rule_id=%s)", self.task_name, self.rule_id)

    def fetch_list(
        self,
        page_num: int = 1,
        section_name: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> List[ArticleData]:
        """
        获取文章列表

        Args:
            page_num: 页码（动态爬虫目前只支持单页）
            section_name: 板块名称（动态爬虫不使用）
            limit: 抓取数量上限

        Returns:
            文章数据列表
        """
        if not self.target_url:
            logger.error("[%s] 未配置目标 URL", self.SOURCE_NAME)
            self.last_fetch_status = "error"
            self.last_fetch_error = "未配置目标 URL"
            self.last_fetched_count = 0
            return []

        articles: List[ArticleData] = []
        self.last_fetch_error = ""
        self.last_fetched_count = 0
        self.last_field_hit_stats = {}
        effective_limit = self._resolve_fetch_limit(limit)
        seen_article_urls: set[str] = set()
        requested_page_budget = kwargs.get('page_budget')
        page_budget = self._resolve_page_budget(requested_page_budget)
        cancel_event = kwargs.get('cancel_event')

        try:
            if self.pagination_mode in {'next_link', 'load_more'}:
                page_url = self.target_url
                visited_page_urls: set[str] = set()
                page_counter = 0

                while page_url and page_counter < page_budget:
                    if page_url in visited_page_urls:
                        break

                    visited_page_urls.add(page_url)
                    page_counter += 1
                    remaining_limit = (
                        effective_limit - len(articles) if effective_limit > 0 else 0
                    )
                    page_articles, soup, page_error = self._fetch_page_articles(
                        page_url,
                        len(articles),
                        remaining_limit,
                        cancel_event=cancel_event,
                    )
                    if page_error:
                        if not articles:
                            logger.warning("[%s] %s", self.SOURCE_NAME, page_error)
                            self.last_fetch_status = "error"
                            self.last_fetch_error = page_error
                            return []
                        logger.warning("[%s] 分页抓取中断: %s", self.SOURCE_NAME, page_error)
                        break

                    self._append_unique_articles(
                        articles,
                        page_articles,
                        seen_article_urls,
                    )

                    if effective_limit > 0 and len(articles) >= effective_limit:
                        break

                    if self.pagination_mode == 'next_link':
                        page_url = self._extract_next_page_url(soup, page_url)
                    else:
                        page_url = self._extract_load_more_url(soup, page_url)
            else:
                page_urls = [self.target_url]

                if self.pagination_mode == 'url_template':
                    page_urls.extend(self._build_template_page_urls(page_budget))

                for page_url in page_urls[:page_budget]:
                    remaining_limit = (
                        effective_limit - len(articles) if effective_limit > 0 else 0
                    )
                    page_articles, _soup, page_error = self._fetch_page_articles(
                        page_url,
                        len(articles),
                        remaining_limit,
                        cancel_event=cancel_event,
                    )
                    if page_error:
                        if not articles:
                            logger.warning("[%s] %s", self.SOURCE_NAME, page_error)
                            self.last_fetch_status = "error"
                            self.last_fetch_error = page_error
                            return []
                        logger.warning("[%s] 分页抓取中断: %s", self.SOURCE_NAME, page_error)
                        break

                    self._append_unique_articles(
                        articles,
                        page_articles,
                        seen_article_urls,
                    )

                    if effective_limit > 0 and len(articles) >= effective_limit:
                        break

            logger.info("[%s] 成功提取 %d 篇文章", self.SOURCE_NAME, len(articles))
            self.last_fetched_count = len(articles)
            self.last_fetch_status = "healthy" if articles else "empty"
            self.last_fetch_error = ""
            self.last_field_hit_stats = self._build_field_hit_stats(articles)
            return articles

        except Exception as e:
            logger.error("[%s] 抓取失败: %s", self.SOURCE_NAME, e, exc_info=True)
            self.last_fetch_status = "error"
            self.last_fetch_error = str(e)
            self.last_fetched_count = 0
            self.last_field_hit_stats = {}
            return []

    def _build_field_hit_stats(
        self,
        articles: List[ArticleData],
    ) -> Dict[str, Dict[str, Any]]:
        total_count = len(articles or [])
        if total_count <= 0:
            return {}

        stats: Dict[str, Dict[str, Any]] = {}
        for field_name, selector in (self.field_selectors or {}).items():
            hit_count = 0
            for article in articles:
                dynamic_fields = (
                    article.get("dynamic_fields")
                    if isinstance(article.get("dynamic_fields"), dict)
                    else {}
                )
                value = str(dynamic_fields.get(field_name) or "").strip()
                if value:
                    hit_count += 1
            stats[str(field_name)] = {
                "hit_count": hit_count,
                "total_count": total_count,
                "hit_rate": round(hit_count / total_count, 4),
                "selector": str(selector or "").strip(),
            }
        return stats

    def _resolve_fetch_limit(self, limit: Optional[int]) -> int:
        runtime_limit = (
            _coerce_positive_int(limit, 0, minimum=1) if limit is not None else 0
        )
        configured_limit = (
            _coerce_positive_int(self.max_items, 0, minimum=1)
            if self.max_items is not None
            else 0
        )

        if runtime_limit and configured_limit:
            return min(runtime_limit, configured_limit)
        return runtime_limit or configured_limit or 0

    def _resolve_page_budget(self, requested_budget: Any = None) -> int:
        resolved_budget = _coerce_positive_int(
            requested_budget,
            self.max_pages,
            minimum=1,
        )
        return min(max(resolved_budget, 1), max(self.max_pages, 1))

    def _build_template_page_urls(self, page_budget: Optional[int] = None) -> List[str]:
        if self.pagination_mode != 'url_template' or not self.page_url_template:
            return []

        if '{page}' not in self.page_url_template:
            logger.warning(
                "[%s] 页码模板缺少 {page} 占位符，已降级为单页抓取",
                self.SOURCE_NAME,
            )
            return []

        page_urls: List[str] = []
        seen_urls: set[str] = {self.target_url}
        total_extra_pages = max(self._resolve_page_budget(page_budget) - 1, 0)

        for offset in range(total_extra_pages):
            page_number = self.page_start + offset
            built_url = self.page_url_template.replace('{page}', str(page_number))
            page_url = self.safe_urljoin(self.target_url, built_url)
            if not page_url or page_url in seen_urls:
                continue
            seen_urls.add(page_url)
            page_urls.append(page_url)

        return page_urls

    def _extract_next_page_url(self, soup: BeautifulSoup, current_url: str) -> str:
        if self.pagination_mode != 'next_link' or not self.next_page_selector:
            return ""

        try:
            selector = _normalize_selector(self.next_page_selector)
            raw_next_url = _extract_value_with_selector(soup, selector, 'url')
        except Exception as e:
            logger.debug("[%s] 下一页选择器提取失败: %s", self.SOURCE_NAME, e)
            return ""

        next_url = str(raw_next_url or '').strip()
        if not next_url or next_url.startswith('javascript:'):
            return ""
        return self.safe_urljoin(current_url, next_url)

    def _extract_load_more_url(self, soup: BeautifulSoup, current_url: str) -> str:
        if self.pagination_mode != 'load_more' or not self.load_more_selector:
            return ""

        try:
            selector = _normalize_selector(self.load_more_selector)
            raw_candidate = _extract_value_with_selector(soup, selector, 'url')
            candidate = str(raw_candidate or '').strip()
            if (
                _looks_like_url_candidate(candidate)
                and candidate != current_url
                and not candidate.startswith('javascript:')
            ):
                return self.safe_urljoin(current_url, candidate)

            button_node = soup.select_one(self.load_more_selector)
            load_more_url = _extract_url_from_node(
                button_node,
                current_url,
                allow_onclick=True,
            )
            if load_more_url and load_more_url != current_url:
                return load_more_url
        except Exception as e:
            logger.debug("[%s] 加载更多选择器提取失败: %s", self.SOURCE_NAME, e)
            return ""

        return ""

    def _fetch_page_articles(
        self,
        page_url: str,
        start_index: int,
        remaining_limit: int = 0,
        cancel_event: Any = None,
    ) -> tuple[List[ArticleData], Optional[BeautifulSoup], str]:
        response = self._safe_get(
            page_url,
            cancel_event=cancel_event,
            use_list_request_config=True,
        )
        if not response:
            return [], None, self._last_transport_error or f"无法获取页面: {page_url}"

        soup = _create_soup(response.text)

        container = soup.select_one(self.list_container)
        if not container:
            return [], soup, f"未找到列表容器: {self.list_container}"

        items = container.select(self.item_selector)
        if not items:
            return [], soup, f"未找到列表项: {self.item_selector}"

        logger.info("[%s] 页面 %s 找到 %d 个列表项", self.SOURCE_NAME, page_url, len(items))

        if remaining_limit > 0:
            items = items[:remaining_limit]

        page_articles: List[ArticleData] = []
        for offset, item in enumerate(items):
            try:
                article = self._extract_article_from_item(
                    item,
                    start_index + offset,
                    page_url=page_url,
                )
                if article:
                    page_articles.append(article)
            except Exception as e:
                logger.warning(
                    "[%s] 提取页面 %s 第 %d 项失败: %s",
                    self.SOURCE_NAME,
                    page_url,
                    offset + 1,
                    e,
                )
                continue

        return page_articles, soup, ""

    def _append_unique_articles(
        self,
        target: List[ArticleData],
        incoming: List[ArticleData],
        seen_article_urls: set[str],
    ) -> None:
        for article in incoming:
            article_url = str(article.get('url') or '').strip()
            if article_url and article_url in seen_article_urls:
                continue
            if article_url:
                seen_article_urls.add(article_url)
            target.append(article)

    def _extract_article_from_item(
        self,
        item: Any,
        index: int,
        page_url: Optional[str] = None,
    ) -> Optional[ArticleData]:
        """
        从单个列表项中提取文章数据

        Args:
            item: BeautifulSoup 元素
            index: 项目索引（用于生成唯一标识）
            page_url: 当前列表页 URL（用于生成稳定哈希）

        Returns:
            文章数据字典
        """
        # 提取所有字段
        fields: Dict[str, str] = {}

        for field_name, raw_selector in self.field_selectors.items():
            try:
                # 🌟 规范化选择器（修复 AI 生成的非法格式）
                selector = _normalize_selector(raw_selector)

                # 🌟 使用统一的提取函数
                value = _extract_value_with_selector(item, selector, field_name)

                # 🌟 如果是 URL 字段，转换为绝对 URL
                if field_name.lower() in self.URL_ALIASES and value:
                    if not value.startswith(('http://', 'https://')):
                        value = urljoin(self.target_url, value)
                    value = canonicalize_article_url(value)

                fields[field_name] = value

            except Exception as e:
                logger.debug("[%s] 提取字段 %s 失败: %s", self.SOURCE_NAME, field_name, e)
                fields[field_name] = ""

        # 智能识别标题
        title = self._find_field_by_aliases(fields, self.TITLE_ALIASES)
        if not title:
            # 如果没有明确的标题字段，使用第一个非空字段
            for v in fields.values():
                if v and len(v) > 2:
                    title = v[:100]  # 限制长度
                    break
        if not title:
            title = f"动态数据 #{index + 1}"

        # 智能识别链接
        url = self._find_field_by_aliases(fields, self.URL_ALIASES)
        if not url:
            # 如果没有链接，生成一个与列表顺序无关的稳定虚拟标识
            hash_base_url = page_url or self.target_url
            url_hash = build_stable_article_fingerprint(
                source_name=self.SOURCE_NAME,
                page_url=hash_base_url,
                title=title,
                date=self._find_field_by_aliases(fields, self.DATE_ALIASES) or "",
                body_text=fields.get(self.body_field, '') if self.body_field and self.body_field in fields else self._format_fields_as_text(fields),
                fields=fields,
                extra_parts=[self.task_name, self.task_purpose],
            )
            url = f"{hash_base_url}#item-{url_hash}"
        else:
            url = canonicalize_article_url(url)

        # 智能识别日期
        date = self._find_field_by_aliases(fields, self.DATE_ALIASES) or ""
        # 🌟 日期清洗：从混合文本中提取纯日期格式
        date = self._extract_clean_date(date)

        body_text = (
            fields.get(self.body_field, '')
            if self.body_field and self.body_field in fields
            else self._format_fields_as_text(fields)
        )
        body_html = ""
        raw_markdown = ""
        content_blocks: List[Dict[str, Any]] = []
        image_assets: List[Dict[str, Any]] = []
        images: List[str] = []
        attachments: List[Dict[str, Any]] = []

        if self.body_field and self.body_field in self.field_selectors:
            try:
                body_selector = _normalize_selector(self.field_selectors[self.body_field])
                body_node = _resolve_selected_node(item, body_selector)
            except Exception as e:
                logger.debug("[%s] 正文结构化选择器解析失败: %s", self.SOURCE_NAME, e)
                body_node = None

            if body_node:
                normalized_fragment = self._normalize_detail_fragment(
                    str(body_node),
                    page_url or self.target_url,
                )
                body_text = (
                    _normalize_extracted_plain_text(
                        str(normalized_fragment.get("plain_text") or "")
                    )
                    or _normalize_extracted_plain_text(body_node.get_text(" ", strip=True))
                    or fields.get(self.body_field, '')
                )
                body_html = str(normalized_fragment.get("body_html") or "")
                raw_markdown = str(normalized_fragment.get("raw_markdown") or "").strip()
                content_blocks = (
                    normalized_fragment.get("content_blocks")
                    if isinstance(normalized_fragment.get("content_blocks"), list)
                    else []
                )
                image_assets = (
                    normalized_fragment.get("image_assets")
                    if isinstance(normalized_fragment.get("image_assets"), list)
                    else []
                )
                images = (
                    normalized_fragment.get("images")
                    if isinstance(normalized_fragment.get("images"), list)
                    else []
                )
                attachments = (
                    normalized_fragment.get("attachments")
                    if isinstance(normalized_fragment.get("attachments"), list)
                    else []
                )
                attachments = self._merge_attachments(
                    attachments,
                    self._extract_attachments(body_node, page_url or self.target_url),
                )

        # 构建完整的文章数据
        article: ArticleData = {
            'title': title,
            'url': url,
            'date': date,
            'source_name': self.SOURCE_NAME,
            # 🌟 核心字段：类别（来自任务目的）
            'category': self.task_purpose,
            # 🌟 核心字段：部门（来自任务名称）
            'department': self.task_name,
            # 🌟 核心字段：动态字段 JSON（供后续处理使用）
            'dynamic_fields': fields,
            # 🌟 核心字段：是否需要 AI 摘要
            'require_ai_summary': self.require_ai_summary,
            # 🌟 核心字段：专属 AI 提示词
            'custom_summary_prompt': self.custom_summary_prompt,
            # 🌟 核心字段：规则 ID（用于溯源）
            'rule_id': self.rule_id,
            # 🌟 格式化的内容文本（用于 AI 摘要或直接显示）
            # 如果配置了 body_field 且字段存在，使用该字段；否则拼接所有字段
            'body_text': body_text,
            'body_html': body_html,
            'raw_markdown': raw_markdown,
            'content_blocks': content_blocks,
            'image_assets': image_assets,
            'images': images,
            'attachments': attachments,
            'detail_strategy': self.detail_strategy,
        }

        return article

    def _find_field_by_aliases(self, fields: Dict[str, str], aliases: List[str]) -> str:
        """
        根据别名列表查找字段值（不区分大小写）

        Args:
            fields: 字段字典
            aliases: 别名列表

        Returns:
            找到的字段值，未找到返回空字符串
        """
        for alias in aliases:
            # 精确匹配
            if alias in fields:
                return fields[alias]
            # 不区分大小写匹配
            for key, value in fields.items():
                if key.lower() == alias.lower():
                    return value
        return ""

    def _extract_clean_date(self, raw_date: str) -> str:
        """
        从混合文本中提取纯日期格式

        支持的日期格式：
        - 2025-09-08
        - 2025/09/08
        - 2025.09.08
        - 2025年9月8日
        - 09-08（仅月日，默认当年）
        - 9月8日

        Args:
            raw_date: 原始日期字符串（可能包含其他文本）

        Returns:
            清洗后的日期字符串（YYYY-MM-DD 格式），如果无法解析则返回原字符串
        """
        if not raw_date or not raw_date.strip():
            return ""

        text = raw_date.strip()

        # 1. 尝试匹配完整日期格式 YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
        patterns_full = [
            r'(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})',  # 2025-09-08, 2025/9/8, 2025.09.08
        ]
        for pattern in patterns_full:
            match = re.search(pattern, text)
            if match:
                date_str = match.group(1)
                # 统一分隔符为 -
                date_str = re.sub(r'[-/.]', '-', date_str)
                # 补齐月日为两位数
                parts = date_str.split('-')
                if len(parts) == 3:
                    year, month, day = parts
                    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 2. 尝试匹配中文日期格式 YYYY年M月D日
        match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 3. 尝试匹配仅月日格式 M月D日（默认当年）
        match = re.search(r'(\d{1,2})月(\d{1,2})日', text)
        if match:
            month, day = match.groups()
            from datetime import datetime
            year = datetime.now().year
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 4. 尝试匹配仅月日格式 MM-DD（默认当年）
        match = re.search(r'(\d{1,2})[-/.](\d{1,2})(?!\d)', text)
        if match:
            month, day = match.groups()
            from datetime import datetime
            year = datetime.now().year
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 无法解析，返回原字符串
        return text

    def _format_fields_as_text(self, fields: Dict[str, str]) -> str:
        """
        将动态字段格式化为易读的文本

        Args:
            fields: 字段字典

        Returns:
            格式化后的文本
        """
        lines = []
        for key, value in fields.items():
            if value and value.strip():
                lines.append(f"**{key}**: {value}")
        return "\n".join(lines) if lines else ""

    def _has_custom_detail_rules(self) -> bool:
        return any(
            [
                self.detail_body_selector,
                self.detail_time_selector,
                self.detail_attachment_selector,
                self.detail_image_selector,
            ]
        )

    def _normalize_exact_time_value(self, value: str) -> str:
        clean_value = str(value or "").strip()
        if not clean_value:
            return ""
        return (
            clean_value.replace('年', '-')
            .replace('月', '-')
            .replace('日', '')
            .replace('/', '-')
        )

    def _extract_detail_time_with_selector(self, soup: BeautifulSoup) -> str:
        if not self.detail_time_selector:
            return ""

        try:
            selector = _normalize_selector(self.detail_time_selector)
            raw_value = _extract_value_with_selector(soup, selector, "exact_time")
            return self._normalize_exact_time_value(raw_value)
        except Exception as e:
            logger.debug("[%s] 详情时间选择器提取失败: %s", self.SOURCE_NAME, e)
            return ""

    def _merge_attachments(
        self,
        target: List[Dict[str, Any]],
        incoming: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        existing_urls = {
            str(item.get("url") or "").strip()
            for item in target
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        }
        for item in incoming:
            if not isinstance(item, dict):
                continue
            attachment_url = str(item.get("url") or "").strip()
            if not attachment_url or attachment_url in existing_urls:
                continue
            existing_urls.add(attachment_url)
            target.append(item)
        return target

    def _extract_attachments_with_selector(
        self, soup: BeautifulSoup, url: str
    ) -> List[Dict[str, Any]]:
        selector = self.detail_attachment_selector
        if not selector:
            return []

        try:
            nodes = soup.select(selector)
        except Exception as e:
            logger.debug("[%s] 详情附件选择器无效: %s", self.SOURCE_NAME, e)
            return []

        attachments: List[Dict[str, Any]] = []
        download_pattern = re.compile(
            r'download\.jsp|DownloadAttachUrl|\.(pdf|docx?|xlsx?|zip|rar)',
            re.IGNORECASE,
        )

        for node in nodes:
            if getattr(node, "name", None) == "a":
                href = node.get("href")
                if not href or href.startswith("javascript:"):
                    continue
                if not download_pattern.search(href):
                    continue

                full_url = self.safe_urljoin(url, href)
                name = node.get_text(strip=True) or "查看附件"
                if self._is_attachment_blacklisted(name):
                    continue

                self._merge_attachments(
                    attachments,
                    [
                        {
                            "name": name,
                            "url": full_url,
                            "download_type": self._get_download_type(),
                        }
                    ],
                )
                continue

            scoped_attachments = self._extract_attachments(node, url)
            self._merge_attachments(attachments, scoped_attachments)

        return attachments

    def _extract_images_with_selector(
        self, soup: BeautifulSoup, url: str
    ) -> Dict[str, List[Dict[str, Any]] | List[str]]:
        selector = self.detail_image_selector
        if not selector:
            return {"image_assets": [], "images": []}

        try:
            nodes = soup.select(selector)
        except Exception as e:
            logger.debug("[%s] 详情图片选择器无效: %s", self.SOURCE_NAME, e)
            return {"image_assets": [], "images": []}

        image_assets: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        for node in nodes:
            candidate_images = (
                [node] if getattr(node, "name", None) == "img" else node.find_all("img")
            )
            for img_tag in candidate_images:
                raw_url = img_tag.get("data-src") or img_tag.get("src")
                image_url = self.safe_urljoin(url, str(raw_url or "").strip())
                if not image_url or image_url in seen_urls:
                    continue

                seen_urls.add(image_url)
                caption = ""
                parent = getattr(img_tag, "parent", None)
                if parent and getattr(parent, "name", None) == "figure":
                    caption_tag = parent.find("figcaption")
                    if caption_tag:
                        caption = caption_tag.get_text(strip=True)

                image_assets.append(
                    {
                        "url": image_url,
                        "name": str(img_tag.get("alt") or "图片").strip() or "图片",
                        "source": "html_detail_rule",
                        "category": "body",
                        "alt": str(img_tag.get("alt") or "").strip(),
                        "caption": caption or str(img_tag.get("title") or "").strip(),
                        "index": len(image_assets),
                    }
                )

        return {
            "image_assets": image_assets,
            "images": [str(asset.get("url") or "").strip() for asset in image_assets],
        }

    def _build_empty_detail_result(self, url: str, title: str = "") -> ArticleData:
        return {
            'title': title,
            'url': url,
            'body_text': '',
            'body_html': '',
            'raw_markdown': '',
            'content_blocks': [],
            'image_assets': [],
            'images': [],
            'attachments': [],
            'source_name': self.SOURCE_NAME,
            'exact_time': '',
            'dynamic_fields': {},
        }

    def _fetch_detail_with_custom_rules(self, url: str) -> Optional[ArticleData]:
        if not self._has_custom_detail_rules():
            return None

        response = self._safe_get(url)
        if not response:
            return None

        try:
            soup = _create_soup(response.text)
        except Exception as e:
            logger.debug("[%s] 自定义详情规则解析失败: %s", self.SOURCE_NAME, e)
            return None

        title = soup.title.get_text(strip=True).split('-')[0].strip() if soup.title else ""
        detail = self._build_empty_detail_result(url, title=title)

        if self.detail_body_selector:
            try:
                body_node = soup.select_one(self.detail_body_selector)
            except Exception as e:
                logger.debug("[%s] 详情正文选择器无效: %s", self.SOURCE_NAME, e)
                body_node = None

            if body_node:
                normalized_content = self._normalize_detail_fragment(str(body_node), url)
                detail_payload = {
                    "body_text": (
                        _normalize_extracted_plain_text(
                            str(normalized_content.get("plain_text") or "")
                        )
                        or _normalize_extracted_plain_text(body_node.get_text(" ", strip=True))
                    ),
                    "body_html": normalized_content.get("body_html", ""),
                    "raw_markdown": normalized_content.get("raw_markdown", ""),
                    "content_blocks": normalized_content.get("content_blocks", []),
                    "image_assets": normalized_content.get("image_assets", []),
                    "images": normalized_content.get("images", []),
                    "attachments": (
                        normalized_content.get("attachments")
                        if isinstance(normalized_content.get("attachments"), list)
                        else []
                    ),
                }
                detail_payload["attachments"] = self._merge_attachments(
                    detail_payload["attachments"],
                    self._extract_attachments(body_node, url),
                )
                iframe_payload = self._extract_iframe_detail_payload(body_node, url)
                if iframe_payload:
                    detail_payload = self._merge_detail_payload(
                        detail_payload,
                        iframe_payload,
                    )
                detail["body_text"] = (
                    _normalize_extracted_plain_text(
                        str(detail_payload.get("body_text") or "")
                    )
                )
                detail["body_html"] = detail_payload.get("body_html", "")
                detail["raw_markdown"] = detail_payload.get("raw_markdown", "")
                detail["content_blocks"] = detail_payload.get("content_blocks", [])
                detail["image_assets"] = detail_payload.get("image_assets", [])
                detail["images"] = detail_payload.get("images", [])
                detail["attachments"] = detail_payload.get("attachments", [])

        detail["exact_time"] = self._extract_detail_time_with_selector(soup)

        if self.detail_attachment_selector:
            detail["attachments"] = self._merge_attachments(
                (
                    detail.get("attachments")
                    if isinstance(detail.get("attachments"), list)
                    else []
                ),
                self._extract_attachments_with_selector(soup, url),
            )

        if self.detail_image_selector:
            selector_images = self._extract_images_with_selector(soup, url)
            existing_assets = (
                detail.get("image_assets")
                if isinstance(detail.get("image_assets"), list)
                else []
            )
            existing_urls = {
                str(asset.get("url") or "").strip()
                for asset in existing_assets
                if isinstance(asset, dict) and str(asset.get("url") or "").strip()
            }
            for asset in selector_images.get("image_assets", []):
                if not isinstance(asset, dict):
                    continue
                image_url = str(asset.get("url") or "").strip()
                if not image_url or image_url in existing_urls:
                    continue
                existing_urls.add(image_url)
                existing_assets.append(asset)
            detail["image_assets"] = existing_assets
            detail["images"] = list(
                dict.fromkeys(
                    [
                        *(
                            detail.get("images")
                            if isinstance(detail.get("images"), list)
                            else []
                        ),
                        *(
                            selector_images.get("images")
                            if isinstance(selector_images.get("images"), list)
                            else []
                        ),
                    ]
                )
            )

        has_useful_content = any(
            [
                str(detail.get("body_text") or "").strip(),
                str(detail.get("exact_time") or "").strip(),
                bool(detail.get("attachments")),
                bool(detail.get("images")),
            ]
        )
        return detail if has_useful_content else None

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        对于动态爬虫，`list_only` 模式会直接跳过详情抓取；
        否则调用父类 BaseSpider 的详情抓取逻辑，获得更可靠的正文提取。

        Args:
            url: 文章 URL

        Returns:
            文章详情字典
        """
        # 检查是否是生成的哈希 URL（不需要抓取详情）
        if '#item-' in url:
            return {
                'title': '',
                'url': url,
                'body_text': '',
                'attachments': [],
                'source_name': self.SOURCE_NAME,
                'exact_time': '',
                'dynamic_fields': {}
            }

        # 列表页足够模式：直接跳过详情抓取
        if self.detail_strategy == 'list_only':
            return {
                'title': '',
                'url': url,
                'body_text': '',
                'attachments': [],
                'source_name': self.SOURCE_NAME,
                'exact_time': '',
                'dynamic_fields': {}
            }

        custom_detail = self._fetch_detail_with_custom_rules(url)
        if custom_detail:
            logger.debug("[%s] 使用自定义详情规则提取正文: %s", self.SOURCE_NAME, url)
            return custom_detail

        # 🌟 调用父类的详情抓取方法（包含多容器正文提取、附件解析、时间解析等）
        try:
            detail = super().fetch_detail(url)
            if detail:
                # 确保返回的数据结构包含 dynamic_fields
                if 'dynamic_fields' not in detail:
                    detail['dynamic_fields'] = {}
                return detail
            else:
                # 父类方法未匹配到容器，使用通用选择器降级
                logger.debug("[%s] 父类方法未匹配，使用通用选择器降级: %s", self.SOURCE_NAME, url)
                return self._fallback_fetch_detail(url)
        except Exception as e:
            logger.warning("[%s] 调用父类 fetch_detail 失败: %s -> %s", self.SOURCE_NAME, url, e)
            return self._fallback_fetch_detail(url)

    def _fallback_fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        降级方案：使用通用选择器提取正文

        当父类 BaseSpider.fetch_detail 无法匹配特定容器时使用。

        Args:
            url: 文章 URL

        Returns:
            文章详情字典
        """
        try:
            response = self._safe_get(url)
            if not response:
                return None

            soup = _create_soup(response.text)

            # 🌟 移除干扰元素
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
                tag.decompose()

            # 通用正文选择器
            body_text = ""
            content_selectors = [
                'article',
                'div.v_news_content', 'div#vsb_content',  # 高校常用
                'div.content_m', 'div.news_conent_two_text',
                'div.article-content', 'div.post-content',
                'div.entry-content', 'div.content',
                'main', 'div.main'
            ]

            for selector in content_selectors:
                content = soup.select_one(selector)
                if content:
                    body_text = content.get_text(" ", strip=True)
                    if len(body_text) > 50:  # 确保提取到有效内容
                        break

            body_html = ""
            raw_markdown = ""
            content_blocks: List[Dict[str, Any]] = []
            image_assets: List[Dict[str, Any]] = []
            images: List[str] = []

            if content:
                normalized_content = self._normalize_detail_fragment(str(content), url)
                detail_payload = {
                    "body_text": (
                        _normalize_extracted_plain_text(
                            str(normalized_content.get("plain_text") or "")
                        )
                        or _normalize_extracted_plain_text(body_text)
                    ),
                    "body_html": str(normalized_content.get("body_html") or ""),
                    "raw_markdown": str(normalized_content.get("raw_markdown") or "").strip(),
                    "content_blocks": (
                        normalized_content.get("content_blocks")
                        if isinstance(normalized_content.get("content_blocks"), list)
                        else []
                    ),
                    "image_assets": (
                        normalized_content.get("image_assets")
                        if isinstance(normalized_content.get("image_assets"), list)
                        else []
                    ),
                    "images": (
                        normalized_content.get("images")
                        if isinstance(normalized_content.get("images"), list)
                        else []
                    ),
                    "attachments": (
                        normalized_content.get("attachments")
                        if isinstance(normalized_content.get("attachments"), list)
                        else []
                    ),
                }
                detail_payload["attachments"] = self._merge_attachments(
                    detail_payload["attachments"],
                    self._extract_attachments(content, url),
                )
                iframe_payload = self._extract_iframe_detail_payload(content, url)
                if iframe_payload:
                    detail_payload = self._merge_detail_payload(
                        detail_payload,
                        iframe_payload,
                    )
                body_text = (
                    _normalize_extracted_plain_text(
                        str(detail_payload.get("body_text") or "")
                    )
                )
                body_html = str(detail_payload.get("body_html") or "")
                raw_markdown = str(detail_payload.get("raw_markdown") or "").strip()
                content_blocks = (
                    detail_payload.get("content_blocks")
                    if isinstance(detail_payload.get("content_blocks"), list)
                    else []
                )
                image_assets = (
                    detail_payload.get("image_assets")
                    if isinstance(detail_payload.get("image_assets"), list)
                    else []
                )
                images = (
                    detail_payload.get("images")
                    if isinstance(detail_payload.get("images"), list)
                    else []
                )
                attachments = (
                    detail_payload.get("attachments")
                    if isinstance(detail_payload.get("attachments"), list)
                    else []
                )
            else:
                attachments = []

            # 尝试提取精确时间
            exact_time = ""
            time_patterns = [
                r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?(?:\s*\d{1,2}:\d{1,2}(?::\d{1,2})?)?)'
            ]
            for pattern in time_patterns:
                match = re.search(pattern, soup.get_text()[:2000])
                if match:
                    exact_time = match.group(1).replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
                    break

            return {
                'title': soup.title.get_text(strip=True) if soup.title else "",
                'url': url,
                'body_text': body_text,
                'body_html': body_html,
                'raw_markdown': raw_markdown,
                'content_blocks': content_blocks,
                'image_assets': image_assets,
                'images': images,
                'attachments': attachments,
                'source_name': self.SOURCE_NAME,
                'exact_time': exact_time,
                'dynamic_fields': {}
            }

        except Exception as e:
            logger.warning("[%s] 降级详情提取失败: %s -> %s", self.SOURCE_NAME, url, e)
            return None

    def _safe_get(self, url: str, **kwargs) -> Optional[requests.Response]:
        """
        安全的 HTTP GET 请求（带重试和随机延迟）

        Args:
            url: 目标 URL
            **kwargs: 传递给 requests.get 的额外参数

        Returns:
            Response 对象，失败返回 None
        """
        try:
            use_list_request_config = bool(
                kwargs.pop("use_list_request_config", False)
            )
            # 🌟 强力伪装：模拟真实浏览器行为，绕过基础反爬
            stealth_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "max-age=0",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }
            # 合并 headers（kwargs 中的 headers 优先级更高）
            incoming_headers = normalize_request_headers(kwargs.pop("headers", {}))
            browser_headers = {
                **self.request_headers,
                **incoming_headers,
            }
            merged_headers = {
                **stealth_headers,
                **self.request_headers,
                **incoming_headers,
            }
            incoming_cookies = kwargs.pop("cookies", {})
            merged_cookies = {
                **self.request_cookies,
                **(
                    parse_cookie_string(incoming_cookies)
                    if not isinstance(incoming_cookies, dict)
                    else {
                        str(key).strip(): str(value).strip()
                        for key, value in incoming_cookies.items()
                        if str(key).strip()
                    }
                ),
            }
            request_timeout = int(kwargs.pop("timeout", 15) or 15)
            cancel_event = kwargs.pop("cancel_event", None)

            # 拟人化延迟
            time.sleep(random.uniform(0.3, 0.8))
            fetch_result = fetch_html_with_strategy(
                url,
                strategy=self.fetch_strategy,
                session=self.session,
                headers=merged_headers,
                browser_headers=browser_headers,
                cookies=merged_cookies,
                request_method=self.request_method if use_list_request_config else "get",
                request_body=self.request_body if use_list_request_config else "",
                request_timeout_seconds=request_timeout,
                browser_timeout_seconds=self._browser_timeout_seconds,
                browser_wait_ms=self._browser_wait_ms,
                request_kwargs=kwargs,
                cancel_event=cancel_event,
            )
            if not fetch_result.success:
                self._last_transport_error = str(
                    fetch_result.error_message or f"无法获取页面: {url}"
                ).strip()
                logger.warning(
                    "[%s] 页面抓取失败 (%s): %s",
                    self.SOURCE_NAME,
                    self.fetch_strategy,
                    self._last_transport_error,
                )
                return None

            self._last_transport_error = ""
            res = requests.Response()
            res.status_code = int(fetch_result.status_code or 200)
            res.url = url
            res.encoding = 'utf-8'
            res._content = str(fetch_result.html or "").encode('utf-8')
            res.headers["X-MicroFlow-Fetch-Engine"] = str(
                fetch_result.engine or self.fetch_strategy
            )
            return res

        except requests.exceptions.Timeout:
            logger.warning("[%s] 请求超时: %s", self.SOURCE_NAME, url)
            self._last_transport_error = f"请求超时: {url}"
            return None
        except requests.exceptions.RequestException as e:
            logger.warning("[%s] 请求失败: %s -> %s", self.SOURCE_NAME, url, e)
            self._last_transport_error = str(e)
            return None
        except Exception as e:
            logger.warning("[%s] 请求异常: %s -> %s", self.SOURCE_NAME, url, e)
            self._last_transport_error = str(e)
            return None


def create_dynamic_spider_from_rule(rule_dict: Dict[str, Any]) -> Optional[DynamicSpider]:
    """
    工厂函数：从规则字典创建动态爬虫实例

    Args:
        rule_dict: 规则字典

    Returns:
        DynamicSpider 实例，如果规则无效则返回 None
    """
    try:
        # 验证必要字段
        required_fields = ['url', 'list_container', 'item_selector', 'field_selectors']
        missing = [f for f in required_fields if not rule_dict.get(f)]
        if missing:
            logger.error("规则缺少必要字段: %s", missing)
            return None

        # 检查是否启用
        if rule_dict.get('enabled') is False:
            logger.debug("规则已禁用: %s", rule_dict.get('rule_id'))
            return None

        return DynamicSpider(rule_dict)

    except Exception as e:
        logger.error("创建动态爬虫失败: %s", e)
        return None
