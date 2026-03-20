"""
中德智能制造学院爬虫 - V3 多源数据订阅架构

支持板块：
- 学院新闻

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 图文动画列表样式解析
- 纯图片内容防御
- 附件智能提取（带类型卫士）
"""
import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class SgimSpider(BaseSpider):
    """中德智能制造学院网站爬虫"""

    SOURCE_NAME = "中德智能制造学院"
    BASE_URL = "https://sgim.sztu.edu.cn/"

    sections = {
            "学院新闻": "https://sgim.sztu.edu.cn/xyxw.htm",
            "通知公告": "https://sgim.sztu.edu.cn/list2022.jsp?urltype=tree.TreeTempUrl&wbtreeid=1045"
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

        for section, entry_url in sections_to_fetch.items():
            try:
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

            soup = BeautifulSoup(html_content, 'html.parser')

            # 使用 .content-list .item（从截图看外层是 .content-list）
            items = soup.select('.content-list .item')
            logger.info(f"[{self.SOURCE_NAME}] CSS 选择器 '.content-list .item' 找到 {len(items)} 个元素")

            # 备用选择器
            if not items:
                items = soup.select('.item')
                logger.info(f"[{self.SOURCE_NAME}] 备用选择器 '.item' 找到 {len(items)} 个元素")

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

        logger.info(f"[{self.SOURCE_NAME}] 列表页抓取到了 {len(articles)} 条项目")
        return articles

    def _parse_list_item(self, item, section: str) -> Optional[ArticleData]:
        """解析列表项"""
        a_tag = item.find('a')
        if not a_tag:
            return None

        # 提取标题
        title_elem = item.select_one('.title')
        if title_elem:
            title = title_elem.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        # 提取日期
        date_str = ""
        date_elem = item.select_one('.date')
        if date_elem:
            date_str = date_elem.get_text(strip=True)

        href = a_tag.get('href', '')
        if not title or not href:
            return None

        full_url = self.safe_urljoin(self.BASE_URL, href)

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _normalize_date(self, date_str: str) -> str:
        """标准化日期格式

        输入示例：
        - "发布日期： 2026-01-13" -> "2026-01-13"
        - "2026年1月7日" -> "2026-01-07"
        - "2026/03/20 14:30" -> "2026-03-20 14:30" (保留时间)
        """
        if not date_str:
            return ""

        # 1. 移除中文前缀（如"发布日期："、"发布时间："等）
        cleaned = re.sub(r'^[^\d]*', '', date_str).strip()

        # 2. 尝试匹配带时间的格式：2026-01-13 14:30 或 2026年1月13日 14:30
        time_match = re.search(
            r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s+(\d{1,2}:\d{1,2}(?::\d{1,2})?)',
            cleaned
        )
        if time_match:
            year, month, day, time_part = time_match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)} {time_part}"

        # 3. 匹配纯日期格式：2026-01-13 或 2026年1月13日
        date_match = re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})', cleaned)
        if date_match:
            year, month, day = date_match.groups()
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        # 4. 兜底：原有逻辑
        normalized = re.sub(r'[/\.年月]', '-', cleaned)
        normalized = re.sub(r'-+', '-', normalized).strip('-')

        return normalized

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """获取文章详情"""
        # 基类已自动处理微信链接路由，直接调用基类方法
        return super().fetch_detail(url)