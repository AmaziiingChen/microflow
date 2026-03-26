"""
动态爬虫 - 基于用户自定义 CSS 选择器规则的通用爬虫

支持运行时加载用户通过 AI 生成的规则，无需硬编码选择器。
"""

import hashlib
import json
import logging
import re
import time
import random
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.spiders.base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


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
        logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
        return selector

    # 处理纯 JS 链接/图片属性
    if selector_lower in js_attr_props:
        selector = js_attr_props[selector_lower]
        logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
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

        # AI 摘要开关
        self.require_ai_summary = rule_dict.get('require_ai_summary', True)

        # 动态爬虫标记（用于 ArticleProcessor 识别）
        self._is_dynamic_spider = True
        self._rule_id = self.rule_id

        logger.info(f"🕷️ DynamicSpider 初始化: {self.task_name} (rule_id={self.rule_id})")

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
            logger.error(f"[{self.SOURCE_NAME}] 未配置目标 URL")
            return []

        articles: List[ArticleData] = []

        try:
            # 1. 获取网页内容
            response = self._safe_get(self.target_url)
            if not response:
                logger.warning(f"[{self.SOURCE_NAME}] 无法获取页面: {self.target_url}")
                return []

            # 2. 解析 HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # 3. 查找列表容器
            container = soup.select_one(self.list_container)
            if not container:
                logger.warning(f"[{self.SOURCE_NAME}] 未找到列表容器: {self.list_container}")
                return []

            # 4. 查找所有列表项
            items = container.select(self.item_selector)
            if not items:
                logger.warning(f"[{self.SOURCE_NAME}] 未找到列表项: {self.item_selector}")
                return []

            logger.info(f"[{self.SOURCE_NAME}] 找到 {len(items)} 个列表项")

            # 5. 应用 limit 限制
            if limit and limit > 0:
                items = items[:limit]

            # 6. 遍历并提取字段
            for idx, item in enumerate(items):
                try:
                    article = self._extract_article_from_item(item, idx)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"[{self.SOURCE_NAME}] 提取第 {idx+1} 项失败: {e}")
                    continue

            logger.info(f"[{self.SOURCE_NAME}] 成功提取 {len(articles)} 篇文章")
            return articles

        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] 抓取失败: {e}", exc_info=True)
            return []

    def _extract_article_from_item(self, item: Any, index: int) -> Optional[ArticleData]:
        """
        从单个列表项中提取文章数据

        Args:
            item: BeautifulSoup 元素
            index: 项目索引（用于生成唯一标识）

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

                fields[field_name] = value

            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 提取字段 {field_name} 失败: {e}")
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
            # 如果没有链接，生成一个唯一标识
            url_hash = hashlib.md5(f"{self.target_url}#{index}#{title}".encode()).hexdigest()[:12]
            url = f"{self.target_url}#item-{url_hash}"

        # 智能识别日期
        date = self._find_field_by_aliases(fields, self.DATE_ALIASES) or ""
        # 🌟 日期清洗：从混合文本中提取纯日期格式
        date = self._extract_clean_date(date)

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
            # 🌟 核心字段：规则 ID（用于溯源）
            'rule_id': self.rule_id,
            # 格式化的内容文本（用于 AI 摘要或直接显示）
            'body_text': self._format_fields_as_text(fields),
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

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        对于动态爬虫，详情页通常不需要单独抓取，
        因为列表页已经包含了所有必要信息。

        如果 URL 指向外部网站，可以尝试抓取详情。

        Args:
            url: 文章 URL

        Returns:
            文章详情字典
        """
        # 检查是否是生成的哈希 URL（不需要抓取详情）
        if '#item-' in url:
            # 返回一个空的详情，表示不需要进一步抓取
            return {
                'title': '',
                'url': url,
                'body_text': '',
                'attachments': [],
                'source_name': self.SOURCE_NAME,
                'exact_time': ''
            }

        # 尝试抓取外部链接的详情
        try:
            response = self._safe_get(url)
            if not response:
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # 尝试提取正文（通用策略）
            body_text = ""
            content_selectors = [
                'article', 'div.content', 'div.article-content',
                'div.post-content', 'div.entry-content', 'main'
            ]

            for selector in content_selectors:
                content = soup.select_one(selector)
                if content:
                    body_text = content.get_text(separator='\n', strip=True)
                    break

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
                'attachments': [],
                'source_name': self.SOURCE_NAME,
                'exact_time': exact_time
            }

        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] 获取详情失败: {url} -> {e}")
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
            merged_headers = {**stealth_headers, **kwargs.pop("headers", {})}

            # 拟人化延迟
            time.sleep(random.uniform(0.3, 0.8))
            res = self.session.get(url, timeout=15, headers=merged_headers, **kwargs)

            # 自动处理编码
            if res.encoding is None or res.encoding == 'ISO-8859-1':
                res.encoding = res.apparent_encoding or 'utf-8'

            return res

        except requests.exceptions.Timeout:
            logger.warning(f"[{self.SOURCE_NAME}] 请求超时: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"[{self.SOURCE_NAME}] 请求失败: {url} -> {e}")
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
            logger.error(f"规则缺少必要字段: {missing}")
            return None

        # 检查是否启用
        if rule_dict.get('enabled') is False:
            logger.debug(f"规则已禁用: {rule_dict.get('rule_id')}")
            return None

        return DynamicSpider(rule_dict)

    except Exception as e:
        logger.error(f"创建动态爬虫失败: {e}")
        return None
