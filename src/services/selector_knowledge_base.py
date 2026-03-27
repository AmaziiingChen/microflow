"""
选择器知识库 - 存储常见网站的 CSS 选择器模板

用于加速规则生成，已知网站直接使用验证过的选择器模板。
"""

from typing import Dict, List, Optional, Any
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


# 🌟 选择器模板库
# 格式: {域名模式: {字段: 选择器}}
SELECTOR_TEMPLATES: Dict[str, Dict[str, Any]] = {
    # ==================== 深圳技术大学相关 ====================
    "sztu.edu.cn": {
        "patterns": [
            {
                "url_pattern": r".*\.sztu\.edu\.cn.*",
                "list_container": "ul.list-gl",
                "item_selector": "li",
                "field_selectors": {
                    "title": "a::attr(title)",
                    "url": "a::attr(href)",
                    "date": "span::text",
                },
                "notes": "深技大标准列表格式",
            }
        ]
    },

    # ==================== 通用教育网站 ====================
    "edu.cn": {
        "patterns": [
            {
                "url_pattern": r".*\.edu\.cn.*",
                "list_container": "ul.news-list, ul.list, dl.article-list",
                "item_selector": "li, dt",
                "field_selectors": {
                    "title": "a::attr(title), a::text",
                    "url": "a::attr(href)",
                    "date": "span::text, .date::text",
                },
                "notes": "中国教育网站通用格式",
            }
        ]
    },

    # ==================== 政府网站 ====================
    "gov.cn": {
        "patterns": [
            {
                "url_pattern": r".*\.gov\.cn.*",
                "list_container": "ul.xxgk, ul.list, table.news",
                "item_selector": "li, tr",
                "field_selectors": {
                    "title": "a::attr(title), a::text",
                    "url": "a::attr(href)",
                    "date": "span::text, td:last-child::text",
                },
                "notes": "中国政府网站通用格式",
            }
        ]
    },

    # ==================== 新闻门户 ====================
    "news.sina.com.cn": {
        "patterns": [
            {
                "url_pattern": r".*sina\.com\.cn.*",
                "list_container": "ul.news-list, section.article",
                "item_selector": "li, article",
                "field_selectors": {
                    "title": "h2 a::text, a::attr(title)",
                    "url": "a::attr(href)",
                    "date": "time::text, .date::text",
                },
                "notes": "新浪新闻格式",
            }
        ]
    },

    # ==================== 知乎 ====================
    "zhihu.com": {
        "patterns": [
            {
                "url_pattern": r".*zhihu\.com.*",
                "list_container": "div.List, div.ContentItem",
                "item_selector": "div.ContentItem",
                "field_selectors": {
                    "title": "h2.ContentItem-title a::text",
                    "url": "a::attr(href)",
                    "summary": "div.RichContent-inner::text",
                },
                "notes": "知乎列表格式",
            }
        ]
    },

    # ==================== 博客园 ====================
    "cnblogs.com": {
        "patterns": [
            {
                "url_pattern": r".*cnblogs\.com.*",
                "list_container": "div.post-list",
                "item_selector": "article.post-item",
                "field_selectors": {
                    "title": "a.post-item-title::text",
                    "url": "a.post-item-title::attr(href)",
                    "summary": "p.post-item-summary::text",
                    "date": "span.post-meta-item::text",
                },
                "notes": "博客园列表格式",
            }
        ]
    },

    # ==================== 掘金 ====================
    "juejin.cn": {
        "patterns": [
            {
                "url_pattern": r".*juejin\.cn.*",
                "list_container": "div.entry-list",
                "item_selector": "div.entry",
                "field_selectors": {
                    "title": "a.title::text",
                    "url": "a.title::attr(href)",
                    "author": "a.username::text",
                },
                "notes": "掘金列表格式（可能需要 JS 渲染）",
            }
        ]
    },

    # ==================== 微信公众号 ====================
    "mp.weixin.qq.com": {
        "patterns": [
            {
                "url_pattern": r"mp\.weixin\.qq\.com.*",
                "list_container": "div.rich_media_content",
                "item_selector": "section, p",
                "field_selectors": {
                    "title": "h1.rich_media_title::text",
                    "content": "div.rich_media_content::text",
                    "date": "em.rich_media_meta::text",
                },
                "notes": "微信公众号文章格式",
            }
        ]
    },
}


def get_domain_from_url(url: str) -> str:
    """
    从 URL 提取域名

    Args:
        url: 完整 URL

    Returns:
        域名字符串
    """
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def find_matching_template(url: str) -> Optional[Dict[str, Any]]:
    """
    根据 URL 查找匹配的选择器模板

    Args:
        url: 目标 URL

    Returns:
        匹配的模板字典，未找到返回 None
    """
    import re

    domain = get_domain_from_url(url)
    if not domain:
        return None

    # 1. 精确匹配域名
    for pattern_domain, template_data in SELECTOR_TEMPLATES.items():
        if pattern_domain in domain or domain in pattern_domain:
            # 检查 URL 模式匹配
            for pattern in template_data.get("patterns", []):
                url_pattern = pattern.get("url_pattern", "")
                if re.match(url_pattern, url):
                    logger.info(f"📚 匹配到已知模板: {pattern_domain}")
                    return pattern

    return None


def get_all_known_domains() -> List[str]:
    """
    获取所有已知的域名列表

    Returns:
        域名列表
    """
    return list(SELECTOR_TEMPLATES.keys())


def add_custom_template(domain: str, template: Dict[str, Any]) -> None:
    """
    添加自定义模板到知识库（运行时）

    Args:
        domain: 域名
        template: 模板数据
    """
    if domain not in SELECTOR_TEMPLATES:
        SELECTOR_TEMPLATES[domain] = {"patterns": []}

    SELECTOR_TEMPLATES[domain]["patterns"].append(template)
    logger.info(f"📚 添加自定义模板: {domain}")


# 导出
__all__ = [
    "SELECTOR_TEMPLATES",
    "get_domain_from_url",
    "find_matching_template",
    "get_all_known_domains",
    "add_custom_template",
]
