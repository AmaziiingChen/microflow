"""Text cleaning helpers used across UI and AI output paths."""

from __future__ import annotations

import re

# Covers common emoji blocks, symbol emojis, and regional indicators.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\uFE0F"
    "\u200D"
    "]",
    flags=re.UNICODE,
)


def strip_emoji(text: str | None) -> str:
    """Remove emoji and emoji glue characters from text."""
    if not text:
        return ""
    return _EMOJI_PATTERN.sub("", text)

