"""
工程物理学院爬虫 - V3 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 新闻动态、通知公告

特性：
- 自动翻页推演（使用基类 get_all_page_urls）
- 微信公众号外链内容提取（跨域支持）
- 纯图片内容防御
- 附件智能提取
"""
import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class CepSpider(BaseSpider):
    """工程物理学院网站爬虫"""

    SOURCE_NAME = "工程物理学院"
    BASE_URL = "https://cep.sztu.edu.cn/"

    def __init__(self):
        super().__init__()
        # 板块配置
        self.sections = {
            "新闻动态": "tzgg1/xwdt.htm",
            "通知公告": "tzgg1/tzg.htm"
        }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """
        获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        logger.info(f"🚀 正在启动 {self.SOURCE_NAME} 爬虫，任务列表: {self.sections}")

        articles = []

        # 确定要抓取的板块
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
        """
        抓取单个板块的文章列表（智能翻页，按需停止）
        """
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

            # 强制使用 UTF-8 或检测到的编码
            response.encoding = response.apparent_encoding or 'utf-8'
            html_content = response.text

            logger.debug(f"[{self.SOURCE_NAME}] HTML 长度: {len(html_content)}")

            soup = BeautifulSoup(html_content, 'lxml')

            # 🔧 核心：精准锁定右侧新闻列表区域
            container = soup.select_one('.main_list.fr')
            if not container:
                container = soup.find('div', class_='main_list')

            if container:
                items = container.find_all('li')
            else:
                items = []
                logger.warning(f"[{self.SOURCE_NAME}] 未找到列表容器")

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
        解析列表项

        结构：li 标签，内部包含 a 标签
        - 标题：优先获取 a 标签的 title 属性
        - 链接：a 标签的 href（可能是微信链接）
        - 日期：span.date 文本

        Args:
            item: BeautifulSoup Tag 元素（li）
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取 a 标签
        a_tag = item.find('a')
        if not a_tag:
            return None

        # 🔧 日期校验：必须带有 span.date，否则是导航项
        date_span = item.select_one('span.date')
        if not date_span:
            date_span = a_tag.find('span', class_='date')
            if not date_span:
                return None

        # 提取标题：优先使用 title 属性
        title = a_tag.get('title', '')
        if not title:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 🔧 噪音过滤：标题长度校验
        if len(title) < 5:
            logger.debug(f"[{self.SOURCE_NAME}] 跳过疑似噪音（标题过短）: {title}")
            return None

        # 🔧 噪音过滤：导航关键词黑名单
        nav_keywords = ["首页", "返回", "学校主页", "上一页", "下一页", "EN", "English"]
        if str(title or "").strip() in nav_keywords:
            logger.debug(f"[{self.SOURCE_NAME}] 跳过导航项: {title}")
            return None

        # 提取链接
        raw_href = a_tag.get('href', '')
        if not raw_href:
            return None

        # 提取日期
        date_str = date_span.get_text(strip=True)

        raw_href_str = str(raw_href or "")

        if raw_href_str.startswith('http'):
            full_url = raw_href_str
        else:
            clean_path = raw_href_str.replace('../', '')
            full_url = self.safe_urljoin(self.BASE_URL, clean_path)

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
        """获取文章详情（支持跨域：校园网 CMS 和微信公众号）"""
        # 基类已自动处理微信链接路由
        if 'sztu.edu.cn' in url or 'mp.weixin.qq.com' in url:
            return super().fetch_detail(url)
        else:
            logger.warning(f"[{self.SOURCE_NAME}] 检测到未知域名: {url}")
            return self._fetch_generic_detail(url)

    def _fetch_generic_detail(self, url: str) -> Optional[ArticleData]:
        """通用详情提取（用于未知域名）"""
        response = self._safe_get(url, timeout=15)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'lxml')

        title = ""
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

        body_html = ""
        body_text = ""
        content_div = (
            soup.find('div', class_='v_news_content') or
            soup.find('div', id='js_content') or
            soup.find('div', class_='rich_media_content') or
            soup.find('article') or
            soup.find('main')
        )

        if content_div:
            for tag in content_div.find_all(['script', 'style']):
                tag.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')

        return {
            'title': title,
            'url': url,
            'date': '',
            'body_html': body_html,
            'body_text': body_text,
            'attachments': [],
            'source_name': self.SOURCE_NAME,
            'exact_time': ''
        }