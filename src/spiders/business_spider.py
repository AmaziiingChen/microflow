"""
商学院爬虫 - V3 多源数据订阅架构

支持 4 个板块的聚合抓取：
- 新闻动态、通知公告、学术动态、校园生活

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 精准匹配 li > a（标题/链接）和 li > span（日期）
- 纯图片内容防御
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class BusinessSpider(BaseSpider):
    """商学院网站爬虫"""

    SOURCE_NAME = "商学院"
    BASE_URL = "https://bs.sztu.edu.cn"

    # 4 个板块的入口配置
    SECTIONS = {
        "新闻动态": "https://bs.sztu.edu.cn/index/xwdt.htm",
        "通知公告": "https://bs.sztu.edu.cn/index/tzgg.htm",
        "学术动态": "https://bs.sztu.edu.cn/index/xsdt.htm",
        "校园生活": "https://bs.sztu.edu.cn/index/xysh.htm"
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

            soup = BeautifulSoup(response.text, 'lxml')
            page_articles = []  # 🌟 必须在循环内新建局部空列表！

            # 尝试优先精准匹配
            ul_tags = soup.find_all('ul', class_='list-gl') or soup.find_all('ul', class_='news-list')
            for ul in ul_tags:
                for li in ul.find_all('li', recursive=False):
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            page_articles.append(article)
                            # 🌟 达到上限立即停止
                            if limit is not None and len(articles) + len(page_articles) >= limit:
                                break
                    except Exception:
                        continue

            # 🌟 核心兜底逻辑：如果当前页精准匹配失败，尝试全局查找 li
            if not page_articles:
                for li in soup.select('ul li'):
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            page_articles.append(article)
                            # 🌟 达到上限立即停止
                            if limit is not None and len(articles) + len(page_articles) >= limit:
                                break
                    except Exception:
                        continue

            # 将当前页的数据追加到总列表
            articles.extend(page_articles)

        # 最终截断（兜底保护）
        if limit is not None:
            articles = articles[:limit]

        return articles
    def _parse_list_item(self, li, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        结构：<li><a href="...">标题</a><span>YYYY-MM-DD</span></li>

        Args:
            li: BeautifulSoup 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 精准提取：直接子级 a 标签
        a_tag = li.find('a', recursive=False)
        if not a_tag:
            # 降级：任意 a 标签
            a_tag = li.find('a')

        if not a_tag:
            return None

        # 提取标题
        title = a_tag.get_text(strip=True)
        href = a_tag.get('href', '')

        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 精准提取：直接子级 span 标签（日期）
        date_str = ""
        span = li.find('span', recursive=False)
        if span:
            date_str = span.get_text(strip=True)
        else:
            # 降级：查找任意 span
            span = li.find('span')
            if span:
                date_str = span.get_text(strip=True)

        # 日期强制校验：真正的公文一定有日期
        # 如果没有日期或日期不包含四位数字，直接过滤
        if not date_str or not re.search(r'\d{4}', date_str):
            return None

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
            date_str: 原始日期字符串（如 2026-01-08 或 2026/01/08）

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
