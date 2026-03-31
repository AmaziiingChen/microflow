"""Helpers for article URL canonicalization and stable synthetic identities."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_PARAM_PREFIXES = (
    "utm_",
)

_TRACKING_PARAM_NAMES = {
    "_hsenc",
    "_hsmi",
    "fbclid",
    "from",
    "gclid",
    "igshid",
    "mkt_tok",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "share_from",
    "share_source",
    "si",
    "spm",
    "yclid",
}

_TRACKING_PARAM_NAME_PATTERNS = (
    re.compile(r"^session(?:id)?$", re.IGNORECASE),
    re.compile(r"^jsessionid$", re.IGNORECASE),
    re.compile(r"^phpsessid$", re.IGNORECASE),
)

_VIRTUAL_FRAGMENT_PREFIXES = ("item-", "virtual-")
_URL_FIELD_KEYWORDS = ("url", "link", "href")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split()).strip()


def _normalize_netloc(split_result) -> str:
    host = str(split_result.hostname or "").strip().lower()
    if not host:
        return str(split_result.netloc or "").strip().lower()

    port = split_result.port
    if (split_result.scheme or "").lower() == "http" and port == 80:
        port = None
    if (split_result.scheme or "").lower() == "https" and port == 443:
        port = None

    return f"{host}:{port}" if port else host


def _should_drop_query_param(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if not lowered:
        return False
    if lowered in _TRACKING_PARAM_NAMES:
        return True
    if any(lowered.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES):
        return True
    return any(pattern.match(lowered) for pattern in _TRACKING_PARAM_NAME_PATTERNS)


def _is_virtual_fragment(fragment: str) -> bool:
    lowered = str(fragment or "").strip().lower()
    return any(lowered.startswith(prefix) for prefix in _VIRTUAL_FRAGMENT_PREFIXES)


def canonicalize_article_url(
    url: str,
    *,
    preserve_virtual_fragment: bool = True,
) -> str:
    """Normalize article URLs so tracking-only differences do not create duplicates."""

    raw = str(url or "").strip().replace("&amp;", "&")
    if not raw:
        return ""

    try:
        parts = urlsplit(raw)
    except Exception:
        return raw

    scheme = str(parts.scheme or "").strip().lower()
    netloc = _normalize_netloc(parts)

    path = str(parts.path or "").strip()
    path = re.sub(r"/{2,}", "/", path)
    if path.endswith("/") and path != "/":
        path = path[:-1]

    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _should_drop_query_param(key)
    ]
    query_items.sort(key=lambda item: (str(item[0]).lower(), str(item[1])))
    query = urlencode(query_items, doseq=True)

    fragment = str(parts.fragment or "").strip()
    if not (preserve_virtual_fragment and _is_virtual_fragment(fragment)):
        fragment = ""

    try:
        return urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:
        return raw


def build_stable_article_fingerprint(
    *,
    source_name: str = "",
    page_url: str = "",
    title: str = "",
    date: str = "",
    body_text: str = "",
    fields: Optional[Dict[str, Any]] = None,
    extra_parts: Optional[Iterable[Any]] = None,
) -> str:
    """Build a stable digest for articles that do not expose a real URL."""

    normalized_fields: List[Tuple[str, str]] = []
    for key, value in sorted((fields or {}).items(), key=lambda item: str(item[0]).lower()):
        normalized_key = _clean_text(key).lower()
        if not normalized_key:
            continue
        if any(keyword in normalized_key for keyword in _URL_FIELD_KEYWORDS):
            continue
        normalized_value = _clean_text(value)
        if not normalized_value:
            continue
        normalized_fields.append((normalized_key, normalized_value))

    parts: List[str] = [
        canonicalize_article_url(page_url, preserve_virtual_fragment=False),
        _clean_text(source_name),
        _clean_text(title),
        _clean_text(date),
        _clean_text(body_text),
        json.dumps(normalized_fields, ensure_ascii=False, separators=(",", ":")),
    ]

    if extra_parts:
        parts.extend(_clean_text(item) for item in extra_parts if _clean_text(item))

    joined = "||".join(part for part in parts if part)
    digest_source = joined or json.dumps(
        normalized_fields,
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:16]
