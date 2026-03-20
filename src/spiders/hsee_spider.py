import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)

class HseeSpider(BaseSpider):
    """健康与环境工程学院网站爬虫"""

    SOURCE_NAME = "健康与环境工程学院"
    BASE_URL = "https://hsee.sztu.edu.cn/"

    def __init__(self):
        super().__init__()
        self.sections = {
            "学院动态": "xydt.htm",
            "通知公告": "tzgg.htm"
        }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        articles = []
        sections_to_fetch = {section_name: self.sections[section_name]} if section_name else self.sections

        for section, entry_path in sections_to_fetch.items():
            try:
                entry_url = self.safe_urljoin(self.BASE_URL, entry_path)
                section_articles = self._fetch_section_list(entry_url, section, limit)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, limit: Optional[int] = None) -> List[ArticleData]:
        """抓取单个板块的文章列表（智能翻页，按需停止）"""
        articles = []
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            # 🌟 已达到上限，停止请求
            if limit is not None and len(articles) >= limit:
                break

            response = self._safe_get(target_url)
            if not response:
                continue

            response.encoding = response.apparent_encoding or 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')
            page_articles = []

            # 🌟 核心修复：根据板块采用异构解析逻辑
            if section == "学院动态":
                # 学院动态使用图文列表模式：.n_tulist ul li
                items = soup.select('.n_tulist ul li')
                for item in items:
                    try:
                        article = self._parse_list_item_xydt(item, section)
                        if article:
                            page_articles.append(article)
                            # 🌟 达到上限立即停止
                            if limit is not None and len(articles) + len(page_articles) >= limit:
                                break
                    except Exception:
                        continue
            else:
                # 通知公告使用纯文本列表模式：.n_list ul li.cleafix
                items = soup.select('.n_list ul li.cleafix')
                if not items:
                    items = soup.find_all('li', class_='cleafix')
                for item in items:
                    try:
                        article = self._parse_list_item_tzgg(item, section)
                        if article:
                            page_articles.append(article)
                            # 🌟 达到上限立即停止
                            if limit is not None and len(articles) + len(page_articles) >= limit:
                                break
                    except Exception:
                        continue

            articles.extend(page_articles)

        # 最终截断（兜底保护）
        if limit is not None:
            articles = articles[:limit]

        return articles

    def _parse_list_item_xydt(self, item: Tag, section: str) -> Optional[ArticleData]:
        """解析学院动态列表项 (针对 h4 标题和 h6 日期)"""
        a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        title = ""
        h4_tag = item.find('h4')
        if h4_tag:
            title = h4_tag.get_text(strip=True)

        if not title:
            return None

        date_str = ""
        h6_tag = item.find('h6')
        if h6_tag:
            date_str = h6_tag.get_text(strip=True)

        full_url = self.safe_urljoin(self.BASE_URL, str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _parse_list_item_tzgg(self, item: Tag, section: str) -> Optional[ArticleData]:
        """解析通知公告列表项 (针对 a 标签 title 和 i 标签日期)"""
        a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        title = a_tag.get('title', '')
        if not title:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        date_str = ""
        i_tag = item.find('i')
        if i_tag:
            date_str = i_tag.get_text(strip=True)

        full_url = self.safe_urljoin(self.BASE_URL, str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _normalize_date(self, date_str: str) -> str:
        """标准化日期格式"""
        if not date_str:
            return ""

        normalized = re.sub(r'[/\\.年月]', '-', date_str)
        normalized = re.sub(r'-+', '-', normalized).strip('-')
        return normalized

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """获取文章详情，完全委托给 V3.1 基类处理"""
        return super().fetch_detail(url)