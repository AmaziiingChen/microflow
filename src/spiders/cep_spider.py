"""
工程物理学院爬虫 - V2 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 新闻动态、通知公告

特性：
- 逆向分页逻辑处理
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

    # 逆向分页配置：第 2 页为 17.htm
    MAX_PAGE = 17

    def __init__(self):
        super().__init__()
        # 板块配置（使用小写的 self.sections）
        self.sections = {
            "新闻动态": "tzgg1/xwdt.htm",
            "通知公告": "tzgg1/tzg.htm"
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
                entry_url = self.safe_urljoin(self.BASE_URL, entry_path)
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

        # 强制使用 UTF-8 或检测到的编码
        response.encoding = response.apparent_encoding or 'utf-8'
        html_content = response.text

        logger.debug(f"[{self.SOURCE_NAME}] HTML 长度: {len(html_content)}")

        soup = BeautifulSoup(html_content, 'html.parser')

        # 🔧 核心：精准锁定右侧新闻列表区域
        # 优先尝试 .main_list.fr（浮动在右侧的列表）
        container = soup.select_one('.main_list.fr')
        if not container:
            # 备选：如果 class 拼写有变，寻找包含新闻列表的 ul
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
            except Exception as e:
                logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                continue

        logger.info(f"[{self.SOURCE_NAME}] 板块 '{section}' 抓取到 {len(articles)} 条文章")
        return articles

    def _calculate_page_url(self, entry_url: str, page_num: int) -> str:
        """
        计算分页 URL（逆向分页，子目录风格）

        CEP 学院分页规则：
        - 第 1 页：xxx.htm
        - 第 2 页：17.htm, 16.htm...（逆向递减）
        - 公式：索引 = MAX_PAGE - page_num + 2

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

        base = entry_url.rsplit('.', 1)[0]  # 去掉 .htm
        return f"{base}/{page_index}.htm"

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
            # 备用：检查 a 标签内部是否有 span
            date_span = a_tag.find('span', class_='date')
            if not date_span:
                # 没有日期的项可能是导航，跳过
                return None

        # 提取标题：优先使用 title 属性
        title = a_tag.get('title', '')
        if not title:
            # 备用：使用 a 标签的文本（需要剔除日期部分）
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 🔧 噪音过滤：标题长度校验
        if len(title) < 5:
            logger.debug(f"[{self.SOURCE_NAME}] 跳过疑似噪音（标题过短）: {title}")
            return None

        # 🔧 噪音过滤：导航关键词黑名单
        nav_keywords = ["首页", "返回", "学校主页", "上一页", "下一页", "EN", "English"]
        # 🔧 修复 1: 确保 title 是字符串再 strip
        if str(title or "").strip() in nav_keywords:
            logger.debug(f"[{self.SOURCE_NAME}] 跳过导航项: {title}")
            return None

        # 提取链接
        raw_href = a_tag.get('href', '')
        if not raw_href:
            return None

        # 提取日期
        date_str = date_span.get_text(strip=True)

        # 🔧 修复 2: 显式转换 raw_href 为字符串
        raw_href_str = str(raw_href or "")
        
        if raw_href_str.startswith('http'):
            # 微信等外链，直接使用原链接
            full_url = raw_href_str
        else:
            # 内网链接，处理 ../info/... 格式
            # 🔧 修复 3: 使用转换后的字符串进行 replace
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
        """
        标准化日期格式

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
        获取文章详情（支持跨域：校园网 CMS 和微信公众号）

        Args:
            url: 文章 URL

        Returns:
            标准化的文章详情
        """
        # 根据域名判断来源
        if 'mp.weixin.qq.com' in url:
            return self._fetch_wechat_detail(url)
        elif 'sztu.edu.cn' in url:
            return self._fetch_campus_detail(url)
        else:
            # 其他外链，尝试通用提取
            logger.warning(f"[{self.SOURCE_NAME}] 检测到未知域名: {url}")
            return self._fetch_generic_detail(url)

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

        # 提取正文：仅限 div.v_news_content
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='v_news_content')

        if content_div:
            # 移除动态脚本
            for script in content_div.find_all('script'):
                script.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')

        # 纯图片内容防御
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
        response = self._safe_get(url, timeout=15)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取标题
        title = ""
        title_tag = soup.find('h1', class_='rich_media_title') or soup.find('h1')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取微信公众号正文：优先 js_content，其次 rich_media_content
        body_html = ""
        body_text = ""
        content_div = soup.find('div', id='js_content')

        if not content_div:
            content_div = soup.find('div', class_='rich_media_content')

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
            'attachments': [],  # 微信公众号无传统附件
            'source_name': self.SOURCE_NAME,
            'exact_time': exact_time
        }

    def _fetch_generic_detail(self, url: str) -> Optional[ArticleData]:
        """
        通用详情提取（用于未知域名）

        Args:
            url: 文章 URL

        Returns:
            文章详情
        """
        response = self._safe_get(url, timeout=15)
        if not response:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')

        # 提取标题
        title = ""
        title_tag = soup.find('h1') or soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)

        # 尝试多种正文容器
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

    def _extract_exact_time(self, soup: BeautifulSoup) -> str:
        """
        提取精确发布时间

        Args:
            soup: BeautifulSoup 对象

        Returns:
            精确时间字符串
        """
        full_text = soup.get_text()

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
