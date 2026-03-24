"""守护进程管理器 - 负责后台定时任务的调度"""

import os
import time
import threading
import logging
import random
import datetime
from typing import Callable, Optional, Dict, Any

from src.core.network_utils import (
    check_network_status,
    NetworkStatus,
    get_network_description,
)
from src.core.paths import LAST_FETCH_TIME_PATH, ensure_data_dir_exists

logger = logging.getLogger(__name__)

# 确保数据目录存在
ensure_data_dir_exists()


class DaemonManager:
    """
    守护进程管理器 - 单一职责：后台定时任务

    使用方式：
        daemon = DaemonManager()
        daemon.start(
            task_callback=self.check_updates,
            interval_getter=lambda: 900,
            on_new_articles=self._on_new_articles
        )

    特性：
    - 固定间隔 + 边界抖动 + 夜间模式
    - 早8点/晚8点抖动抓取（0-30分钟随机延迟）
    - 深夜静默（20:00-08:00）
    - 断网退避与断线补偿
    - 后端持久化冷却（防止重启绕过）
    """

    # 断线补偿阈值（秒）：网络恢复后，如果距离上次成功抓取超过此时间，触发补偿
    COMPENSATION_THRESHOLD = 300  # 5 分钟

    # 断网退避最大间隔（秒）
    MAX_BACKOFF_INTERVAL = 3600  # 1 小时

    # 最小执行间隔（秒）：防止连续触发
    MIN_RUN_INTERVAL = 600  # 10 分钟

    # 🌟 手动更新冷却时间（秒）
    MANUAL_UPDATE_COOLDOWN = 120  # 2 分钟

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False

        # 网络状态追踪
        self._last_network_status: Optional[NetworkStatus] = None
        self._last_successful_run: float = 0  # 上次成功抓取的时间戳

    # ==================== 持久化冷却方法 ====================

    def _save_last_fetch_time(self) -> None:
        """将当前时间戳持久化到文件"""
        try:
            # 确保目录存在（paths 模块已处理，这里双重保险）
            LAST_FETCH_TIME_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_FETCH_TIME_PATH.write_text(str(time.time()), encoding="utf-8")
            logger.debug(f"💾 已保存抓取时间戳")
        except Exception as e:
            logger.warning(f"保存抓取时间戳失败: {e}")

    def _get_last_fetch_time(self) -> float:
        """从文件读取上次抓取时间戳，失败返回 0.0"""
        try:
            if LAST_FETCH_TIME_PATH.exists():
                return float(LAST_FETCH_TIME_PATH.read_text(encoding="utf-8").strip())
        except Exception as e:
            logger.debug(f"读取抓取时间戳失败: {e}")
        return 0.0

    def record_manual_update(self) -> None:
        """
        公开方法：记录手动更新时间
        供 api.py 的手动更新接口调用
        """
        self._save_last_fetch_time()
        self._last_successful_run = time.time()
        logger.info("📝 已记录手动更新时间")

    # =========================================================

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
        interval_getter: Callable[[], int] = lambda: 900,
        initial_wait: int = 10,
        on_new_articles: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        debug_mode: bool = False,
    ) -> None:
        """
        启动守护线程

        Args:
            task_callback: 要执行的任务（返回 {"status": ..., "new_count": ...}）
            interval_getter: 获取轮询间隔的函数（返回秒数），支持热重载
            initial_wait: 初始等待时间（秒）
            on_new_articles: 发现新文章时的回调，参数为 (count, result)
            debug_mode: 调试模式，缩短初始等待时间
        """
        if self._is_running:
            logger.warning("守护线程已在运行中，跳过重复启动")
            return

        self._is_running = True
        self._stop_event.clear()

        # 🌟 从持久化文件恢复上次抓取时间
        persisted_last_fetch_time = self._get_last_fetch_time()
        self._last_successful_run = (
            persisted_last_fetch_time if persisted_last_fetch_time > 0 else time.time()
        )

        def worker():
            # 🌟 首次获取间隔
            current_interval = interval_getter()
            print(f"🚀 [Debug] 守护线程准备就绪 (当前轮询间隔: {current_interval}秒)")
            logger.info(f"🛡️ 守护线程已启动 (当前轮询间隔: {current_interval}秒)")

            # 初始等待（调试模式下缩短）
            actual_initial_wait = 2 if debug_mode else initial_wait
            if self._stop_event.wait(actual_initial_wait):
                return

            # 🌟 时间追踪变量
            first_run = True
            last_run_time = persisted_last_fetch_time  # 🌟 使用持久化的时间戳初始化
            current_date = ""

            # 🌟 抖动状态
            morning_ran = False
            evening_ran = False
            morning_target = 0.0
            evening_target = 0.0

            # 🌟 断网退避
            current_wait_interval = current_interval

            while not self._stop_event.is_set():
                # 🌟 每次循环开始时，强制从硬盘同步最新抓取时间，防止在 sleep 期间被手动抓取"偷家"
                disk_time = self._get_last_fetch_time()
                if disk_time > last_run_time:
                    last_run_time = disk_time

                # 🌟 热重载：动态获取最新间隔
                current_interval = interval_getter()

                now = datetime.datetime.now()
                current_time = time.time()
                hour_float = now.hour + now.minute / 60.0
                today_str = now.strftime("%Y-%m-%d")

                # 🌟 跨天重置逻辑
                if today_str != current_date:
                    current_date = today_str
                    morning_ran = False
                    evening_ran = False
                    morning_target = 0.0
                    evening_target = 0.0
                    logger.info(f"📅 新的一天开始: {current_date}，抖动状态已重置")

                # 🌟 抖动目标生成
                # 早8点抖动区间 (8:00-8:30)
                if 8.0 <= hour_float < 8.5 and not morning_ran and morning_target == 0:
                    morning_target = current_time + random.randint(0, 1800)
                    logger.info(
                        f"🌅 早间抖动目标已设定: {datetime.datetime.fromtimestamp(morning_target).strftime('%H:%M:%S')}"
                    )

                # 晚8点抖动区间 (19:30-20:00)
                if (
                    19.5 <= hour_float < 20.0
                    and not evening_ran
                    and evening_target == 0
                ):
                    evening_target = current_time + random.randint(0, 1800)
                    logger.info(
                        f"🌆 晚间抖动目标已设定: {datetime.datetime.fromtimestamp(evening_target).strftime('%H:%M:%S')}"
                    )

                # 🌟 执行判断逻辑（互斥 if-elif）
                should_run = False

                if first_run:
                    # 🌟 首次运行冷却校验：防止用户通过重启绕过冷却限制
                    time_since_last_fetch = current_time - last_run_time
                    if time_since_last_fetch < self.MANUAL_UPDATE_COOLDOWN:
                        # 刚刚抓取过，跳过首抓
                        remaining_cooldown = int(
                            self.MANUAL_UPDATE_COOLDOWN - time_since_last_fetch
                        )
                        logger.info(
                            f"⏳ 检测到近期已抓取（{int(time_since_last_fetch)}秒前），跳过首抓，剩余冷却: {remaining_cooldown}秒"
                        )
                        print(
                            f"⏳ [Debug] 跳过首抓（剩余冷却: {remaining_cooldown}秒）"
                        )
                    else:
                        # 冷却已过，正常执行首抓
                        should_run = True
                        logger.info("🚀 首次运行，立即执行抓取")
                    first_run = False

                elif hour_float >= 20.0 or hour_float < 8.0:
                    # 深夜阶段：非首抓则绝对静默
                    pass

                elif (
                    8.0 <= hour_float < 8.5
                    and not morning_ran
                    and current_time >= morning_target
                ):
                    # 早间抖动触发
                    if current_time >= last_run_time + self.MIN_RUN_INTERVAL:
                        should_run = True
                        logger.info(f"🌅 早间抖动触发 (目标时间已到)")

                elif (
                    19.5 <= hour_float < 20.0
                    and not evening_ran
                    and current_time >= evening_target
                ):
                    # 晚间抖动触发
                    if current_time >= last_run_time + self.MIN_RUN_INTERVAL:
                        should_run = True
                        logger.info(f"🌆 晚间抖动触发 (目标时间已到)")

                elif 8.0 <= hour_float < 20.0:
                    # 白天正常轮询
                    if (
                        current_time >= last_run_time + current_interval
                        and current_time >= last_run_time + self.MIN_RUN_INTERVAL
                    ):
                        should_run = True
                        logger.debug(
                            f"☀️ 白天定时轮询触发 (当前间隔: {current_interval}秒)"
                        )

                # 🌟 执行任务
                if should_run:
                    # 执行网络检测
                    current_network_status = check_network_status()
                    previous_network_status = self._last_network_status
                    self._last_network_status = current_network_status

                    # 断线补偿逻辑
                    should_compensate = False
                    if previous_network_status == NetworkStatus.NO_NETWORK:
                        if current_network_status != NetworkStatus.NO_NETWORK:
                            time_since_last_success = (
                                current_time - self._last_successful_run
                            )
                            if time_since_last_success >= self.COMPENSATION_THRESHOLD:
                                logger.info(
                                    f"🔄 网络恢复！触发补偿抓取（距上次成功: {int(time_since_last_success)}秒）"
                                )
                                should_compensate = True

                    # 断网跳过
                    if current_network_status == NetworkStatus.NO_NETWORK:
                        logger.debug(f"💤 无网络连接，跳过本轮检测")
                        current_wait_interval = min(
                            current_wait_interval * 2, self.MAX_BACKOFF_INTERVAL
                        )
                        last_run_time = current_time
                        continue

                    # 网络正常，执行任务
                    network_desc = get_network_description(current_network_status)
                    reason = "补偿抓取" if should_compensate else "定时检测"
                    print(f"⏳ [Debug] 触发{reason}... (网络: {network_desc})")
                    logger.info(f"🔍 触发{reason}... (网络: {network_desc})")

                    try:
                        result = task_callback()
                        print(
                            f"✅ [Debug] 抓取执行完毕！状态: {result.get('status')}, 提交数量: {result.get('submitted_count')}"
                        )

                        if result and isinstance(result, dict):
                            if result.get("status") == "success":
                                self._last_successful_run = time.time()
                                # 🌟 恢复为当前动态获取的间隔
                                current_wait_interval = current_interval

                                new_count = result.get("new_count", 0)
                                submitted_count = result.get("submitted_count", 0)
                                if new_count > 0 and on_new_articles:
                                    on_new_articles(new_count, result)
                                elif submitted_count > 0:
                                    logger.info(
                                        f"📊 已提交 {submitted_count} 篇文章到处理队列"
                                    )
                            elif result.get("status") == "read_only":
                                logger.debug("💤 后台轮询被静默拦截：当前处于只读模式")
                            elif result.get("status") == "cooldown":
                                logger.debug(f"⏳ 触发被拦截：{result.get('message')}")
                            else:
                                logger.debug(
                                    f"⚠️ 任务返回异常状态: {result.get('status', 'unknown')}"
                                )

                    except Exception as e:
                        print(
                            f"❌ [Debug] 守护线程发生致命错误 (已被拦截，线程继续存活): {e}"
                        )
                        import traceback

                        traceback.print_exc()

                    finally:
                        # 🌟 收尾更新
                        last_run_time = time.time()
                        # 🌟 持久化抓取时间
                        self._save_last_fetch_time()
                        print(f"💤 [Debug] 本轮结束，等待下一次触发...")

                        # 如果当前在抖动区间，标记为已运行
                        if 8.0 <= hour_float < 8.5:
                            morning_ran = True
                            logger.info("🌅 早间抖动任务已完成")
                        if 19.5 <= hour_float < 20.0:
                            evening_ran = True
                            logger.info("🌆 晚间抖动任务已完成")

                # 🌟 基础心跳：每 60 秒检查一次
                if self._stop_event.wait(60):
                    return

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

    def get_cooldown_remaining(self, cooldown_seconds: int = None) -> int:
        """
        获取冷却剩余时间（秒）

        Args:
            cooldown_seconds: 冷却总时长，默认使用 MANUAL_UPDATE_COOLDOWN

        Returns:
            剩余冷却秒数，若已过期则返回 0
        """
        if cooldown_seconds is None:
            cooldown_seconds = self.MANUAL_UPDATE_COOLDOWN

        last_fetch = self._get_last_fetch_time()
        if last_fetch <= 0:
            return 0

        elapsed = time.time() - last_fetch
        remaining = int(cooldown_seconds - elapsed)
        return max(0, remaining)
