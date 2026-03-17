"""
创意设计学院爬虫 - V2 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 学院焦点、院系新闻、通知公告
- 党团工作、社会服务、校园生活

特性：
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

    # 逆向分页配置：第 2 页为 17.htm（需根据实际情况调整）
    MAX_PAGE = 17

    # 英文月份映射表（支持全称和缩写）
    MONTH_MAP = {
        # 全称
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        # 缩写
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'jun': 6, 'jul': 7, 'aug': 8,
        'sep': 9, 'sept': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }

    def __init__(self):
        super().__init__()
        # 板块配置
        self.sections = {
            "学院焦点": "xydt/xyjd.htm",
            "院系新闻": "xydt/yxxw.htm",
            "通知公告": "xydt/tzgg.htm",
            "党团工作": "xydt/dtgz.htm",
            "社会服务": "xydt/shfw.htm",
            "校园生活": "xydt/xysh.htm",
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
        logger.info(f"🚀 正在启动 {self.SOURCE_NAME} 爬虫，任务列表: {self.sections}")

        articles = []

        # 确定要抓取的板块
        if section_name:
            sections_to_fetch = {section_name: self.sections[section_name]}
        else:
            sections_to_fetch = self.sections

        for section, entry_path in sections_to_fetch.items():
            try:
                entry_url = self.safe_urljoin(self.BASE_URL + '/', entry_path)
                logger.info(f"[{self.SOURCE_NAME}] 正在抓取板块 '{section}': {entry_url}")
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

        response.encoding = response.apparent_encoding or 'utf-8'
        html_content = response.text

        soup = BeautifulSoup(html_content, 'html.parser')

        # 尝试两种列表项格式
        # 格式 1：li.news-item（学院焦点、院系新闻）
        items = soup.select('li.news-item')

        # 格式 2：a.notice-item（通知公告）
        if not items:
            items = soup.select('a.notice-item')

        # 备用：查找所有带 news-item 或 notice-item 类的元素
        if not items:
            items = soup.find_all(class_='news-item') + soup.find_all(class_='notice-item')

        logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 找到 {len(items)} 个列表项")

        for item in items:
            try:
                article = self._parse_list_item(item, section)
                if article:
                    articles.append(article)
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                continue

        logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 抓取到 {len(articles)} 条文章")
        return articles

    def _calculate_page_url(self, entry_url: str, page_num: int) -> str:
        """
        计算分页 URL（逆向分页，子目录风格）

        Args:
            entry_url: 板块入口 URL
            page_num: 页码

        Returns:
            实际请求 URL
        """
        if page_num == 1:
            return entry_url

        # 动态计算：page_index = MAX_PAGE - page_num + 2
        page_index = self.MAX_PAGE - page_num + 2

        if page_index < 1:
            page_index = 1

        base = entry_url.rsplit('.', 1)[0]
        return f"{base}/{page_index}.htm"

    def _parse_list_item(self, item: Tag, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        支持两种格式：
        1. li.news-item：标题在 h3.news-item__title
        2. a.notice-item：标题在 h3.notice-item__title

        Args:
            item: BeautifulSoup Tag 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 判断元素类型并精准定位标题
        title = ""
        a_tag = None

        if item.name == 'a':
            # notice-item 格式：本身就是链接
            a_tag = item
            # 精准定位标题：h3.notice-item__title
            title_tag = item.find('h3', class_='notice-item__title')
            if title_tag:
                title = title_tag.get_text(strip=True)
        else:
            # news-item 格式：查找子链接
            a_tag = item.find('a')
            # 精准定位标题：h3.news-item__title
            title_tag = item.find('h3', class_='news-item__title')
            if title_tag:
                title = title_tag.get_text(strip=True)

        if not a_tag:
            return None

        # 提取链接
        href = a_tag.get('href', '')
        if not href:
            return None

        # 如果精准定位失败，回退到 a 标签的 title 属性
        if not title:
            title = a_tag.get('title', '')

        if not title:
            return None

        # 🌟 标题清洗：剔除英文日期后缀（如 -July 8, 2025）
        title = self._clean_title(title)

        # 提取日期：尝试多种位置
        date_str = ""
        # 方式 1：li.news-item 内的日期元素
        date_span = item.find('span', class_='date') or item.find('i')
        if date_span:
            date_str = date_span.get_text(strip=True)
        # 方式 2：a.notice-item 内的时间元素
        if not date_str:
            time_tag = item.find('time') or item.find('span', class_='time')
            if time_tag:
                date_str = time_tag.get_text(strip=True)
        # 方式 3：直接在 item 文本中查找日期格式
        if not date_str:
            text = item.get_text()
            date_str = self._extract_date_from_text(text)

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL + '/', str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._parse_english_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _clean_title(self, title: str) -> str:
        """
        清洗标题：剔除英文日期后缀

        常见污染格式：
        - "标题文字-July 8, 2025"
        - "标题文字 - February 5, 2026"

        Args:
            title: 原始标题

        Returns:
            清洗后的纯净标题
        """
        if not title:
            return title

        # 匹配：短横线 + 英文月份（全称或缩写）+ 日期 + 年份
        # 例如：-July 8, 2025 或 - Feb 12, 2026
        date_suffix_pattern = r'\s*-\s*(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+\d{1,2},?\s*\d{4}\s*$'

        cleaned = re.sub(date_suffix_pattern, '', title, flags=re.IGNORECASE)

        return cleaned.strip()

    def _extract_date_from_text(self, text: str) -> str:
        """
        从文本中提取英文日期

        Args:
            text: 包含日期的文本

        Returns:
            提取的日期字符串
        """
        # 匹配英文月份日期格式
        # 例如：February 5, 2026 或 Mar 12, 2026
        pattern = r'([A-Za-z]+\s+\d{1,2},?\s+\d{4})'
        match = re.search(pattern, text)
        if match:
            return match.group(1)
        return ""

    def _parse_english_date(self, date_str: str) -> str:
        """
        解析英文日期格式并转换为 YYYY-MM-DD

        支持格式：
        - February 5, 2026（全称）
        - Mar 12, 2026（缩写）
        - March 5 2026（无逗号）

        Args:
            date_str: 英文日期字符串

        Returns:
            标准化日期（如 2026-03-15）
        """
        if not date_str:
            return ""

        # 清理字符串
        date_str = date_str.strip().replace(',', '')

        # 尝试解析英文日期
        parts = date_str.split()

        if len(parts) >= 3:
            month_str = parts[0].lower()
            day_str = parts[1].rstrip(',')
            year_str = parts[2]

            try:
                # 查找月份
                month = self.MONTH_MAP.get(month_str)
                if month:
                    day = int(day_str)
                    year = int(year_str)
                    return f"{year:04d}-{month:02d}-{day:02d}"
            except (ValueError, TypeError):
                pass

        # 如果英文解析失败，尝试中文日期格式
        return self._normalize_chinese_date(date_str)

    def _normalize_chinese_date(self, date_str: str) -> str:
        """
        标准化中文日期格式

        Args:
            date_str: 原始日期字符串

        Returns:
            标准化日期（如 2026-03-15）
        """
        if not date_str:
            return ""

        # 替换各种分隔符为 -
        normalized = re.sub(r'[/\\.年月]', '-', date_str)
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

        # 提取标题：使用 h1 标签
        title = ""
        title_tag = soup.find('h1')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取正文：优先 div#vsb_content，备选 div.v_news_content
        body_html = ""
        body_text = ""
        content_div = soup.find('div', id='vsb_content')

        if not content_div:
            content_div = soup.find('div', class_='v_news_content')

        if content_div:
            # 移除动态脚本
            for script in content_div.find_all('script'):
                script.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')

        # 纯图片内容防御
        if len(body_text) < 50 and '<img' in body_html:
            logger.info(f"[{self.SOURCE_NAME}] 检测到纯图片内容，保留 HTML 结构")

        # 提取附件：复用基类方法
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

        # 提取正文
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='rich_media_content', id='js_content')

        if content_div:
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
            'attachments': [],
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
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*\d{1,2}:\d{1,2}:\d{1,2})',
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*\d{1,2}:\d{1,2})',
            r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?)'
        ]

        for pattern in patterns:
            match = re.search(pattern, full_text)
            if match:
                time_str = match.group(1)
                time_str = time_str.replace('年', '-').replace('月', '-').replace('日', '')
                return time_str.strip()

        return ""
