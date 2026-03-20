"""
未来技术学院爬虫 - V3 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 新闻中心、教务通知、科研通知、学工通知、校园通知、行政通知

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 兼容两种列表 DOM 结构（.hireBox 和 .listBox）
- 微信公众号链接智能处理
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class FutureTechSpider(BaseSpider):
    """未来技术学院网站爬虫"""

    SOURCE_NAME = "未来技术学院"
    BASE_URL = "https://futuretechnologyschool.sztu.edu.cn"

    # 6 个板块的入口配置
    SECTIONS = {
        "新闻中心": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/xwzx.htm",
        "教务通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/jw.htm",
        "科研通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/ky.htm",
        "学工通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xg.htm",
        "校园通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xy.htm",
        "行政通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xz.htm",
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

            # 双路 DOM 兼容：尝试 .hireBox（通知类）和 .listBox（新闻类）
            containers = soup.find_all('ul', class_='hireBox')
            if not containers:
                containers = soup.find_all('ul', class_='listBox')

            for ul in containers:
                li_tags = ul.find_all('li', recursive=False)
                for li in li_tags:
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            articles.append(article)
                    except Exception as e:
                        logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                        continue

        return articles

    def _parse_list_item(self, li: Tag, section: str) -> Optional[ArticleData]:
        """解析列表项（兼容两种 DOM 结构）"""
        # 提取日期（两种结构都有 .time）
        time_tag = li.select_one('.time')
        date_str = time_tag.get_text(strip=True) if time_tag else ""

        # 日期强制校验
        if not date_str or not re.search(r'\d{4}', date_str):
            return None

        # 尝试方式 A（通知类 - .hireBox）：.name a
        a_tag = li.select_one('.name a')
        if a_tag:
            title = a_tag.get_text(strip=True)
            raw_href = a_tag.get('href')
        else:
            # 尝试方式 B（新闻类 - .listBox）：.title 获取标题，a 获取链接
            title_tag = li.select_one('.title')
            title = title_tag.get_text(strip=True) if title_tag else ""

            a_tag = li.find('a')
            if not a_tag:
                return None
            raw_href = a_tag.get('href')

            if not title and a_tag:
                title = a_tag.get('title', '')

        if not title or not raw_href:
            return None

        if not isinstance(raw_href, str):
            return None

        full_url = self.safe_urljoin(self.BASE_URL, raw_href)

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
        """获取文章详情"""
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)