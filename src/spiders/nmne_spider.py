"""
新能源与智能工程学院爬虫 - V2 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 学院动态、通知公告、讲座通知、学术动态、合作交流、实验平台

特性：
- 逆向分页逻辑处理
- 微信公众号外链内容提取
- 纯图片内容防御
- 附件智能提取
"""
import re
import json
import logging
from typing import Dict, List, Optional, Any
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class NmneSpider(BaseSpider):
    """新能源与智能工程学院网站爬虫"""

    SOURCE_NAME = "新材料与新能源学院"
    BASE_URL = "https://nmne.sztu.edu.cn/"

    # 逆向分页配置：第 2 页为 10.htm
    MAX_PAGE = 10

    # 6 个板块的入口配置
    SECTIONS = {
        "学院动态": "https://nmne.sztu.edu.cn/xwzx/xydt.htm",
        "通知公告": "https://nmne.sztu.edu.cn/xwzx/tzgg.htm",
        "讲座通知": "https://nmne.sztu.edu.cn/xwzx/jzt.htm",
        "学术动态": "https://nmne.sztu.edu.cn/xwzx/xsd.htm",
        "合作交流": "https://nmne.sztu.edu.cn/xwzx/hzj.htm",
        "实验平台": "https://nmne.sztu.edu.cn/xwzx/sypt.htm"
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
        sections_to_fetch = {section_name: self.SECTIONS[section_name]} if section_name else self.SECTIONS

        for section, entry_url in sections_to_fetch.items():
            try:
                section_articles = self._fetch_section_list(entry_url, section, page_num)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, page_num: int) -> List[ArticleData]:
        """
        抓取单个板块的文章列表

        Args:
            entry_url: 板块入口 URL
            section: 板块名称
            page_num: 页码

        Returns:
            文章列表
        """
        articles = []

        # 计算实际请求 URL（处理逆向分页）
        target_url = self._calculate_page_url(entry_url, page_num)

        response = self._safe_get(target_url)
        if not response:
            return articles

        soup = BeautifulSoup(response.text, 'html.parser')

        # 查找所有文章条目（<li id="line_u9_0"> 格式）
        li_tags = soup.find_all('li', id=re.compile(r'line_u9_\d+'))

        if not li_tags:
            # 尝试备用选择器
            li_tags = soup.select('ul.list-gl li')

        for li in li_tags:
            try:
                article = self._parse_list_item(li, section)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                continue

        return articles

    def _calculate_page_url(self, entry_url: str, page_num: int) -> str:
        """
        计算分页 URL（逆向分页，子目录风格）

        NMNE 学院分页规则：
        - 第 1 页：xxx.htm
        - 第 2 页：10.htm, 第 3 页：11.htm...（递增模式）
        - 公式：索引 = MAX_PAGE + page_num - 2

        Args:
            entry_url: 板块入口 URL
            page_num: 页码

        Returns:
            实际请求 URL
        """
        if page_num == 1:
            return entry_url

        # NMNE 特殊：递增分页（而非递减）
        # page_index = MAX_PAGE + page_num - 2
        # 示例：MAX_PAGE=10 时
        #   - page_num=2 -> 10+2-2 = 10
        #   - page_num=3 -> 10+3-2 = 11
        page_index = self.MAX_PAGE + page_num - 2

        base = entry_url.rsplit('.', 1)[0]  # 去掉 .htm
        return f"{base}/{page_index}.htm"

    def _parse_list_item(self, li, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        Args:
            li: BeautifulSoup 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取日期（<span>2026/01/08</span>）
        span = li.find('span')
        date_str = span.get_text(strip=True) if span else ""

        # 提取标题和链接（<a href="../info/1073/4372.htm">标题</a>）
        a_tag = li.find('a')
        if not a_tag:
            return None

        title = a_tag.get_text(strip=True)
        href = a_tag.get('href', '')

        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

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

        # 提取正文
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
            # 保留图片 HTML 结构
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
