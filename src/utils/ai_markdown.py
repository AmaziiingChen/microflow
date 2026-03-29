"""Helpers for RSS AI markdown payloads.

These helpers keep tag parsing/composition consistent across the backend and UI
compatibility layer while we migrate away from the legacy `summary` blob.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Tuple


_TAG_RE = re.compile(r"【([^】\n]{1,15})】")


def normalize_tags(tags: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    for tag in tags:
        clean = str(tag or "").replace("【", "").replace("】", "").strip()
        if not clean:
            continue
        clean = clean[:15]
        if clean not in normalized:
            normalized.append(clean)
        if len(normalized) >= 3:
            break
    return normalized


def build_tag_items(tags: Iterable[str]) -> List[Dict[str, Any]]:
    normalized = normalize_tags(tags)
    return [
        {
            "text": tag,
            "priority": index + 1,
            "is_primary": index == 0,
        }
        for index, tag in enumerate(normalized)
    ]


def extract_leading_tags(markdown: str) -> Tuple[List[str], str]:
    text = str(markdown or "").strip()
    if not text.startswith("【"):
        return [], text

    lines = text.splitlines()
    if not lines:
        return [], text

    first_line = lines[0].strip()
    tags = normalize_tags(_TAG_RE.findall(first_line))
    if not tags:
        return [], text

    body = "\n".join(lines[1:]).strip()
    return tags, body


def compose_tagged_markdown(tags: Iterable[str], body: str) -> str:
    normalized = normalize_tags(tags)
    clean_body = str(body or "").strip()
    if not normalized:
        return clean_body
    tag_line = "".join(f"【{tag}】" for tag in normalized)
    if not clean_body:
        return tag_line
    return f"{tag_line}\n{clean_body}"


def serialize_tags(tags: Iterable[str]) -> str:
    return json.dumps(normalize_tags(tags), ensure_ascii=False)


def deserialize_tags(raw: object) -> List[str]:
    if isinstance(raw, list):
        return normalize_tags(raw)
    if raw is None:
        return []

    text = str(raw).strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return normalize_tags(parsed)
    except Exception:
        pass

    return normalize_tags(_TAG_RE.findall(text))


def resolve_article_summary_payload(article: Mapping[str, Any]) -> Dict[str, Any]:
    """Resolve article summary/tags/markdown with new fields first.

    This keeps downstream consumers aligned while we gradually move away from
    the legacy ``summary`` blob.
    """

    summary = str(article.get("summary") or "").strip()
    ai_summary = str(article.get("ai_summary") or article.get("aiSummary") or "").strip()
    enhanced_markdown = str(
        article.get("enhanced_markdown") or article.get("enhancedMarkdown") or ""
    ).strip()
    raw_markdown = str(
        article.get("raw_markdown")
        or article.get("rawMarkdown")
        or article.get("raw_text")
        or article.get("raw_content")
        or article.get("body_text")
        or ""
    ).strip()

    summary_tags, summary_body = extract_leading_tags(summary)
    ai_tags = deserialize_tags(article.get("ai_tags") if "ai_tags" in article else article.get("aiTags"))

    tags = ai_tags or summary_tags
    resolved_summary_body = ai_summary or summary_body
    summary_markdown = (
        summary
        or compose_tagged_markdown(tags, resolved_summary_body)
        or enhanced_markdown
        or raw_markdown
    )
    preview_markdown = (
        resolved_summary_body
        or enhanced_markdown
        or raw_markdown
        or summary_markdown
    )

    return {
        "tags": tags,
        "tag_items": build_tag_items(tags),
        "summary_body": resolved_summary_body,
        "summary_markdown": summary_markdown,
        "preview_markdown": preview_markdown,
        "enhanced_markdown": enhanced_markdown or raw_markdown,
        "raw_markdown": raw_markdown,
    }
