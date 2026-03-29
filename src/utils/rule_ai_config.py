"""Rule AI configuration normalization helpers.

This module keeps the new split RSS AI fields and the legacy fields aligned,
so older saved rules can continue to work while the UI migrates forward.
"""

from __future__ import annotations

from typing import Any, Dict


def _clean_prompt(value: Any) -> str:
    return str(value or "").strip()


def normalize_rule_ai_config(rule_dict: Dict[str, Any] | None) -> Dict[str, Any]:
    """Normalize split AI config fields for both RSS and HTML rules."""
    normalized = dict(rule_dict or {})
    source_type = str(normalized.get("source_type") or "html").strip().lower() or "html"
    normalized["source_type"] = source_type

    legacy_enabled = bool(normalized.get("require_ai_summary", False))
    legacy_prompt = _clean_prompt(normalized.get("custom_summary_prompt"))

    formatting_prompt = _clean_prompt(normalized.get("formatting_prompt"))
    summary_prompt = _clean_prompt(normalized.get("summary_prompt"))

    has_split_fields = any(
        key in normalized
        for key in (
            "enable_ai_formatting",
            "enable_ai_summary",
            "formatting_prompt",
            "summary_prompt",
        )
    )

    if source_type == "rss":
        if has_split_fields:
            if not formatting_prompt and not summary_prompt and legacy_prompt:
                if bool(normalized.get("enable_ai_formatting", False)):
                    formatting_prompt = legacy_prompt
                if bool(normalized.get("enable_ai_summary", False)):
                    summary_prompt = legacy_prompt
            enable_ai_formatting = bool(
                normalized.get("enable_ai_formatting", False) or formatting_prompt
            )
            enable_ai_summary = bool(
                normalized.get("enable_ai_summary", False) or summary_prompt
            )
        else:
            # Legacy RSS rules used one switch and one prompt for both stages.
            legacy_active = bool(legacy_enabled or legacy_prompt)
            if not formatting_prompt and legacy_prompt:
                formatting_prompt = legacy_prompt
            if not summary_prompt and legacy_prompt:
                summary_prompt = legacy_prompt
            enable_ai_formatting = legacy_active
            enable_ai_summary = legacy_active

        normalized["enable_ai_formatting"] = enable_ai_formatting
        normalized["enable_ai_summary"] = enable_ai_summary
        normalized["formatting_prompt"] = formatting_prompt
        normalized["summary_prompt"] = summary_prompt
        normalized["require_ai_summary"] = bool(
            enable_ai_formatting or enable_ai_summary
        )
        normalized["custom_summary_prompt"] = (
            summary_prompt
            or (
                legacy_prompt
                if not has_split_fields and enable_ai_summary
                else ""
            )
        )
        return normalized

    enable_ai_summary = bool(
        normalized.get("enable_ai_summary", legacy_enabled) or summary_prompt or legacy_prompt
    )
    summary_prompt = summary_prompt or legacy_prompt

    normalized["enable_ai_formatting"] = False
    normalized["formatting_prompt"] = ""
    normalized["enable_ai_summary"] = enable_ai_summary
    normalized["summary_prompt"] = summary_prompt
    normalized["require_ai_summary"] = enable_ai_summary
    normalized["custom_summary_prompt"] = summary_prompt
    return normalized
