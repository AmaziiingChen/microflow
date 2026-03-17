"""
商学院爬虫 - V2 多源数据订阅架构

支持 4 个板块的聚合抓取：
- 新闻动态、通知公告、学术动态、校园生活

特性：
- 动态解析总页数，实现精准逆向分页
- 精准匹配 li > a（标题/链接）和 li > span（日期）
- 纯图片内容防御
- 附件智能提取
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class BusinessSpider(BaseSpider):
    """商学院网站爬虫"""

    SOURCE_NAME = "商学院"
    BASE_URL = "https://bs.sztu.edu.cn"

    # 4 个板块的入口配置
    SECTIONS = {
        "新闻动态": "https://bs.sztu.edu.cn/index/xwdt.htm",
        "通知公告": "https://bs.sztu.edu.cn/index/tzgg.htm",
        "学术动态": "https://bs.sztu.edu.cn/index/xsdt.htm",
        "校园生活": "https://bs.sztu.edu.cn/index/xysh.htm"
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
        - <span class="p_count">共17页</span>
        - <a class="p_last" href="1.htm">尾页</a>
        - 分页链接中的最大数字

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

        # 方案1：查找 span.p_count（如 "共17页"）
        p_count = soup.find('span', class_='p_count')
        if p_count:
            text = p_count.get_text(strip=True)
            match = re.search(r'(\d+)', text)
            if match:
                total_pages = int(match.group(1))
                logger.debug(f"[{self.SOURCE_NAME}] 从 p_count 解析到总页数: {total_pages}")
                self._page_cache[entry_url] = total_pages
                return total_pages

        # 方案2：查找 a.p_last 的 href（如 "1.htm" 表示最后一页是 1.htm）
        p_last = soup.find('a', class_='p_last')
        if p_last:
            raw_href = p_last.get('href')
            if raw_href and isinstance(raw_href, str):
                href = str(raw_href)
                match = re.search(r'/(\d+)\.htm', href)
                if match:
                    last_page_num = int(match.group(1))
                    # 逆向分页：最后一页是 1.htm，倒数第二页是 2.htm...
                    # 总页数 = last_page_num 的倒数对应的页数
                    # 如果最后一页是 1.htm，说明总页数需要从其他地方获取
                    # 但通常 p_last 的数字就是最后一页对应的分页索引
                    # 对于逆向分页：第2页=N.htm，第3页=N-1.htm...最后一页=1.htm
                    # 所以从 p_last 的 href 中提取的数字就是第2页对应的数字
                    total_pages = last_page_num + 1  # 总页数 = 第2页索引 + 1
                    logger.debug(f"[{self.SOURCE_NAME}] 从 p_last 解析到总页数: {total_pages}")
                    self._page_cache[entry_url] = total_pages
                    return total_pages

        # 方案3：查找所有分页链接，取最大数字
        # 分页链接格式：/index/xwdt/16.htm, /index/xwdt/15.htm...
        # 过滤掉文章链接（包含 info/ 或 content/）
        page_links = soup.find_all('a', href=re.compile(r'/\d+\.htm$'))
        max_index = 0
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
                if index > max_index:
                    max_index = index

        if max_index > 0:
            # 逆向分页：最大数字对应第2页
            total_pages = max_index + 1
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

        # 精准匹配：li > a（标题/链接）和 li > span（日期）
        # 查找包含文章列表的 ul 容器
        ul_tags = soup.find_all('ul', class_='list-gl')
        if not ul_tags:
            ul_tags = soup.find_all('ul', class_='news-list')

        for ul in ul_tags:
            li_tags = ul.find_all('li', recursive=False)
            for li in li_tags:
                try:
                    article = self._parse_list_item(li, section)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                    continue

        # 如果没找到，尝试全局查找
        if not articles:
            li_tags = soup.select('ul li')
            for li in li_tags:
                # 只处理包含 a 标签的 li
                a_tag = li.find('a', recursive=False)
                if not a_tag:
                    continue
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
        根据总页数计算分页 URL

        商学院分页规则（逆向递减）：
        - 第 1 页：category.htm（入口 URL）
        - 第 2 页：N.htm（N = 总页数）
        - 第 3 页：N-1.htm
        - ...
        - 第 N 页：1.htm

        公式：page_index = total_pages - (page_num - 1)

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

    def _parse_list_item(self, li, section: str) -> Optional[ArticleData]:
        """
        解析列表项

        结构：<li><a href="...">标题</a><span>YYYY-MM-DD</span></li>

        Args:
            li: BeautifulSoup 元素
            section: 板块名称

        Returns:
            标准化的文章数据
        """
        # 精准提取：直接子级 a 标签
        a_tag = li.find('a', recursive=False)
        if not a_tag:
            # 降级：任意 a 标签
            a_tag = li.find('a')

        if not a_tag:
            return None

        # 提取标题
        title = a_tag.get_text(strip=True)
        href = a_tag.get('href', '')

        if not title or not href:
            return None

        # 转换为绝对 URL
        full_url = self.safe_urljoin(self.BASE_URL, href)

        # 精准提取：直接子级 span 标签（日期）
        date_str = ""
        span = li.find('span', recursive=False)
        if span:
            date_str = span.get_text(strip=True)
        else:
            # 降级：查找任意 span
            span = li.find('span')
            if span:
                date_str = span.get_text(strip=True)

        # 日期强制校验：真正的公文一定有日期
        # 如果没有日期或日期不包含四位数字，直接过滤
        if not date_str or not re.search(r'\d{4}', date_str):
            return None

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
            date_str: 原始日期字符串（如 2026-01-08 或 2026/01/08）

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

        # 提取标题（优先精准匹配，降级过滤导航栏）
        title = ""
        # 优先尝试精准匹配 VSB 常见的正文标题容器
        title_tag = soup.select_one('.show01 h3, .show01 h1, .news_conent_two_title')

        # 降级方案：遍历 h1 和 h3，剔除导航栏的无用文字
        if not title_tag:
            for tag in soup.find_all(['h1', 'h3']):
                text = tag.get_text(strip=True)
                if text and text not in ["首页", "商学院", "正文"]:
                    title_tag = tag
                    break

        if not title_tag:
            title_tag = soup.find('title')

        if title_tag:
            title = title_tag.get_text(strip=True)

        # 提取正文（div.v_news_content）
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

        # 提取附件：在整个 soup 中全局搜索
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