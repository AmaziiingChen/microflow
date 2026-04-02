"""性能监控服务 - 监控CPU和内存使用情况"""

import psutil
import threading
import time
import logging
from typing import Dict, Any, Optional, Callable, List
from collections import deque

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """
    性能监控服务

    功能：
    - 实时监控CPU和内存使用率
    - 保留最近N个采样点的历史数据
    - 支持回调通知前端
    """

    def __init__(self, interval: int = 5, history_size: int = 60):
        """
        初始化性能监控

        Args:
            interval: 采样间隔（秒），默认5秒
            history_size: 保留历史数据点数量，默认60个（5分钟）
        """
        self.interval = interval
        self.history_size = history_size

        # 历史数据（使用deque自动限制大小）
        self._cpu_history: deque = deque(maxlen=history_size)
        self._memory_history: deque = deque(maxlen=history_size)

        # 当前进程
        self._process = psutil.Process()

        # 控制标志
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

        # 回调函数（用于通知前端）
        self._callback: Optional[Callable[[Dict[str, Any]], None]] = None

        # 统计信息
        self._stats = {
            'peak_cpu': 0.0,
            'peak_memory': 0.0,
            'avg_cpu': 0.0,
            'avg_memory': 0.0,
        }

    def set_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """设置性能数据回调函数"""
        self._callback = callback

    def start(self) -> None:
        """启动监控"""
        if self._is_running:
            logger.warning("性能监控已在运行")
            return

        self._is_running = True
        self._stop_event.clear()

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

        logger.info(f"性能监控已启动（间隔: {self.interval}秒）")

    def stop(self) -> None:
        """停止监控"""
        if not self._is_running:
            return

        self._stop_event.set()
        self._is_running = False

        if self._thread:
            self._thread.join(timeout=2)

        logger.info("性能监控已停止")

    def _monitor_loop(self) -> None:
        """监控主循环"""
        while not self._stop_event.is_set():
            try:
                # 采集性能数据
                cpu_percent = self._process.cpu_percent(interval=0.1)
                memory_info = self._process.memory_info()
                memory_mb = memory_info.rss / 1024 / 1024  # 转换为MB

                # 保存历史数据
                timestamp = time.time()
                self._cpu_history.append({'time': timestamp, 'value': cpu_percent})
                self._memory_history.append({'time': timestamp, 'value': memory_mb})

                # 更新统计信息
                self._update_stats(cpu_percent, memory_mb)

                # 构建数据包
                data = {
                    'timestamp': timestamp,
                    'cpu_percent': round(cpu_percent, 2),
                    'memory_mb': round(memory_mb, 2),
                    'stats': dict(self._stats),
                }

                # 回调通知
                if self._callback:
                    try:
                        self._callback(data)
                    except Exception as e:
                        logger.debug(f"性能监控回调失败: {e}")

            except Exception as e:
                logger.warning(f"性能采集异常: {e}")

            # 等待下一次采样
            self._stop_event.wait(self.interval)

    def _update_stats(self, cpu: float, memory: float) -> None:
        """更新统计信息"""
        # 更新峰值
        self._stats['peak_cpu'] = max(self._stats['peak_cpu'], cpu)
        self._stats['peak_memory'] = max(self._stats['peak_memory'], memory)

        # 计算平均值
        if self._cpu_history:
            self._stats['avg_cpu'] = sum(d['value'] for d in self._cpu_history) / len(self._cpu_history)
        if self._memory_history:
            self._stats['avg_memory'] = sum(d['value'] for d in self._memory_history) / len(self._memory_history)

        # 四舍五入
        self._stats['avg_cpu'] = round(self._stats['avg_cpu'], 2)
        self._stats['avg_memory'] = round(self._stats['avg_memory'], 2)

    def get_current_stats(self) -> Dict[str, Any]:
        """获取当前性能统计"""
        try:
            cpu_percent = self._process.cpu_percent(interval=0.1)
            memory_info = self._process.memory_info()
            memory_mb = memory_info.rss / 1024 / 1024

            return {
                'cpu_percent': round(cpu_percent, 2),
                'memory_mb': round(memory_mb, 2),
                'stats': dict(self._stats),
                'history': {
                    'cpu': list(self._cpu_history),
                    'memory': list(self._memory_history),
                }
            }
        except Exception as e:
            logger.error(f"获取性能统计失败: {e}")
            return {}

    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = {
            'peak_cpu': 0.0,
            'peak_memory': 0.0,
            'avg_cpu': 0.0,
            'avg_memory': 0.0,
        }
        self._cpu_history.clear()
        self._memory_history.clear()
        logger.info("性能统计已重置")


# 全局单例
_monitor_instance: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """获取性能监控单例"""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = PerformanceMonitor()
    return _monitor_instance
