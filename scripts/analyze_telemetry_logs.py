#!/usr/bin/env python3
"""分析腾讯云函数日志中的遥测数据

使用方法：
    python scripts/analyze_telemetry_logs.py <日志文件路径>

示例：
    python scripts/analyze_telemetry_logs.py logs.txt
"""

import re
import sys
from collections import Counter, defaultdict
from datetime import datetime


def parse_log_file(file_path):
    """解析日志文件"""
    events = []

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取事件信息：- event_name from platform version
    pattern = r'- (\w+) (?:from|\|) (\w+) (?:from|\|) ([\w\.]+)'
    matches = re.findall(pattern, content)

    for event_name, platform, version in matches:
        events.append({
            'event': event_name,
            'platform': platform,
            'version': version,
        })

    # 提取收到事件的总数
    total_pattern = r'📊 收到 (\d+) 条事件'
    total_matches = re.findall(total_pattern, content)
    total_events = sum(int(n) for n in total_matches)

    return events, total_events


def analyze_events(events, total_events):
    """分析事件数据"""
    print("="*60)
    print("📊 MicroFlow 遥测数据分析报告")
    print("="*60)
    print()

    # 总体统计
    print(f"📈 总事件数: {total_events}")
    print(f"📝 日志中显示的事件数: {len(events)}")
    print()

    # 事件类型统计
    event_types = Counter(e['event'] for e in events)
    print("🎯 事件类型分布:")
    for event, count in event_types.most_common():
        percentage = (count / len(events) * 100) if events else 0
        print(f"  {event:30s} {count:4d} 次 ({percentage:5.1f}%)")
    print()

    # 平台分布
    platforms = Counter(e['platform'] for e in events)
    print("💻 平台分布:")
    for platform, count in platforms.most_common():
        platform_name = {'darwin': 'macOS', 'windows': 'Windows'}.get(platform, platform)
        percentage = (count / len(events) * 100) if events else 0
        print(f"  {platform_name:15s} {count:4d} 次 ({percentage:5.1f}%)")
    print()

    # 版本分布
    versions = Counter(e['version'] for e in events)
    print("📦 版本分布:")
    for version, count in versions.most_common():
        percentage = (count / len(events) * 100) if events else 0
        print(f"  {version:15s} {count:4d} 次 ({percentage:5.1f}%)")
    print()

    # 功能使用热度 Top 10
    print("🔥 功能使用热度 Top 10:")
    for i, (event, count) in enumerate(event_types.most_common(10), 1):
        print(f"  {i:2d}. {event:30s} {count:4d} 次")
    print()

    # 用户行为分析
    user_actions = {
        'article_open': '文章打开',
        'search_submit': '搜索',
        'article_favorite_toggle': '收藏',
        'article_copy': '复制',
        'article_snapshot': '截图',
        'detail_mode_switch': '模式切换',
    }

    action_counts = {k: event_types.get(k, 0) for k in user_actions.keys()}
    if any(action_counts.values()):
        print("👤 用户行为统计:")
        for event, label in user_actions.items():
            count = action_counts[event]
            if count > 0:
                print(f"  {label:15s} {count:4d} 次")
        print()

    # 错误统计
    error_events = {k: v for k, v in event_types.items() if k.startswith('error_')}
    if error_events:
        print("⚠️  错误事件统计:")
        for event, count in error_events.items():
            print(f"  {event:30s} {count:4d} 次")
        print()

    # AI 功能使用
    ai_events = {k: v for k, v in event_types.items() if 'ai_' in k}
    if ai_events:
        print("🤖 AI 功能使用:")
        for event, count in ai_events.items():
            print(f"  {event:30s} {count:4d} 次")
        print()

    print("="*60)


def main():
    if len(sys.argv) < 2:
        print("使用方法: python analyze_telemetry_logs.py <日志文件路径>")
        print("示例: python analyze_telemetry_logs.py logs.txt")
        sys.exit(1)

    log_file = sys.argv[1]

    try:
        events, total_events = parse_log_file(log_file)
        analyze_events(events, total_events)
    except FileNotFoundError:
        print(f"❌ 错误: 找不到文件 {log_file}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
