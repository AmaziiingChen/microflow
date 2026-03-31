"""HTTP access config helpers for custom HTML rules."""

from __future__ import annotations

import json
from typing import Any, Dict


DEFAULT_REQUEST_METHOD = "get"
SUPPORTED_REQUEST_METHODS = {"get", "post"}


def normalize_request_headers(value: Any) -> Dict[str, str]:
    """Normalize stored request headers to a clean dict."""
    if isinstance(value, dict):
        return {
            str(key).strip(): str(item).strip()
            for key, item in value.items()
            if str(key).strip() and str(item).strip()
        }

    text = str(value or "").strip()
    if not text:
        return {}

    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            return normalize_request_headers(parsed)

    normalized: Dict[str, str] = {}
    for raw_line in text.replace("\r", "\n").splitlines():
        line = str(raw_line or "").strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        clean_key = str(key or "").strip()
        clean_value = str(raw_value or "").strip()
        if clean_key and clean_value:
            normalized[clean_key] = clean_value
    return normalized


def normalize_cookie_string(value: Any) -> str:
    """Normalize raw cookie text into a single header-style string."""
    text = str(value or "").strip()
    if not text:
        return ""

    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()

    normalized_parts = []
    for raw_part in text.replace("\n", ";").split(";"):
        part = str(raw_part or "").strip()
        if not part:
            continue
        normalized_parts.append(part)

    return "; ".join(normalized_parts)


def parse_cookie_string(value: Any) -> Dict[str, str]:
    """Parse raw cookie text into a name/value mapping."""
    normalized = normalize_cookie_string(value)
    if not normalized:
        return {}

    cookies: Dict[str, str] = {}
    for raw_part in normalized.split(";"):
        part = str(raw_part or "").strip()
        if not part or "=" not in part:
            continue
        name, raw_value = part.split("=", 1)
        clean_name = str(name or "").strip()
        clean_value = str(raw_value or "").strip()
        if clean_name:
            cookies[clean_name] = clean_value
    return cookies


def normalize_request_method(value: Any) -> str:
    """Normalize HTTP method for HTML list fetching."""
    normalized = str(value or DEFAULT_REQUEST_METHOD).strip().lower()
    if normalized not in SUPPORTED_REQUEST_METHODS:
        return DEFAULT_REQUEST_METHOD
    return normalized


def normalize_request_body(value: Any) -> str:
    """Normalize raw request body text for POST list fetching."""
    text = str(value or "")
    stripped = text.strip()
    return stripped if stripped else ""


def ensure_body_content_type(
    headers: Dict[str, str] | None,
    request_method: Any,
    request_body: Any,
) -> Dict[str, str]:
    """Add a sensible default Content-Type for POST bodies when missing."""
    normalized_headers = normalize_request_headers(headers or {})
    method = normalize_request_method(request_method)
    body = normalize_request_body(request_body)

    if method != "post" or not body:
        return normalized_headers

    if any(str(key or "").strip().lower() == "content-type" for key in normalized_headers):
        return normalized_headers

    try:
        json.loads(body)
    except Exception:
        looks_like_json = False
    else:
        looks_like_json = True

    if looks_like_json:
        normalized_headers["Content-Type"] = "application/json; charset=UTF-8"
    elif "=" in body:
        normalized_headers["Content-Type"] = (
            "application/x-www-form-urlencoded; charset=UTF-8"
        )

    return normalized_headers
