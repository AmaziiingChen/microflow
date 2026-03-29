"""RSS content normalization helpers.

This module converts RSS/Atom HTML fragments into readable Markdown-like text
that is safer to feed into AI and render in the card/detail views.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag


_BLOCK_TAGS = {
    "article",
    "aside",
    "blockquote",
    "div",
    "figure",
    "footer",
    "header",
    "main",
    "p",
    "section",
}

_LIST_TAGS = {"ul", "ol"}
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "canvas", "svg", "path"}
_HEADING_TAGS = {f"h{i}" for i in range(1, 7)}
_IMAGE_CAPTION_CLASS_KEYWORDS = (
    "caption",
    "desc",
    "description",
    "summary",
    "image-desc",
    "img_desc",
    "img-desc",
    "figure-desc",
    "pictext",
    "photo-desc",
)
_CAPTION_TEXT_TAGS = {"figcaption", "p", "div", "span", "small", "em", "strong"}
_STRICT_IMAGE_CAPTION_MODE = "strict"


@dataclass
class NormalizedRssContent:
    markdown: str
    plain_text: str
    images: List[str]
    attachments: List[Dict[str, str]]
    blocks: List[Dict[str, Any]]
    image_assets: List[Dict[str, Any]]


def _clean_text(value: str) -> str:
    value = html_lib.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _looks_like_image(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith(
        (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif")
    )


def _get_hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _normalize_url_key(url: str) -> str:
    return str(url or "").strip().replace("&amp;", "&").split("#", 1)[0]


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        number = float(text)
        if number <= 0:
            return None
        return number
    except Exception:
        return None


def _looks_like_wechat_image(url: str) -> bool:
    hostname = _get_hostname(url)
    return "mmbiz.qpic.cn" in hostname or "qq.com" in hostname


def _extract_node_text_candidate(node: Optional[Tag]) -> str:
    if not node or not isinstance(node, Tag):
        return ""
    if node.find(["img", "video", "audio", "iframe", "table", "ul", "ol"]):
        return ""
    return _clean_text(node.get_text(" ", strip=True))


def _has_explicit_caption_marker(node: Tag) -> bool:
    if not isinstance(node, Tag):
        return False

    if node.name and node.name.lower() == "figcaption":
        return True

    for attr_name in (
        "data-caption",
        "data-desc",
        "data-image-caption",
        "data-image-desc",
        "data-figure-caption",
        "data-img-caption",
    ):
        if _clean_text(str(node.get(attr_name) or "")):
            return True

    class_text = " ".join(str(cls or "") for cls in node.get("class", [])).lower()
    if any(keyword in class_text for keyword in _IMAGE_CAPTION_CLASS_KEYWORDS):
        return True

    role_text = " ".join(
        _clean_text(str(node.get(attr_name) or "")).lower()
        for attr_name in ("data-role", "data-type", "role")
    )
    return any(keyword in role_text for keyword in ("caption", "desc"))


def _extract_explicit_caption_text(node: Optional[Tag]) -> str:
    if not node or not isinstance(node, Tag):
        return ""

    for attr_name in (
        "data-caption",
        "data-desc",
        "data-image-caption",
        "data-image-desc",
        "data-figure-caption",
        "data-img-caption",
    ):
        text = _clean_text(str(node.get(attr_name) or ""))
        if text:
            return text

    return _extract_node_text_candidate(node)


def _extract_image_title_hint(node: Tag) -> str:
    for value in (
        node.get("data-alt"),
        node.get("data-title"),
        node.get("title"),
        node.get("aria-label"),
    ):
        text = _clean_text(str(value or ""))
        if text:
            return text
    return ""


def _extract_image_caption(node: Tag) -> str:
    if _STRICT_IMAGE_CAPTION_MODE != "strict":
        return ""

    for attr_name in (
        "data-caption",
        "data-desc",
        "data-image-caption",
        "data-image-desc",
        "data-figure-caption",
        "data-img-caption",
    ):
        text = _clean_text(str(node.get(attr_name) or ""))
        if text:
            return text

    parent = node.parent if isinstance(node.parent, Tag) else None
    if parent:
        if parent.name.lower() == "figure":
            figcaption = parent.find("figcaption")
            if figcaption:
                text = _extract_explicit_caption_text(figcaption)
                if text:
                    return text

        for sibling in list(parent.children):
            if sibling is node or not isinstance(sibling, Tag):
                continue
            if _has_explicit_caption_marker(sibling):
                text = _extract_explicit_caption_text(sibling)
                if text:
                    return text

    next_sibling = node.find_next_sibling()
    if isinstance(next_sibling, Tag) and _has_explicit_caption_marker(next_sibling):
        text = _extract_explicit_caption_text(next_sibling)
        if text:
            return text

    return ""


def _image_role_label(category: str) -> str:
    normalized = str(category or "").strip().lower()
    mapping = {
        "cover": "封面图",
        "body": "正文图",
        "attachment": "附件图",
        "external": "外链图",
    }
    return mapping.get(normalized, "图片")


def _register_image_asset(
    image_assets: List[Dict[str, Any]],
    url: str,
    category: str,
    name: str = "",
    source: str = "",
    alt: str = "",
    caption: str = "",
    width: Optional[float] = None,
    height: Optional[float] = None,
    aspect_ratio: Optional[float] = None,
    hostname: str = "",
    is_wechat: Optional[bool] = None,
) -> None:
    clean_url = str(url or "").strip()
    if not clean_url:
        return

    clean_alt = _clean_text(alt or "")
    clean_caption = _clean_text(caption or "")
    clean_name = _clean_text(name or clean_caption or clean_alt or "图片") or "图片"
    clean_source = str(source or "").strip() or "content"
    clean_hostname = str(hostname or _get_hostname(clean_url)).strip()
    normalized_width = _safe_float(width)
    normalized_height = _safe_float(height)
    normalized_ratio = _safe_float(aspect_ratio)
    if not normalized_ratio and normalized_width and normalized_height:
        normalized_ratio = normalized_width / normalized_height
    resolved_is_wechat = (
        bool(is_wechat) if is_wechat is not None else _looks_like_wechat_image(clean_url)
    )

    for asset in image_assets:
        if asset.get("url") != clean_url:
            continue
        if not asset.get("name") and clean_name:
            asset["name"] = clean_name
        if not asset.get("source") and clean_source:
            asset["source"] = clean_source
        if category == "cover" and asset.get("category") != "cover":
            asset["category"] = "cover"
        if not asset.get("alt") and clean_alt:
            asset["alt"] = clean_alt
        if not asset.get("caption") and clean_caption:
            asset["caption"] = clean_caption
        if not asset.get("hostname") and clean_hostname:
            asset["hostname"] = clean_hostname
        if not asset.get("width") and normalized_width:
            asset["width"] = normalized_width
        if not asset.get("height") and normalized_height:
            asset["height"] = normalized_height
        if not asset.get("aspect_ratio") and normalized_ratio:
            asset["aspect_ratio"] = normalized_ratio
        if "is_wechat" not in asset:
            asset["is_wechat"] = resolved_is_wechat
        return

    image_assets.append(
        {
            "url": clean_url,
            "category": category,
            "name": clean_name,
            "alt": clean_alt,
            "caption": clean_caption,
            "source": clean_source,
            "hostname": clean_hostname,
            "width": normalized_width,
            "height": normalized_height,
            "aspect_ratio": normalized_ratio,
            "is_wechat": resolved_is_wechat,
            "index": len(image_assets),
        }
    )


def _resolve_url(base_url: str, candidate: str) -> str:
    candidate = (candidate or "").strip()
    if not candidate:
        return ""
    return urljoin(base_url, candidate) if base_url else candidate


def _make_soup(fragment: str) -> BeautifulSoup:
    """Create a BeautifulSoup parser with a graceful fallback."""
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(fragment, parser)
        except Exception:
            continue
    return BeautifulSoup(fragment, "html.parser")


def _inline_text(
    node: Any,
    base_url: str,
    images: List[str],
    image_assets: List[Dict[str, Any]],
) -> str:
    if isinstance(node, NavigableString):
        return str(node)

    if not isinstance(node, Tag):
        return ""

    tag_name = node.name.lower()

    if tag_name in _SKIP_TAGS:
        return ""

    if tag_name == "br":
        return "\n"

    if tag_name == "img":
        src = _resolve_url(base_url, node.get("data-src") or node.get("src") or "")
        if src and src not in images:
            images.append(src)
        alt = _clean_text(node.get("alt") or node.get("data-alt") or "")
        caption = _extract_image_caption(node)
        title_hint = _extract_image_title_hint(node)
        width = _safe_float(
            node.get("data-w") or node.get("data-width") or node.get("width")
        )
        height = _safe_float(
            node.get("data-h") or node.get("data-height") or node.get("height")
        )
        aspect_ratio = _safe_float(
            node.get("data-ratio") or node.get("data-aspect-ratio")
        )
        image_name = caption or alt or title_hint or "正文图片"
        if src:
            _register_image_asset(
                image_assets,
                src,
                category="body",
                name=image_name,
                source="inline_image",
                alt=alt,
                caption=caption,
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
            )
        markdown_alt = alt or caption or title_hint or "图片"
        return f"![{markdown_alt}]({src})" if src else ""

    if tag_name == "a":
        href = _resolve_url(base_url, node.get("href") or "")
        text = _clean_text(
            "".join(
                _inline_text(child, base_url, images, image_assets)
                for child in node.children
            )
        )
        if not text or (href and text == href):
            text = _display_text_for_url(href) or href
        if href and _looks_like_image(href):
            _register_image_asset(
                image_assets,
                href,
                category="external",
                name=text or "外链图片",
                source="anchor_image",
                alt=text,
                hostname=_get_hostname(href),
            )
        if href:
            return f"[{text}]({href})"
        return text

    if tag_name in {"strong", "b"}:
        inner = _clean_text(
            "".join(
                _inline_text(child, base_url, images, image_assets)
                for child in node.children
            )
        )
        return f"**{inner}**" if inner else ""

    if tag_name in {"em", "i"}:
        inner = _clean_text(
            "".join(
                _inline_text(child, base_url, images, image_assets)
                for child in node.children
            )
        )
        return f"*{inner}*" if inner else ""

    if tag_name == "code":
        inner = _clean_text(node.get_text(" ", strip=True))
        return f"`{inner}`" if inner else ""

    if tag_name == "sup":
        return f"^{_clean_text(node.get_text(' ', strip=True))}"

    if tag_name in _BLOCK_TAGS | _LIST_TAGS | _HEADING_TAGS:
        return _block_to_markdown(
            node,
            base_url,
            images,
            image_assets,
            list_indent=0,
        )

    return "".join(
        _inline_text(child, base_url, images, image_assets) for child in node.children
    )


def _render_list_items(
    list_node: Tag,
    base_url: str,
    images: List[str],
    image_assets: List[Dict[str, Any]],
    indent: int,
) -> str:
    lines: List[str] = []
    ordered = list_node.name.lower() == "ol"
    index = 1

    for li in list_node.find_all("li", recursive=False):
        inline_parts: List[str] = []
        nested_parts: List[str] = []
        for child in li.children:
            if isinstance(child, Tag) and child.name and child.name.lower() in _LIST_TAGS:
                nested = _block_to_markdown(
                    child,
                    base_url,
                    images,
                    image_assets,
                    indent + 1,
                )
                if nested.strip():
                    nested_parts.append(nested.rstrip())
            else:
                piece = _inline_text(child, base_url, images, image_assets)
                if piece:
                    inline_parts.append(piece)

        content = _normalize_bare_links(_clean_text("".join(inline_parts)))
        prefix = f"{index}. " if ordered else "- "
        pad = "  " * indent
        if content:
            lines.append(f"{pad}{prefix}{content}")
        else:
            lines.append(f"{pad}{prefix}")
        for nested in nested_parts:
            lines.append(nested)
        index += 1

    return "\n".join(lines)


def _block_to_markdown(
    node: Tag,
    base_url: str,
    images: List[str],
    image_assets: List[Dict[str, Any]],
    list_indent: int = 0,
) -> str:
    tag_name = node.name.lower()

    if tag_name in _SKIP_TAGS:
        return ""

    if tag_name in _HEADING_TAGS:
        level = min(max(int(tag_name[1]), 1), 6)
        prefix = "#" * level
        text = _clean_text(
            "".join(
                _inline_text(child, base_url, images, image_assets)
                for child in node.children
            )
        )
        return f"{prefix} {text}" if text else ""

    if tag_name == "p":
        text = _clean_text(
            "".join(
                _inline_text(child, base_url, images, image_assets)
                for child in node.children
            )
        )
        return text

    if tag_name == "blockquote":
        rendered = _render_children(node, base_url, images, image_assets, list_indent)
        quoted = "\n".join(
            f"> {line}" if line.strip() else ">"
            for line in rendered.splitlines()
        ).strip()
        return quoted

    if tag_name in _LIST_TAGS:
        return _render_list_items(node, base_url, images, image_assets, list_indent)

    if tag_name == "pre":
        code = node.get_text("\n", strip=False).strip("\n")
        return f"```\n{code}\n```" if code else ""

    if tag_name == "hr":
        return "---"

    if tag_name == "img":
        return _inline_text(node, base_url, images, image_assets)

    if tag_name == "figure":
        rendered = _render_children(node, base_url, images, image_assets, list_indent)
        return rendered

    if tag_name in _BLOCK_TAGS:
        return _render_children(node, base_url, images, image_assets, list_indent)

    return _clean_text(node.get_text(" ", strip=True))


def _render_children(
    node: Tag,
    base_url: str,
    images: List[str],
    image_assets: List[Dict[str, Any]],
    list_indent: int = 0,
) -> str:
    chunks: List[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _clean_text(str(child))
            if text:
                chunks.append(text)
            continue

        if not isinstance(child, Tag):
            continue

        rendered = _block_to_markdown(
            child,
            base_url,
            images,
            image_assets,
            list_indent,
        )
        if rendered:
            chunks.append(rendered)

    return "\n\n".join(chunk for chunk in chunks if chunk)


def _extract_attachments(soup: BeautifulSoup, base_url: str) -> List[Dict[str, str]]:
    attachments: List[Dict[str, str]] = []
    seen: set[str] = set()

    for link in soup.find_all("link", attrs={"rel": True, "href": True}):
        rel = " ".join(link.get("rel", [])) if isinstance(link.get("rel"), list) else str(link.get("rel") or "")
        href = _resolve_url(base_url, link.get("href") or "")
        if not href or href in seen:
            continue
        if "enclosure" not in rel.lower() and "related" not in rel.lower():
            continue
        mime_type = str(link.get("type") or "").strip()
        title = _clean_text(link.get("title") or link.get_text(" ", strip=True) or href.rsplit("/", 1)[-1])
        attachments.append({"name": title, "url": href, "type": mime_type})
        seen.add(href)

    return attachments


_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED_LIST_RE = re.compile(r"^\d+\.\s+(.*)$")
_UNORDERED_LIST_RE = re.compile(r"^-\s+(.*)$")
_QUOTE_LINE_RE = re.compile(r"^>\s?(.*)$")
_IMAGE_LINE_RE = re.compile(r"^!\[(.*?)\]\((.*?)\)$")
_LINK_LINE_RE = re.compile(r"^\[(.*?)\]\((.*?)\)$")
_MARKDOWN_PROTECTED_RE = re.compile(r"!\[[^\]]*]\([^)]+\)|\[[^\]]+]\([^)]+\)|`[^`]+`")
_BARE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>\"]+)")
_INLINE_SEGMENT_RE = re.compile(
    r"!\[(?P<img_alt>[^\]]*)\]\((?P<img_url>[^)]+)\)"
    r"|\[(?P<link_text>[^\]]+)\]\((?P<link_url>[^)]+)\)"
    r"|`(?P<code>[^`]+)`"
    r"|\*\*(?P<strong>.+?)\*\*"
    r"|\*(?P<emphasis>[^*]+)\*"
)


def _display_text_for_url(url: str) -> str:
    clean_url = str(url or "").strip()
    if not clean_url:
        return ""

    try:
        parsed = urlparse(clean_url)
    except Exception:
        return clean_url

    hostname = str(parsed.netloc or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    path = str(parsed.path or "").strip("/")
    if not hostname:
        return clean_url
    if not path:
        return hostname

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        return hostname

    suffix = "/".join(segments[-2:]) if len(segments) > 1 else segments[-1]
    label = f"{hostname}/{suffix}"
    return label[:48] + "..." if len(label) > 48 else label


def _normalize_bare_links(text: str) -> str:
    source = str(text or "")
    if not source.strip():
        return ""

    protected: List[str] = []

    def shield(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"__MF_LINK_{len(protected) - 1}__"

    def unshield(value: str) -> str:
        restored = value
        for index, chunk in enumerate(protected):
            restored = restored.replace(f"__MF_LINK_{index}__", chunk)
        return restored

    def replace(match: re.Match[str]) -> str:
        raw_url = match.group("url")
        trailing = ""
        while raw_url and raw_url[-1] in ".,;:!?)]}":
            trailing = raw_url[-1] + trailing
            raw_url = raw_url[:-1]
        label = _display_text_for_url(raw_url) or raw_url
        return f"[{label}]({raw_url}){trailing}"

    shielded = _MARKDOWN_PROTECTED_RE.sub(shield, source)
    normalized = _BARE_URL_RE.sub(replace, shielded)
    return unshield(normalized)


def _slugify_heading(text: str, fallback_index: int) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text or "").strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug or f"section-{fallback_index}"


def _parse_inline_segments(text: str) -> List[Dict[str, Any]]:
    source = str(text or "")
    if not source:
        return []

    segments: List[Dict[str, Any]] = []
    cursor = 0
    for match in _INLINE_SEGMENT_RE.finditer(source):
        start, end = match.span()
        if start > cursor:
            plain = source[cursor:start]
            if plain:
                segments.append({"type": "text", "text": plain})

        if match.group("img_url"):
            segments.append(
                {
                    "type": "image",
                    "alt": str(match.group("img_alt") or "").strip() or "图片",
                    "url": str(match.group("img_url") or "").strip(),
                }
            )
        elif match.group("link_url"):
            url = str(match.group("link_url") or "").strip()
            link_text = str(match.group("link_text") or "").strip() or _display_text_for_url(url)
            segments.append(
                {
                    "type": "link",
                    "text": link_text,
                    "url": url,
                    "hostname": _get_hostname(url),
                }
            )
        elif match.group("code"):
            segments.append(
                {
                    "type": "code",
                    "text": str(match.group("code") or "").strip(),
                }
            )
        elif match.group("strong"):
            segments.append(
                {
                    "type": "strong",
                    "text": str(match.group("strong") or "").strip(),
                }
            )
        elif match.group("emphasis"):
            segments.append(
                {
                    "type": "emphasis",
                    "text": str(match.group("emphasis") or "").strip(),
                }
            )
        cursor = end

    if cursor < len(source):
        tail = source[cursor:]
        if tail:
            segments.append({"type": "text", "text": tail})

    return segments


def markdown_to_blocks(markdown: str) -> List[Dict[str, Any]]:
    """Split normalized markdown into a simple block-level structure."""
    lines = str(markdown or "").splitlines()
    blocks: List[Dict[str, Any]] = []
    paragraph_buffer: List[str] = []
    idx = 0
    block_index = 0

    def next_block_index() -> int:
        nonlocal block_index
        current = block_index
        block_index += 1
        return current

    def flush_paragraph() -> None:
        nonlocal paragraph_buffer
        if not paragraph_buffer:
            return
        text = "\n".join(line.rstrip() for line in paragraph_buffer).strip()
        paragraph_buffer = []
        if not text:
            return
        image_match = _IMAGE_LINE_RE.match(text)
        if image_match:
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "image",
                    "alt": image_match.group(1).strip() or "图片",
                    "url": image_match.group(2).strip(),
                    "hostname": _get_hostname(image_match.group(2).strip()),
                }
            )
            return
        link_match = _LINK_LINE_RE.match(text)
        if link_match:
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "link",
                    "text": link_match.group(1).strip() or link_match.group(2).strip(),
                    "url": link_match.group(2).strip(),
                    "hostname": _get_hostname(link_match.group(2).strip()),
                }
            )
            return
        blocks.append(
            {
                "block_index": next_block_index(),
                "type": "paragraph",
                "text": text,
                "segments": _parse_inline_segments(text),
                "char_count": len(text),
            }
        )

    while idx < len(lines):
        line = lines[idx].rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            idx += 1
            continue

        heading_match = _HEADING_LINE_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "title",
                    "level": len(heading_match.group(1)),
                    "text": heading_match.group(2).strip(),
                    "anchor_id": _slugify_heading(
                        heading_match.group(2).strip(),
                        block_index,
                    ),
                    "segments": _parse_inline_segments(heading_match.group(2).strip()),
                }
            )
            idx += 1
            continue

        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            blocks.append({"block_index": next_block_index(), "type": "divider"})
            idx += 1
            continue

        quote_match = _QUOTE_LINE_RE.match(stripped)
        if quote_match:
            flush_paragraph()
            quote_lines = [quote_match.group(1).strip()]
            idx += 1
            while idx < len(lines):
                next_match = _QUOTE_LINE_RE.match(lines[idx].strip())
                if not next_match:
                    break
                quote_lines.append(next_match.group(1).strip())
                idx += 1
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "quote",
                    "text": "\n".join(line for line in quote_lines if line).strip(),
                    "lines": [line for line in quote_lines if line],
                    "segments": _parse_inline_segments(
                        "\n".join(line for line in quote_lines if line).strip()
                    ),
                }
            )
            continue

        list_match = _UNORDERED_LIST_RE.match(stripped) or _ORDERED_LIST_RE.match(stripped)
        if list_match:
            flush_paragraph()
            ordered = bool(_ORDERED_LIST_RE.match(stripped))
            items = [list_match.group(1).strip()]
            idx += 1
            while idx < len(lines):
                next_line = lines[idx].strip()
                next_match = (_ORDERED_LIST_RE if ordered else _UNORDERED_LIST_RE).match(
                    next_line
                )
                if not next_match:
                    break
                items.append(next_match.group(1).strip())
                idx += 1
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "list",
                    "ordered": ordered,
                    "items": [item for item in items if item],
                    "item_blocks": [
                        {
                            "text": item,
                            "segments": _parse_inline_segments(item),
                        }
                        for item in items
                        if item
                    ],
                }
            )
            continue

        image_match = _IMAGE_LINE_RE.match(stripped)
        if image_match:
            flush_paragraph()
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "image",
                    "alt": image_match.group(1).strip() or "图片",
                    "url": image_match.group(2).strip(),
                    "hostname": _get_hostname(image_match.group(2).strip()),
                }
            )
            idx += 1
            continue

        link_match = _LINK_LINE_RE.match(stripped)
        if link_match:
            flush_paragraph()
            blocks.append(
                {
                    "block_index": next_block_index(),
                    "type": "link",
                    "text": link_match.group(1).strip() or link_match.group(2).strip(),
                    "url": link_match.group(2).strip(),
                    "hostname": _get_hostname(link_match.group(2).strip()),
                }
            )
            idx += 1
            continue

        paragraph_buffer.append(line)
        idx += 1

    flush_paragraph()
    return blocks


def attach_image_asset_metadata(
    blocks: List[Dict[str, Any]],
    image_assets: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Enrich image blocks/assets with role, caption and group metadata."""
    normalized_blocks = [
        dict(block) for block in (blocks or []) if isinstance(block, dict)
    ]
    normalized_assets = [
        dict(asset) for asset in (image_assets or []) if isinstance(asset, dict)
    ]

    asset_index_by_key: Dict[str, List[int]] = {}
    for asset_index, asset in enumerate(normalized_assets):
        clean_url = _normalize_url_key(str(asset.get("url") or ""))
        if not clean_url:
            continue
        candidate_keys = {clean_url}
        if "?" in clean_url:
            candidate_keys.add(clean_url.split("?", 1)[0])
        for key in candidate_keys:
            asset_index_by_key.setdefault(key, []).append(asset_index)

    used_asset_indexes: set[int] = set()
    block_asset_map: Dict[int, int] = {}

    def find_asset_index(url: str) -> Optional[int]:
        clean_url = _normalize_url_key(url)
        if not clean_url:
            return None
        candidate_keys = [clean_url]
        if "?" in clean_url:
            candidate_keys.append(clean_url.split("?", 1)[0])

        for key in candidate_keys:
            for asset_index in asset_index_by_key.get(key, []):
                if asset_index not in used_asset_indexes:
                    return asset_index
        for key in candidate_keys:
            candidates = asset_index_by_key.get(key, [])
            if candidates:
                return candidates[0]
        return None

    for asset in normalized_assets:
        category = str(asset.get("category") or "body").strip() or "body"
        role_label = _image_role_label(category)
        asset["role_label"] = role_label
        asset.setdefault("group_id", "")
        asset["group_size"] = int(asset.get("group_size") or 1)
        asset["group_index"] = int(asset.get("group_index") or 0)
        asset["display_variant"] = (
            str(asset.get("display_variant") or "single").strip() or "single"
        )

    for block_index, block in enumerate(normalized_blocks):
        if str(block.get("type") or "").strip() != "image":
            continue
        asset_index = find_asset_index(str(block.get("url") or ""))
        if asset_index is None:
            continue
        used_asset_indexes.add(asset_index)
        block_asset_map[block_index] = asset_index
        asset = normalized_assets[asset_index]
        block["caption"] = str(asset.get("caption") or "").strip()
        block["category"] = str(asset.get("category") or "body").strip() or "body"
        block["role_label"] = str(asset.get("role_label") or "图片").strip() or "图片"
        block["is_wechat"] = bool(asset.get("is_wechat"))
        block["asset_index"] = asset.get("index", asset_index)

    group_counter = 0
    cursor = 0
    while cursor < len(normalized_blocks):
        block = normalized_blocks[cursor]
        if str(block.get("type") or "").strip() != "image":
            cursor += 1
            continue

        group_end = cursor
        while (
            group_end < len(normalized_blocks)
            and str(normalized_blocks[group_end].get("type") or "").strip() == "image"
        ):
            group_end += 1

        group_size = group_end - cursor
        if group_size >= 2:
            group_id = f"image-group-{group_counter}"
            group_counter += 1
            for offset in range(group_size):
                current_block_index = cursor + offset
                current_block = normalized_blocks[current_block_index]
                current_block["group_id"] = group_id
                current_block["group_size"] = group_size
                current_block["group_index"] = offset
                current_block["display_variant"] = "group"

                asset_index = block_asset_map.get(current_block_index)
                if asset_index is None:
                    continue
                normalized_assets[asset_index]["group_id"] = group_id
                normalized_assets[asset_index]["group_size"] = group_size
                normalized_assets[asset_index]["group_index"] = offset
                normalized_assets[asset_index]["display_variant"] = "group"
        else:
            current_block = normalized_blocks[cursor]
            current_block["group_id"] = ""
            current_block["group_size"] = 1
            current_block["group_index"] = 0
            current_block["display_variant"] = "single"
        cursor = group_end

    return normalized_blocks, normalized_assets


