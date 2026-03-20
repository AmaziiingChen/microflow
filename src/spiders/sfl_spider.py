"""
外国语学院爬虫 - V3 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 通知公告、学院新闻

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 穿透兼容图文列表与纯文本列表
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class SflSpider(BaseSpider):
    """外国语学院网站爬虫"""

    SOURCE_NAME = "外国语学院"
    BASE_URL = "https://sfl.sztu.edu.cn/"

    SECTIONS = {
        "通知公告": "https://sfl.sztu.edu.cn/tzgg.htm",
        "学院新闻": "https://sfl.sztu.edu.cn/xyxw.htm"
    }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, **kwargs) -> List[ArticleData]:
        """获取文章列表"""
        articles = []
        sections_to_fetch = {section_name: self.SECTIONS[section_name]} if section_name else self.SECTIONS

        for section, entry_url in sections_to_fetch.items():
            try:
                section_articles = self._fetch_section_list(entry_url, section)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str) -> List[ArticleData]:
        """抓取单个板块的文章列表（使用基类自动翻页推演）"""
        articles = []

        # 🌟 V3 升级：使用基类的自动翻页推演
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            response = self._safe_get(target_url)
            if not response:
                continue

            soup = BeautifulSoup(response.text, 'html.parser')

            # SFL 列表通常在 ul.news_fly 中
            ul_tags = soup.find_all('ul', class_='news_fly')
            if not ul_tags:
                ul_tags = soup.find_all('ul')

            for ul in ul_tags:
                for li in ul.find_all('li', recursive=False):
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            articles.append(article)
                    except Exception as e:
                        logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                        continue

        return articles

    def _parse_list_item(self, li: Tag, section: str) -> Optional[ArticleData]:
        """解析列表项"""
        a_tag = li.find('a')
        if not a_tag:
            return None

        raw_href = a_tag.get('href')
        if not raw_href:
            return None

        # 穿透获取标题：优先 <p>，其次 <a> 的文本
        title = ""
        p_tag = a_tag.find('p')
        if p_tag:
            title = p_tag.get('title') or p_tag.get_text(strip=True)
        else:
            title = a_tag.get('title') or a_tag.get_text(strip=True)

        # 穿透获取日期：<span> 标签
        date_str = ""
        span_tag = a_tag.find('span')
        if span_tag:
            date_str = span_tag.get_text(strip=True)

        if not title or not date_str or not re.search(r'\d{4}', date_str):
            return None

        full_url = self.safe_urljoin(self.BASE_URL, str(raw_href))

        # 标准化日期
        date_str = re.sub(r'[/\\.年月]', '-', date_str)
        date_str = re.sub(r'-+', '-', date_str).strip('-')

        return {
            'title': title,
            'url': full_url,
            'date': date_str,
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """获取文章详情"""
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)