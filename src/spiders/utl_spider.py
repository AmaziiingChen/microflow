"""
城市交通与物流学院爬虫 - V2 多源数据订阅架构

支持 2 个板块的聚合抓取：
- 学院动态、通知公告

特性：
- 异构页面分支解析（两个板块结构差异大）
- 逆向分页逻辑处理
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

    # 逆向分页配置：第 2 页为 23.htm
    MAX_PAGE = 23

    def __init__(self):
        super().__init__()
        # 板块配置（使用小写的 self.sections）
        self.sections = {
            "学院动态": "xwzx/xydt.htm",
            "通知公告": "xwzx/tzgg.htm"
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

    def _calculate_page_url(self, entry_url: str, page_num: int) -> str:
        """
        计算分页 URL（逆向分页，子目录风格）

        UTL 学院分页规则：
        - 第 1 页：xxx.htm
        - 第 2 页：xxx/23.htm, 22.htm...（逆向递减）
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
        # 示例：MAX_PAGE=23 时
        #   - page_num=2 -> 23-2+2 = 23
        #   - page_num=3 -> 23-3+2 = 22
        page_index = self.MAX_PAGE - page_num + 2

        base = entry_url.rsplit('.', 1)[0]  # 去掉 .htm
        return f"{base}/{page_index}.htm"

    def _parse_list_item_xydt(self, item: Tag, section: str) -> Optional[ArticleData]:
        """
        解析学院动态列表项

        结构：div.new_center_item
        - 标题：内部 h4 文本
        - 链接：内部 a.new_center_item_box 的 href
        - 日期：div.day-time 的文本

        Args:
            item: BeautifulSoup Tag 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取链接
        a_tag = item.select_one('a.new_center_item_box')
        if not a_tag:
            a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        # 提取标题
        h4 = item.select_one('h4')
        if h4:
            title = h4.get_text(strip=True)
        else:
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 提取日期
        date_str = ""
        date_elem = item.select_one('div.day-time')
        if date_elem:
            date_str = date_elem.get_text(strip=True)

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, str(href))

        return {
            'title': title,
            'url': full_url,
            'date': self._normalize_date(date_str),
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _parse_list_item_tzgg(self, item: Tag, section: str) -> Optional[ArticleData]:
        """
        解析通知公告列表项

        结构：div.new_item
        - 标题：内部 h3 文本
        - 链接：最外层 a 的 href
        - 日期：span.day + div.year-month 组合

        Args:
            item: BeautifulSoup Tag 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 提取链接（最外层 a）
        a_tag = item.find('a')
        if not a_tag:
            return None

        href = a_tag.get('href', '')
        if not href:
            return None

        # 提取标题
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
            # year_month 格式可能是 "2026-03" 或 "2026年03月"
            date_str = f"{year_month}-{day}"
        elif day_elem:
            date_str = day_elem.get_text(strip=True)

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, str(href))

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

        # 提取正文：仅限 div.v_news_content（不移除备用提取，避免噪音）
        body_html = ""
        body_text = ""
        content_div = soup.find('div', class_='v_news_content')

        if content_div:
            # 移除动态脚本
            for script in content_div.find_all('script'):
                script.decompose()

            body_html = str(content_div)
            body_text = content_div.get_text(strip=True, separator='\n')
        # 如果找不到 v_news_content，宁可留空也不回退到其他容器

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
