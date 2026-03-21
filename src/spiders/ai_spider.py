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

    # 🌟 修复：提升为类属性，供 Scheduler 调度器读取
    SECTIONS = {
        "院系新闻": "https://ai.sztu.edu.cn/xwzx/yxxw1.htm",
        "通知公告": "https://ai.sztu.edu.cn/xwzx/tzgg1/qb.htm"
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
        all_pages = self.get_all_page_urls(entry_url)

        for target_url in all_pages:
            # 已达到上限，停止请求
            if len(articles) >= limit:
                break

            response = self._safe_get(target_url)
            if not response: continue

            soup = BeautifulSoup(response.text, 'html.parser')

            container = soup.find('div', class_='havePictureList_list') or \
                        soup.find('div', class_='news_list') or \
                        soup.find('ul', class_='list-gl')

            a_tags = container.select('a') if container else soup.select('a')

            for a_tag in a_tags:
                # 达到上限立即停止
                if len(articles) >= limit:
                    break
                try:
                    article = self._parse_list_item(a_tag, section)
                    if article:
                        articles.append(article)
                except Exception:
                    continue

        return articles[:limit]

    def _parse_list_item(self, a_tag: Tag, section: str) -> Optional[ArticleData]:
        href = a_tag.get('href', '')
        if not href or href.startswith('javascript:') or href.startswith('#'):
            return None

        title = a_tag.get('title')
        date_str = ""

        # 🌟 优先从标题标签提取（h4/h5），避免提取到 <i> 序号
        if not title:
            title_tag = a_tag.find('h4', class_='text_single_lines') or a_tag.find('h5')
            if title_tag:
                title = title_tag.get_text(strip=True)

        # 最后的 fallback：从整个 a 标签提取，但需要清洗序号
        if not title:
            title = self._clean_title(a_tag.get_text(strip=True))

        dl = a_tag.find('dl')
        if dl:
            dd = dl.find('dd')
            if dd: date_str = dd.get_text(strip=True)

        if not date_str:
            time_more = a_tag.find('div', class_='time-more')
            if time_more:
                date_str = time_more.get_text(strip=True)

        # 🌟 核心防御网：解决 html.parser 暴力切开 <a> 和 <div> 的 Bug！
        # 如果 a 标签内是空的，我们就顺藤摸瓜去找它隔壁的“兄弟节点”
        if not title or not date_str:
            next_sibling = a_tag.find_next_sibling()
            info_plate = None
            
            # 找到包裹标题和时间的 div.info_plate
            if next_sibling and 'an_image_box' in next_sibling.get('class', []):
                info_plate = next_sibling.find_next_sibling('div', class_='info_plate')
            elif next_sibling and 'info_plate' in next_sibling.get('class', []):
                info_plate = next_sibling

            # 如果找到了跑出来的“兄弟节点”，从中抓取丢失的标题和时间
            if info_plate:
                if not title:
                    h4 = info_plate.find('h4', class_='text_single_lines')
                    title = h4.get_text(strip=True) if h4 else ""
                if not date_str:
                    time_more = info_plate.find('div', class_='time-more')
                    if time_more:
                        date_str = time_more.get_text(strip=True)

        if not title:
            return None

        normalized_date = self._normalize_date(date_str)
        if not normalized_date:
            return None

        full_url = self.safe_urljoin(self.BASE_URL, href)

        return {
            'title': title,
            'url': full_url,
            'date': normalized_date,
            'category': section,
            'source_name': self.SOURCE_NAME
        }

    def _normalize_date(self, date_str: str) -> str:
        """
        🔥 降维打击时间清洗：放弃字符串替换，直接强制抠取数字。
        无视不可见字符、乱码前缀、以及结尾的“日”字。
        """
        if not date_str: return ""
        
        # 直接把字符串里所有的连续数字块揪出来
        nums = re.findall(r'\d+', date_str)
        
        # 只要能抓到 3 块数字（年、月、日），并且第一块是 4 位数
        if len(nums) >= 3 and len(nums[0]) == 4:
            year, month, day = nums[0], nums[1], nums[2]
            # 强制组装为标准格式，个位数自动补 0
            return f"{year}-{int(month):02d}-{int(day):02d}"
            
        return ""

    def _clean_title(self, title: str) -> str:
        """
        清洗标题：移除开头的数字序号

        支持格式：
        - "1关于..." -> "关于..."
        - "10. 关于..." -> "关于..."
        - "100、关于..." -> "关于..."
        """
        if not title:
            return title

        # 匹配开头的数字序号（可选的点、顿号、空格等）
        cleaned = re.sub(r'^\d+[.、\s]\s*', '', title)

        return cleaned.strip()