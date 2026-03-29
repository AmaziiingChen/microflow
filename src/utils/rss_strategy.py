"""RSS source-level strategy helpers.

Provide lightweight source profile inference and template defaults so text-heavy
and long-form feeds can use more suitable fetch limits and AI instructions
without forcing every source to share the same behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from src.utils.rss_preview import markdown_to_preview_text
from src.utils.rule_ai_config import normalize_rule_ai_config


RSS_SOURCE_PROFILES: Dict[str, Dict[str, str]] = {
    "news": {
        "label": "资讯类",
        "description": "偏短讯、更新频繁，适合轻排版和短摘要。",
        "default_template_id": "news_brief",
    },
    "longform": {
        "label": "长文类",
        "description": "正文较长、结构较深，适合层次化排版和重点提炼。",
        "default_template_id": "longform_focus",
    },
    "visual": {
        "label": "图文类",
        "description": "图文混排较多，适合保留图文关系并做轻摘要。",
        "default_template_id": "visual_digest",
    },
}

RSS_STRATEGY_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "news_brief": {
        "profile": "news",
        "name": "快讯精读",
        "description": "适合资讯流，偏轻排版和短摘要。",
        "default_max_items": 30,
        "formatting_prompt": (
            "按资讯快讯阅读优化：尽量保留原始结构，只做轻量 Markdown 整理；"
            "优先突出时间、对象、动作；长段落拆成短段；不要过度改写。"
        ),
        "summary_prompt": (
            "按资讯快讯摘要输出：先给 3 个按重要性排序的标签；"
            "再用简洁 Markdown 提炼最新变化、影响对象和关键动作；"
            "避免展开过多背景。"
        ),
    },
    "longform_focus": {
        "profile": "longform",
        "name": "长文提炼",
        "description": "适合长篇文章，强调层次、论点和步骤。",
        "default_max_items": 12,
        "formatting_prompt": (
            "按长文阅读优化：优先整理 H3/H4 层级，拆分长段落，保留原文论证结构；"
            "把结论、步骤、关键转折点放到更清晰的位置，但不要压缩成短讯。"
        ),
        "summary_prompt": (
            "按长文摘要输出：先给 3 个按重要性排序的标签；"
            "再用结构化 Markdown 提炼核心观点、关键论据、执行步骤或结论；"
            "允许使用小标题，但避免复写原文。"
        ),
    },
    "visual_digest": {
        "profile": "visual",
        "name": "图文摘要",
        "description": "适合图文源，兼顾图片上下文和文字主线。",
        "default_max_items": 18,
        "formatting_prompt": (
            "按图文资讯阅读优化：正文仍以文字主线组织，保留必要图片位置和说明；"
            "段落不要过长，优先突出每张图对应的关键信息。"
        ),
        "summary_prompt": (
            "按图文资讯摘要输出：先给 3 个按重要性排序的标签；"
            "再用简洁 Markdown 提炼主题、对象、动作，并在必要时提示图文重点。"
        ),
    },
}

_PROFILE_ALIASES = {
    "mixed": "visual",
    "image": "visual",
    "image_heavy": "visual",
    "graphic": "visual",
    "photo": "visual",
}

_VISUAL_HINT_KEYWORDS = (
    "图集",
    "相册",
    "摄影",
    "画报",
    "gallery",
    "photo",
    "album",
)
_LONGFORM_HINT_KEYWORDS = (
    "专栏",
    "深度",
    "长文",
    "博客",
    "blog",
    "column",
    "essay",
    "newsletter",
    "weekly",
)


def _normalize_profile(value: Any) -> str:
    profile = str(value or "").strip().lower()
    profile = _PROFILE_ALIASES.get(profile, profile)
    return profile if profile in RSS_SOURCE_PROFILES else ""


def _normalize_template_id(value: Any) -> str:
    template_id = str(value or "").strip()
    return template_id if template_id in RSS_STRATEGY_TEMPLATES else ""


def get_rss_strategy_catalog() -> Dict[str, List[Dict[str, Any]]]:
    """Return RSS strategy profile/template catalog for API and UI usage."""
    profiles = [
        {
            "id": profile_id,
            "label": str(meta["label"]),
            "description": str(meta["description"]),
            "default_template_id": str(meta["default_template_id"]),
        }
        for profile_id, meta in RSS_SOURCE_PROFILES.items()
    ]
    templates = [
        {
            "id": template_id,
            "profile": str(template["profile"]),
            "profile_label": str(
                RSS_SOURCE_PROFILES[str(template["profile"])]["label"]
            ),
            "name": str(template["name"]),
            "description": str(template["description"]),
            "default_max_items": int(template["default_max_items"]),
        }
        for template_id, template in RSS_STRATEGY_TEMPLATES.items()
    ]
    return {
        "profiles": profiles,
        "templates": templates,
    }


def _coerce_positive_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _extract_article_markdown(article: Dict[str, Any]) -> str:
    for key in ("body_text", "raw_markdown", "raw_text", "content_preview"):
        value = str(article.get(key) or "").strip()
        if value:
            return value
    return ""


def _extract_block_metrics(content_blocks: Any) -> Dict[str, int]:
    heading_count = 0
    list_count = 0
    paragraph_count = 0

    for block in content_blocks or []:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip().lower()
        if block_type in {"title", "heading"}:
            heading_count += 1
        elif block_type == "list":
            list_count += 1
        elif block_type in {"paragraph", "quote"}:
            paragraph_count += 1

    return {
        "heading_count": heading_count,
        "list_count": list_count,
        "paragraph_count": paragraph_count,
    }


def _infer_profile_from_keywords(rule_dict: Dict[str, Any]) -> Optional[Dict[str, str]]:
    haystack = " ".join(
        [
            str(rule_dict.get("task_name") or "").lower(),
            str(rule_dict.get("task_purpose") or "").lower(),
            str(rule_dict.get("url") or "").lower(),
        ]
    )
    if any(keyword in haystack for keyword in _VISUAL_HINT_KEYWORDS):
        return {
            "profile": "visual",
            "reason": "根据源名称或 URL 关键词，推断为图文类订阅源。",
        }
    if any(keyword in haystack for keyword in _LONGFORM_HINT_KEYWORDS):
        return {
            "profile": "longform",
            "reason": "根据源名称或 URL 关键词，推断为长文类订阅源。",
        }
    return None


def analyze_rss_source_profile(
    sample_articles: Optional[Iterable[Dict[str, Any]]] = None,
    *,
    rule_dict: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Infer a lightweight RSS source profile from sample articles or rule hints."""
    articles = [article for article in (sample_articles or []) if isinstance(article, dict)]

    if not articles:
        keyword_guess = _infer_profile_from_keywords(rule_dict or {})
        if keyword_guess:
            profile = keyword_guess["profile"]
            meta = RSS_SOURCE_PROFILES[profile]
            return {
                "profile": profile,
                "profile_label": meta["label"],
                "reason": keyword_guess["reason"],
                "signals": {},
            }
        meta = RSS_SOURCE_PROFILES["news"]
        return {
            "profile": "news",
            "profile_label": meta["label"],
            "reason": "缺少样本内容，先按资讯类默认策略处理。",
            "signals": {},
        }

    total_body_chars = 0
    total_heading_count = 0
    total_list_count = 0
    total_image_count = 0
    max_body_chars = 0

    for article in articles:
        plain_text = markdown_to_preview_text(_extract_article_markdown(article))
        body_chars = len(plain_text)
        block_metrics = _extract_block_metrics(article.get("content_blocks"))
        image_count = len(article.get("image_assets") or [])
        if not image_count:
            image_count = int(article.get("image_count") or 0)

        total_body_chars += body_chars
        total_heading_count += block_metrics["heading_count"]
        total_list_count += block_metrics["list_count"]
        total_image_count += image_count
        max_body_chars = max(max_body_chars, body_chars)

    article_count = max(len(articles), 1)
    avg_body_chars = total_body_chars / article_count
    avg_heading_count = total_heading_count / article_count
    avg_list_count = total_list_count / article_count
    avg_image_count = total_image_count / article_count

    if avg_image_count >= 3 and avg_body_chars < 1000:
        profile = "visual"
        reason = "样本文字偏短且图片占比较高，按图文类策略处理。"
    elif max_body_chars >= 2600 or avg_body_chars >= 1800 or avg_heading_count >= 3:
        profile = "longform"
        reason = "样本正文较长且结构层级明显，按长文类策略处理。"
    else:
        profile = "news"
        reason = "样本篇幅较紧凑，更适合资讯类快讯策略。"

    meta = RSS_SOURCE_PROFILES[profile]
    return {
        "profile": profile,
        "profile_label": meta["label"],
        "reason": reason,
        "signals": {
            "article_count": article_count,
            "avg_body_chars": round(avg_body_chars),
            "max_body_chars": max_body_chars,
            "avg_heading_count": round(avg_heading_count, 2),
            "avg_list_count": round(avg_list_count, 2),
            "avg_image_count": round(avg_image_count, 2),
        },
    }


