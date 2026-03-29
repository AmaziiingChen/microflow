"""
RSS/Atom 订阅爬虫 - 标准化 Feed 解析

支持 RSS 1.0、RSS 2.0、Atom 格式，无需 CSS 选择器。
自动解析 feed 结构，支持 AI 摘要与自定义提示词。
"""

import feedparser
import logging
import time
from typing import Dict, List, Optional, Any

from bs4 import BeautifulSoup

from src.spiders.base_spider import BaseSpider, ArticleData
from src.utils.rule_ai_config import normalize_rule_ai_config
from src.utils.rss_strategy import resolve_rss_rule_strategy
from src.utils.rss_content import (
    normalize_rss_content,
    markdown_to_blocks,
    attach_image_asset_metadata,
    _looks_like_image,
    _get_hostname,
)

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

    # 当 RSS feed 本身只给摘要时，正文长度通常会很短
    _MIN_FEED_MARKDOWN_LENGTH = 160
    _MIN_FEED_TEXT_LENGTH = 120

    # 详情页正文容器选择器：优先匹配主流博客/新闻站点
    _DETAIL_CONTAINER_SELECTORS = [
        "article",
        "main",
        "[role='main']",
        "[itemprop='articleBody']",
        ".article-content",
        ".article-body",
        ".articleBody",
        ".entry-content",
        ".post-content",
        ".post-body",
        ".news-content",
        ".story-content",
        ".content",
        ".markdown-body",
    ]

    def __init__(self, rule_dict: Dict[str, Any]):
        """
        初始化 RSS 爬虫

        Args:
            rule_dict: 规则字典，包含订阅元数据
        """
        super().__init__()

        # 保存规则
        self.rule_dict = normalize_rule_ai_config(rule_dict)
        self.strategy = resolve_rss_rule_strategy(self.rule_dict)

        # 元数据
        self.rule_id = self.rule_dict.get('rule_id', 'unknown')
        self.task_id = self.rule_dict.get('task_id', 'unknown')
        self.task_name = self.rule_dict.get('task_name', 'RSS 订阅')
        self.task_purpose = self.rule_dict.get('task_purpose', '')
        self.url = self.rule_dict.get('url', '')

        # 覆盖基类的 SOURCE_NAME（使用 task_name 作为来源名称）
        self.SOURCE_NAME = self.task_name
        self.BASE_URL = self.url

        # AI 摘要配置
        self.enable_ai_formatting = bool(self.rule_dict.get('enable_ai_formatting', False))
        self.enable_ai_summary = bool(self.rule_dict.get('enable_ai_summary', False))
        self.source_profile = str(self.strategy.get("profile") or "").strip()
        self.source_template_id = str(self.strategy.get("template_id") or "").strip()
        self.formatting_prompt = str(
            self.strategy.get("effective_formatting_prompt") or ""
        ).strip()
        self.summary_prompt = str(
            self.strategy.get("effective_summary_prompt") or ""
        ).strip()
        self.custom_summary_prompt = str(
            self.rule_dict.get('custom_summary_prompt') or self.summary_prompt or self.formatting_prompt
        ).strip()
        self.require_ai_summary = bool(
            self.enable_ai_formatting or self.enable_ai_summary
        )

        # 动态爬虫标记（用于 ArticleProcessor 识别）
        self._is_dynamic_spider = True
        self._rule_id = self.rule_id

        # 🌟 数据源类型标记（用于调度器识别 RSS 并跳过冷启动配额）
        self._source_type = self.rule_dict.get('source_type', 'rss')

        # 🌟 单次抓取最大条目数（可由规则配置或使用默认值）
        self.max_items = self.strategy.get("effective_max_items")
        self.last_fetch_status = "idle"
        self.last_fetch_error = ""
        self.last_fetched_count = 0

        logger.info(f"📡 RssSpider 初始化: {self.task_name} ({self.url})")

    def _is_thin_rss_content(self, normalized: Dict[str, Any]) -> bool:
        """判断 feed 返回的内容是否过薄，若过薄则尝试回源详情页。"""
        markdown = str(normalized.get("markdown") or "")
        plain_text = str(normalized.get("plain_text") or "")
        image_count = len(normalized.get("images") or [])

        if len(markdown.strip()) >= self._MIN_FEED_MARKDOWN_LENGTH:
            return False

        if len(plain_text.strip()) >= self._MIN_FEED_TEXT_LENGTH and image_count > 1:
            return False

        return True

    def _extract_detail_page_content(self, url: str) -> Optional[Dict[str, Any]]:
        """
        回源文章详情页，提取更完整的正文、图片和附件。

        仅在 RSS feed 内容过薄时启用，用来补足站点只给摘要/封面图的情况。
        """
        response = self._safe_get(url)
        if not response:
            return None

        if getattr(response, "status_code", 200) >= 400:
            logger.debug(
                f"[{self.SOURCE_NAME}] 详情页返回异常状态 {response.status_code}，跳过回源: {url}"
            )
            return None

        soup = BeautifulSoup(response.text, "lxml")

        candidates = []
        for selector in self._DETAIL_CONTAINER_SELECTORS:
            for node in soup.select(selector):
                text = node.get_text(" ", strip=True)
                score = len(text)
                if score >= 80 or node.find("img"):
                    candidates.append((score, node))

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            fragment_html = str(candidates[0][1])
        else:
            # 如果找不到明显的正文容器，退回整页做一次标准化。
            fragment_html = response.text

        normalized = normalize_rss_content(fragment_html, base_url=url)

        # 兜底补充 OG 图，避免部分文章正文图片被懒加载脚本挡住
        if not normalized.get("images"):
            for meta_key in (
                ("property", "og:image"),
                ("name", "twitter:image"),
                ("name", "twitter:image:src"),
            ):
                tag = soup.find("meta", attrs={meta_key[0]: meta_key[1]})
                if tag and tag.get("content"):
                    image_url = str(tag.get("content")).strip()
                    if image_url:
                        image_assets = normalized.setdefault("image_assets", [])
                        if all(asset.get("url") != image_url for asset in image_assets):
                            image_assets.append(
                                {
                                    "url": image_url,
                                    "category": "cover",
                                    "name": "封面图",
                                    "source": "meta_image",
                                }
                            )
                        attachments = normalized.setdefault("attachments", [])
                        if all(att.get("url") != image_url for att in attachments):
                            attachments.append(
                                {
                                    "name": "封面图",
                                    "url": image_url,
                                    "type": "image",
                                }
                            )
                    break

        return normalized

    @staticmethod
    def _merge_image_assets(
        base_assets: Optional[List[Dict[str, Any]]] = None,
        extra_assets: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        index_by_url: Dict[str, int] = {}

        def upsert(asset: Dict[str, Any]) -> None:
            url = str(asset.get("url") or "").strip()
            if not url:
                return
            category = str(asset.get("category") or "body").strip() or "body"
            name = str(asset.get("name") or "图片").strip() or "图片"
            source = str(asset.get("source") or "").strip()
            alt = str(asset.get("alt") or "").strip()
            caption = str(asset.get("caption") or "").strip()
            hostname = str(asset.get("hostname") or "").strip()
            width = asset.get("width")
            height = asset.get("height")
            aspect_ratio = asset.get("aspect_ratio")
            is_wechat = asset.get("is_wechat")

            if url in index_by_url:
                existing = merged[index_by_url[url]]
                if category == "cover" and existing.get("category") != "cover":
                    existing["category"] = "cover"
                if not existing.get("name") and name:
                    existing["name"] = name
                if not existing.get("source") and source:
                    existing["source"] = source
                if not existing.get("alt") and alt:
                    existing["alt"] = alt
                if not existing.get("caption") and caption:
                    existing["caption"] = caption
                if not existing.get("hostname") and hostname:
                    existing["hostname"] = hostname
                if not existing.get("width") and width:
                    existing["width"] = width
                if not existing.get("height") and height:
                    existing["height"] = height
                if not existing.get("aspect_ratio") and aspect_ratio:
                    existing["aspect_ratio"] = aspect_ratio
                if "is_wechat" not in existing and is_wechat is not None:
                    existing["is_wechat"] = is_wechat
                return

            index_by_url[url] = len(merged)
            merged.append(
                {
                    "url": url,
                    "category": category,
                    "name": name,
                    "source": source or "rss",
                    "alt": alt,
                    "caption": caption,
                    "hostname": hostname,
                    "width": width,
                    "height": height,
                    "aspect_ratio": aspect_ratio,
                    "is_wechat": is_wechat,
                    "index": len(merged),
                }
            )

        for asset in base_assets or []:
            if isinstance(asset, dict):
                upsert(asset)
        for asset in extra_assets or []:
            if isinstance(asset, dict):
                upsert(asset)

        return merged

    def _extract_entry_cover_images(self, entry) -> List[Dict[str, str]]:
        """从 feed 元信息中提取封面图。"""
        cover_assets: List[Dict[str, str]] = []
        seen = set()

        def add_cover(url: str, source: str) -> None:
            clean_url = str(url or "").strip()
            if not clean_url or clean_url in seen:
                return
            seen.add(clean_url)
            cover_assets.append(
                {
                    "url": clean_url,
                    "category": "cover",
                    "name": "封面图",
                    "source": source,
                    "hostname": _get_hostname(clean_url),
                    "is_wechat": "mmbiz.qpic.cn" in _get_hostname(clean_url),
                }
            )

        for media in getattr(entry, "media_thumbnail", []) or []:
            href = getattr(media, "url", "") or media.get("url", "")
            add_cover(href, "media_thumbnail")

        for media in getattr(entry, "media_content", []) or []:
            href = getattr(media, "url", "") or media.get("url", "")
            medium = str(getattr(media, "medium", "") or media.get("medium", "")).lower()
            mime_type = str(getattr(media, "type", "") or media.get("type", "")).lower()
            if medium == "image" or mime_type.startswith("image"):
                add_cover(href, "media_content")

        entry_image = getattr(entry, "image", None)
        if entry_image:
            if isinstance(entry_image, dict):
                add_cover(entry_image.get("href", ""), "entry_image")
            else:
                add_cover(getattr(entry_image, "href", ""), "entry_image")

        return cover_assets

    def _extract_entry_attachments(self, entry, existing_urls: Optional[List[str]] = None) -> List[Dict[str, str]]:
        """从 feedparser entry 的 enclosure/media 字段补充附件列表"""
        attachments: List[Dict[str, str]] = []
        seen = set(existing_urls or [])

        def add_attachment(href: str, title: str = "", mime_type: str = "") -> None:
            href = (href or "").strip()
            if not href or href in seen:
                return
            seen.add(href)
            name = title or href.rsplit("/", 1)[-1] or "attachment"
            attachments.append({
                "name": name,
                "url": href,
                "type": mime_type,
            })

        for enclosure in getattr(entry, "enclosures", []) or []:
            href = getattr(enclosure, "href", "") or enclosure.get("href", "")
            mime_type = getattr(enclosure, "type", "") or enclosure.get("type", "")
            title = getattr(enclosure, "title", "") or enclosure.get("title", "")
            add_attachment(href, title, mime_type)

        for link in getattr(entry, "links", []) or []:
            rel = str(getattr(link, "rel", "") or link.get("rel", "")).lower()
            if "enclosure" not in rel and "related" not in rel:
                continue
            href = getattr(link, "href", "") or link.get("href", "")
            mime_type = getattr(link, "type", "") or link.get("type", "")
            title = getattr(link, "title", "") or link.get("title", "")
            add_attachment(href, title, mime_type)

        return attachments

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
            self.last_fetch_status = "error"
            self.last_fetch_error = "未配置 RSS URL"
            self.last_fetched_count = 0
            return []

        articles: List[ArticleData] = []
        self.last_fetch_error = ""
        self.last_fetched_count = 0

        # 🌟 使用带重试和超时的请求获取 RSS 内容
        response = self._safe_get(self.url)
        if not response:
            logger.error(f"[{self.SOURCE_NAME}] 无法获取 RSS 内容: {self.url}")
            self.last_fetch_status = "error"
            self.last_fetch_error = "无法获取 RSS 内容"
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
                self.last_fetch_status = "error"
                self.last_fetch_error = str(error_msg)
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
            self.last_fetched_count = len(articles)
            self.last_fetch_status = "healthy" if articles else "empty"
            return articles

        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] RSS 抓取失败: {e}", exc_info=True)
            self.last_fetch_status = "error"
            self.last_fetch_error = str(e)
            self.last_fetched_count = 0
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
            raw_html = self._extract_content(entry)
            normalized = normalize_rss_content(raw_html, base_url=self.url)

            # RSS 片段过薄时，自动回源详情页补正文和图片
            if self._is_thin_rss_content(normalized):
                detail_normalized = self._extract_detail_page_content(url)
                if detail_normalized:
                    detail_markdown = str(detail_normalized.get("markdown") or "")
                    detail_plain = str(detail_normalized.get("plain_text") or "")
                    current_markdown = str(normalized.get("markdown") or "")
                    current_plain = str(normalized.get("plain_text") or "")
                    if (
                        len(detail_markdown.strip()) > len(current_markdown.strip())
                        or len(detail_plain.strip()) > len(current_plain.strip())
                    ):
                        normalized = detail_normalized
                        logger.info(f"[{self.SOURCE_NAME}] RSS 内容过薄，已回源详情页补全: {title}")

            raw_text = normalized.get("markdown") or normalized.get("plain_text") or ""
            inline_images = normalized.get("images", []) or []
            attachments = normalized.get("attachments", [])
            image_assets = list(normalized.get("image_assets") or [])
            attachments.extend(
                self._extract_entry_attachments(
                    entry,
                    existing_urls=[att.get("url", "") for att in attachments if att.get("url")],
                )
            )
            image_assets = self._merge_image_assets(
                image_assets,
                self._extract_entry_cover_images(entry),
            )

            # 如果正文里没有内嵌图片，但 feed 通过 enclosure 提供了图片，则补一段 Markdown 图文区
            image_attachments = [
                att for att in attachments
                if _looks_like_image(att.get("url", "")) or str(att.get("type", "")).startswith("image")
            ]
            image_assets = self._merge_image_assets(
                image_assets,
                [
                    {
                        "url": str(att.get("url") or "").strip(),
                        "category": "attachment",
                        "name": str(att.get("name") or "附件图片").strip() or "附件图片",
                        "source": "enclosure",
                        "hostname": _get_hostname(str(att.get("url") or "").strip()),
                        "is_wechat": "mmbiz.qpic.cn"
                        in _get_hostname(str(att.get("url") or "").strip()),
                    }
                    for att in image_attachments
                    if str(att.get("url") or "").strip()
                ],
            )
            if image_attachments and not inline_images:
                image_lines = "\n\n".join(
                    f"![{att.get('name', '图片')}]({att.get('url', '')})"
                    for att in image_attachments
                    if att.get("url")
                )
                if image_lines:
                    raw_text = f"{raw_text}\n\n### 图片\n\n{image_lines}" if raw_text else f"### 图片\n\n{image_lines}"

            content_blocks, image_assets = attach_image_asset_metadata(
                markdown_to_blocks(raw_text),
                image_assets,
            )

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
                'enable_ai_formatting': self.enable_ai_formatting,
                'enable_ai_summary': self.enable_ai_summary,
                'formatting_prompt': self.formatting_prompt,
                'summary_prompt': self.summary_prompt,
                # 核心字段：专属 AI 提示词
                'custom_summary_prompt': self.summary_prompt or self.custom_summary_prompt,
                # 核心字段：规则 ID（用于溯源）
                'rule_id': self.rule_id,
                'source_profile': self.source_profile,
                'source_template_id': self.source_template_id,
                # 格式化的内容文本（用于 AI 摘要或直接显示）
                'body_text': raw_text,
                # 附件 / 图片（RSS 可能包含 enclosure 或正文内图片）
                'attachments': attachments,
                'content_blocks': content_blocks,
                'image_assets': image_assets,
                'source_type': self._source_type,
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
