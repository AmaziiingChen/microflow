"""
公文通爬虫 - V2 多源数据订阅架构

基于原有 TongWenScraper 重构，继承 BaseSpider 基类。
保持原有抓取逻辑不变，输出格式对齐基类规范。

数据来源：深圳技术大学公文通 (https://nbw.sztu.edu.cn/)
"""

import re
import json
import logging
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class GwtSpider(BaseSpider):
    """公文通网站爬虫"""

    SOURCE_NAME = "公文通"
    BASE_URL = "https://nbw.sztu.edu.cn/"

    # 列表页入口 URL
    LIST_ENTRY = "https://nbw.sztu.edu.cn/list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1029"

    def fetch_list(self, page_num: int = 1, limit: int = 50, **kwargs) -> List[ArticleData]:
        """
        获取公文列表页

        Args:
            page_num: 页码，从 1 开始
            limit: 最大抓取数量

        Returns:
            标准化的文章摘要列表
        """
        notices = []

        # 构造分页 URL
        current_url = f"https://nbw.sztu.edu.cn/list.jsp?PAGENUM={page_num}&urltype=tree.TreeTempUrl&wbtreeid=1029"
        logger.info(f"[{self.SOURCE_NAME}] 正在抓取列表 (第 {page_num} 页): {current_url}")

        response = self._safe_get(current_url)
        if not response:
            return notices

        soup = BeautifulSoup(response.text, 'html.parser')

        # 定位公文列表项
        articles_tags = soup.select('ul.news-ul li.clearfix')

        if not articles_tags:
            logger.info(f"[{self.SOURCE_NAME}] 已到达列表最后一页")
            return notices

        for article in articles_tags:
            try:
                item = self._parse_list_item(article)
                if item:
                    notices.append(item)
                    if len(notices) >= limit:
                        break
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                continue

        return notices

    def _parse_list_item(self, article) -> Optional[ArticleData]:
        """
        解析列表项

        Args:
            article: BeautifulSoup 元素

        Returns:
            标准化的文章数据
        """
        # 提取标题和链接
        title_tag = article.select_one('div.width04 a')
        if not title_tag:
            return None

        title = title_tag.get('title', '') or title_tag.get_text(strip=True)
        if not isinstance(title, str):
            title = str(title)
        title = title.strip()

        href = title_tag.get('href', '')
        if not isinstance(href, str) or not href:
            return None

        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 提取其他维度
        date_tag = article.select_one('div.width06')
        category_tag = article.select_one('div.width02 a')
        department_tag = article.select_one('div.width03 a')

        date_str = date_tag.get_text(strip=True) if date_tag else "未知时间"
        category = category_tag.get_text(strip=True) if category_tag else "未知类别"
        department = department_tag.get_text(strip=True) if department_tag else "未知单位"

        return {
            'title': title,
            'url': full_url,
            'date': date_str,
            'category': category,
            'department': department,
            'source_name': self.SOURCE_NAME
        }

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取公文详情页

        Args:
            url: 公文 URL

        Returns:
            标准化的文章详情
        """
        logger.info(f"[{self.SOURCE_NAME}] 正在抓取详情: {url}")

        response = self._safe_get(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取正文
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='v_news_content')

        if content_div:
            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')
        elif soup.body:
            body_text = soup.body.get_text(strip=True, separator='\n')[:3000]
            body_html = body_text

        # 提取附件
        attachments = self._extract_attachments(soup, url)

        # 提取精确时间
        exact_time = self._extract_exact_time(soup)

        # 提取标题（备用）
        title = ""
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

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

    def _extract_attachments(self, soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
        """
        提取附件列表

        Args:
            soup: BeautifulSoup 对象
            base_url: 基础 URL

        Returns:
            附件列表
        """
        attachments = []
        valid_keywords = [
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
            '.rar', '.zip', 'clickdown', 'download.jsp', 'downloadattachurl'
        ]

        for a_tag in soup.find_all('a', href=True):
            href = a_tag.get('href', '')

            if not isinstance(href, str):
                continue

            text = a_tag.get_text(strip=True) or "未命名附件"

            if any(kw in href.lower() for kw in valid_keywords):
                full_url = self.safe_urljoin(base_url, href)
                # 公文通附件可直接下载
                attachments.append({'name': text, 'url': full_url, 'download_type': 'direct'})

        return attachments

    def _extract_exact_time(self, soup: BeautifulSoup) -> str:
        """
        提取精确发布时间

        Args:
            soup: BeautifulSoup 对象

        Returns:
            精确时间字符串
        """
        full_text = soup.get_text()
        time_match = re.search(r"(\d{4}[-年/.]\d{1,2}[-月/.]\d{1,2}日?\s*\d{1,2}:\d{1,2})", full_text)

        if time_match:
            return time_match.group(1).replace('  ', ' ')

        return ""

    # ===== 兼容旧接口方法 =====
    # 以下方法保留用于向后兼容，后续可逐步废弃

    def fetch_notice_list(self, limit=50):
        """
        [兼容旧接口] 抓取公文列表

        Args:
            limit: 最大抓取数量

        Returns:
            旧格式列表 [{'title', 'url', 'date', 'category', 'department'}, ...]
        """
        articles = []
        page = 1

        while len(articles) < limit:
            page_articles = self.fetch_list(page_num=page, limit=limit - len(articles))

            if not page_articles:
                break

            # 转换为旧格式
            for item in page_articles:
                articles.append({
                    'title': item['title'],
                    'url': item['url'],
                    'date': item['date'],
                    'category': item.get('category', '未知类别'),
                    'department': item.get('department', '未知单位')
                })

            page += 1

        return articles

    def fetch_article_content(self, url: str) -> Optional[Dict[str, Any]]:
        """
        [兼容旧接口] 抓取详情页内容

        Args:
            url: 文章 URL

        Returns:
            旧格式字典 {'raw_text', 'attachments', 'exact_time'}
        """
        detail = self.fetch_detail(url)

        if not detail:
            return None

        return {
            'raw_text': detail.get('body_text', ''),
            'attachments': json.dumps(detail.get('attachments', []), ensure_ascii=False),
            'exact_time': detail.get('exact_time', '')
        }