def resolve_rss_rule_strategy(
    rule_dict: Optional[Dict[str, Any]],
    *,
    sample_articles: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resolve source profile, template defaults and effective RSS prompts."""
    normalized = normalize_rule_ai_config(rule_dict or {})
    source_type = str(normalized.get("source_type") or "html").strip().lower() or "html"
    if source_type != "rss":
        return {
            "profile": "",
            "profile_label": "",
            "profile_source": "",
            "profile_reason": "",
            "template_id": "",
            "template_name": "",
            "template_description": "",
            "default_max_items": None,
            "effective_max_items": _coerce_positive_int(normalized.get("max_items")),
            "effective_formatting_prompt": str(
                normalized.get("formatting_prompt") or ""
            ).strip(),
            "effective_summary_prompt": str(
                normalized.get("summary_prompt") or normalized.get("custom_summary_prompt") or ""
            ).strip(),
            "signals": {},
        }

    explicit_profile = _normalize_profile(normalized.get("source_profile"))
    explicit_template_id = _normalize_template_id(normalized.get("source_template_id"))
    profile_source = str(normalized.get("source_profile_source") or "").strip().lower()
    profile_meta: Dict[str, Any]
    if explicit_profile:
        profile = explicit_profile
        profile_meta = {
            "profile": profile,
            "profile_label": RSS_SOURCE_PROFILES[profile]["label"],
            "reason": "使用已保存的源级策略。",
            "signals": {},
        }
        if profile_source not in {"manual", "inferred"}:
            profile_source = "manual"
    elif explicit_template_id:
        template_profile = str(
            RSS_STRATEGY_TEMPLATES[explicit_template_id]["profile"]
        ).strip()
        profile = template_profile
        profile_meta = {
            "profile": profile,
            "profile_label": RSS_SOURCE_PROFILES[profile]["label"],
            "reason": "使用手动指定的 RSS 模板。",
            "signals": {},
        }
        profile_source = "manual"
    else:
        profile_meta = analyze_rss_source_profile(sample_articles, rule_dict=normalized)
        profile = profile_meta["profile"]
        profile_source = "inferred"

    template_id = explicit_template_id
    if not template_id:
        template_id = RSS_SOURCE_PROFILES[profile]["default_template_id"]
    template = RSS_STRATEGY_TEMPLATES[template_id]

    explicit_max_items = _coerce_positive_int(normalized.get("max_items"))
    effective_max_items = explicit_max_items or int(template["default_max_items"])

    explicit_formatting_prompt = str(normalized.get("formatting_prompt") or "").strip()
    explicit_summary_prompt = str(
        normalized.get("summary_prompt") or normalized.get("custom_summary_prompt") or ""
    ).strip()
    enable_ai_formatting = bool(normalized.get("enable_ai_formatting", False))
    enable_ai_summary = bool(normalized.get("enable_ai_summary", False))

    return {
        "profile": profile,
        "profile_label": RSS_SOURCE_PROFILES[profile]["label"],
        "profile_source": profile_source,
        "profile_reason": str(profile_meta.get("reason") or "").strip(),
        "template_id": template_id,
        "template_name": str(template["name"]),
        "template_description": str(template["description"]),
        "default_max_items": int(template["default_max_items"]),
        "effective_max_items": effective_max_items,
        "effective_formatting_prompt": (
            explicit_formatting_prompt
            or (str(template["formatting_prompt"]).strip() if enable_ai_formatting else "")
        ),
        "effective_summary_prompt": (
            explicit_summary_prompt
            or (str(template["summary_prompt"]).strip() if enable_ai_summary else "")
        ),
        "signals": dict(profile_meta.get("signals") or {}),
    }


def attach_rss_strategy_metadata(
    rule_dict: Optional[Dict[str, Any]],
    *,
    sample_articles: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Persist lightweight source profile metadata onto an RSS rule."""
    normalized = normalize_rule_ai_config(rule_dict or {})
    if str(normalized.get("source_type") or "html").strip().lower() != "rss":
        return normalized

    strategy = resolve_rss_rule_strategy(normalized, sample_articles=sample_articles)
    normalized["source_profile"] = strategy["profile"]
    normalized["source_profile_source"] = strategy["profile_source"]
    normalized["source_template_id"] = strategy["template_id"]
    return normalized


__all__ = [
    "RSS_SOURCE_PROFILES",
    "RSS_STRATEGY_TEMPLATES",
    "analyze_rss_source_profile",
    "attach_rss_strategy_metadata",
    "get_rss_strategy_catalog",
    "resolve_rss_rule_strategy",
]