def normalize_rss_content(html_fragment: str, base_url: str = "") -> Dict[str, Any]:
    """Normalize RSS HTML/summary fragments into Markdown and metadata."""
    if not html_fragment:
        return {
            "markdown": "",
            "plain_text": "",
            "images": [],
            "attachments": [],
            "blocks": [],
            "image_assets": [],
        }

    fragment = html_lib.unescape(str(html_fragment))
    soup = _make_soup(fragment)

    for tag in soup.find_all(list(_SKIP_TAGS)):
        tag.decompose()

    images: List[str] = []
    image_assets: List[Dict[str, Any]] = []
    blocks: List[str] = []

    root_children: Iterable[Any]
    if soup.body:
        root_children = soup.body.children
    else:
        root_children = soup.children

    for child in root_children:
        if isinstance(child, NavigableString):
            text = _clean_text(str(child))
            if text:
                blocks.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        rendered = _block_to_markdown(
            child,
            base_url,
            images,
            image_assets,
            list_indent=0,
        )
        if rendered:
            blocks.append(rendered)

    markdown = "\n\n".join(blocks)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    markdown = _normalize_bare_links(markdown)

    plain_text = soup.get_text(" ", strip=True)
    attachments = _extract_attachments(soup, base_url)
    for attachment in attachments:
        href = str(attachment.get("url") or "").strip()
        if _looks_like_image(href) or str(attachment.get("type") or "").startswith("image"):
            _register_image_asset(
                image_assets,
                href,
                category="attachment",
                name=str(attachment.get("name") or "附件图片").strip() or "附件图片",
                source="attachment",
                hostname=_get_hostname(href),
                is_wechat=_looks_like_wechat_image(href),
            )

    blocks, image_assets = attach_image_asset_metadata(
        markdown_to_blocks(markdown),
        image_assets,
    )

    return {
        "markdown": markdown,
        "plain_text": plain_text,
        "images": images,
        "attachments": attachments,
        "blocks": blocks,
        "image_assets": image_assets,
    }
