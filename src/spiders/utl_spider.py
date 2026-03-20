"""
城市交通与物流学院爬虫 - V3 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 学院动态、通知公告

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 异构页面分支解析（两个板块结构差异大）
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


class UtlSpider(BaseSpider):
    """城市交通与物流学院网站爬虫"""

    SOURCE_NAME = "城市交通与物流学院"
    BASE_URL = "https://utl.sztu.edu.cn/"

    def __init__(self):
        super().__init__()
        self.sections = {
            "学院动态": "xwzx/xydt.htm",
            "通知公告": "xwzx/tzgg.htm"
        }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, **kwargs) -> List[ArticleData]:
        """获取文章列表"""
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

            response.encoding = response.apparent_encoding or 'utf-8'
            html_content = response.text

            logger.debug(f"[{self.SOURCE_NAME}] HTML 长度: {len(html_content)}")

            soup = BeautifulSoup(html_content, 'html.parser')

            # 根据板块选择不同的解析策略
            if section == "学院动态":
                items = soup.select('div.new_center_item')
                logger.info(f"[{self.SOURCE_NAME}] 学院动态找到 {len(items)} 个元素")
                for item in items:
                    try:
                        article = self._parse_list_item_xydt(item, section)
                        if article:
                            articles.append(article)
                    except Exception as e:
                        logger.debug(f"[{self.SOURCE_NAME}] 解析学院动态列表项失败: {e}")
                        continue
            else:  # 通知公告
                items = soup.select('div.new_item')
                logger.info(f"[{self.SOURCE_NAME}] 通知公告找到 {len(items)} 个元素")
                for item in items:
                    try:
                        article = self._parse_list_item_tzgg(item, section)
                        if article:
                            articles.append(article)
                    except Exception as e:
                        logger.debug(f"[{self.SOURCE_NAME}] 解析通知公告列表项失败: {e}")
                        continue

        logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 抓取到 {len(articles)} 条文章")
        return articles

    def _parse_list_item_xydt(self, item: Tag, section: str) -> Optional[ArticleData]:
        """解析学院动态列表项"""
        a_tag = item.select_one('a.new_center_item_box')
        if not a_tag:
            a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        h4 = item.select_one('h4')
        if h4:
            title = h4.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        date_str = ""
        date_elem = item.select_one('div.day-time')
        if date_elem:
            date_str = date_elem.get_text(strip=True)

        full_url = self.safe_urljoin(self.BASE_URL, str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _parse_list_item_tzgg(self, item: Tag, section: str) -> Optional[ArticleData]:
        """解析通知公告列表项"""
        a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        h3 = item.select_one('h3')
        if h3:
            title = h3.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 提取日期：组合 day 和 year-month
        date_str = ""
        day_elem = item.select_one('span.day')
        year_month_elem = item.select_one('div.year-month')

        if day_elem and year_month_elem:
            day = day_elem.get_text(strip=True)
            year_month = year_month_elem.get_text(strip=True)
            date_str = f"{year_month}-{day}"
        elif day_elem:
            date_str = day_elem.get_text(strip=True)

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
        """获取文章详情"""
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)