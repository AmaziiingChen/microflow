"""
新能源与智能工程学院爬虫 - V3 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 学院动态、通知公告、讲座通知、学术动态、合作交流、实验平台

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 微信公众号外链内容提取
- 纯图片内容防御
- 附件智能提取
"""
import re
import json
import logging
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class NmneSpider(BaseSpider):
    """新能源与智能工程学院网站爬虫"""

    SOURCE_NAME = "新材料与新能源学院"
    BASE_URL = "https://nmne.sztu.edu.cn/"

    # 6 个板块的入口配置
    SECTIONS = {
        "学院动态": "https://nmne.sztu.edu.cn/xwzx/xydt.htm",
        "通知公告": "https://nmne.sztu.edu.cn/xwzx/tzgg.htm",
        "讲座通知": "https://nmne.sztu.edu.cn/xwzx/jzt.htm",
        "学术动态": "https://nmne.sztu.edu.cn/xwzx/xsd.htm",
        "合作交流": "https://nmne.sztu.edu.cn/xwzx/hzj.htm",
        "实验平台": "https://nmne.sztu.edu.cn/xwzx/sypt.htm"
    }

    # 🌟 V3 升级：特殊容器类名传递给基类
    CONTENT_CONTAINER_CLASS = 'v_news_content'

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
        sections_to_fetch = {section_name: self.SECTIONS[section_name]} if section_name else self.SECTIONS

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

            soup = BeautifulSoup(response.text, 'lxml')

            # 查找所有文章条目（<li id="line_u9_0"> 格式）
            li_tags = soup.find_all('li', id=re.compile(r'line_u9_\d+'))

            if not li_tags:
                # 尝试备用选择器
                li_tags = soup.select('ul.list-gl li')

            for li in li_tags:
                try:
                    article = self._parse_list_item(li, section)
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

    def _parse_list_item(self, li, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        Args:
            li: BeautifulSoup 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取日期（<span>2026/01/08</span>）
        span = li.find('span')
        date_str = span.get_text(strip=True) if span else ""

        # 提取标题和链接（<a href="../info/1073/4372.htm">标题</a>）
        a_tag = li.find('a')
        if not a_tag:
            return None

        title = a_tag.get_text(strip=True)
        href = a_tag.get('href', '')

        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

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