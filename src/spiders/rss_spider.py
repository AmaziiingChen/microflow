"""
RSS/Atom 订阅爬虫 - 标准化 Feed 解析

支持 RSS 1.0、RSS 2.0、Atom 格式，无需 CSS 选择器。
自动解析 feed 结构，支持 AI 摘要与自定义提示词。
"""

import feedparser
import html
import logging
import time
from typing import Dict, List, Optional, Any

from src.spiders.base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class RssSpider(BaseSpider):
    """
    RSS/Atom 订阅爬虫

    支持标准 RSS 2.0、RSS 1.0、Atom 格式。
    无需 CSS 选择器，直接解析 feed 结构。

    规则字典格式（rule_dict）:
    {
        "rule_id": "rule_abc123",
        "task_id": "task_001",
        "task_name": "科技博客",
        "url": "https://example.com/feed.xml",
        "source_type": "rss",
        "require_ai_summary": true,
        "custom_summary_prompt": "",
        "enabled": true
    }
    """

    def __init__(self, rule_dict: Dict[str, Any]):
        """
        初始化 RSS 爬虫

        Args:
            rule_dict: 规则字典，包含订阅元数据
        """
        super().__init__()

        # 保存规则
        self.rule_dict = rule_dict

        # 元数据
        self.rule_id = rule_dict.get('rule_id', 'unknown')
        self.task_id = rule_dict.get('task_id', 'unknown')
        self.task_name = rule_dict.get('task_name', 'RSS 订阅')
        self.task_purpose = rule_dict.get('task_purpose', '')
        self.url = rule_dict.get('url', '')

        # 覆盖基类的 SOURCE_NAME（使用 task_name 作为来源名称）
        self.SOURCE_NAME = self.task_name
        self.BASE_URL = self.url

        # AI 摘要配置
        self.require_ai_summary = rule_dict.get('require_ai_summary', False)
        self.custom_summary_prompt = rule_dict.get('custom_summary_prompt', '')

        # 动态爬虫标记（用于 ArticleProcessor 识别）
        self._is_dynamic_spider = True
        self._rule_id = self.rule_id

        # 🌟 数据源类型标记（用于调度器识别 RSS 并跳过冷启动配额）
        self._source_type = rule_dict.get('source_type', 'rss')

        # 🌟 单次抓取最大条目数（可由规则配置或使用默认值）
        self.max_items = rule_dict.get('max_items')

        logger.info(f"📡 RssSpider 初始化: {self.task_name} ({self.url})")

    def fetch_list(
        self,
        page_num: int = 1,
        section_name: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> List[ArticleData]:
        """
        解析 RSS feed，返回文章列表

        Args:
            page_num: 页码（RSS 忽略此参数）
            section_name: 板块名称（RSS 忽略此参数）
            limit: 抓取数量上限

        Returns:
            文章数据列表
        """
        if not self.url:
            logger.error(f"[{self.SOURCE_NAME}] 未配置 RSS URL")
            return []

        articles: List[ArticleData] = []

        # 🌟 使用带重试和超时的请求获取 RSS 内容
        response = self._safe_get(self.url)
        if not response:
            logger.error(f"[{self.SOURCE_NAME}] 无法获取 RSS 内容: {self.url}")
            return []

        try:
            # 解析 RSS
            logger.info(f"[{self.SOURCE_NAME}] 正在解析 RSS: {self.url}")
            # 使用 response.content（字节）传入，feedparser 会自动处理编码
            feed = feedparser.parse(response.content)

            # 检查解析错误
            if feed.bozo and not feed.entries:
                error_msg = getattr(feed, 'bozo_exception', '未知错误')
                logger.error(f"[{self.SOURCE_NAME}] RSS 解析失败: {error_msg}")
                return []

            # 获取 feed 标题（用于日志）
            feed_title = getattr(feed.feed, 'title', self.SOURCE_NAME)
            logger.info(
                f"[{self.SOURCE_NAME}] 获取到 {len(feed.entries)} 条 RSS 条目 (来自: {feed_title})"
            )

            # 应用数量限制
            entries = feed.entries[:limit] if limit else feed.entries

            # 遍历并解析每个 entry
            for entry in entries:
                try:
                    article = self._parse_entry(entry)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.warning(f"[{self.SOURCE_NAME}] 解析 entry 失败: {e}")
                    continue

            logger.info(f"[{self.SOURCE_NAME}] 成功解析 {len(articles)} 篇文章")
            return articles

        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] RSS 抓取失败: {e}", exc_info=True)
            return []

    def _parse_entry(self, entry) -> Optional[ArticleData]:
        """
        解析单个 RSS entry

        Args:
            entry: feedparser 解析的 entry 对象

        Returns:
            文章数据字典，解析失败返回 None
        """
        try:
            # 标题
            title = getattr(entry, 'title', '无标题')
            if not title or title == '无标题':
                logger.debug(f"[{self.SOURCE_NAME}] entry 缺少标题，跳过")
                return None

            # 链接
            url = getattr(entry, 'link', '')
            if not url:
                logger.debug(f"[{self.SOURCE_NAME}] entry 缺少链接，跳过: {title}")
                return None

            # 日期
            date = self._parse_date(entry)

            # 正文内容
            raw_text = self._extract_content(entry)
            # HTML 实体解码
            raw_text = html.unescape(raw_text) if raw_text else ""

            # 构建文章数据
            article: ArticleData = {
                'title': title,
                'url': url,
                'date': date,
                'source_name': self.SOURCE_NAME,
                # 核心字段：类别（来自任务目的）
                'category': self.task_purpose,
                # 核心字段：部门（来自任务名称）
                'department': self.task_name,
                # 核心字段：是否需要 AI 摘要
                'require_ai_summary': self.require_ai_summary,
                # 核心字段：专属 AI 提示词
                'custom_summary_prompt': self.custom_summary_prompt,
                # 核心字段：规则 ID（用于溯源）
                'rule_id': self.rule_id,
                # 格式化的内容文本（用于 AI 摘要或直接显示）
                'body_text': raw_text,
                # 附件（RSS 通常无附件）
                'attachments': [],
            }

            return article

        except Exception as e:
            logger.warning(f"[{self.SOURCE_NAME}] 解析 entry 失败: {e}")
            return None

    def _parse_date(self, entry) -> str:
        """
        解析日期为 YYYY-MM-DD 格式

        feedparser 已经将日期解析为 time.struct_time，
        我们只需要格式化输出即可。
        若 feedparser 解析失败，尝试从原始字符串字段解析。

        Args:
            entry: feedparser 解析的 entry 对象

        Returns:
            格式化的日期字符串（YYYY-MM-DD），解析失败返回空字符串
        """
        # 优先使用 published_parsed
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                return time.strftime('%Y-%m-%d', entry.published_parsed)
            except (ValueError, TypeError):
                pass

        # 备用：updated_parsed
        if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            try:
                return time.strftime('%Y-%m-%d', entry.updated_parsed)
            except (ValueError, TypeError):
                pass

        # 🌟 回退：从原始字符串字段解析
        from src.utils.date_utils import format_date

        for field in ['published', 'updated', 'pubDate', 'dc:date']:
            raw_date = getattr(entry, field, None)
            if raw_date:
                formatted = format_date(raw_date)
                if formatted:
                    logger.debug(f"[{self.SOURCE_NAME}] 从 {field} 解析日期: {raw_date} -> {formatted}")
                    return formatted

        return ""

    def _extract_content(self, entry) -> str:
        """
        提取正文内容（多格式兼容，HTML 优先）

        优先级：
        1. Atom content（优先选择 HTML 格式）
        2. RSS 2.0 content:encoded
        3. summary/description

        Args:
            entry: feedparser 解析的 entry 对象

        Returns:
            正文内容字符串
        """
        # 1. Atom content：优先选择 HTML 格式
        if hasattr(entry, 'content') and entry.content:
            html_content = None
            fallback = None
            for content in entry.content:
                content_type = getattr(content, 'type', '')
                value = getattr(content, 'value', '')
                if value:
                    # 检查是否为 HTML 格式
                    if 'html' in content_type.lower() or 'xhtml' in content_type.lower():
                        return value  # 找到 HTML 内容，直接返回
                    if fallback is None:
                        fallback = value  # 记录第一个非空内容作为回退
            # 没有 HTML 格式，返回第一个非空内容
            return fallback or ''

        # 2. RSS 2.0 content:encoded（通过 content_encoded 属性访问）
        if hasattr(entry, 'content_encoded') and entry.content_encoded:
            return entry.content_encoded

        # 3. summary（RSS 2.0 的 description）
        if hasattr(entry, 'summary') and entry.summary:
            return entry.summary

        # 4. 兜底：description 属性
        if hasattr(entry, 'description') and entry.description:
            return entry.description

        return ""

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        RSS 订阅的内容已在 fetch_list 中完整获取，无需再抓取详情页。
        此方法不应被调用（ArticleProcessor 会根据 source_type='rss' 跳过详情抓取）。

        Args:
            url: 文章 URL

        Returns:
            None（表示无需详情，内容已在列表中获取）
        """
        # RSS 订阅模式下，内容已在 feed 中获取，返回 None 表示无需详情
        return None


def create_rss_spider_from_rule(rule_dict: Dict[str, Any]) -> Optional[RssSpider]:
    """
    工厂函数：从规则字典创建 RSS 爬虫实例

    Args:
        rule_dict: 规则字典

    Returns:
        RssSpider 实例，如果规则无效则返回 None
    """
    try:
        # 验证必要字段
        if not rule_dict.get('url'):
            logger.error("RSS 规则缺少 URL 字段")
            return None

        # 检查是否启用
        if rule_dict.get('enabled') is False:
            logger.debug(f"RSS 规则已禁用: {rule_dict.get('rule_id')}")
            return None

        return RssSpider(rule_dict)

    except Exception as e:
        logger.error(f"创建 RSS 爬虫失败: {e}")
        return None
