"""
创意设计学院爬虫 - V3 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 学院焦点、院系新闻、通知公告
- 党团工作、社会服务、校园生活

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 英文日期格式解析（February 5, 2026 / Mar 12, 2026）
- 双列表项格式支持（li.news-item / a.notice-item）
- 精准标题提取（h3.news-item__title / h3.notice-item__title）
- 标题清洗（剔除英文日期后缀）
- 附件智能提取
"""

import re
import logging
from datetime import datetime
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class DesignSpider(BaseSpider):
    """创意设计学院网站爬虫"""

    SOURCE_NAME = "创意设计学院"
    BASE_URL = "https://design.sztu.edu.cn"

    # 英文月份映射表（支持全称和缩写）
    MONTH_MAP = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8,
        'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    def __init__(self):
        super().__init__()
        self.sections = {
            "学院焦点": "xydt/xyjd.htm",
            "院系新闻": "xydt/yxxw.htm",
            "通知公告": "xydt/tzgg.htm",
            "党团工作": "xydt/dtgz.htm",
            "社会服务": "xydt/shfw.htm",
            "校园生活": "xydt/xysh.htm",
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
                entry_url = self.safe_urljoin(self.BASE_URL + '/', entry_path)
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

            soup = BeautifulSoup(html_content, 'lxml')

            # 尝试两种列表项格式
            items = soup.select('li.news-item')

            if not items:
                items = soup.select('a.notice-item')

            if not items:
                items = soup.find_all(class_='news-item') + soup.find_all(class_='notice-item')

            logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 找到 {len(items)} 个列表项")

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
        """解析列表项"""
        title = ""
        a_tag = None

        if item.name == 'a':
            a_tag = item
            title_tag = item.find('h3', class_='notice-item__title')
            if title_tag:
                title = title_tag.get_text(strip=True)
        else:
            a_tag = item.find('a')
            title_tag = item.find('h3', class_='news-item__title')
            if title_tag:
                title = title_tag.get_text(strip=True)

        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        if not title:
            title = a_tag.get('title', '')

        if not title:
            return None

        # 标题清洗：剔除英文日期后缀
        title = self._clean_title(title)

        # 提取日期
        date_str = ""
        date_span = item.find('span', class_='date') or item.find('i')
        if date_span:
            date_str = date_span.get_text(strip=True)
        if not date_str:
            time_tag = item.find('time') or item.find('span', class_='time')
            if time_tag:
                date_str = time_tag.get_text(strip=True)
        if not date_str:
            text = item.get_text()
            date_str = self._extract_date_from_text(text)

        full_url = self.safe_urljoin(self.BASE_URL + '/', str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._parse_english_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _clean_title(self, title: str) -> str:
        """清洗标题：剔除英文日期后缀"""
        if not title:
            return title

        date_suffix_pattern = r'\s*-\s*(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4}\s*$'

        cleaned = re.sub(date_suffix_pattern, '', title, flags=re.IGNORECASE)

        return cleaned.strip()

    def _extract_date_from_text(self, text: str) -> str:
        """从文本中提取英文日期"""
        pattern = r'([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
        match = re.search(pattern, text)
        if match:
            return match.group(1)
        return ""

    def _parse_english_date(self, date_str: str) -> str:
        """解析英文日期格式并转换为 YYYY-MM-DD"""
        if not date_str:
            return ""

        date_str = date_str.strip().replace(',', '')

        parts = date_str.split()

        if len(parts) >= 3:
            month_str = parts[0].lower()
            day_str = parts[1].rstrip(',')
            year_str = parts[2]

            try:
                month = self.MONTH_MAP.get(month_str)
                if month:
                    day = int(day_str)
                    year = int(year_str)
                    return f"{year:04d}-{month:02d}-{day:02d}"
            except (ValueError, TypeError):
                pass

        return self._normalize_chinese_date(date_str)

    def _normalize_chinese_date(self, date_str: str) -> str:
        """标准化中文日期格式"""
        if not date_str:
            return ""

        normalized = re.sub(r'[/\\.年月]', '-', date_str)
        normalized = re.sub(r'-+', '-', normalized).strip('-')

        return normalized

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """获取文章详情"""
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)