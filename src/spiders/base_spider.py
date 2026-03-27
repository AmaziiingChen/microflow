import logging
import re
import requests
import time
import random
import json
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin, urlparse, parse_qs, urlencode
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 标准化的文章数据结构
ArticleData = Dict[str, Any]

class BaseSpider(ABC):
    """
    爬虫抽象基类 V3.1 - 全自动感知架构
    
    升级亮点：
    1. 自动翻页推演：支持常规 p_no 和 NMNE 递增兜底。
    2. 附件精准定位：结合全网页扫描与容器锁定，杜绝导航栏污染。
    3. 底层时间溯源：三级时间防御体系（Meta -> 信息栏 -> 全文截断锚定）。
    4. 统一微信解析：基类接管跨域的微信公众号图文解析。
    """

    SOURCE_NAME: str = "unknown"
    BASE_URL: str = ""
    ATTACHMENT_NAME_BLACKLIST = ["实验室介绍", "返回顶部", "打印本页", "关闭窗口"]

    def __init__(self):
        """初始化带重试机制的 Session"""
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        
        # 👇 核心修改在这里：显式扩展底层 Socket 连接池大小
        adapter = HTTPAdapter(
            pool_connections=20,  # 缓存的独立域名连接池数量（应对图片/附件CDN跨域）
            pool_maxsize=20,      # 连接池最大容量，避免高频请求时频繁断开/建立 TCP 握手
            max_retries=retry_strategy
        )
        
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    @abstractmethod
    def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
        """获取文章列表（由子类实现具体选择器）

        Args:
            page_num: 页码（保留兼容）
            section_name: 板块名称
            limit: 每个板块抓取的文章上限，None 表示不限制
        """
        pass

    # --- 核心升级：全自动翻页推演中枢 (修复 NMNE 缺失 p_no 的问题) ---
    def get_all_page_urls(self, entry_url: str) -> List[str]:
        response = self._safe_get(entry_url)
        if not response: return [entry_url]
        
        soup = BeautifulSoup(response.text, 'lxml')
        page_urls = [entry_url]

        # 寻找"尾页"按钮，这是所有规律的源头
        last_btn = soup.find('a', string=lambda t: t and '尾页' in t)  # type: ignore[call-overload]
        if not last_btn: 
            logger.info(f"[{self.SOURCE_NAME}] 未找到翻页组件，视为单页板块。")
            return page_urls

        href = last_btn.get('href', '')
        
        try:
            # 模式 1：动态参数型 (例如中德通知公告: ?...p=25)
            if '?' in href and ('p=' in href or 'page=' in href or 'a' in href):
                total_match = re.search(r'[p|page|a]\d*=(\d+)', href)
                if total_match:
                    total_pages = int(total_match.group(1))
                    base_parts = urlparse(href)
                    query = parse_qs(base_parts.query)
                    page_key = [k for k in query.keys() if 'p' in k.lower() or 'page' in k.lower()][0]
                    
                    for p in range(2, total_pages + 1):
                        query[page_key] = [str(p)]
                        new_url = urljoin(entry_url, f"?{urlencode(query, doseq=True)}")
                        page_urls.append(new_url)

            # 模式 2：静态逆向型
            else:
                total_pages = 1
                # 尝试一：通过 p_no 找总页数
                total_text = last_btn.find_parent().find_previous_sibling('span', class_='p_no')
                if total_text:
                    total_pages = int(total_text.get_text(strip=True))
                
                # 尝试二（NMNE专属兜底）：通过"下页"的链接提取总页数
                if total_pages == 1:
                    next_btn = soup.find('a', string=lambda t: t and ('下页' in t or '下一页' in t))  # type: ignore[call-overload]
                    if next_btn:
                        n_match = re.search(r'(\d+)\.htm', next_btn.get('href', ''))
                        if n_match: total_pages = int(n_match.group(1)) + 1

                prefix_match = re.match(r'(.*)\d+\.htm', href)
                prefix = prefix_match.group(1) if prefix_match else ""
                
                for k in range(2, total_pages + 1):
                    index = total_pages - k + 1
                    page_urls.append(urljoin(entry_url, f"{prefix}{index}.htm"))

        except Exception as e:
            logger.error(f"[{self.SOURCE_NAME}] 翻页推演失败: {e}")

        return list(dict.fromkeys(page_urls))

    # --- 核心升级：高精度详情页解析器（多容器兼容 + 三级时间防御） ---
    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        # 1. 拦截微信外链
        if 'mp.weixin.qq.com' in url: 
            return self._fetch_wechat_detail(url)

        response = self._safe_get(url)
        if not response: return None
        soup = BeautifulSoup(response.text, 'lxml')
        
        # 2. 三级精确时间防御体系
        exact_time = ""
        # 第一级：Meta 底层寻找
        meta_date = soup.find('meta', attrs={'name': 'PubDate'})
        if meta_date and meta_date.get('content'):
            exact_time = meta_date['content']
            
        if not exact_time:
            info_selectors = ['.v_news_info', '.content_t', '.cnt_note', '.info', '.article-meta', '.news_info', '.time', '.article-time', '.source', '.detail_message', '.message_right']
            
            # 🚨 把这里原本那个坑人的 next() 替换成：
            info_bar = None
            for selector in info_selectors:
                info_bar = soup.select_one(selector)
                if info_bar: 
                    break
            
            if info_bar:
                match = re.search(r'(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?(?:\s*\d{1,2}:\d{1,2}(?::\d{1,2})?)?)', info_bar.get_text())
                if match: exact_time = match.group(1)
                
        if not exact_time:
            # 第三级：前 1000 字符带锁盲狙
            match = re.search(r'(?:发布时间|时间|日期)[：:]\s*(\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?(?:\s*\d{1,2}:\d{1,2}(?::\d{1,2})?)?)', soup.get_text()[:1000])
            if match: exact_time = match.group(1)

        if exact_time:
            exact_time = str(exact_time).replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')

        # 3. 多路径正文提取
        content_selectors = [
            ('div', {'class': 'v_news_content'}),      
            ('div', {'id': 'vsb_content'}),           
            ('div', {'class': 'news_conent_two_text'}), 
            ('div', {'class': 'content_m'}),          
            ('div', {'class': 'article-content'}),    
            ('div', {'class': 'content'})             
        ]
        
        core_container = None
        for tag, attrs in content_selectors:
            core_container = soup.find(tag, attrs=attrs)  # type: ignore[arg-type]
            if core_container:
                break 
        
        if not core_container:
            logger.warning(f"[{self.SOURCE_NAME}] 所有已知容器均未匹配到正文: {url}")
            return None

        # 4. 附件提取（隔离导航栏）
        article_wrapper = core_container.find_parent('form', attrs={"name": "_newscontent_fromname"}) or \
                          core_container.parent.parent
        
        attachments = self._extract_attachments(article_wrapper if article_wrapper else soup, url)

        return {
            'title': soup.title.get_text(strip=True).split('-')[0].strip() if soup.title else "",
            'url': url,
            'body_text': core_container.get_text(separator='\n', strip=True),
            'attachments': attachments,
            'source_name': self.SOURCE_NAME,
            'exact_time': exact_time
        }

    # --- 新增：微信公众号统一解析器（穿透 JS 动态渲染版） ---
    def _fetch_wechat_detail(self, url: str) -> Optional[ArticleData]:
        response = self._safe_get(url)
        if not response: return None

        html_content = response.text
        soup = BeautifulSoup(html_content, 'lxml')

        title_tag = soup.find('h1', class_='rich_media_title')
        title = title_tag.get_text(strip=True) if title_tag else ""

        content_div = soup.find('div', class_='rich_media_content', id='js_content')
        body_text = ""
        body_html = ""
        images = []  # 🌟 新增：提取图片链接列表
        if content_div:
            # 保存原始 HTML（用于纯图片检测）
            body_html = str(content_div)
            # 提取纯文本
            for tag in content_div.find_all(['script', 'style']): tag.decompose()
            body_text = content_div.get_text(separator='\n', strip=True)

            # 🌟 提取所有图片链接
            for img in content_div.find_all('img'):
                # 微信图片的真实地址在 data-src 属性中
                img_url = img.get('data-src') or img.get('src')
                if img_url and img_url.startswith('http'):
                    images.append(img_url)

        # 🌟 核心升级：直接从 JS 变量中提取精确的 Unix 时间戳
        exact_time = ""
        import datetime # 确保时间戳转换可用

        # 优先狙击：var ct = "1689048000";
        ct_match = re.search(r'var\s+ct\s*=\s*"(\d+)";', html_content)
        if ct_match:
            timestamp = int(ct_match.group(1))
            # 转换为 2026-03-20 14:30:00 的标准格式
            exact_time = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
        else:
            # 备用狙击：var publish_time = "2023-07-11"
            pub_match = re.search(r'var\s+publish_time\s*=\s*"([^"]+)"', html_content)
            if pub_match:
                exact_time = pub_match.group(1)

        # 极端兜底（万一微信哪天改了 JS 变量名）
        if not exact_time:
            time_tag = soup.find('em', id='publish_time')
            exact_time = time_tag.get_text(strip=True) if time_tag else ""

        return {
            'title': title, 'url': url, 'body_text': body_text, 'body_html': body_html,
            'images': images,  # 🌟 新增：图片链接列表
            'attachments': [], 'source_name': self.SOURCE_NAME, 'exact_time': exact_time
        }
    # --- 附件处理逻辑（保留自你原版） ---
    def _extract_attachments(self, scope: Any, article_url: str) -> List[Dict[str, str]]:
        """提取并清洗附件链接"""
        attachments = []
        seen_urls = set()
        
        # 统一匹配系统下载接口和常见后缀
        download_pattern = re.compile(r'download\.jsp|DownloadAttachUrl|\.(pdf|docx?|xlsx?|zip|rar)', re.IGNORECASE)

        for a_tag in scope.find_all('a', href=download_pattern):
            href = a_tag.get('href')
            if not href or href.startswith('javascript:'): continue
            
            full_url = self.safe_urljoin(article_url, href)
            if full_url in seen_urls: continue
            
            name = a_tag.get_text(strip=True) or "查看附件"
            if self._is_attachment_blacklisted(name): continue
            
            seen_urls.add(full_url)
            attachments.append({
                'name': name,
                'url': full_url,
                'download_type': self._get_download_type() 
            })
        return attachments

    # --- 其他工具方法（完全保留自你原版） ---
    def safe_urljoin(self, base: str, relative: str) -> str:
        return urljoin(base, relative) if relative else ""

    def _safe_get(self, url: str, **kwargs) -> Optional[requests.Response]:
        try:
            # 底层拟人化微抖动，防止被学校防火墙封禁
            time.sleep(random.uniform(0.2, 0.5))
            res = self.session.get(url, timeout=12, **kwargs)
            res.encoding = 'utf-8'
            return res
        except Exception as e:
            logger.warning(f"请求失败: {url} -> {e}")
            return None

    def _is_attachment_blacklisted(self, name: str) -> bool:
        return any(kw in name for kw in self.ATTACHMENT_NAME_BLACKLIST)

    def _get_download_type(self) -> str:
        return "direct" if self.SOURCE_NAME == "公文通" else "external"

    def close(self):
        self.session.close()

    def __enter__(self): return self
    def __exit__(self, *args): self.close()