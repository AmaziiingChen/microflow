"""
爬虫基类模块 - V2 多源数据订阅架构

定义所有爬虫必须遵循的接口规范和通用工具方法。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin
import logging
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

# 标准化的文章数据结构
ArticleData = Dict[str, Any]


class BaseSpider(ABC):
    """
    爬虫抽象基类

    所有数据源爬虫必须继承此类并实现以下抽象方法：
    - fetch_list(): 获取文章列表
    - fetch_detail(): 获取文章详情

    统一返回格式确保后端处理逻辑的一致性。
    """

    # 子类必须设置的属性
    SOURCE_NAME: str = "unknown"  # 数据来源名称，如 '公文通', '新能源学院'
    BASE_URL: str = ""            # 站点基础 URL

    def __init__(self):
        """初始化带重试机制的 Session"""
        self.session = requests.Session()

        # 配置重试策略
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        # 设置默认请求头
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        })

    @abstractmethod
    def fetch_list(self, page_num: int = 1, **kwargs) -> List[ArticleData]:
        """
        获取文章列表（抽象方法）

        Args:
            page_num: 页码，从 1 开始
            **kwargs: 额外参数（如板块 ID 等）

        Returns:
            标准化的文章摘要列表，每项包含：
            {
                'title': str,       # 文章标题
                'url': str,         # 文章绝对 URL
                'date': str,        # 发布日期
                'category': str,    # 分类（可选）
                'source_name': str  # 来源名称
            }
        """
        pass

    @abstractmethod
    def fetch_detail(self, url: str) -> Optional[ArticleData]:
        """
        获取文章详情（抽象方法）

        Args:
            url: 文章的绝对 URL

        Returns:
            标准化的文章详情字典：
            {
                'title': str,           # 文章标题
                'url': str,             # 文章 URL
                'date': str,            # 发布日期
                'body_html': str,       # 正文 HTML（或纯文本）
                'body_text': str,       # 正文纯文本
                'attachments': list,    # 附件列表 [{'name': str, 'url': str}, ...]
                'source_name': str,     # 来源名称
                'exact_time': str       # 精确时间（可选）
            }
            如果抓取失败返回 None
        """
        pass

    def safe_urljoin(self, base: str, relative: str) -> str:
        """
        安全的 URL 拼接工具方法

        Args:
            base: 基础 URL
            relative: 相对路径

        Returns:
            绝对 URL
        """
        if not relative:
            return ""
        return urljoin(base, relative)

    def _safe_get(self, url: str, timeout: int = 10, **kwargs) -> Optional[requests.Response]:
        """
        安全的 HTTP GET 请求

        Args:
            url: 请求 URL
            timeout: 超时时间（秒）
            **kwargs: 传递给 requests.get 的额外参数

        Returns:
            Response 对象，失败返回 None
        """
        try:
            response = self.session.get(url, timeout=timeout, **kwargs)
            response.encoding = 'utf-8'
            return response
        except requests.exceptions.RequestException as e:
            logger.warning(f"网络请求失败: {url} -> {e}")
            return None
        except Exception as e:
            logger.error(f"未知请求错误: {url} -> {e}")
            return None

    def _calc_reverse_page_url(
        self,
        entry_url: str,
        page_num: int,
        max_page: int,
        path_style: str = "subdir"
    ) -> str:
        """
        逆向分页 URL 计算工具方法

        适用于深圳技术学院各学院的博达 CMS 系统，        这类系统的分页特点是：
        - 第 1 页：xxx.htm
        - 第 2 页：xxx/{max_page-1}.htm（例如 17.htm）
        - 第 3 页：xxx/{max_page-2}.htm（例如 16.htm）
        - 分页数字随页码增加而递减

        通用公式：
        - page_index = max_page - page_num + 2
        - 鎖：当 page_num=1 时直接返回 entry_url

        Args:
            entry_url: 板块入口 URL（如 https://xxx.sztu.edu.cn/xydt.htm）
            page_num: 目标页码（从 1 开始）
            max_page: 该板块的最大分页基数（第 2 页对应的数字）
            path_style: URL 构造风格
                - "subdir"（默认）: xxx.htm -> xxx/{index}.htm
                - "flat"（扁平）: xxx.htm -> xxx/{index}.htm（同级目录）

        Returns:
            计算后的完整 URL
        """
        # 第 1 页极速返回：直接使用入口 URL
        if page_num == 1:
            return entry_url

        # 防御：无效页码
        if page_num < 1:
            logger.warning(f"无效页码: {page_num}，将使用第 1 页")
            return entry_url

        # 动态计算分页索引
        # 公式：索引 = max_page - page_num + 2
        # 示例：max_page=18 时
        #   - page_num=2 -> 18-2+2 = 18
        #   - page_num=3 -> 18-3+2 = 17
        page_index = max_page - page_num + 2

        # 防御：索引不能小于 1
        if page_index < 1:
            logger.warning(f"计算的分页索引 {page_index} 无效，已超过最大页数")
            page_index = 1

        # 构造分页 URL
        # https://xxx/xydt.htm -> https://xxx/xydt/{index}.htm
        base = entry_url.rsplit('.', 1)[0]  # 去掉 .htm
        return f"{base}/{page_index}.htm"

    # 附件名称黑名单：过滤噪音链接
    ATTACHMENT_NAME_BLACKLIST = ["实验室介绍", "返回顶部"]

    def _extract_attachments(
        self,
        soup: BeautifulSoup,
        article_url: str = ""
    ) -> List[Dict[str, str]]:
        """
        统一的全局附件提取方法（所有子类共享）

        核心策略：
        1. 打破容器限制：在整个详情页 soup 中全局搜索
        2. 精准匹配：查找 href 包含 download.jsp 或 DownloadAttachUrl 的 a 标签
        3. 路径绝对化：使用 self.safe_urljoin(self.BASE_URL, href) 生成完整 URL
        4. 数据清洗：提取链接文本作为文件名，过滤空链接
        5. 智能分流：根据来源名称注入 download_type 字段
        6. 黑名单过滤：忽略名称包含特定关键词的附件

        Args:
            soup: BeautifulSoup 对象（整篇详情页）
            article_url: 文章 URL（备用，用于提取域名）

        Returns:
            附件列表 [{'name': str, 'url': str, 'download_type': str}, ...]
            download_type: "direct"（可直接下载）或 "external"（需浏览器处理验证码）
        """
        attachments = []
        seen_urls = set()  # 去重

        if not soup:
            return attachments

        # 🌟 核心正则：匹配 download.jsp 或 DownloadAttachUrl
        download_pattern = re.compile(r'download\.jsp|DownloadAttachUrl', re.IGNORECASE)

        # 在整个 soup 中全局搜索所有匹配的 a 标签
        for a_tag in soup.find_all('a', href=download_pattern):
            raw_href = a_tag.get('href')
            if not raw_href or not isinstance(raw_href, str):
                continue

            # 跳过空链接
            raw_href = raw_href.strip()
            if not raw_href:
                continue

            # 转换为绝对路径
            full_url = self.safe_urljoin(self.BASE_URL, raw_href)

            # 去重
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # 提取文件名
            name = a_tag.get_text(strip=True) or "未命名附件"

            # 🌟 黑名单过滤：忽略特定名称的附件
            if self._is_attachment_blacklisted(name):
                logger.debug(f"[{self.SOURCE_NAME}] 过滤黑名单附件: {name}")
                continue

            # 🌟 智能分流：根据来源名称注入 download_type
            download_type = self._get_download_type()

            attachments.append({
                'name': name,
                'url': full_url,
                'download_type': download_type
            })

        # 如果核心正则没找到，尝试备用方案：传统文件后缀
        if not attachments:
            file_pattern = re.compile(r'\.(pdf|doc|docx|xls|xlsx|ppt|pptx|rar|zip|7z|tar|gz|txt|rtf|wps)$', re.IGNORECASE)
            for a_tag in soup.find_all('a', href=file_pattern):
                raw_href = a_tag.get('href')
                if not raw_href or not isinstance(raw_href, str):
                    continue

                raw_href = raw_href.strip()
                if not raw_href:
                    continue

                full_url = self.safe_urljoin(self.BASE_URL, raw_href)

                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                name = a_tag.get_text(strip=True) or "未命名附件"

                # 黑名单过滤
                if self._is_attachment_blacklisted(name):
                    logger.debug(f"[{self.SOURCE_NAME}] 过滤黑名单附件: {name}")
                    continue

                download_type = self._get_download_type()
                attachments.append({'name': name, 'url': full_url, 'download_type': download_type})

        if attachments:
            logger.info(f"[{self.SOURCE_NAME}] 提取到 {len(attachments)} 个附件")

        return attachments

    def _is_attachment_blacklisted(self, name: str) -> bool:
        """
        检查附件名称是否在黑名单中

        Args:
            name: 附件名称

        Returns:
            是否在黑名单中
        """
        for keyword in self.ATTACHMENT_NAME_BLACKLIST:
            if keyword in name:
                return True
        return False

    def _get_download_type(self) -> str:
        """
        根据来源名称判断附件下载类型

        Returns:
            "direct" - 可直接下载（公文通）
            "external" - 需要浏览器处理验证码（学院新闻）
        """
        # 公文通：可直接下载
        if self.SOURCE_NAME == "公文通":
            return "direct"

        # 学院类来源：需要浏览器处理验证码
        college_keywords = ["学院", "中心", "AI", "工程物理", "健康与环境",
                          "中德智能制造", "城市交通与物流", "新能源与新材料"]
        for keyword in college_keywords:
            if keyword in self.SOURCE_NAME:
                return "external"

        # 默认：需要浏览器处理
        return "external"

    def close(self):
        """关闭 Session 连接池"""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
