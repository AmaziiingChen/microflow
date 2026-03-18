"""
外国语学院爬虫 - V2 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 通知公告、学院新闻

特性：
- 动态解析总页数，精准逆向分页
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

    _page_cache: Dict[str, int] = {}

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, **kwargs) -> List[ArticleData]:
        articles = []
        sections_to_fetch = {section_name: self.SECTIONS[section_name]} if section_name else self.SECTIONS

        for section, entry_url in sections_to_fetch.items():
            try:
                if entry_url not in self._page_cache:
                    self._parse_total_pages(entry_url)

                section_articles = self._fetch_section_list(entry_url, section, page_num)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _parse_total_pages(self, entry_url: str) -> int:
        response = self._safe_get(entry_url)
        if not response:
            self._page_cache[entry_url] = 1
            return 1

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取 <span class="p_t">/26页</span>
        for p_t in soup.find_all('span', class_='p_t'):
            text = p_t.get_text(strip=True)
            match = re.search(r'/(\d+)页', text)
            if match:
                total_pages = int(match.group(1))
                self._page_cache[entry_url] = total_pages
                return total_pages

        # 降级：查找所有分页链接取最大数字
        page_links = soup.find_all('a', href=re.compile(r'/\d+\.htm'))
        max_index = 0
        for link in page_links:
            raw_href = link.get('href')
            if not raw_href or not isinstance(raw_href, str): continue
            href = str(raw_href)
            if 'info/' in href or 'content/' in href: continue

            match = re.search(r'/(\d+)\.htm', href)
            if match:
                index = int(match.group(1))
                if index > max_index: max_index = index

        total_pages = max_index + 1 if max_index > 0 else 1
        self._page_cache[entry_url] = total_pages
        return total_pages

    def _fetch_section_list(self, entry_url: str, section: str, page_num: int) -> List[ArticleData]:
        articles = []
        total_pages = self._page_cache.get(entry_url, 1)
        target_url = self._get_page_url(entry_url, page_num, total_pages)

        response = self._safe_get(target_url)
        if not response: return articles

        soup = BeautifulSoup(response.text, 'html.parser')

        # SFL 列表通常在 ul.news_fly 中
        ul_tags = soup.find_all('ul', class_='news_fly')
        if not ul_tags:
            ul_tags = soup.find_all('ul')  # 降级

        for ul in ul_tags:
            for li in ul.find_all('li', recursive=False):
                try:
                    article = self._parse_list_item(li, section)
                    if article: articles.append(article)
                except Exception as e:
                    logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                    continue
        return articles

    def _get_page_url(self, category_url: str, page_num: int, total_pages: int) -> str:
        if page_num == 1: return category_url
        if page_num > total_pages: return category_url

        page_index = total_pages - (page_num - 1)
        base = category_url.rsplit('.', 1)[0]
        return f"{base}/{page_index}.htm"

    def _parse_list_item(self, li: Tag, section: str) -> Optional[ArticleData]:
        a_tag = li.find('a')
        if not a_tag: return None

        raw_href = a_tag.get('href')
        if not raw_href: return None

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

        # 标准化日期 2026/02/04 -> 2026-02-04
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
        response = self._safe_get(url)
        if not response: return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取标题
        title = ""
        title_tag = soup.find('h2') or soup.find('h3') or soup.find('h1')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取正文
        body_html, body_text = "", ""
        content_div = soup.find('div', class_='v_news_content')
        if content_div:
            for script in content_div.find_all('script'): script.decompose()
            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')

        # 提取精确时间
        exact_time = ""
        note_div = soup.find('div', class_='cnt_note')
        if note_div:
            note_text = note_div.get_text(strip=True)
            match = re.search(r'时间[：:]\s*(\d{4}-\d{1,2}-\d{1,2})', note_text)
            if match: exact_time = match.group(1)

        # 提取附件
        attachments = self._extract_attachments(soup, url)

        return {
            'title': title,
            'url': url,
            'date': '',
            'body_html': body_html,
            'body_text': body_text,
            'attachments': attachments,
            'source_name': self.SOURCE_NAME,
            'exact_time': exact_time
        }
