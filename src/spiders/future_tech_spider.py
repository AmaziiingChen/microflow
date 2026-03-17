"""
未来技术学院爬虫 - V2 多源数据订阅架构

支持 6 个板块的聚合抓取：
- 新闻中心、教务通知、科研通知、学工通知、校园通知、行政通知

特性：
- 动态解析总页数，实现精准逆向分页
- 兼容两种列表 DOM 结构（.hireBox 和 .listBox）
- 微信公众号链接智能处理
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class FutureTechSpider(BaseSpider):
    """未来技术学院网站爬虫"""

    SOURCE_NAME = "未来技术学院"
    BASE_URL = "https://futuretechnologyschool.sztu.edu.cn"

    # 6 个板块的入口配置
    SECTIONS = {
        "新闻中心": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/xwzx.htm",
        "教务通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/jw.htm",
        "科研通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/ky.htm",
        "学工通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xg.htm",
        "校园通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xy.htm",
        "行政通知": "https://futuretechnologyschool.sztu.edu.cn/xw_hd/tzgg1/xz.htm",
    }

    # 板块总页数缓存 {section_url: total_pages}
    _page_cache: Dict[str, int] = {}

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
                # 首次请求时解析总页数
                if entry_url not in self._page_cache:
                    self._parse_total_pages(entry_url)

                section_articles = self._fetch_section_list(entry_url, section, page_num)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _parse_total_pages(self, entry_url: str) -> int:
        """
        从板块首页解析总页数

        分页元素通常为：
        - <a class="p_last" href="xwzx/1.htm">尾页</a>
        - 分页链接中的最小数字 + 1

        Args:
            entry_url: 板块入口 URL

        Returns:
            总页数
        """
        response = self._safe_get(entry_url)
        if not response:
            self._page_cache[entry_url] = 1
            return 1

        soup = BeautifulSoup(response.text, 'html.parser')
        total_pages = 1

        # 方案1：查找 a.p_last 的 href（如 "xwzx/1.htm" 表示最后一页是 1.htm）
        p_last = soup.find('a', class_='p_last')
        if p_last:
            raw_href = p_last.get('href')
            if raw_href and isinstance(raw_href, str):
                href = str(raw_href)
                match = re.search(r'/(\d+)\.htm', href)
                if match:
                    last_page_num = int(match.group(1))
                    total_pages = last_page_num + 1
                    logger.debug(f"[{self.SOURCE_NAME}] 从 p_last 解析到总页数: {total_pages}")
                    self._page_cache[entry_url] = total_pages
                    return total_pages

        # 方案2：查找所有分页链接，取最小数字
        # 过滤掉文章链接（包含 info/）
        page_links = soup.find_all('a', href=re.compile(r'/\d+\.htm$'))
        min_index = float('inf')
        for link in page_links:
            raw_href = link.get('href')
            if not raw_href or not isinstance(raw_href, str):
                continue
            href = str(raw_href)
            # 严格过滤：排除文章链接
            if 'info/' in href or 'content/' in href:
                continue
            match = re.search(r'/(\d+)\.htm', href)
            if match:
                index = int(match.group(1))
                if index < min_index:
                    min_index = index

        if min_index != float('inf'):
            # 逆向分页：最小数字对应最后一页
            total_pages = min_index + 1
            logger.debug(f"[{self.SOURCE_NAME}] 从分页链接解析到总页数: {total_pages}")
            self._page_cache[entry_url] = total_pages
            return total_pages

        # 默认值
        logger.debug(f"[{self.SOURCE_NAME}] 未能解析总页数，使用默认值 1")
        self._page_cache[entry_url] = 1
        return 1

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

        # 计算实际请求 URL
        total_pages = self._page_cache.get(entry_url, 1)
        target_url = self._get_page_url(entry_url, page_num, total_pages)

        response = self._safe_get(target_url)
        if not response:
            return articles

        soup = BeautifulSoup(response.text, 'html.parser')

        # 🌟 双路 DOM 兼容：尝试 .hireBox（通知类）和 .listBox（新闻类）
        containers = soup.find_all('ul', class_='hireBox')
        if not containers:
            containers = soup.find_all('ul', class_='listBox')

        for ul in containers:
            li_tags = ul.find_all('li', recursive=False)
            for li in li_tags:
                try:
                    article = self._parse_list_item(li, section)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                    continue

        return articles

    def _get_page_url(self, category_url: str, page_num: int, total_pages: int) -> str:
        """
        根据总页数计算分页 URL（逆向分页）

        Args:
            category_url: 板块入口 URL
            page_num: 页码（从 1 开始）
            total_pages: 总页数

        Returns:
            实际请求 URL
        """
        if page_num == 1:
            return category_url

        if page_num > total_pages:
            logger.warning(f"[{self.SOURCE_NAME}] 请求页码 {page_num} 超过总页数 {total_pages}")
            return category_url

        # 逆向分页计算：page_index = total_pages - (page_num - 1)
        page_index = total_pages - (page_num - 1)

        # 构造 URL：category.htm -> category/page_index.htm
        base = category_url.rsplit('.', 1)[0]
        return f"{base}/{page_index}.htm"

    def _parse_list_item(self, li: Tag, section: str) -> Optional[ArticleData]:
        """
        解析列表项（兼容两种 DOM 结构）

        结构1（通知类 - .hireBox）：
        <li>
            <div class="name">
                <span></span>
                <a href="../../info/1133/1651.htm" title="...">标题</a>
            </div>
            <p class="time">2023-09-22</p>
        </li>

        结构2（新闻类 - .listBox）：
        <li>
            <a href="https://mp.weixin.qq.com/s/..." class="img"><img ...></a>
            <div class="content">
                <p class="time">2025-07-11</p>
                <div class="title">标题</div>
                <a href="https://mp.weixin.qq.com/s/..."><button>查看详情</button></a>
            </div>
        </li>

        Args:
            li: BeautifulSoup 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取日期（两种结构都有 .time）
        time_tag = li.select_one('.time')
        date_str = time_tag.get_text(strip=True) if time_tag else ""

        # 日期强制校验：真正的公文一定有日期
        if not date_str or not re.search(r'\d{4}', date_str):
            return None

        # 🌟 尝试方式 A（通知类 - .hireBox）：.name a
        a_tag = li.select_one('.name a')
        if a_tag:
            title = a_tag.get_text(strip=True)
            raw_href = a_tag.get('href')
        else:
            # 🌟 尝试方式 B（新闻类 - .listBox）：.title 获取标题，a 获取链接
            title_tag = li.select_one('.title')
            title = title_tag.get_text(strip=True) if title_tag else ""

            # 获取链接（可能是第一个 a 或包含 button 的 a）
            a_tag = li.find('a')
            if not a_tag:
                return None
            raw_href = a_tag.get('href')

            # 如果没找到标题，尝试从 a 标签的 title 属性获取
            if not title and a_tag:
                title = a_tag.get('title', '')

        if not title or not raw_href:
            return None

        # 确保 raw_href 是字符串
        if not isinstance(raw_href, str):
            return None

        # 转换为绝对 URL（使用 safe_urljoin 处理微信链接）
        full_url = self.safe_urljoin(self.BASE_URL, raw_href)

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
            标准化日期（如 2026-03-04）
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

        # 提取标题：.left h2.title 或降级使用通用 h1/h3
        title = ""
        title_tag = soup.select_one('.left h2.title')
        if title_tag:
            title = title_tag.get_text(strip=True)

        if not title:
            # 降级方案
            title_tag = soup.find('h2') or soup.find('h1') or soup.find('h3')
            if title_tag:
                title = title_tag.get_text(strip=True)

        # 提取正文：#vsb_content 或 .content
        body_html = ""
        body_text = ""
        content_div = soup.find('div', id='vsb_content')

        if content_div:
            # 移除动态脚本
            for script in content_div.find_all('script'):
                script.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')
        else:
            # 备用提取：.content
            content_div = soup.find('div', class_='content')
            if content_div:
                for script in content_div.find_all('script'):
                    script.decompose()
                body_html = str(content_div)
                body_text = content_div.get_text(strip=True, separator='\n')

        # 纯图片内容防御
        if len(body_text) < 50 and '<img' in body_html:
            logger.info(f"[{self.SOURCE_NAME}] 检测到纯图片内容，保留 HTML 结构: {url[:50]}...")

        # 提取附件（使用基类的终极附件打捞逻辑）
        attachments = self._extract_attachments(soup, url)

        # 提取精确时间：.info 中包含『发布时间:2023-09-22』
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
        # 优先从 .info 中提取（如『发布时间:2023-09-22』）
        info_div = soup.find('div', class_='info')
        if info_div:
            text = info_div.get_text(strip=True)
            # 匹配发布时间
            match = re.search(r'发布时间[：:]\s*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)', text)
            if match:
                time_str = match.group(1)
                time_str = time_str.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
                return time_str

        # 降级：全文匹配
        full_text = soup.get_text()
        patterns = [
            r'发布时间[：:]\s*(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?)',
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
