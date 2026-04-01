"""Helpers for recovering meaningful text from structured article content."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup


_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\([^)]+\)")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _compact_length(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def markdown_to_ai_plain_text(markdown_text: Any) -> str:
    """Collapse Markdown into plain text suitable for AI summarization."""
    text = str(markdown_text or "")
    if not text.strip():
        return ""

    # Drop Markdown image syntax entirely to avoid mistaking image placeholders for正文。
    text = _MARKDOWN_IMAGE_RE.sub(" ", text)
    text = _MARKDOWN_LINK_RE.sub(lambda match: match.group(1) or match.group(2) or "", text)
    text = re.sub(r"</?(?:loc|date|contact)[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = text.replace("`", " ")
    text = re.sub(r"[*_~|]", " ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text)
    return text.strip()


def html_to_ai_plain_text(html_text: Any) -> str:
    """Extract readable plain text from HTML."""
    html = str(html_text or "")
    if not html.strip():
        return ""

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            text = _HTML_TAG_RE.sub(" ", html)
            return _WHITESPACE_RE.sub(" ", text).strip()

    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()
    return _WHITESPACE_RE.sub(" ", soup.get_text(" ", strip=True)).strip()


def resolve_effective_article_text(
    raw_text: Any = "",
    raw_markdown: Any = "",
    body_html: Any = "",
) -> str:
    """Return the most informative text candidate among raw/html/markdown inputs."""
    candidates = [
        str(raw_text or "").strip(),
        markdown_to_ai_plain_text(raw_markdown),
        html_to_ai_plain_text(body_html),
    ]

    best = ""
    best_len = -1
    for candidate in candidates:
        current_len = _compact_length(candidate)
        if current_len > best_len:
            best = candidate
            best_len = current_len
    return best.strip()


__all__ = [
    "html_to_ai_plain_text",
    "markdown_to_ai_plain_text",
    "resolve_effective_article_text",
]
