"""
日期解析工具 - 支持多种日期格式的健壮解析

提供统一的日期解析函数，供 scheduler、rss_spider 等模块共享。
"""

import re
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def parse_date_safe(date_str: str) -> Optional[datetime]:
    """
    增强的日期解析函数，支持多种常见格式。

    支持格式：
    - YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    - YYYY年MM月DD日
    - MM/DD/YYYY, DD/MM/YYYY
    - ISO 8601 格式（带时间）
    - 带时间戳的格式

    Args:
        date_str: 日期字符串

    Returns:
        datetime 对象（仅保留日期部分），解析失败返回 None
    """
    if not date_str or not date_str.strip():
        return None

    text = date_str.strip()

    # 1. 尝试使用 dateutil 解析（如果可用）
    try:
        from dateutil import parser

        dt = parser.parse(text, fuzzy=False)
        # 只保留日期部分（统一为 00:00:00）
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    except ImportError:
        pass  # dateutil 未安装，使用回退正则
    except Exception:
        pass  # 解析失败，继续尝试正则

    # 2. 中文日期格式：YYYY年MM月DD日
    match = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    if match:
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day))
        except ValueError:
            pass

    # 3. ISO 8601 格式：YYYY-MM-DDTHH:MM:SS
    match = re.search(r'(\d{4})-(\d{2})-(\d{2})T', text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            pass

    # 4. 标准日期格式：YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD
    patterns = [
        r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})',  # YYYY-MM-DD 等
        r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})',  # MM/DD/YYYY 或 DD/MM/YYYY
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            groups = match.groups()
            if len(groups) == 3:
                # 判断模式类型
                if pattern == r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})':
                    # MM/DD/YYYY 或 DD/MM/YYYY
                    # 启发式：如果第一个数 > 12，肯定是日，否则假设美式 MM/DD
                    first, second, year = groups
                    if int(first) > 12:
                        day, month = first, second
                    else:
                        month, day = first, second
                else:
                    year, month, day = groups

                try:
                    return datetime(int(year), int(month), int(day))
                except ValueError:
                    continue

    # 5. 回退：仅保留前 10 字符（原有逻辑）
    normalized = text.replace("/", "-")
    date_part = normalized[:10]
    try:
        return datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        return None


def format_date(date_str: str) -> str:
    """
    将日期字符串格式化为 YYYY-MM-DD 格式

    Args:
        date_str: 原始日期字符串

    Returns:
        格式化后的日期字符串，解析失败返回空字符串
    """
    dt = parse_date_safe(date_str)
    if dt:
        return dt.strftime("%Y-%m-%d")
    return ""
