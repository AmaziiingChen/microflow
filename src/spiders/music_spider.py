"""
音乐学院爬虫 - V3 多源数据订阅架构

特性：
- 继承 BaseSpider，复用翻页推演、详情解析、微信支持
- 自动翻页推演（使用基类 get_all_page_urls）
- 微信公众号链接智能识别与解析
- 日期格式标准化
- 附件智能提取

优化重点：
- 音乐学院文章多数为微信公众号链接，基类已内置微信解析支持
- 列表页识别微信链接并标记，详情页自动路由到微信解析器
"""

import re
import logging
from typing import Dict, List, Optional
from bs4 import BeautifulSoup, Tag

from .base_spider import BaseSpider, ArticleData

logger = logging.getLogger(__name__)


class MusicSpider(BaseSpider):
    """音乐学院网站爬虫"""

    SOURCE_NAME = "音乐学院"
    BASE_URL = "https://musicyyds.sztu.edu.cn"

    # 🌟 提升为类属性，供 Scheduler 调度器读取
    SECTIONS = {
        "封面新闻": "https://musicyyds.sztu.edu.cn/zxdt/fmxw.htm",
        "学生事务": "https://musicyyds.sztu.edu.cn/zxdt/xssw.htm",
        "教研活动": "https://musicyyds.sztu.edu.cn/zxdt/jyhd.htm",
    }

    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """
        获取文章列表

        Args:
            page_num: 页码，从 1 开始
            section_name: 指定板块名称，为 None 时遍历所有板块
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        # 兼容旧调用方式：如果 limit 为 None，使用默认值
        if limit is None:
            limit = 25

        articles = []
        sections_to_fetch = {section_name: self.SECTIONS[section_name]} if section_name else self.SECTIONS

        for section, entry_url in sections_to_fetch.items():
            try:
                section_articles = self._fetch_section_list(entry_url, section, limit=limit)
                articles.extend(section_articles)
            except Exception as e:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 列表抓取失败: {e}")
                continue

        return articles

    def _fetch_section_list(self, entry_url: str, section: str, limit: Optional[int] = None) -> List[ArticleData]:
        """抓取单个板块的文章列表（智能翻页，按需停止）"""
        # 兼容旧调用方式：如果 limit 为 None，使用默认值
        if limit is None:
            limit = 25

        articles = []

        # 🌟 使用基类的自动翻页推演
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            # 已达到上限，停止请求
            if len(articles) >= limit:
                break

            response = self._safe_get(target_url)
            if not response:
                continue

            soup = BeautifulSoup(response.text, 'lxml')

            # 兼容深技大常见的列表容器
            list_container = (
                soup.select_one("ul.picture_fly")  # 音乐学院带图片列表
                or soup.select_one(".list ul")
                or soup.select_one(".news_list ul")
                or soup.select_one(".list-box ul")
                or soup.select_one("ul.list-gl")
            )

            if not list_container:
                logger.warning(f"[{self.SOURCE_NAME}] 板块 '{section}' 未找到列表容器: {target_url}")
                continue

            items = list_container.find_all("li")

            for item in items:
                # 达到上限立即停止
                if len(articles) >= limit:
                    break
                try:
                    article = self._parse_list_item(item, section)
                    if article:
                        articles.append(article)
                except Exception as e:
                    logger.debug(f"[{self.SOURCE_NAME}] 解析列表项失败: {e}")
                    continue

        return articles[:limit]

    def _parse_list_item(self, item: Tag, section: str) -> Optional[ArticleData]:
        """解析单条列表项"""
        a_tag = item.find("a")
        if not a_tag:
            return None

        # 提取标题：优先从 h3 的 title 属性获取，其次从 a 的 title 属性获取
        h3_tag = a_tag.find("h3")
        if h3_tag:
            title = h3_tag.get("title") or h3_tag.get_text(strip=True)
        else:
            title = a_tag.get("title")

        if not title:
            # 尝试从文本中提取，但需要清洗
            title = a_tag.get_text(strip=True)

        if not title:
            return None

        # 清洗标题：移除多余的空白和特殊字符
        title = self._clean_title(title)

        href = a_tag.get("href")
        if not href or href.startswith('javascript:') or href == '#':
            return None

        # 构建完整 URL
        full_url = self.safe_urljoin(self.BASE_URL + '/', href)

        # 提取日期：优先从 info 容器中的 span 获取
        date_str = ""
        info_div = a_tag.find("div", class_="info")
        if info_div:
            date_span = info_div.find("span")
            if date_span:
                date_str = date_span.get_text(strip=True)
        else:
            # 兼容旧格式
            date_span = item.find("span")
            if date_span:
                date_str = date_span.get_text(strip=True)

        # 标准化日期格式
        normalized_date = self._normalize_date(date_str)

        # 🌟 检测是否为微信公众号链接
        is_wechat = 'mp.weixin.qq.com' in full_url

        return {
            'title': title,
            'url': full_url,
            'date': normalized_date,
            'category': section,
            'source_name': self.SOURCE_NAME,
            'is_wechat': is_wechat  # 标记微信文章，便于后续处理
        }

    def _clean_title(self, title: str) -> str:
        """
        清洗标题：移除开头的数字序号和多余空白

        支持格式：
        - "1关于..." -> "关于..."
        - "10. 关于..." -> "关于..."
        - "100、关于..." -> "关于..."
        """
        if not title:
            return title

        # 匹配开头的数字序号（可选的点、顿号、空格等）
        cleaned = re.sub(r'^\d+[.、\s]\s*', '', title)

        # 移除多余的空白字符
        cleaned = re.sub(r'\s+', ' ', cleaned)

        return cleaned.strip()

    def _normalize_date(self, date_str: str) -> str:
        """
        标准化日期格式为 YYYY-MM-DD

        支持格式：
        - 2024-03-15
        - 2024/03/15
        - 2024年3月15日
        """
        if not date_str:
            return ""

        # 直接把字符串里所有的连续数字块揪出来
        nums = re.findall(r'\d+', date_str)

        # 只要能抓到 3 块数字（年、月、日），并且第一块是 4 位数
        if len(nums) >= 3 and len(nums[0]) == 4:
            year, month, day = nums[0], nums[1], nums[2]
            # 强制组装为标准格式，个位数自动补 0
            return f"{year}-{int(month):02d}-{int(day):02d}"

        return ""

    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情

        🌟 基类已自动处理微信链接路由：
        - 微信链接 -> _fetch_wechat_detail（支持图片提取、时间戳解析）
        - 普通链接 -> 标准详情解析（三级时间防御、附件提取）
        """
        return super().fetch_detail(url)

    # 保留旧的兼容方法名，供旧代码调用
    def get_soup(self, url: str) -> Optional[BeautifulSoup]:
        """兼容旧代码的方法：获取 BeautifulSoup 对象"""
        response = self._safe_get(url)
        if not response:
            return None
        return BeautifulSoup(response.text, 'lxml')
