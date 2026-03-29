"""RSS rule preview diagnostics helpers."""

from __future__ import annotations

import re
from typing import Any, Dict, List


_BODY_SHORT_THRESHOLD = 70
_IMAGE_HEAVY_MIN_COUNT = 3
_IMAGE_HEAVY_BODY_THRESHOLD = 160
_MISSING_MEDIA_HINT_BODY_THRESHOLD = 40

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def markdown_to_preview_text(markdown_text: str) -> str:
    """Collapse Markdown into plain text for preview diagnostics."""
    text = str(markdown_text or "")
    if not text.strip():
        return ""

    text = _IMAGE_RE.sub(lambda match: f"{match.group(1) or '图片'} ", text)
    text = _LINK_RE.sub(lambda match: match.group(1) or match.group(2) or "", text)
    text = re.sub(r"</?(?:loc|date|contact)[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    text = text.replace("**", "")
    text = re.sub(r"[*_~]", "", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def analyze_rss_preview_content(
    markdown_text: str,
    *,
    image_count: int = 0,
    attachment_count: int = 0,
) -> Dict[str, Any]:
    """Generate deterministic preview diagnostics for RSS sample items."""
    plain_text = markdown_to_preview_text(markdown_text)
    body_char_count = len(plain_text)
    has_body = body_char_count > 0
    has_images = image_count > 0
    preview_warnings: List[Dict[str, str]] = []

    if not has_body:
        preview_warnings.append(
            {
                "code": "body_missing",
                "label": "未识别到可读正文",
                "tone": "danger",
            }
        )
        preview_status = "empty"
        preview_status_label = "正文缺失"
    else:
        if body_char_count < _BODY_SHORT_THRESHOLD:
            preview_warnings.append(
                {
                    "code": "body_short",
                    "label": "正文偏短，可能只有摘要",
                    "tone": "warning",
                }
            )

        if (
            image_count >= _IMAGE_HEAVY_MIN_COUNT
            and body_char_count < _IMAGE_HEAVY_BODY_THRESHOLD
        ):
            preview_warnings.append(
                {
                    "code": "image_heavy",
                    "label": "图多文少，可检查是否需要回源补正文",
                    "tone": "warning",
                }
            )

        if (
            not has_images
            and attachment_count == 0
            and body_char_count < _MISSING_MEDIA_HINT_BODY_THRESHOLD
        ):
            preview_warnings.append(
                {
                    "code": "media_missing",
                    "label": "当前样本未识别图片，如该源应有配图可再检查",
                    "tone": "muted",
                }
            )

        if preview_warnings:
            preview_status = "warning"
            preview_status_label = "建议检查"
        else:
            preview_status = "ready"
            preview_status_label = "状态良好"

    return {
        "body_plain_text": plain_text,
        "body_char_count": body_char_count,
        "has_body": has_body,
        "has_images": has_images,
        "is_body_short": has_body and body_char_count < _BODY_SHORT_THRESHOLD,
        "preview_status": preview_status,
        "preview_status_label": preview_status_label,
        "preview_warnings": preview_warnings,
    }


__all__ = [
    "analyze_rss_preview_content",
    "markdown_to_preview_text",
]
