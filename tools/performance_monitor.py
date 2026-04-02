#!/usr/bin/env python3
"""性能监控独立窗口 - 用于实时监控 MicroFlow 性能"""

import webview
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.services.performance_monitor import get_performance_monitor


class PerformanceAPI:
    """性能监控API"""

    def __init__(self):
        self.monitor = get_performance_monitor()
        self.monitor.start()

    def get_performance_stats(self):
        """获取性能统计"""
        return self.monitor.get_current_stats()

    def reset_performance_stats(self):
        """重置统计"""
        self.monitor.reset_stats()
        return {"status": "success", "message": "统计已重置"}


if __name__ == "__main__":
    # 获取前端文件路径
    frontend_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend",
        "performance-monitor.html"
    )

    # 创建API实例
    api = PerformanceAPI()

    # 创建窗口
    window = webview.create_window(
        title="MicroFlow 性能监控",
        url=frontend_path,
        js_api=api,
        width=900,
        height=700,
        resizable=True,
        frameless=False,
    )

    # 启动
    webview.start(debug=False)
