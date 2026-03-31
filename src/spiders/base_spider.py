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

from src.utils.rss_content import normalize_rss_content

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

    def _normalize_detail_fragment(self, fragment_html: str, page_url: str) -> Dict[str, Any]:
        normalized = normalize_rss_content(fragment_html or "", base_url=page_url)
        raw_markdown = str(normalized.get("markdown") or "").strip()
        content_blocks = normalized.get("blocks")
        image_assets = normalized.get("image_assets")
        attachments = normalized.get("attachments")

        if not isinstance(content_blocks, list):
            content_blocks = []
        if not isinstance(image_assets, list):
            image_assets = []
        if not isinstance(attachments, list):
            attachments = []

        images: List[str] = []
        for asset in image_assets:
            if not isinstance(asset, dict):
                continue
            image_url = str(asset.get("url") or "").strip()
            if image_url and image_url not in images:
                images.append(image_url)

        return {
            "plain_text": str(normalized.get("plain_text") or "").strip(),
            "body_html": fragment_html or "",
            "raw_markdown": raw_markdown,
            "content_blocks": content_blocks,
            "image_assets": image_assets,
            "images": images,
            "attachments": attachments,
        }

    def _get_site_domain_key(self, url: str) -> str:
        hostname = str(urlparse(url).hostname or "").strip().lower()
        if not hostname:
            return ""
        parts = [part for part in hostname.split(".") if part]
        if len(parts) <= 2:
            return hostname
        cn_suffixes = {"com.cn", "edu.cn", "gov.cn", "org.cn", "net.cn"}
        suffix = ".".join(parts[-2:])
        if suffix in cn_suffixes and len(parts) >= 3:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    def _extract_iframe_urls(
        self,
        scope: Any,
        page_url: str,
        max_iframes: int = 3,
    ) -> List[str]:
        if not scope:
            return []

        allowed_domain = self._get_site_domain_key(page_url)
        iframe_urls: List[str] = []
        seen_urls = set()

        for node in scope.find_all(["iframe", "frame"]):
            raw_src = (
                node.get("src")
                or node.get("data-src")
                or node.get("data-original")
                or ""
            )
            iframe_url = self.safe_urljoin(page_url, str(raw_src or "").strip())
            if not iframe_url or iframe_url in seen_urls:
                continue
            if iframe_url.startswith(("javascript:", "about:", "data:")):
                continue

            iframe_domain = self._get_site_domain_key(iframe_url)
            if allowed_domain and iframe_domain and iframe_domain != allowed_domain:
                continue

            seen_urls.add(iframe_url)
            iframe_urls.append(iframe_url)
            if len(iframe_urls) >= max_iframes:
                break

        return iframe_urls

    def _merge_detail_payload(
        self,
        target: Dict[str, Any],
        incoming: Dict[str, Any],
    ) -> Dict[str, Any]:
        incoming_body = str(incoming.get("body_text") or "").strip()
        if incoming_body:
            current_body = str(target.get("body_text") or "").strip()
            target["body_text"] = (
                f"{current_body}\n\n{incoming_body}" if current_body else incoming_body
            )

        for field_name in ("body_html", "raw_markdown"):
            incoming_value = str(incoming.get(field_name) or "").strip()
            if not incoming_value:
                continue
            current_value = str(target.get(field_name) or "").strip()
            target[field_name] = (
                f"{current_value}\n\n{incoming_value}"
                if current_value
                else incoming_value
            )

        for list_field in ("content_blocks", "image_assets"):
            current_items = (
                target.get(list_field)
                if isinstance(target.get(list_field), list)
                else []
            )
            incoming_items = (
                incoming.get(list_field)
                if isinstance(incoming.get(list_field), list)
                else []
            )
            target[list_field] = [*current_items, *incoming_items]

        current_images = target.get("images") if isinstance(target.get("images"), list) else []
        incoming_images = (
            incoming.get("images") if isinstance(incoming.get("images"), list) else []
        )
        seen_images = {
            str(item or "").strip()
            for item in current_images
            if str(item or "").strip()
        }
        for image_url in incoming_images:
            clean_url = str(image_url or "").strip()
            if not clean_url or clean_url in seen_images:
                continue
            seen_images.add(clean_url)
            current_images.append(clean_url)
        target["images"] = current_images

        current_attachments = (
            target.get("attachments")
            if isinstance(target.get("attachments"), list)
            else []
        )
        incoming_attachments = (
            incoming.get("attachments")
            if isinstance(incoming.get("attachments"), list)
            else []
        )
        seen_attachment_urls = {
            str(item.get("url") or "").strip()
            for item in current_attachments
            if isinstance(item, dict) and str(item.get("url") or "").strip()
        }
        for item in incoming_attachments:
            if not isinstance(item, dict):
                continue
            attachment_url = str(item.get("url") or "").strip()
            if not attachment_url or attachment_url in seen_attachment_urls:
                continue
            seen_attachment_urls.add(attachment_url)
            current_attachments.append(dict(item))
        target["attachments"] = current_attachments

        return target

    def _extract_iframe_detail_payload(
        self,
        scope: Any,
        page_url: str,
    ) -> Dict[str, Any]:
        iframe_urls = self._extract_iframe_urls(scope, page_url)
        if not iframe_urls:
            return {}

        payload: Dict[str, Any] = {
            "body_text": "",
            "body_html": "",
            "raw_markdown": "",
            "content_blocks": [],
            "image_assets": [],
            "images": [],
            "attachments": [],
        }
        content_selectors = [
            ".v_news_content",
            "#vsb_content",
            ".news_conent_two_text",
            ".content_m",
            ".article-content",
            ".article-body",
            ".content",
            "main",
            "article",
            "body",
        ]

        for iframe_url in iframe_urls:
            response = self._safe_get(iframe_url)
            if not response:
                continue

            try:
                iframe_soup = BeautifulSoup(response.text, "lxml")
            except Exception:
                try:
                    iframe_soup = BeautifulSoup(response.text, "html.parser")
                except Exception:
                    continue

            for tag in iframe_soup(["script", "style", "noscript"]):
                tag.decompose()

            iframe_node = None
            for selector in content_selectors:
                iframe_node = iframe_soup.select_one(selector)
                if iframe_node:
                    break

            if not iframe_node:
                continue

            normalized_content = self._normalize_detail_fragment(
                str(iframe_node),
                iframe_url,
            )
            iframe_payload = {
                "body_text": (
                    str(normalized_content.get("plain_text") or "").strip()
                    or iframe_node.get_text(" ", strip=True)
                ),
                "body_html": str(normalized_content.get("body_html") or "").strip(),
                "raw_markdown": str(
                    normalized_content.get("raw_markdown") or ""
                ).strip(),
                "content_blocks": (
                    normalized_content.get("content_blocks")
                    if isinstance(normalized_content.get("content_blocks"), list)
                    else []
                ),
                "image_assets": (
                    normalized_content.get("image_assets")
                    if isinstance(normalized_content.get("image_assets"), list)
                    else []
                ),
                "images": (
                    normalized_content.get("images")
                    if isinstance(normalized_content.get("images"), list)
                    else []
                ),
                "attachments": self._extract_attachments(iframe_node, iframe_url),
            }
            normalized_attachments = (
                normalized_content.get("attachments")
                if isinstance(normalized_content.get("attachments"), list)
                else []
            )
            iframe_payload["attachments"] = self._merge_detail_payload(
                {"attachments": iframe_payload["attachments"]},
                {"attachments": normalized_attachments},
            ).get("attachments", [])

            if str(iframe_payload.get("body_text") or "").strip():
                payload = self._merge_detail_payload(payload, iframe_payload)

        return payload

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

        fragment_html = str(core_container)
        normalized_content = self._normalize_detail_fragment(fragment_html, url)
        normalized_attachments = normalized_content.get("attachments")
        if isinstance(normalized_attachments, list):
            seen_attachment_urls = {
                str(item.get("url") or "").strip()
                for item in attachments
                if isinstance(item, dict) and str(item.get("url") or "").strip()
            }
            for item in normalized_attachments:
                if not isinstance(item, dict):
                    continue
                attachment_url = str(item.get("url") or "").strip()
                if not attachment_url or attachment_url in seen_attachment_urls:
                    continue
                seen_attachment_urls.add(attachment_url)
                attachments.append(dict(item))

        detail_payload = {
            "body_text": core_container.get_text(separator='\n', strip=True),
            "body_html": normalized_content.get("body_html", ""),
            "raw_markdown": normalized_content.get("raw_markdown", ""),
            "content_blocks": normalized_content.get("content_blocks", []),
            "image_assets": normalized_content.get("image_assets", []),
            "images": normalized_content.get("images", []),
            "attachments": attachments,
        }
        iframe_payload = self._extract_iframe_detail_payload(core_container, url)
        if iframe_payload:
            detail_payload = self._merge_detail_payload(detail_payload, iframe_payload)

        return {
            'title': soup.title.get_text(strip=True).split('-')[0].strip() if soup.title else "",
            'url': url,
            'body_text': detail_payload.get("body_text", ""),
            'body_html': detail_payload.get("body_html", ""),
            'raw_markdown': detail_payload.get("raw_markdown", ""),
            'content_blocks': detail_payload.get("content_blocks", []),
            'image_assets': detail_payload.get("image_assets", []),
            'images': detail_payload.get("images", []),
            'attachments': detail_payload.get("attachments", []),
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
        images = []  # 🌟 新增：提取图片链接列表
        body_html = ""
        raw_markdown = ""
        content_blocks: List[Dict[str, Any]] = []
        image_assets: List[Dict[str, Any]] = []
        if content_div:
            normalized_content = self._normalize_detail_fragment(str(content_div), url)
            body_html = str(normalized_content.get("body_html") or "")
            raw_markdown = str(normalized_content.get("raw_markdown") or "").strip()
            content_blocks = (
                normalized_content.get("content_blocks")
                if isinstance(normalized_content.get("content_blocks"), list)
                else []
            )
            image_assets = (
                normalized_content.get("image_assets")
                if isinstance(normalized_content.get("image_assets"), list)
                else []
            )
            images = (
                normalized_content.get("images")
                if isinstance(normalized_content.get("images"), list)
                else []
            )

            content_soup = BeautifulSoup(body_html, 'lxml')
            # 提取纯文本
            for tag in content_soup.find_all(['script', 'style']):
                tag.decompose()
            body_text = content_soup.get_text(separator='\n', strip=True)

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
            'raw_markdown': raw_markdown,
            'content_blocks': content_blocks,
            'image_assets': image_assets,
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
