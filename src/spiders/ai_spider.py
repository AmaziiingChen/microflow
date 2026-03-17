"""
人工智能学院爬虫 - V2 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 院系新闻、通知公告

特性：
- 基于板块基数的递减分页计算
- 图文列表样式解析
- 纯图片内容防御
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class AiSpider(BaseSpider):
    """人工智能学院网站爬虫"""

    SOURCE_NAME = "人工智能学院"
    BASE_URL = "https://ai.sztu.edu.cn/"

    # 2 个板块的入口配置及分页基数
    # 分页基数用于计算递减分页：第2页 = 基数 - (page_num - 2)
    SECTIONS = {
        "院系新闻": {
            "url": "https://ai.sztu.edu.cn/xwzx/yxxw1.htm",
            "page_base": 20  # 第2页为 19.htm，所以基数是 20
        },
        "通知公告": {
            "url": "https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm",
            "page_base": 75  # 第2页为 74.htm，所以基数是 75
        }
    }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, **kwargs) -> List[ArticleData]:
        """
        获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块

        Returns:
            标准化的文章摘要列表
        """
        articles = []

        # 确定要抓取的板块
        if section_name:
            sections_to_fetch = {section_name: self.SECTIONS[section_name]}
        else:
            sections_to_fetch = self.SECTIONS

        for section, config in sections_to_fetch.items():
            try:
                section_articles = self._fetch_section_list(
                    config["url"], section, page_num, config["page_base"]
                )
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, page_num: int, page_base: int) -> List[ArticleData]:
        """
        抓取单个板块的文章列表

        Args:
            entry_url: 板块入口 URL
            section: 板块名称
            page_num: 页码
            page_base: 分页基数

        Returns:
            文章列表
        """
        articles = []

        # 计算实际请求 URL（处理递减分页）
        target_url = self._calculate_page_url(entry_url, page_num, page_base)

        response = self._safe_get(target_url)
        if not response:
            return articles

        soup = BeautifulSoup(response.text, 'html.parser')

        # 查找图文列表容器下的所有文章链接
        # 容器定位：.havePictureList_list 下的直接子级 a 标签
        container = soup.find('div', class_='havePictureList_list')
        if not container:
            # 备用选择器
            container = soup

        a_tags = container.select('.havePictureList_list > a') if container != soup else container.select('a')

        for a_tag in a_tags:
            try:
                article = self._parse_list_item(a_tag, section)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                continue

        return articles

    def _calculate_page_url(self, entry_url: str, page_num: int, page_base: int) -> str:
        """
        计算分页 URL（处理递减分页逻辑）

        人工智能学院分页规则：
        - 第 1 页：xxx.htm
        - 第 2 页起：递减数字（如 19.htm, 18.htm... 或 74.htm, 73.htm...）

        Args:
            entry_url: 板块入口 URL
            page_num: 页码
            page_base: 分页基数（第2页对应的数字 + 1）

        Returns:
            实际请求 URL
        """
        if page_num == 1:
            return entry_url

        # 递减分页计算：page_index = page_base - (page_num - 2)
        # 例如：page_base=20, page_num=2 -> 20 - 0 = 20（但实际第2页是19）
        # 所以：page_index = page_base - (page_num - 1)
        page_index = page_base - (page_num - 1)

        # 构造分页 URL
        # 例如：https://ai.sztu.edu.cn/xwzx/yxxw1.htm -> https://ai.sztu.edu.cn/xwzx/yxxw1/19.htm
        base = entry_url.rsplit('.', 1)[0]  # 去掉 .htm
        return f"{base}/{page_index}.htm"

    def _parse_list_item(self, a_tag, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        Args:
            a_tag: BeautifulSoup a 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取标题：h4.text_single_lines
        h4 = a_tag.find('h4', class_='text_single_lines')
        if not h4:
            # 备用：直接获取 a 标签文本
            title = a_tag.get_text(strip=True)
        else:
            title = h4.get_text(strip=True)

        # 提取链接
        href = a_tag.get('href', '')
        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 提取日期：.time-more 下的 span
        date_str = ""
        time_more = a_tag.find('div', class_='time-more') or a_tag.find('span', class_='time-more')
        if time_more:
            span = time_more.find('span')
            if span:
                date_str = span.get_text(strip=True)
            else:
                date_str = time_more.get_text(strip=True)

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _normalize_date(self, date_str: str) -> str:
        """
        标准化日期格式

        Args:
            date_str: 原始日期字符串（如 2026/01/08）

        Returns:
            标准化日期（如 2026-01-08）
        """
        if not date_str:
            return ""

        # 替换各种分隔符为 -
        normalized = re.sub(r'[/\.年月]', '-', date_str)
        # 清理多余的 -
        normalized = re.sub(r'-+', '-', normalized).strip('-')

        return normalized

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        Args:
            url: 文章 URL

        Returns:
            标准化的文章详情
        """
        # 判断是否为微信公众号链接
        if 'mp.weixin.qq.com' in url:
            return self._fetch_wechat_detail(url)

        # 常规校园网页面
        return self._fetch_campus_detail(url)

    def _fetch_campus_detail(self, url: str) -> Optional[ArticleData]:
        """
        抓取校园网文章详情

        Args:
            url: 文章 URL

        Returns:
            文章详情
        """
        response = self._safe_get(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取标题
        title = ""
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取正文：.v_news_content（与 NMNE 一致）
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='v_news_content')

        if content_div:
            # 移除动态脚本
            for script in content_div.find_all('script'):
                script.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')
        else:
            # 备用提取：查找可能的内容区域
            main_content = soup.find('div', class_='article-content') or soup.find('div', id='content')
            if main_content:
                body_html = str(main_content)
                body_text = main_content.get_text(strip=True, separator='\n')

        # 纯图片内容防御：如果正文很短但包含图片
        if len(body_text) < 50 and '<img' in body_html:
            logger.info(f"[{self.SOURCE_NAME}] 检测到纯图片内容，保留 HTML 结构: {url[:50]}...")

        # 提取附件：在整个 soup 中全局搜索（打破容器限制）
        attachments = self._extract_attachments(soup, url)

        # 提取精确时间
        exact_time = self._extract_exact_time(soup)

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

    def _fetch_wechat_detail(self, url: str) -> Optional[ArticleData]:
        """
        抓取微信公众号文章详情

        Args:
            url: 微信文章 URL

        Returns:
            文章详情
        """
        response = self._safe_get(url)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取标题
        title = ""
        title_tag = soup.find('h1', class_='rich_media_title') or soup.find('h1')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取微信公众号正文
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='rich_media_content', id='js_content')

        if content_div:
            # 移除脚本和样式
            for tag in content_div.find_all(['script', 'style']):
                tag.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')

        # 提取发布时间
        exact_time = ""
        time_tag = soup.find('em', id='publish_time')
        if time_tag:
            exact_time = time_tag.get_text(strip=True)

        return {
            'title': title,
            'url': url,
            'date': '',
            'body_html': body_html,
            'body_text': body_text,
            'attachments': [],  # 微信公众号通常没有附件下载
            'source_name': self.SOURCE_NAME,
            'exact_time': exact_time
        }

    def _extract_exact_time(self, soup: BeautifulSoup) -> str:
        """
        提取精确发布时间

        Args:
            soup: BeautifulSoup 对象

        Returns:
            精确时间字符串
        """
        full_text = soup.get_text()

        # 匹配各种时间格式
        patterns = [
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*\d{1,2}:\d{1,2}:\d{1,2})',  # 带秒
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*\d{1,2}:\d{1,2})',         # 不带秒
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?)'                             # 仅日期
        ]

        for pattern in patterns:
            match = re.search(pattern, full_text)
            if match:
                time_str = match.group(1)
                # 标准化时间格式
                time_str = time_str.replace('年', '-').replace('月', '-').replace('日', '')
                return time_str.strip()

        return ""
