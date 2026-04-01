"""Helpers for lowering provider content-risk triggers before LLM calls."""

from __future__ import annotations

import re
from typing import List, Tuple

_CONTENT_RISK_RULES: Tuple[Tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"以习近平同志为核心的党中央"),
        "上级统一部署",
        "核心政治表述",
    ),
    (
        re.compile(r"习近平总书记和党中央"),
        "上级统一部署",
        "领导与机构表述",
    ),
    (
        re.compile(r"习近平新时代中国特色社会主义思想"),
        "相关指导思想",
        "指导思想表述",
    ),
    (
        re.compile(r"在[^。；\n]{0,40}(?:习近平总书记|党中央|中共中央|有关领导|上级部门)[^。；\n]{0,40}领导下"),
        "在统一部署下",
        "领导表述句式",
    ),
    (
        re.compile(r"习近平总书记"),
        "有关领导",
        "领导称谓",
    ),
    (
        re.compile(r"习近平"),
        "有关领导",
        "领导姓名",
    ),
    (
        re.compile(r"党中央"),
        "上级部门",
        "机构称谓",
    ),
    (
        re.compile(r"中共中央"),
        "上级部门",
        "机构称谓",
    ),
    (
        re.compile(r"重要指示批示精神"),
        "有关要求",
        "政策表述",
    ),
    (
        re.compile(r"重要讲话精神"),
        "有关讲话要点",
        "政策表述",
    ),
    (
        re.compile(r"党的二十大精神"),
        "有关会议精神",
        "会议表述",
    ),
    (
        re.compile(r"二十届[^\s，。；]{0,8}全会精神"),
        "有关会议精神",
        "会议表述",
    ),
)


def sanitize_llm_provider_risk_text(text: str | None) -> tuple[str, List[dict]]:
    """Return a softer provider-facing variant for content-risk retries only."""
    if not text:
        return "", []

    sanitized = str(text)
    replacement_stats: List[dict] = []

    for pattern, replacement, label in _CONTENT_RISK_RULES:
        sanitized, count = pattern.subn(replacement, sanitized)
        if count:
            replacement_stats.append(
                {
                    "label": label,
                    "count": count,
                    "replacement": replacement,
                }
            )

    sanitized = re.sub(r"[ \t]{2,}", " ", sanitized)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    return sanitized, replacement_stats
