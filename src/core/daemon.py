"""守护进程管理器 - 负责后台定时任务的调度"""

import time
import threading
import logging
from typing import Callable, Optional, Dict, Any

from src.core.network_utils import check_network_status, NetworkStatus, get_network_description

logger = logging.getLogger(__name__)


class DaemonManager:
    """
    守护进程管理器 - 单一职责：后台定时任务

    使用方式：
        daemon = DaemonManager()
        daemon.start(
            task_callback=self.check_updates,
            interval_seconds=900,
            on_new_articles=self._on_new_articles
        )

    特性：
    - 智能休眠：每 2 秒检查一次网络状态
    - 断线补偿：网络恢复后立即触发补偿抓取
    """

    # 断线补偿阈值（秒）：网络恢复后，如果距离上次成功抓取超过此时间，触发补偿
    COMPENSATION_THRESHOLD = 300  # 5 分钟

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

        # 网络状态追踪
        self._last_network_status: Optional[NetworkStatus] = None
        self._last_successful_run: float = 0  # 上次成功抓取的时间戳
        self._network_check_interval = 2  # 网络检查间隔（秒）

    @property
    def is_running(self) -> bool:
        """守护线程是否正在运行"""
        return self._is_running

    @property
    def stop_event(self) -> threading.Event:
        """获取停止事件（用于外部等待或设置）"""
        return self._stop_event

    def start(
        self,
        task_callback: Callable[[], Dict[str, Any]],
        interval_seconds: int = 900,
        initial_wait: int = 10,
        on_new_articles: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        debug_mode: bool = False
    ) -> None:
        """
        启动守护线程

        Args:
            task_callback: 要执行的任务（返回 {"status": ..., "new_count": ...}）
            interval_seconds: 轮询间隔（秒），默认 15 分钟
            initial_wait: 初始等待时间（秒）
            on_new_articles: 发现新文章时的回调，参数为 (count, result)
            debug_mode: 调试模式，缩短初始等待时间
        """
        if self._is_running:
            logger.warning("守护线程已在运行中，跳过重复启动")
            return

        self._is_running = True
        self._stop_event.clear()
        self._last_successful_run = time.time()  # 初始化为当前时间

        def worker():
            # 1. 强行打印，绕过 logger 可能的级别过滤
            print(f"🚀 [Debug] 守护线程准备就绪 (轮询间隔: {interval_seconds}秒)")
            logger.info(f"🛡️ 守护线程已启动 (轮询间隔: {interval_seconds}秒)")

            # 初始等待（调试模式下缩短）
            actual_initial_wait = 2 if debug_mode else initial_wait
            if self._stop_event.wait(actual_initial_wait):
                return

            last_run_time = 0

            while not self._stop_event.is_set():
                current_time = time.time()
                time_elapsed = current_time - last_run_time

                # 🌟 智能网络检测
                current_network_status = check_network_status()
                previous_network_status = self._last_network_status

                # 更新网络状态记录
                self._last_network_status = current_network_status

                # 🌟 断线补偿逻辑
                should_compensate = False
                if previous_network_status == NetworkStatus.NO_NETWORK:
                    # 之前无网络，现在有网络了
                    if current_network_status != NetworkStatus.NO_NETWORK:
                        time_since_last_success = current_time - self._last_successful_run
                        if time_since_last_success >= self.COMPENSATION_THRESHOLD:
                            logger.info(f"🔄 网络恢复！触发补偿抓取（距上次成功: {int(time_since_last_success)}秒）")
                            should_compensate = True

                # 首次运行、达到轮询间隔、或需要补偿时执行任务
                if should_compensate or time_elapsed >= interval_seconds or last_run_time == 0:
                    # 无网络时跳过
                    if current_network_status == NetworkStatus.NO_NETWORK:
                        logger.debug("💤 无网络连接，跳过本轮检测")
                        self._stop_event.wait(self._network_check_interval)
                        continue

                    network_desc = get_network_description(current_network_status)
                    reason = "补偿抓取" if should_compensate else "定时检测"
                    print(f"⏳ [Debug] 触发{reason}... (网络: {network_desc})")
                    logger.info(f"🔍 触发{reason}... (网络: {network_desc})")

                    # 2. 加上极其重要的 try...except 保护罩
                    try:
                        result = task_callback()
                        print(f"✅ [Debug] 抓取执行完毕！状态: {result.get('status')}, 提交数量: {result.get('submitted_count')}")

                        # 增加一层类型检查，防止 result 为 None 导致 get() 报错崩溃
                        if result and isinstance(result, dict):
                            if result.get("status") == "success":
                                # 更新成功抓取时间
                                self._last_successful_run = time.time()

                                # 检查是否有新文章
                                new_count = result.get("new_count", 0)
                                submitted_count = result.get("submitted_count", 0)
                                if new_count > 0 and on_new_articles:
                                    on_new_articles(new_count, result)
                                elif submitted_count > 0:
                                    logger.info(f"📊 已提交 {submitted_count} 篇文章到处理队列")
                        else:
                            print(f"⚠️ [Debug] 任务返回的格式异常: {result}")

                    except Exception as e:
                        print(f"❌ [Debug] 守护线程发生致命错误 (已被拦截，线程继续存活): {e}")
                        import traceback
                        traceback.print_exc()

                    finally:
                        # 3. 无论成功还是崩溃，都必须更新时间戳，防止无限死循环狂刷目标网站
                        last_run_time = time.time()
                        print(f"💤 [Debug] 本轮结束，等待下一次触发...")
                else:
                    # 短间隔等待，便于快速响应停止信号和网络变化
                    self._stop_event.wait(self._network_check_interval)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止守护线程"""
        if self._is_running:
            self._stop_event.set()
            self._is_running = False
            logger.info("守护线程已停止")

    def request_stop(self) -> None:
        """请求停止守护线程（设置停止事件）"""
        self._stop_event.set()

    def wait_for_stop(self, timeout: Optional[float] = None) -> bool:
        """
        等待守护线程停止

        Args:
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            True 如果线程已停止，False 如果超时
        """
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True

    def get_network_status(self) -> Optional[str]:
        """获取当前网络状态（用于前端展示）"""
        if self._last_network_status:
            return self._last_network_status.value
        return None