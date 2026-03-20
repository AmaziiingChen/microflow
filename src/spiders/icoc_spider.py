"""
集成电路与光电芯片学院爬虫 - V3 多源数据订阅架构

支持 3 个板块的聚合抓取：
- 通知公告、学术成果、学院新闻

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 兼容两种列表 DOM 结构（b_t/h3 标题，date/p 日期）
- 纯图片内容防御
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class IcocSpider(BaseSpider):
    """集成电路与光电芯片学院网站爬虫"""

    SOURCE_NAME = "集成电路与光电芯片学院"
    BASE_URL = "https://icoc.sztu.edu.cn"

    # 3 个板块的入口配置
    SECTIONS = {
        "通知公告": "https://icoc.sztu.edu.cn/xwzx/tzgg.htm",
        "学术成果": "https://icoc.sztu.edu.cn/kxyj/xscg.htm",
        "学院新闻": "https://icoc.sztu.edu.cn/xwzx/xyxw.htm"
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
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            response = self._safe_get(target_url)
            if not response:
                continue

            soup = BeautifulSoup(response.text, 'html.parser')
            page_articles = []  # 🌟 必须在循环内新建局部空列表！

            # 查找文章列表容器下的所有 li
            containers = soup.find_all('ul', class_='list-gl') or \
                         soup.find_all('ul', class_='news-list') or \
                         soup.find_all('ul', class_='list_pic')

            for ul in containers:
                for li in ul.find_all('li', recursive=False):
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            page_articles.append(article)
                    except Exception:
                        continue

            # 🌟 如果当前页没找到，尝试全局查找 (修复了克隆 Bug)
            if not page_articles:
                for li in soup.select('ul li'):
                    try:
                        article = self._parse_list_item(li, section)
                        if article:
                            page_articles.append(article)
                    except Exception:
                        continue

            articles.extend(page_articles)

        return articles
    def _parse_list_item(self, li, section: str) -> Optional[ArticleData]:
        """解析列表项（兼容两种 DOM 结构）"""
        a_tag = li.find('a', recursive=False)
        if not a_tag:
            a_tag = li.find('a')

        if not a_tag:
            return None

        raw_href = a_tag.get('href')
        if not raw_href or not isinstance(raw_href, str):
            return None
        href = str(raw_href)

        # 提取标题：优先 div.b_t，降级 h3
        title = ""
        b_t = a_tag.find('div', class_='b_t')
        if b_t:
            title = b_t.get_text(strip=True)
        else:
            h3 = a_tag.find('h3')
            if h3:
                title = h3.get_text(strip=True)

        if not title:
            title = a_tag.get_text(strip=True)

        if not title or not href:
            return None

        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 提取日期：优先 div.date，降级 p
        date_str = ""
        date_div = a_tag.find('div', class_='date')
        if date_div:
            date_str = date_div.get_text(strip=True)
        else:
            p_tag = a_tag.find('p')
            if p_tag:
                date_str = p_tag.get_text(strip=True)

        # 日期强制校验
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