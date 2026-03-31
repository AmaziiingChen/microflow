"""HTML 动态规则的详情抓取策略工具。"""

from __future__ import annotations

from typing import Any


DEFAULT_DETAIL_STRATEGY = "detail_preferred"
SUPPORTED_DETAIL_STRATEGIES = {
    "list_only",
    "detail_preferred",
    "hybrid",
}


def normalize_detail_strategy(
    value: Any,
    *,
    skip_detail: bool = False,
) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_DETAIL_STRATEGIES:
        return normalized
    if skip_detail:
        return "list_only"
    return DEFAULT_DETAIL_STRATEGY


def should_skip_detail_fetch(detail_strategy: Any) -> bool:
    return normalize_detail_strategy(detail_strategy) == "list_only"


__all__ = [
    "DEFAULT_DETAIL_STRATEGY",
    "SUPPORTED_DETAIL_STRATEGIES",
    "normalize_detail_strategy",
    "should_skip_detail_fetch",
]
