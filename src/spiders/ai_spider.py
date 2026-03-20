"""
人工智能学院爬虫 - V3 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 院系新闻、通知公告

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 图文列表样式解析
- 纯图片内容防御
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class AiSpider(BaseSpider):
    """人工智能学院网站爬虫"""

    SOURCE_NAME = "人工智能学院"
    BASE_URL = "https://ai.sztu.edu.cn/"

    # 2 个板块的入口配置
    SECTIONS = {
        "院系新闻": "https://ai.sztu.edu.cn/xwzx/yxxw1.htm",
        "通知公告": "https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm"
    }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """
        获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        articles = []

        # 确定要抓取的板块
        if section_name:
            sections_to_fetch = {section_name: self.SECTIONS[section_name]}
        else:
            sections_to_fetch = self.SECTIONS

        for section, entry_url in sections_to_fetch.items():
            try:
                section_articles = self._fetch_section_list(entry_url, section, limit)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, limit: Optional[int] = None) -> List[ArticleData]:
        """
        抓取单个板块的文章列表（智能翻页，按需停止）
        """
        articles = []

        # 🌟 V3 升级：使用基类的自动翻页推演
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            # 🌟 已达到上限，停止请求
            if limit is not None and len(articles) >= limit:
                break

            response = self._safe_get(target_url)
            if not response:
                continue

            soup = BeautifulSoup(response.text, 'html.parser')

            # 查找图文列表容器下的所有文章链接
            # 容器定位：.havePictureList_list 下的直接子级 a 标签
            container = soup.find('div', class_='havePictureList_list')
            if not container:
                # 备用选择器
                container = soup

            a_tags = container.select('.havePictureList_list > a') if container != soup else container.select('a')

            for a_tag in a_tags:
                try:
                    article = self._parse_list_item(a_tag, section)
                    if article:
                        articles.append(article)
                        # 🌟 达到上限立即停止
                        if limit is not None and len(articles) >= limit:
                            break
                except Exception as e:
                    logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                    continue

        # 最终截断（兜底保护）
        if limit is not None:
            articles = articles[:limit]

        return articles

    def _parse_list_item(self, a_tag, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        Args:
            a_tag: BeautifulSoup a 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取标题：h4.text_single_lines
        h4 = a_tag.find('h4', class_='text_single_lines')
        if not h4:
            # 备用：直接获取 a 标签文本
            title = a_tag.get_text(strip=True)
        else:
            title = h4.get_text(strip=True)

        # 提取链接
        href = a_tag.get('href', '')
        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 提取日期：.time-more 下的 span
        date_str = ""
        time_more = a_tag.find('div', class_='time-more') or a_tag.find('span', class_='time-more')
        if time_more:
            span = time_more.find('span')
            if span:
                date_str = span.get_text(strip=True)
            else:
                date_str = time_more.get_text(strip=True)

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _normalize_date(self, date_str: str) -> str:
        """
        标准化日期格式

        Args:
            date_str: 原始日期字符串（如 2026/01/08）

        Returns:
            标准化日期（如 2026-01-08）
        """
        if not date_str:
            return ""

        # 替换各种分隔符为 -
        normalized = re.sub(r'[/\.年月]', '-', date_str)
        # 清理多余的 -
        normalized = re.sub(r'-+', '-', normalized).strip('-')

        return normalized

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        Args:
            url: 文章 URL

        Returns:
            标准化的文章详情
        """
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)