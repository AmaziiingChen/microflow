"""
药学院爬虫 - V3 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 学院新闻、通知公告

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 精确日期拼接（year + day）
- 微信公众号外链内容提取
- 纯图片内容防御
- 附件智能提取
"""
import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class CopSpider(BaseSpider):
    """药学院网站爬虫"""

    SOURCE_NAME = "药学院"
    BASE_URL = "https://cop.sztu.edu.cn/"

    def __init__(self):
        super().__init__()
        self.sections = {
            "学院新闻": "index/xyxw.htm",
            "通知公告": "index/tzgg.htm"
        }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        logger.info(f"🚀 正在启动 {self.SOURCE_NAME} 爬虫，任务列表: {self.sections}")

        articles = []

        if section_name:
            sections_to_fetch = {section_name: self.sections[section_name]}
        else:
            sections_to_fetch = self.sections

        for section, entry_path in sections_to_fetch.items():
            try:
                entry_url = self.safe_urljoin(self.BASE_URL, entry_path)
                logger.info(f"[{self.SOURCE_NAME}] 正在抓取板块 '{section}': {entry_url}")
                section_articles = self._fetch_section_list(entry_url, section, limit)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, limit: Optional[int] = None) -> List[ArticleData]:
        """抓取单个板块的文章列表（智能翻页，按需停止）"""
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

            response.encoding = response.apparent_encoding or 'utf-8'
            html_content = response.text

            logger.debug(f"[{self.SOURCE_NAME}] HTML 长度: {len(html_content)}")

            soup = BeautifulSoup(html_content, 'lxml')

            items = soup.find_all('li')
            logger.info(f"[{self.SOURCE_NAME}] 找到 {len(items)} 个 li 元素")

            for item in items:
                try:
                    article = self._parse_list_item(item, section)
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

        logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 抓取到 {len(articles)} 条文章")
        return articles

    def _parse_list_item(self, item: Tag, section: str) -> Optional[ArticleData]:
        """
        解析列表项（严格按照验证成功的逻辑）

        结构：li 下的 a 标签
        - 标题：a 标签内的 h5 标签
        - 链接：a 标签的 href
        - 日期：li 内的 div.year + div.day 拼接
        """
        a_tag = item.find('a')
        if not a_tag:
            return None

        h5_tag = a_tag.find('h5')
        title = h5_tag.get_text(strip=True) if h5_tag else ""

        raw_href = a_tag.get('href', '')
        clean_href = str(raw_href).replace('../', '')
        url = self.safe_urljoin(self.BASE_URL, clean_href)

        year_tag = item.find('div', class_='year')
        day_tag = item.find('div', class_='day')
        year = year_tag.get_text(strip=True) if year_tag else ""
        day = day_tag.get_text(strip=True) if day_tag else ""
        date = f"{year}-{day}" if year and day else ""

        if not title or not url:
            return None

        return {
            'title': title,
            'url': url,
            'date': self._normalize_date(date),
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