"""
HTML 模板库与站点画像

为 HTML 网页 AI 爬虫提供：
1. 常见高校 / 政府 / CMS 站点模板
2. 站点画像推断
3. 模板候选排序与推荐
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from bs4 import BeautifulSoup, FeatureNotFound

logger = logging.getLogger(__name__)


def _create_soup(html: str) -> BeautifulSoup:
    """优先使用 lxml，缺失时自动回退到 html.parser。"""
    try:
        return BeautifulSoup(html or "", "lxml")
    except FeatureNotFound:
        return BeautifulSoup(html or "", "html.parser")


HTML_TEMPLATE_LIBRARY: List[Dict[str, Any]] = [
    {
        "id": "sztu_standard_ul_list",
        "name": "深技大标准列表",
        "profile_id": "college_department",
        "profile_label": "高校院系站",
        "domain_keywords": ["sztu.edu.cn"],
        "url_patterns": [r".*\.sztu\.edu\.cn.*"],
        "title_keywords": ["学院", "通知", "公告", "新闻"],
        "html_markers": [
            "ul.list-gl",
            "div.wp_article_list",
            "span.Article_PublishDate",
            ".wp_articlecontent",
        ],
        "list_container": "ul.list-gl, div.wp_article_list ul, ul.news-list",
        "item_selector": "li",
        "field_selectors": {
            "title": "a::attr(title), a::text",
            "url": "a::attr(href)",
            "date": "span::text, .Article_PublishDate::text, .wp_article_time::text",
        },
        "detail_body_selector": ".wp_articlecontent, .wp_article_content, .v_news_content",
        "detail_time_selector": ".Article_PublishDate::text, .arti_update::text, .wp_article_time::text",
        "detail_attachment_selector": "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip']",
        "detail_image_selector": ".wp_articlecontent img, .wp_article_content img, .v_news_content img",
        "notes": "适合深技大及同类高校二级站的标准新闻/公告列表页面。",
    },
    {
        "id": "edu_news_generic",
        "name": "高校新闻列表",
        "profile_id": "university_portal",
        "profile_label": "高校门户站",
        "domain_keywords": ["edu.cn"],
        "url_patterns": [r".*\.edu\.cn.*"],
        "title_keywords": ["大学", "学院", "学校", "教务", "通知"],
        "html_markers": [
            "ul.news-list",
            "ul.list",
            "dl.article-list",
            "div.news_list",
            "table tr td a",
        ],
        "list_container": "ul.news-list, ul.list, dl.article-list, div.news_list, table",
        "item_selector": "li, dt, tr",
        "field_selectors": {
            "title": "a::attr(title), a::text",
            "url": "a::attr(href)",
            "date": ".date::text, span::text, td:last-child::text, time::text",
        },
        "detail_body_selector": ".article, .content, .article-content, #vsb_content, .TRS_Editor, .v_news_content",
        "detail_time_selector": "time::text, .date::text, .arti_update::text, .Article_PublishDate::text",
        "detail_attachment_selector": "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip']",
        "detail_image_selector": ".article img, .content img, .article-content img, #vsb_content img, .TRS_Editor img",
        "notes": "适合国内高校门户、院系站和教务公告页。",
    },
    {
        "id": "gov_notice_generic",
        "name": "政府公告列表",
        "profile_id": "government_portal",
        "profile_label": "政府信息公开站",
        "domain_keywords": ["gov.cn"],
        "url_patterns": [r".*\.gov\.cn.*"],
        "title_keywords": ["政府", "政务", "信息公开", "通知", "公告"],
        "html_markers": [
            "ul.xxgk",
            "table.news",
            "div.list ul li",
            ".info-list",
        ],
        "list_container": "ul.xxgk, table.news, .info-list, div.list ul",
        "item_selector": "li, tr",
        "field_selectors": {
            "title": "a::attr(title), a::text",
            "url": "a::attr(href)",
            "date": "td:last-child::text, .date::text, span::text, time::text",
        },
        "detail_body_selector": ".article-content, .content, #Zoom, .TRS_Editor, .pages_content",
        "detail_time_selector": "time::text, .date::text, .pub_time::text, .Article_PublishDate::text",
        "detail_attachment_selector": "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip']",
        "detail_image_selector": ".article-content img, .content img, #Zoom img, .TRS_Editor img",
        "notes": "适合政务公开、公示公告和政策解读页。",
    },
    {
        "id": "campus_cms_list",
        "name": "院校 CMS 列表",
        "profile_id": "campus_cms",
        "profile_label": "校园 CMS 站",
        "domain_keywords": [],
        "url_patterns": [],
        "title_keywords": ["通知", "公告", "新闻", "学院", "学校"],
        "html_markers": [
            ".listbox li",
            ".news-list li",
            ".article-list li",
            ".list li a",
            ".post-list article",
        ],
        "list_container": ".listbox, .news-list, .article-list, .list, .post-list",
        "item_selector": "li, article, .item",
        "field_selectors": {
            "title": "a::attr(title), h2 a::text, h3 a::text, a::text",
            "url": "a::attr(href)",
            "date": ".date::text, time::text, span::text",
        },
        "detail_body_selector": ".article-content, .content, .post-content, .entry-content, .news_content",
        "detail_time_selector": "time::text, .date::text, .post-date::text",
        "detail_attachment_selector": "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip']",
        "detail_image_selector": ".article-content img, .content img, .post-content img, .entry-content img",
        "notes": "适合常见校园新闻 CMS、新闻中心与通知公告页。",
    },
    {
        "id": "wordpress_article_list",
        "name": "WordPress 文章列表",
        "profile_id": "content_cms",
        "profile_label": "内容 CMS 站",
        "domain_keywords": [],
        "url_patterns": [],
        "title_keywords": ["新闻", "Blog", "文章"],
        "html_markers": [
            "article.post",
            "h2.entry-title a",
            ".post-list",
            ".entry-content",
        ],
        "list_container": ".post-list, .posts, main",
        "item_selector": "article.post, article, .post",
        "field_selectors": {
            "title": "h2.entry-title a::text, h3.entry-title a::text, a::attr(title), a::text",
            "url": "h2.entry-title a::attr(href), h3.entry-title a::attr(href), a::attr(href)",
            "date": "time::text, .entry-date::text, .posted-on::text",
        },
        "detail_body_selector": ".entry-content, .post-content, article .content",
        "detail_time_selector": "time::text, .entry-date::text, .posted-on::text",
        "detail_attachment_selector": "a[href$='.pdf'], a[href$='.doc'], a[href$='.docx'], a[href$='.xls'], a[href$='.xlsx'], a[href$='.zip']",
        "detail_image_selector": ".entry-content img, .post-content img, article img",
        "notes": "适合 WordPress 与类博客新闻列表。",
    },
]


def _confidence_label(score: float) -> str:
    if score >= 85:
        return "高置信"
    if score >= 65:
        return "中置信"
    if score >= 45:
        return "低置信"
    return "弱匹配"


def _normalize_target_fields(target_fields: Optional[Sequence[str]]) -> List[str]:
    return [
        str(field or "").strip().lower()
        for field in (target_fields or [])
        if str(field or "").strip()
    ]


def _get_title_text(soup: BeautifulSoup) -> str:
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        h1_text = h1.get_text(" ", strip=True)
        if h1_text:
            title = f"{title} {h1_text}".strip()
    return title


def _selector_exists(soup: BeautifulSoup, selector: str) -> bool:
    clean_selector = str(selector or "").strip()
    if not clean_selector:
        return False
    try:
        return bool(soup.select_one(clean_selector))
    except Exception:
        return False


def _score_template(
    template: Dict[str, Any],
    *,
    url: str,
    soup: BeautifulSoup,
    title_text: str,
    target_fields: List[str],
) -> Tuple[float, List[str], List[str], List[str]]:
    domain = urlparse(url).netloc.lower()
    score = 0.0
    reasons: List[str] = []
    matched_by: List[str] = []
    matched_markers: List[str] = []

    domain_keywords = [str(item).lower() for item in template.get("domain_keywords", [])]
    if domain_keywords and any(keyword in domain for keyword in domain_keywords):
        score += 38
        matched_by.append("domain")
        reasons.append(f"域名命中 {template.get('profile_label', '模板画像')}")

    for pattern in template.get("url_patterns", []):
        try:
            if re.search(pattern, url, re.IGNORECASE):
                score += 22
                matched_by.append("url_pattern")
                reasons.append("URL 路径命中模板规则")
                break
        except re.error:
            continue

    for keyword in template.get("title_keywords", []):
        clean_keyword = str(keyword or "").strip()
        if clean_keyword and clean_keyword.lower() in title_text.lower():
            score += 6
            matched_by.append("title_keyword")
            reasons.append(f"页面标题包含“{clean_keyword}”")
            break

    for marker in template.get("html_markers", []):
        if _selector_exists(soup, str(marker or "").strip()):
            matched_markers.append(str(marker).strip())
    if matched_markers:
        score += min(len(matched_markers) * 12, 36)
        matched_by.append("dom_marker")
        reasons.append(f"命中 {len(matched_markers)} 个 DOM 结构特征")

    field_selectors = (
        template.get("field_selectors")
        if isinstance(template.get("field_selectors"), dict)
        else {}
    )
    if target_fields:
        covered_fields = [
            field for field in target_fields if str(field_selectors.get(field) or "").strip()
        ]
        if covered_fields:
            coverage_score = round(len(covered_fields) / len(target_fields) * 14, 1)
            score += coverage_score
            reasons.append(f"覆盖 {len(covered_fields)}/{len(target_fields)} 个目标字段")

    return score, reasons, list(dict.fromkeys(matched_by)), matched_markers


def _serialize_candidate(
    template: Dict[str, Any],
    *,
    score: float,
    reasons: List[str],
    matched_by: List[str],
    matched_markers: List[str],
) -> Dict[str, Any]:
    field_selectors = (
        template.get("field_selectors")
        if isinstance(template.get("field_selectors"), dict)
        else {}
    )
    return {
        "id": str(template.get("id") or "").strip(),
        "name": str(template.get("name") or "").strip(),
        "profile_id": str(template.get("profile_id") or "").strip(),
        "profile_label": str(template.get("profile_label") or "").strip(),
        "score": round(float(score), 1),
        "confidence_label": _confidence_label(score),
        "matched_by": matched_by,
        "matched_markers": matched_markers[:3],
        "reason": "；".join(reasons[:3]),
        "notes": str(template.get("notes") or "").strip(),
        "list_container": str(template.get("list_container") or "").strip(),
        "item_selector": str(template.get("item_selector") or "").strip(),
        "field_selectors": dict(field_selectors),
        "field_keys": list(field_selectors.keys()),
        "detail_body_selector": str(template.get("detail_body_selector") or "").strip(),
        "detail_time_selector": str(template.get("detail_time_selector") or "").strip(),
        "detail_attachment_selector": str(
            template.get("detail_attachment_selector") or ""
        ).strip(),
        "detail_image_selector": str(template.get("detail_image_selector") or "").strip(),
    }


def match_template_candidates(
    url: str,
    html_content: str,
    target_fields: Optional[Sequence[str]] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """按匹配分数返回最可能命中的模板候选。"""
    soup = _create_soup(html_content)
    title_text = _get_title_text(soup)
    normalized_fields = _normalize_target_fields(target_fields)
    candidates: List[Dict[str, Any]] = []

    for template in HTML_TEMPLATE_LIBRARY:
        score, reasons, matched_by, matched_markers = _score_template(
            template,
            url=url,
            soup=soup,
            title_text=title_text,
            target_fields=normalized_fields,
        )
        if score < 32:
            continue
        candidates.append(
            _serialize_candidate(
                template,
                score=score,
                reasons=reasons,
                matched_by=matched_by,
                matched_markers=matched_markers,
            )
        )

    candidates.sort(
        key=lambda item: (
            float(item.get("score") or 0.0),
            len(item.get("matched_markers") or []),
            len(item.get("matched_by") or []),
        ),
        reverse=True,
    )
    return candidates[: max(int(limit or 0), 0)] if limit else candidates


def find_best_template(
    url: str,
    html_content: str,
    target_fields: Optional[Sequence[str]] = None,
) -> Optional[Dict[str, Any]]:
    """返回评分最高的模板候选。"""
    candidates = match_template_candidates(
        url,
        html_content,
        target_fields=target_fields,
        limit=1,
    )
    return candidates[0] if candidates else None


def build_site_profile(
    url: str,
    html_content: str,
    target_fields: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """推断站点画像，并附带推荐模板摘要。"""
    domain = urlparse(url).netloc.lower()
    soup = _create_soup(html_content)
    title_text = _get_title_text(soup)
    candidates = match_template_candidates(
        url,
        html_content,
        target_fields=target_fields,
        limit=3,
    )

    profile_id = "general_site"
    label = "通用网页"
    reasons: List[str] = []
    score = 36.0

    if ".gov.cn" in domain or "gov.cn" in domain:
        profile_id = "government_portal"
        label = "政府信息公开站"
        score = 80.0
        reasons.append("域名包含 gov.cn")
    elif ".edu.cn" in domain or "edu.cn" in domain:
        if any(keyword in title_text for keyword in ["学院", "书院", "研究院", "实验室"]):
            profile_id = "college_department"
            label = "高校院系站"
            score = 84.0
            reasons.append("域名包含 edu.cn，且标题命中学院/院系语义")
        else:
            profile_id = "university_portal"
            label = "高校门户站"
            score = 78.0
            reasons.append("域名包含 edu.cn")
    elif any(_selector_exists(soup, selector) for selector in [".entry-content", "article.post", ".post-list"]):
        profile_id = "content_cms"
        label = "内容 CMS 站"
        score = 68.0
        reasons.append("命中常见 CMS 内容结构")
    elif any(_selector_exists(soup, selector) for selector in [".listbox", ".news-list", ".article-list"]):
        profile_id = "campus_cms"
        label = "校园 CMS 站"
        score = 62.0
        reasons.append("命中常见新闻列表 DOM 结构")

    if candidates:
        top_candidate = candidates[0]
        if float(top_candidate.get("score") or 0.0) >= score:
            profile_id = str(top_candidate.get("profile_id") or profile_id)
            label = str(top_candidate.get("profile_label") or label)
            score = float(top_candidate.get("score") or score)
        candidate_reason = str(top_candidate.get("reason") or "").strip()
        if candidate_reason:
            reasons.append(f"推荐模板：{top_candidate.get('name')}（{candidate_reason}）")

    return {
        "id": profile_id,
        "label": label,
        "confidence": round(score, 1),
        "confidence_label": _confidence_label(score),
        "reasons": reasons[:3],
        "recommended_template_id": (
            str(candidates[0].get("id") or "").strip() if candidates else ""
        ),
        "recommended_template_name": (
            str(candidates[0].get("name") or "").strip() if candidates else ""
        ),
    }


__all__ = [
    "HTML_TEMPLATE_LIBRARY",
    "build_site_profile",
    "find_best_template",
    "match_template_candidates",
]
