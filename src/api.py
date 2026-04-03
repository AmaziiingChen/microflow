# pyright: reportOptionalMemberAccess=false
# pyright: reportArgumentType=false
"""
V2 版本后端调度引擎 - 多源数据订阅架构

核心改动：
1. 废弃原有 TongWenScraper，引入 GwtSpider 和 NmneSpider
2. 维护爬虫实例列表，遍历抓取所有数据源
3. 支持 source_name 字段，实现多来源聚合
"""

import sys
import logging
import re

# 🛡️ 安全模块：SSL 原生信任库注入 (必须在 requests 导入前执行)
try:
    if sys.version_info >= (3, 10):
        import truststore

        truststore.inject_into_ssl()
        logging.info("🛡️ 已成功注入系统原生 SSL 信任库 (truststore)")
except ImportError:
    logging.warning("⚠️ 未安装 truststore，打包后可能无法验证内网 SSL 证书")
except Exception as e:
    logging.error(f"⚠️ 注入 truststore 失败: {e}")

import webview
import os
import json
import shutil
import requests
import tempfile
import platform
import subprocess
import base64
import colorsys
import hashlib
import threading
import queue
import io
from typing import Dict, Any, Optional, List, Callable
import time
from bs4 import BeautifulSoup
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from src.database import db
from src.llm_service import LLMService
from src.utils.text_cleaner import strip_emoji
from src.utils.ai_markdown import (
    build_tag_items,
    compose_tagged_markdown,
    extract_leading_tags,
)
from src.utils.content_text import resolve_effective_article_text
from src.utils.rss_preview import analyze_rss_preview_content
from src.utils.rule_ai_config import normalize_rule_ai_config
from src.utils.rss_strategy import (
    attach_rss_strategy_metadata,
    get_rss_strategy_catalog,
    resolve_rss_rule_strategy,
)
from src.utils.browser_render import normalize_fetch_strategy
from src.utils.html_rule_strategy import normalize_detail_strategy
from src.utils.http_rule_config import (
    ensure_body_content_type,
    normalize_cookie_string,
    normalize_request_body,
    normalize_request_headers,
    normalize_request_method,
)
from src.services import SystemService, DownloadService, ConfigService, TelemetryService
from src.services.rule_generator import RuleGeneratorService
from src.services.custom_spider_rules_manager import get_rules_manager
from src.core import DaemonManager, SpiderScheduler, ArticleProcessor
from src.core.scheduler import SPIDER_REGISTRY
from src.core.network_utils import check_network_status, NetworkStatus
from src.core.paths import CONFIG_PATH, ensure_data_dir_exists
from src.version import __version__

logger = logging.getLogger(__name__)


ensure_data_dir_exists()


class Api:
    """V2 多源调度引擎 - 门面层"""

    # ============================================================
    # 🌟 云端配置 URL 基础路径（不含文件名）
    # ============================================================
    _VERSION_BASE_URL = "https://microflow-1412347033.cos.ap-guangzhou.myqcloud.com"
    _SYSTEM_CONTENT_RULE_PREFIX = "system:"
    _SYSTEM_CONTENT_SOURCE_NAME = "系统内容"
    _SYSTEM_CONTENT_DEFAULT_FEEDBACK_EMAIL = "amaziiingchen@qq.com"
    _MIN_EFFECTIVE_UPDATE_COOLDOWN_SECONDS = 60
    _SYSTEM_CONTENT_ORDER = (
        "announcement",
        "changelog",
        "feedback",
        "disclaimer",
    )

    def _get_version_url(self) -> str:
        """根据发布渠道动态构建版本文件 URL"""
        channel = self._config_service.get("channel", "stable")
        if channel == "stable":
            return f"{self._VERSION_BASE_URL}/version.json"
        else:
            # 对 channel 名称进行基本过滤，防止路径注入
            safe_channel = re.sub(r"[^a-zA-Z0-9_-]", "", channel)
            return f"{self._VERSION_BASE_URL}/version_{safe_channel}.json"

    @property
    def config_service(self):
        """兼容旧调用入口，统一返回当前配置服务实例。"""
        return self._config_service

    @property
    def telemetry_service(self):
        """暴露匿名遥测服务，供主程序与前端复用。"""
        return self._telemetry

    def _refresh_telemetry_remote_config(self, version_data: Optional[Dict[str, Any]]) -> None:
        telemetry_config = {}
        if isinstance(version_data, dict):
            telemetry_config = (
                version_data.get("telemetry")
                if isinstance(version_data.get("telemetry"), dict)
                else {}
            )
        try:
            self._telemetry.update_remote_config(telemetry_config)
        except Exception as e:
            logger.debug(f"同步遥测远程配置失败: {e}")

    def _track_telemetry(
        self,
        event_name: str,
        props: Optional[Dict[str, Any]] = None,
        *,
        force: bool = False,
    ) -> None:
        try:
            self._telemetry.track(event_name, props, force=force)
        except Exception as e:
            logger.debug(f"记录遥测事件失败 ({event_name}): {e}")

    def _on_source_fetch_result(self, payload: Dict[str, Any]) -> None:
        self._track_telemetry("source_fetch_result", payload)

    def _get_effective_update_cooldown_seconds(self) -> int:
        """后端强制冷却下限，避免配置文件中的值过低。"""
        raw_value = self._config_service.get(
            "updateCooldown",
            self._MIN_EFFECTIVE_UPDATE_COOLDOWN_SECONDS,
        )
        try:
            normalized = int(raw_value)
        except (TypeError, ValueError):
            normalized = self._MIN_EFFECTIVE_UPDATE_COOLDOWN_SECONDS
        return max(normalized, self._MIN_EFFECTIVE_UPDATE_COOLDOWN_SECONDS)

    def __init__(self):
        # 当前软件版本号（从 version.py 统一导入）
        self.CURRENT_VERSION = __version__

        # 🌟 云端版本信息缓存（启动时由 perform_startup_check 填充）
        self._version_info: Dict[str, Any] = {}
        # 🌟 用于 304 缓存协商的 ETag
        self._version_etag: Optional[str] = None

        # 🌟 线程安全的 JS 执行队列（确保 evaluate_js 在主线程调用）
        self._js_queue: queue.Queue = queue.Queue(maxsize=500)
        self._js_thread_running = True
        self._js_thread = threading.Thread(
            target=self._process_js_queue, daemon=True, name="JSExecutor"
        )
        self._js_thread.start()
        logger.info("📱 JS 执行线程已启动")

        # 大模型服务
        self._llm = LLMService()

        # 🌟 服务层：系统交互、文件下载、配置管理
        self._system_service = SystemService()
        self._download_service = DownloadService()
        self._config_service = ConfigService(str(CONFIG_PATH), self._llm.system_prompt)
        self._telemetry = TelemetryService(
            self._config_service,
            db,
            self.CURRENT_VERSION,
        )

        # 🌟 核心组件：文章处理器（传入回调函数用于唤醒窗口）
        self._article_processor = ArticleProcessor(
            self._llm,
            db,
            on_task_complete=self._on_task_complete,  # 🌟 新增：任务完成回调
            on_article_processed=self._on_article_processed,
            on_progress=self._push_ai_progress,  # 🌟 新增：AI 进度回调
            config_service=self._config_service,  # 📧 邮件推送配置服务
        )
        self._scheduler = SpiderScheduler(
            article_processor=self._article_processor,
            progress_callback=self._push_progress,
            config_service=self._config_service,
            source_result_callback=self._on_source_fetch_result,
        )

        # 🌟 核心组件：守护进程管理器
        self._daemon_manager = DaemonManager()
        # 🌟 设置冷却时间获取器（从配置服务动态读取）
        self._daemon_manager.set_cooldown_getter(
            self._get_effective_update_cooldown_seconds
        )

        # 🌟 核心组件：动态爬虫规则生成器和规则管理器
        self._rule_generator = RuleGeneratorService(self._config_service)
        self._rules_manager = get_rules_manager()

        # 🌟 性能监控服务
        self._performance_monitor = None
        try:
            from src.services.performance_monitor import get_performance_monitor

            self._performance_monitor = get_performance_monitor()
            self._performance_monitor.start()
        except Exception as e:
            logger.warning(f"性能监控服务不可用，已跳过初始化: {e}")

        # 线程控制
        self.is_running = True
        self._window: Optional[webview.Window] = None
        self._summary_lock = threading.Lock()
        self._active_summary_tokens: Dict[int, int] = {}
        self._active_summary_events: Dict[int, threading.Event] = {}
        self._rss_preview_lock = threading.Lock()
        self._active_rss_preview_events: Dict[int, threading.Event] = {}
        self._ai_prereq_cache_lock = threading.Lock()
        self._ai_prereq_refresh_lock = threading.Lock()
        self._ai_prereq_cache: Dict[str, Any] = {
            "signature": "",
            "result": None,
            "checked_at": 0.0,
        }
        self._ai_prereq_success_ttl = 300
        self._ai_prereq_failure_ttl = 15

        # 加载配置并应用
        self._apply_config()
        threading.Thread(
            target=self._warm_ai_prereq_cache,
            daemon=True,
            name="AIPrereqWarmup",
        ).start()

    def _get_active_dynamic_sources(self) -> list:
        """获取所有启用的动态爬虫任务名称"""
        try:
            rules = self._rules_manager.load_custom_rules()
            sources = [
                rule.get("task_name") for rule in rules if rule.get("enabled", True)
            ]
            logger.debug(
                "_get_active_dynamic_sources: rules=%s active_sources=%s",
                len(rules),
                sources,
            )
            return sources
        except Exception as e:
            logger.error(f"获取动态来源失败: {e}", exc_info=True)
            return []

    def _get_effective_sources(self) -> Optional[List[str]]:
        """
        统一获取有效来源列表（数据订阅管理 + 自定义数据源）

        逻辑：
        1. 如果用户订阅了来源，返回：订阅来源 + 启用的动态来源
        2. 如果用户没有订阅任何来源，返回：启用的动态来源
        3. 如果两者都为空，返回空列表 []（表示不显示任何内容）

        Returns:
            有效来源列表，或空列表（表示不显示任何内容）
        """
        try:
            subscribed = self._config_service.get("subscribedSources", [])
            logger.debug(
                "_get_effective_sources: subscribed=%s type=%s",
                subscribed,
                type(subscribed),
            )
        except Exception as e:
            logger.debug("_get_effective_sources: read subscribed failed: %s", e)
            subscribed = []

        try:
            dynamic = self._get_active_dynamic_sources()
            logger.debug("_get_effective_sources: dynamic=%s", dynamic)
        except Exception as e:
            logger.debug("_get_effective_sources: load dynamic failed: %s", e)
            dynamic = []

        # 合并两个列表，去重
        effective = []
        for s in subscribed:
            if s not in effective:
                effective.append(s)
        for d in dynamic:
            if d not in effective:
                effective.append(d)

        logger.debug("_get_effective_sources: effective=%s", effective)
        if not effective and self._is_config_effectively_blank():
            logger.warning("配置看起来是空白/损坏状态，临时放开来源过滤以避免首屏空白")
            return None

        return effective  # 🌟 直接返回列表，空列表表示"不显示任何内容"

    def _is_config_effectively_blank(self) -> bool:
        """判断当前配置是否像未初始化或损坏的空白配置。"""
        try:
            critical_values = [
                self._config_service.get("baseUrl", ""),
                self._config_service.get("modelName", ""),
                self._config_service.get("prompt", ""),
                self._config_service.get("apiKey", ""),
                self._config_service.get("subscribedSources", []),
                self._config_service.get("secondaryModels", []),
            ]
            return not any(critical_values)
        except Exception:
            return False

    # ==================== 🌟 线程安全的 JS 执行队列 ====================

    def _process_js_queue(self) -> None:
        """
        后台线程：定期处理 JS 执行队列

        确保 evaluate_js 始终在"伪主线程"中调用，
        避免多线程直接调用 GUI API 导致崩溃。
        """
        while self._js_thread_running:
            try:
                # 🌟 修复CPU 100%问题：阻塞等待1秒，避免忙等待
                task = self._js_queue.get(timeout=1.0)
                if task is None:
                    continue

                js_code, callback = task

                # 在主上下文中执行 JS
                if webview.windows:
                    try:
                        result = webview.windows[0].evaluate_js(js_code)
                        if callback:
                            callback(result)
                    except Exception as e:
                        logger.debug(f"JS 执行失败: {e}")
                        if callback:
                            callback(None)

            except queue.Empty:
                pass  # 超时，继续循环检查停止信号
            except Exception as e:
                logger.warning(f"JS 队列处理异常: {e}")

    def _enqueue_js(
        self, js_code: str, callback: Optional[Callable[[Any], None]] = None
    ) -> None:
        """
        将 JS 代码放入执行队列（线程安全）

        Args:
            js_code: 要执行的 JavaScript 代码
            callback: 执行完成后的回调函数（可选）
        """
        try:
            self._js_queue.put((js_code, callback), block=False)
        except queue.Full:
            logger.warning("JS 执行队列已满，丢弃任务")

    def _stop_js_thread(self) -> None:
        """停止 JS 执行线程（应用退出时调用）"""
        self._js_thread_running = False
        self._js_thread.join(timeout=2)
        logger.info("JS 执行线程已停止")

    # ==================== 进度推送方法 ====================

    def _push_progress(self, completed: int, total: int, current_title: str = ""):
        """
        推送进度更新到前端

        Args:
            completed: 已完成数量
            total: 总数量
            current_title: 当前处理的文章标题
        """
        try:
            # 转义单引号，防止 JS 语法错误
            safe_title = (
                current_title.replace("'", "\\'").replace('"', '\\"')
                if current_title
                else ""
            )
            js_code = f"if(window.updatePyProgress) window.updatePyProgress({completed}, {total}, '{safe_title}');"
            # 🌟 使用队列执行（线程安全）
            self._enqueue_js(js_code)
        except Exception as e:
            logger.debug(f"进度推送失败: {e}")

    def _push_spider_progress(self, current: int, total: int, source_name: str):
        """
        推送爬虫进度到前端

        Args:
            current: 当前处理的爬虫索引（从 0 开始）
            total: 总爬虫数量
            source_name: 当前爬虫名称
        """
        try:
            # 🌟 修复：使用 webview.windows[0] 而不是 self.window，与 _push_progress 保持一致
            if not webview.windows:
                logger.debug("爬虫进度推送失败: 窗口列表为空")
                return
            # 转义单引号
            safe_name = source_name.replace("'", "\\'").replace('"', '\\"')
            js_code = f"if(window.updateSpiderProgress) window.updateSpiderProgress({current}, {total}, '{safe_name}');"
            # 🌟 使用队列执行（线程安全）
            self._enqueue_js(js_code)
            logger.debug(f"爬虫进度推送: {current}/{total} - {source_name}")
        except Exception as e:
            logger.warning(f"爬虫进度推送失败: {e}")

    def _push_ai_progress(self, completed: int, total: int, current_title: str):
        """
        推送 AI 总结进度到前端

        Args:
            completed: 已完成处理的文章数
            total: 总文章数
            current_title: 当前处理完成的文章标题
        """
        try:
            # 🌟 修复：使用 webview.windows[0] 而不是 self.window
            if not webview.windows:
                return
            # 转义单引号
            safe_title = (
                current_title.replace("'", "\\'").replace('"', '\\"')
                if current_title
                else ""
            )
            js_code = f"if(window.updatePyProgress) window.updatePyProgress({completed}, {total}, '{safe_title}');"
            # 🌟 使用队列执行（线程安全）
            self._enqueue_js(js_code)
            logger.debug(
                f"AI 进度推送: {completed}/{total} - {current_title[:20] if current_title else ''}"
            )
        except Exception as e:
            logger.warning(f"AI 进度推送失败: {e}")

    def _notify_fetch_failed(self, message: str, title: str = "检查失败") -> None:
        """通知前端结束当前更新会话，并以失败态收口 toast。"""
        try:
            if not webview.windows:
                logger.debug("更新失败通知跳过: 窗口列表为空")
                return
            safe_title = json.dumps(str(title or "检查失败"), ensure_ascii=False)
            safe_message = json.dumps(
                str(message or "更新未能完成"), ensure_ascii=False
            )
            js_code = f"if(window.onFetchFailed) window.onFetchFailed({safe_message}, {safe_title});"
            self._enqueue_js(js_code)
        except Exception as e:
            logger.debug(f"通知前端更新失败收口失败: {e}")

    def _notify_ai_task_failed(self, payload: Optional[Dict[str, Any]]) -> None:
        """通知前端单篇 AI 处理失败，显示可见 toast。"""
        try:
            if not webview.windows:
                logger.debug("AI 失败通知跳过: 窗口列表为空")
                return
            safe_payload = json.dumps(payload or {}, ensure_ascii=False)
            js_code = (
                "if(window.onAiTaskFailed) " f"window.onAiTaskFailed({safe_payload});"
            )
            self._enqueue_js(js_code)
        except Exception as e:
            logger.debug(f"通知前端 AI 失败失败: {e}")

    def _notify_spider_complete(
        self, has_new_articles: bool, submitted_count: int
    ) -> None:
        """通知前端爬虫阶段已经结束。"""
        try:
            if not webview.windows:
                logger.debug("爬虫完成通知跳过: 窗口列表为空")
                return
            safe_has_new = "true" if has_new_articles else "false"
            safe_submitted_count = max(int(submitted_count or 0), 0)
            js_code = (
                "if(window.onSpiderComplete) "
                f"window.onSpiderComplete({safe_has_new}, {safe_submitted_count});"
            )
            self._enqueue_js(js_code)
        except Exception as e:
            logger.debug(f"通知前端爬虫完成失败: {e}")

    def _on_task_complete(
        self, success: bool, reason: str, article_data: Optional[Dict[str, Any]]
    ):
        """
        单个任务完成后的回调（无论成功失败）：检查是否所有任务都已完成

        Args:
            success: 是否成功入库
            reason: 结果原因（new, updated, unchanged, detail_failed 等）
            article_data: 文章数据（如果成功入库）
        """
        try:
            # 获取最新统计
            stats = self._article_processor.get_stats()
            processed = stats.get("processed", 0)
            submitted = stats.get("submitted", 0)
            ai_total = stats.get("ai_total", 0)
            ai_completed = stats.get("ai_completed", 0)

            logger.info(
                f"任务完成回调: success={success}, reason={reason}, processed={processed}/{submitted}, ai={ai_completed}/{ai_total}"
            )

            if reason in {
                "ai_failed",
                "ai_error",
                "ai_failed_fallback",
                "ai_error_fallback",
            }:
                self._notify_ai_task_failed(article_data)

            # 如果所有任务都处理完了
            if submitted > 0 and processed >= submitted:
                # 如果没有 AI 任务，或者所有 AI 任务都完成了
                if ai_total == 0 or ai_completed >= ai_total:
                    logger.info(
                        f"所有任务处理完成，准备关闭加载状态: processed={processed}/{submitted}, ai_total={ai_total}"
                    )
                    # 通知前端关闭加载状态
                    js_code = """
                        if (window.updatePyProgress) {
                            window.updatePyProgress(0, 0, '');
                            'success';
                        } else {
                            'no_updatePyProgress';
                        }
                    """
                    # 🌟 使用队列执行（线程安全）
                    self._enqueue_js(js_code)
                else:
                    logger.warning("⚠️ webview.windows 为空，无法通知前端")
        except Exception as e:
            logger.error(f"任务完成回调执行失败: {e}")

    def _on_article_processed(self, article_data: dict):
        """
        单篇文章处理完成后的回调：根据静音模式决定唤醒窗口或显示托盘红点

        Args:
            article_data: 完整的文章数据字典
        """
        if not self._window:
            return

        try:
            # 检查静音模式
            mute_mode = self._config_service.get("muteMode", False)
            json_data = json.dumps(article_data, ensure_ascii=False)

            if mute_mode:
                # 静音模式：仅显示托盘红点，静默更新前端数据
                try:
                    import main

                    main.set_tray_alert()
                except Exception as e:
                    logger.warning(f"设置托盘红点失败: {e}")

                # 静默更新前端数据（不弹出详情）
                js_code = f"if(window.silentUpdateArticle) window.silentUpdateArticle({json_data});"
                # 🌟 使用队列执行（线程安全）
                self._enqueue_js(js_code)
                logger.info(
                    f"🔔 静音模式：已显示托盘红点 - {article_data.get('title', '未知标题')}"
                )
            else:
                # 强提醒模式：唤醒窗口并弹出详情
                self._window.restore()
                self._window.show()
                js_code = f"if(window.openArticleDetail) window.openArticleDetail({json_data});"
                # 🌟 使用队列执行（线程安全）
                self._enqueue_js(js_code)
                logger.info(
                    f"🔔 已唤醒窗口并推送文章: {article_data.get('title', '未知标题')}"
                )

        except Exception as e:
            logger.warning(f"文章处理回调执行失败: {e}")

    def check_updates(
        self, is_manual: bool = False, skip_ai_precheck: bool = False
    ) -> Dict[str, Any]:
        """
        触发爬虫检查更新（异步提交到处理队列）

        Args:
            is_manual: 是否为用户手动触发

        Returns:
            {"status": "success/error", "submitted_count": int, "queue_size": int, "data": list, "cooldown_remaining": int}
        """
        # 🌟 拦截只读模式前，先尝试向云端做一次恢复探测
        if self._config_service.current.is_locked:
            logger.info("当前处于只读模式，先尝试向云端探测是否已恢复可用")
            try:
                latest_version_data = self.get_version_info(force_refresh=True)
                if latest_version_data.get("status") == "success":
                    if latest_version_data.get("is_active") is False:
                        kill_reason = latest_version_data.get(
                            "kill_message", "该软件已被禁用"
                        )
                        self._execute_self_destruct(kill_reason)
                        msg = f"服务已暂停: {kill_reason}"
                        logger.warning(f"拦截爬虫请求：{msg}")
                        return {
                            "status": "read_only",
                            "message": msg,
                            "submitted_count": 0,
                            "queue_size": 0,
                            "cooldown_remaining": 0,
                        }

                    # 云端已恢复，执行解锁
                    self._unlock_if_needed()
                    logger.info("云端已恢复可用，已从只读模式恢复")
                else:
                    msg = "服务已暂停，当前为只读模式，仅可查看和利用 AI 分析历史公文"
                    logger.warning(f"只读恢复探测失败，继续拦截：{msg}")
                    return {
                        "status": "read_only",
                        "message": msg,
                        "submitted_count": 0,
                        "queue_size": 0,
                        "cooldown_remaining": 0,
                    }
            except Exception as e:
                msg = "服务已暂停，当前为只读模式，仅可查看和利用 AI 分析历史公文"
                logger.warning(f"只读恢复探测异常 ({e})，继续拦截：{msg}")
                return {
                    "status": "read_only",
                    "message": msg,
                    "submitted_count": 0,
                    "queue_size": 0,
                    "cooldown_remaining": 0,
                }

        # 🌟 二次确认：恢复探测后仍锁定，则继续只读拦截
        if self._config_service.current.is_locked:
            msg = "服务已暂停，当前为只读模式，仅可查看和利用 AI 分析历史公文"
            logger.warning(f"拦截爬虫请求：{msg}")
            return {
                "status": "read_only",
                "message": msg,
                "submitted_count": 0,
                "queue_size": 0,
                "cooldown_remaining": 0,
            }

        # 🌟 核心门禁：AI 前置条件未满足时，后续爬虫全部拦截
        if not skip_ai_precheck:
            ai_gate = self.validate_ai_prerequisites()
            if ai_gate.get("status") != "success":
                logger.warning(
                    "检测到 AI 前置条件不满足，拦截更新请求: %s",
                    ai_gate.get("message", "unknown"),
                )
                if ai_gate.get("stage") == "balance_error":
                    try:
                        js_code = """
                            if (window.updateApiBalanceStatus) {
                                window.updateApiBalanceStatus(false);
                            }
                        """
                        self._enqueue_js(js_code)
                    except Exception as e:
                        logger.debug(f"通知前端显示欠费卡片失败: {e}")
                return {
                    "status": "api_precheck_error",
                    "stage": ai_gate.get("stage", "validation_failed"),
                    "message": ai_gate.get("message", "API 前置检查失败"),
                    "submitted_count": 0,
                    "queue_size": 0,
                    "cooldown_remaining": 0,
                }

        # 🌟 安全心跳：每次触发爬虫前，强制校验云端最新配置（触发 304 极速校验）
        try:
            latest_version_data = self.get_version_info(force_refresh=True)
            if latest_version_data.get("status") == "success":
                # 1. 动态停服检测
                if latest_version_data.get("is_active") is False:
                    kill_reason = latest_version_data.get(
                        "kill_message", "该软件已被禁用"
                    )
                    logger.warning(
                        f"安全心跳检测到停服指令，立即熔断！原因: {kill_reason}"
                    )
                    self._execute_self_destruct(kill_reason)
                    return {
                        "status": "read_only",
                        "message": f"服务已暂停: {kill_reason}",
                        "submitted_count": 0,
                        "queue_size": 0,
                        "cooldown_remaining": 0,
                    }

                # 2. 动态更新检测（静默通知前端）
                latest_ver = latest_version_data.get("version", "")
                if latest_ver and latest_ver > self.CURRENT_VERSION:
                    logger.info(f"安全心跳检测到新版本: {latest_ver}")
                    js_code = f"if(window.onNewVersionAvailable) window.onNewVersionAvailable('{latest_ver}');"
                    # 🌟 使用队列执行（线程安全）
                    self._enqueue_js(js_code)
        except Exception as e:
            logger.debug(f"安全心跳检测失败（网络波动，允许继续执行）: {e}")

        # 🌟 终极防御：如果前端的锁失效了，后端在这里强行拦截
        if is_manual:
            last_fetch = self._daemon_manager._get_last_fetch_time()
            cooldown_seconds = self._get_effective_update_cooldown_seconds()
            remaining = cooldown_seconds - (time.time() - last_fetch)
            if remaining > 0:
                logger.warning(f"拦截高频手动刷新，剩余冷却 {remaining:.1f} 秒")
                return {
                    "status": "cooldown",
                    "remaining": int(remaining),
                    "message": f"刷新太快啦，请等待 {int(remaining)} 秒",
                    "cooldown_remaining": int(remaining),
                }

        # 🌟 无论是否手动，都先记录时间戳，防止守护线程重复执行
        self._daemon_manager.record_manual_update()

        mode = self._config_service.get("trackMode", "continuous")

        # 获取用户订阅的来源列表
        subscribed_sources = self._config_service.get("subscribedSources", None)

        if subscribed_sources is not None:
            subscribed_sources = list(subscribed_sources)  # 复制副本防止污染配置
            subscribed_sources.extend(self._get_active_dynamic_sources())

        # 🌟 核心：在执行爬虫之前，立即通知前端开始加载
        # 无论是手动还是自动，都要通知前端显示进度条和冷却状态
        try:
            # 如果是后台自动执行，合并通知（同时显示进度条和提示消息）
            if not is_manual:
                js_code = """
                    if (window.onStartFetching) {
                        window.onStartFetching();
                    }
                    if (window.showAutoFetchNotice) {
                        window.showAutoFetchNotice();
                    }
                """
                # 🌟 使用队列执行（线程安全）
                self._enqueue_js(js_code)
            else:
                # 手动触发只显示进度条
                js_code = """
                    if (window.onStartFetching) {
                        window.onStartFetching();
                    }
                """
                # 🌟 使用队列执行（线程安全）
                self._enqueue_js(js_code)
            # 🌟 关键修复：给浏览器一个处理事件循环的机会
            # 确保通知在爬虫执行之前被渲染到 UI
            time.sleep(0.05)
        except Exception as e:
            logger.debug(f"通知前端开始执行失败: {e}")

        # 委托给调度器执行（异步提交）
        result = self._scheduler.run_all_spiders(
            mode=mode,
            is_manual=is_manual,
            wait_for_completion=False,  # 不等待处理完成，立即返回
            enabled_sources=subscribed_sources,
            spider_progress_callback=self._push_spider_progress,  # 🌟 新增
        )

        # 如果调度器返回错误，直接返回（带冷却时间）
        if result.get("status") == "error" and result.get("message"):
            logger.warning(
                "检查更新异常结束: is_manual=%s, message=%s",
                is_manual,
                result.get("message"),
            )
            if not is_manual:
                self._notify_fetch_failed(result.get("message") or "后台检查未能完成")
            return {
                **result,
                "cooldown_remaining": self._daemon_manager.get_cooldown_remaining(),
            }

        # 🌟 爬虫阶段完成后，通知前端切换状态
        # 注意：只有当没有新文章时才在这里调用 onSpiderComplete
        # 有新文章时，由 _on_task_complete 统一处理完成通知
        submitted_count = result.get("submitted_count", 0)
        try:
            if not is_manual:
                self._notify_spider_complete(submitted_count > 0, submitted_count)
                logger.debug(
                    "爬虫阶段完成，已通知前端: submitted_count=%s",
                    submitted_count,
                )
            elif submitted_count == 0:
                # 手动触发且无新文章，直接关闭加载状态
                self._notify_spider_complete(False, 0)
                logger.debug("爬虫完成，已通知前端关闭加载状态（无新文章）")
        except Exception as e:
            logger.debug(f"通知前端爬虫完成失败: {e}")

        # 🌟 手动更新成功后，记录时间戳（与守护进程共享冷却状态）
        if is_manual and result.get("status") == "success":
            self._daemon_manager.record_manual_update()

        # 获取最新数据（🌟 修复：必须加上全局白名单过滤）
        filter_sources = self._get_effective_sources()
        if filter_sources == []:
            all_articles = []  # 如果全都取消订阅了，就返回空
        else:
            all_articles = self._mark_article_list_records(
                db.get_articles_paged(
                    limit=20,
                    offset=0,
                    source_names=filter_sources,
                    include_content=False,
                )
            )

        # 🌟 获取冷却剩余时间
        cooldown_remaining = self._daemon_manager.get_cooldown_remaining()

        # 🌟 更新托盘同步时间
        try:
            import main
            from datetime import datetime

            sync_time = datetime.now().strftime("%H:%M")
            main.update_tray_status(sync_time=sync_time)
        except Exception as e:
            logger.debug(f"更新托盘同步时间失败: {e}")

        return {
            "status": "success",
            "submitted_count": result.get("submitted_count", 0),
            "queue_size": result.get("queue_size", 0),
            "data": all_articles,
            "warnings": result.get("warnings"),
            "cooldown_remaining": cooldown_remaining,
        }

    def get_update_cooldown(self) -> Dict[str, Any]:
        """获取真实的剩余更新冷却时间（秒）"""
        remaining = self._daemon_manager.get_cooldown_remaining()

        if remaining > 0:
            return {"status": "cooling", "remaining": remaining}
        return {"status": "ready", "remaining": 0}

    def get_network_access_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """获取校园网访问状态，支持强制刷新。"""
        snapshot = self._daemon_manager.get_network_status_snapshot(
            force_refresh=force_refresh
        )
        network_status = snapshot.get("network_status")

        if network_status is None:
            return {
                "status": "error",
                "message": "校园网状态检测失败",
                **snapshot,
            }

        access_ok = network_status == NetworkStatus.PUBLIC_AND_INTRANET.value
        return {
            "status": "success",
            "network_status": network_status,
            "description": snapshot.get("description", ""),
            "checked_at": snapshot.get("checked_at", 0.0),
            "force_refreshed": snapshot.get("force_refreshed", False),
            "access_ok": access_ok,
        }

    def get_processing_stats(self) -> Dict[str, Any]:
        """获取后台处理任务的统计信息"""
        stats = self._scheduler.get_processor_stats()
        return {"status": "success", "data": stats}

    # ===== 以下是保留的兼容方法 =====

    def get_history(self) -> Dict[str, Any]:
        """前端初始化：读取第一页数据"""
        try:
            articles = self._mark_article_list_records(
                db.get_articles_paged(
                    limit=20,
                    offset=0,
                    include_content=False,
                )
            )
            return {"status": "success", "data": articles}
        except Exception as e:
            logger.error(f"读取历史记录失败: {e}")
            return {"status": "error", "message": "读取本地数据库失败"}

    def get_history_paged(self, page: int = 1, page_size: int = 20, source_name: str = None, source_names: list = None, favorites_only: bool = False) -> Dict[str, Any]:  # type: ignore
        """分页获取历史记录，支持按来源筛选、按收藏筛选

        Args:
            page: 页码
            page_size: 每页数量
            source_name: 单个来源筛选（仅支持指定单个来源，不支持数组）
            source_names: 【已废弃】不再信任前端传来的数组
            favorites_only: 是否只返回收藏的文章
        """
        try:
            offset = (page - 1) * page_size

            # 🌟 统一大门卫：抛弃前端传来的容易出错的数组，完全由后端定夺
            if source_name and source_name != "全部":
                filter_sources = [source_name]
            else:
                filter_sources = self._get_effective_sources()

            logger.debug(
                f"[DEBUG] get_history_paged - source_name={source_name}, filter_sources={filter_sources}"
            )

            # 🌟 致命防御：如果什么都没订阅，直接阻断
            if filter_sources == [] and not source_name:
                return {"status": "success", "data": []}

            articles = self._mark_article_list_records(
                db.get_articles_paged(
                    limit=page_size,
                    offset=offset,
                    source_name=None,
                    source_names=filter_sources,
                    favorites_only=favorites_only,
                    include_content=False,
                )
            )

            # 🌟 详细调试：显示返回文章的来源分布
            from collections import Counter

            source_counts = Counter(a.get("source_name") for a in articles)
            logger.debug(
                f"[DEBUG] get_history_paged - 返回文章来源分布: {dict(source_counts)}"
            )

            return {"status": "success", "data": articles}
        except Exception as e:
            logger.error(f"分页读取失败: {e}")
            return {"status": "error", "message": "读取本地数据库失败"}

    def get_article_detail(self, article_id: int) -> Dict[str, Any]:
        """按需加载文章完整详情，供列表轻载模式进入详情页时补全内容。"""
        try:
            article = db.get_article_by_id(int(article_id))
            if not article:
                return {"status": "error", "message": "文章不存在"}
            return {
                "status": "success",
                "data": self._build_article_detail_response(article),
            }
        except Exception as e:
            logger.error(f"读取文章详情失败: {e}")
            return {"status": "error", "message": "读取文章详情失败"}

    def _extract_article_ai_tags(
        self, article: Optional[Dict[str, Any]], fallback_text: str = ""
    ) -> List[str]:
        """统一解析文章标签，兼容 list / JSON string / 旧 summary 前缀。"""
        raw_value = (article or {}).get("ai_tags")
        if isinstance(raw_value, list):
            return [
                str(tag or "").replace("【", "").replace("】", "").strip()
                for tag in raw_value
                if str(tag or "").strip()
            ][:3]

        if isinstance(raw_value, str) and raw_value.strip():
            try:
                parsed_tags = json.loads(raw_value)
                if isinstance(parsed_tags, list):
                    return [
                        str(tag or "").replace("【", "").replace("】", "").strip()
                        for tag in parsed_tags
                        if str(tag or "").strip()
                    ][:3]
            except Exception:
                parsed_tags, _ = extract_leading_tags(raw_value)
                if parsed_tags:
                    return parsed_tags[:3]

        parsed_tags, _ = extract_leading_tags(str(fallback_text or "").strip())
        return parsed_tags[:3]

    def _build_article_content_payload(
        self,
        article: Dict[str, Any],
        ai_config: Dict[str, Any],
        **overrides: Any,
    ) -> Dict[str, Any]:
        """统一构建文章编辑/重生成后的返回结构。"""
        merged: Dict[str, Any] = {**(article or {})}
        for key, value in overrides.items():
            if value is not None:
                merged[key] = value

        source_type = (
            str(merged.get("source_type") or self._resolve_article_source_type(article))
            .strip()
            .lower()
            or "html"
        )
        raw_text = str(merged.get("raw_text") or "").strip()
        raw_markdown = str(merged.get("raw_markdown") or raw_text).strip()
        enhanced_markdown = str(merged.get("enhanced_markdown") or "").strip()
        if source_type == "rss":
            enhanced_markdown = enhanced_markdown or raw_markdown

        summary_text = str(merged.get("summary") or "").strip()
        ai_summary = str(merged.get("ai_summary") or "").strip()
        ai_tags = self._extract_article_ai_tags(merged, summary_text)

        if not ai_summary:
            summary_tags, summary_body = extract_leading_tags(summary_text)
            ai_summary = str(summary_body or "").strip()
            if not ai_tags:
                ai_tags = summary_tags[:3]

        normalized_summary = (
            compose_tagged_markdown(ai_tags, ai_summary)
            or summary_text
            or (enhanced_markdown if source_type == "rss" else raw_markdown)
        )

        return {
            "summary": normalized_summary,
            "ai_summary": ai_summary,
            "ai_tags": ai_tags,
            "raw_text": raw_text,
            "raw_markdown": raw_markdown,
            "enhanced_markdown": enhanced_markdown,
            "source_type": source_type,
            "enable_ai_formatting": bool(ai_config.get("enable_ai_formatting", False)),
            "enable_ai_summary": bool(ai_config.get("enable_ai_summary", False)),
            "formatting_prompt": str(ai_config.get("formatting_prompt") or "").strip(),
            "summary_prompt": str(ai_config.get("summary_prompt") or "").strip(),
        }

    def _build_article_detail_response(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """统一构建详情页完整文章载荷。"""
        payload = self._build_article_content_payload(
            article,
            self._resolve_article_ai_config(article),
        )
        return {
            **dict(article or {}),
            **payload,
            "has_full_content": True,
        }

    def _mark_article_list_records(
        self, articles: Optional[List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """为轻载列表结果补齐前端可识别的元数据标记。"""
        normalized: List[Dict[str, Any]] = []
        for raw_item in articles or []:
            item = dict(raw_item or {})
            item["has_full_content"] = bool(item.get("has_full_content", False))
            normalized.append(item)
        return normalized

    def _normalize_rule_payload_for_save(
        self,
        rule_dict: Dict[str, Any],
        *,
        expected_source_type: Optional[str] = None,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """保存前统一清洗/校验 HTML 与 RSS 规则。"""
        if not isinstance(rule_dict, dict) or not rule_dict:
            return None, "规则数据不能为空"

        normalized = normalize_rule_ai_config(dict(rule_dict))
        normalized["rule_id"] = str(normalized.get("rule_id") or "").strip()
        normalized["task_id"] = str(normalized.get("task_id") or "").strip()
        normalized["task_name"] = str(normalized.get("task_name") or "").strip()
        normalized["task_purpose"] = str(normalized.get("task_purpose") or "").strip()
        normalized["url"] = str(normalized.get("url") or "").strip()

        source_type = str(normalized.get("source_type") or "html").strip().lower()
        if source_type not in {"html", "rss"}:
            return None, "数据源类型仅支持 html 或 rss"
        if expected_source_type and source_type != expected_source_type:
            return None, f"当前仅支持保存 {expected_source_type.upper()} 规则"
        normalized["source_type"] = source_type

        missing_fields = [
            field_name
            for field_name in ("rule_id", "task_id", "task_name", "url")
            if not normalized.get(field_name)
        ]
        if missing_fields:
            return None, f"缺少必要字段: {', '.join(missing_fields)}"

        parsed_url = urlparse(normalized["url"])
        if not parsed_url.scheme or not parsed_url.netloc:
            return None, "URL 格式不正确，请填写完整地址"

        normalized["enabled"] = bool(normalized.get("enabled", True))
        normalized["require_ai_summary"] = bool(
            normalized.get("require_ai_summary", False)
        )
        normalized["enable_ai_formatting"] = bool(
            normalized.get("enable_ai_formatting", False)
        )
        normalized["enable_ai_summary"] = bool(
            normalized.get("enable_ai_summary", False)
        )
        normalized["custom_summary_prompt"] = str(
            normalized.get("custom_summary_prompt") or ""
        ).strip()
        normalized["formatting_prompt"] = str(
            normalized.get("formatting_prompt") or ""
        ).strip()
        normalized["summary_prompt"] = str(
            normalized.get("summary_prompt") or ""
        ).strip()

        max_items_raw = normalized.get("max_items")
        if str(max_items_raw or "").strip():
            try:
                max_items = int(max_items_raw)
            except (TypeError, ValueError):
                return None, "单次抓取最大条目数必须是正整数"
            if max_items <= 0:
                return None, "单次抓取最大条目数必须大于 0"
            normalized["max_items"] = max_items
        else:
            normalized["max_items"] = None

        if source_type == "rss":
            normalized = attach_rss_strategy_metadata(
                normalized,
                sample_articles=None,
            )
            try:
                from src.models.spider_rule import SpiderRuleOutput

                SpiderRuleOutput(**normalized)
            except Exception as exc:
                return None, f"RSS 规则格式校验失败: {exc}"
            return normalized, ""

        normalized["list_container"] = str(
            normalized.get("list_container") or ""
        ).strip()
        normalized["item_selector"] = str(normalized.get("item_selector") or "").strip()
        if not normalized["list_container"] or not normalized["item_selector"]:
            return None, "HTML 规则缺少列表容器或列表项选择器"

        field_selectors_raw = normalized.get("field_selectors")
        if not isinstance(field_selectors_raw, dict):
            return None, "HTML 规则缺少字段选择器"
        field_selectors = {
            str(key or "").strip(): str(value or "").strip()
            for key, value in field_selectors_raw.items()
            if str(key or "").strip() and str(value or "").strip()
        }
        if not field_selectors:
            return None, "HTML 规则至少需要 1 个有效字段选择器"
        if "title" not in field_selectors or "url" not in field_selectors:
            return None, "HTML 规则至少需要提供 title 与 url 字段选择器"
        normalized["field_selectors"] = field_selectors

        normalized["fetch_strategy"] = normalize_fetch_strategy(
            normalized.get("fetch_strategy")
        )
        normalized["request_method"] = normalize_request_method(
            normalized.get("request_method")
        )
        normalized["request_body"] = normalize_request_body(
            normalized.get("request_body")
        )
        normalized["request_headers"] = ensure_body_content_type(
            normalize_request_headers(normalized.get("request_headers")),
            normalized["request_method"],
            normalized["request_body"],
        )
        normalized["cookie_string"] = normalize_cookie_string(
            normalized.get("cookie_string")
        )

        if normalized["request_method"] != "post":
            normalized["request_body"] = ""

        pagination_mode = (
            str(normalized.get("pagination_mode") or "single").strip().lower()
        )
        if pagination_mode not in {"single", "next_link", "url_template", "load_more"}:
            pagination_mode = "single"
        normalized["pagination_mode"] = pagination_mode
        normalized["next_page_selector"] = str(
            normalized.get("next_page_selector") or ""
        ).strip()
        normalized["page_url_template"] = str(
            normalized.get("page_url_template") or ""
        ).strip()
        normalized["load_more_selector"] = str(
            normalized.get("load_more_selector") or ""
        ).strip()

        try:
            normalized["page_start"] = max(1, int(normalized.get("page_start") or 2))
        except (TypeError, ValueError):
            normalized["page_start"] = 2

        try:
            normalized["incremental_max_pages"] = max(
                1, int(normalized.get("incremental_max_pages") or 1)
            )
        except (TypeError, ValueError):
            normalized["incremental_max_pages"] = 1

        if str(normalized.get("max_pages") or "").strip():
            try:
                normalized["max_pages"] = max(1, int(normalized.get("max_pages")))
            except (TypeError, ValueError):
                return None, "最大翻页数必须是正整数"
        else:
            normalized["max_pages"] = None

        if pagination_mode == "next_link" and not normalized["next_page_selector"]:
            return None, "已启用下一页模式，请填写下一页选择器"
        if pagination_mode == "url_template":
            if not normalized["page_url_template"]:
                return None, "已启用页码模板模式，请填写分页 URL 模板"
            if "{page}" not in normalized["page_url_template"]:
                return None, "分页 URL 模板必须包含 {page} 占位符"
        if pagination_mode == "load_more" and not normalized["load_more_selector"]:
            return None, "已启用加载更多模式，请填写加载更多选择器"

        normalized["skip_detail"] = bool(normalized.get("skip_detail", False))
        normalized["body_field"] = (
            str(normalized.get("body_field") or "").strip() or None
        )
        if normalized["body_field"] and normalized["body_field"] not in field_selectors:
            return None, "正文来源字段必须是已定义的字段选择器"

        normalized["detail_strategy"] = normalize_detail_strategy(
            normalized.get("detail_strategy")
        )
        normalized["detail_body_selector"] = str(
            normalized.get("detail_body_selector") or ""
        ).strip()
        normalized["detail_time_selector"] = str(
            normalized.get("detail_time_selector") or ""
        ).strip()
        normalized["detail_attachment_selector"] = str(
            normalized.get("detail_attachment_selector") or ""
        ).strip()
        normalized["detail_image_selector"] = str(
            normalized.get("detail_image_selector") or ""
        ).strip()

        try:
            from src.models.spider_rule import SpiderRuleOutput

            SpiderRuleOutput(**normalized)
        except Exception as exc:
            return None, f"HTML 规则格式校验失败: {exc}"

        return normalized, ""

    def mark_as_read(self, url: str) -> Dict[str, Any]:
        """标记文章为已读"""
        try:
            db.mark_as_read(url)
            # 🌟 异步刷新托盘未读数量（不阻塞当前请求）
            try:
                import threading

                threading.Thread(target=self._refresh_tray_status, daemon=True).start()
            except Exception:
                pass
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def toggle_favorite(self, url: str) -> Dict[str, Any]:
        """
        切换文章收藏状态

        Args:
            url: 文章 URL（唯一标识）

        Returns:
            {"status": "success", "is_favorite": True/False} 或 {"status": "error", ...}
        """
        try:
            new_status = db.toggle_favorite(url)
            return {"status": "success", "is_favorite": new_status}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def delete_article(
        self, article_id: int, hard_delete: bool = False
    ) -> Dict[str, Any]:
        """
        删除文章

        Args:
            article_id: 文章 ID
            hard_delete: True 为物理删除（清除记录，允许重新抓取），
                        False 为软删除（屏蔽，不再抓取）

        Returns:
            {"status": "success"} 或 {"status": "error", "message": str}
        """
        try:
            success = db.delete_article(article_id, hard_delete=hard_delete)
            if success:
                # 异步刷新托盘状态
                try:
                    import threading

                    threading.Thread(
                        target=self._refresh_tray_status, daemon=True
                    ).start()
                except Exception:
                    pass
                return {"status": "success"}
            return {"status": "error", "message": "删除失败或文章不存在"}
        except Exception as e:
            logger.error(f"删除文章失败: {e}")
            return {"status": "error", "message": str(e)}

    def update_article_summary(
        self, article_id: int, new_summary: str, content_mode: str = "summary"
    ) -> Dict[str, Any]:
        """
        更新文章摘要/正文（用户二次编辑）

        Args:
            article_id: 文章 ID
            new_summary: 新的内容
            content_mode: 编辑模式（summary/raw/enhanced）

        Returns:
            {"status": "success"} 或 {"status": "error", ...}
        """
        try:
            article = db.get_article_by_id(article_id)
            if not article:
                return {"status": "error", "message": "文章不存在或更新失败"}

            source_type = self._resolve_article_source_type(article)
            mode = str(content_mode or "summary").strip().lower()
            clean_content = strip_emoji(new_summary)
            ai_config = self._resolve_article_ai_config(article)

            if source_type != "rss" and mode == "raw":
                current_summary_markdown = str(article.get("summary") or "").strip()
                current_ai_summary = str(article.get("ai_summary") or "").strip()
                current_ai_tags, current_summary_body = extract_leading_tags(
                    current_summary_markdown
                )
                if not current_ai_summary:
                    current_ai_summary = current_summary_body

                success = db.update_rss_detail_content(
                    article_id,
                    raw_text=clean_content,
                    raw_markdown=clean_content,
                )
                if success:
                    logger.info(f"文章 {article_id} 原文已更新")
                    payload = self._build_article_content_payload(
                        article,
                        ai_config,
                        source_type=source_type,
                        summary=current_summary_markdown,
                        ai_summary=current_ai_summary,
                        ai_tags=current_ai_tags,
                        raw_text=clean_content,
                        raw_markdown=clean_content,
                        enhanced_markdown=str(
                            article.get("enhanced_markdown") or ""
                        ).strip(),
                    )
                    return {
                        "status": "success",
                        "message": "原文已更新",
                        **payload,
                    }
                return {"status": "error", "message": "原文更新失败"}

            if source_type != "rss" or mode == "summary":
                tags, body = extract_leading_tags(clean_content)
                compatibility_summary = compose_tagged_markdown(tags, body)
                success = (
                    db.update_rss_detail_content(
                        article_id,
                        ai_summary=body,
                        ai_tags=tags,
                        summary=compatibility_summary,
                    )
                    if source_type == "rss"
                    else db.update_summary(article_id, clean_content)
                )
                if success:
                    logger.info(f"文章 {article_id} 摘要已更新")
                    payload = self._build_article_content_payload(
                        article,
                        ai_config,
                        source_type=source_type,
                        summary=compatibility_summary,
                        ai_summary=body,
                        ai_tags=tags,
                    )
                    return {
                        "status": "success",
                        "message": "摘要已更新",
                        **payload,
                    }
                return {"status": "error", "message": "文章不存在或更新失败"}

            current_raw = str(
                article.get("raw_markdown") or article.get("raw_text") or ""
            ).strip()
            current_enhanced = str(
                article.get("enhanced_markdown") or current_raw
            ).strip()
            current_ai_summary = str(article.get("ai_summary") or "").strip()
            current_ai_tags_raw = article.get("ai_tags")
            if isinstance(current_ai_tags_raw, list):
                current_ai_tags = [
                    str(tag or "").replace("【", "").replace("】", "").strip()
                    for tag in current_ai_tags_raw
                    if str(tag or "").strip()
                ][:3]
            elif isinstance(current_ai_tags_raw, str) and current_ai_tags_raw.strip():
                try:
                    parsed_tags = json.loads(current_ai_tags_raw)
                    current_ai_tags = (
                        [
                            str(tag or "").replace("【", "").replace("】", "").strip()
                            for tag in parsed_tags
                            if str(tag or "").strip()
                        ][:3]
                        if isinstance(parsed_tags, list)
                        else []
                    )
                except Exception:
                    current_ai_tags, _ = extract_leading_tags(current_ai_tags_raw)
            else:
                current_ai_tags = []
            summary_enabled = bool(ai_config.get("enable_ai_summary", False))
            formatting_enabled = bool(ai_config.get("enable_ai_formatting", False))

            if mode == "raw":
                sync_enhanced = (not formatting_enabled) or (
                    current_enhanced == current_raw
                )
                next_enhanced = clean_content if sync_enhanced else current_enhanced
                next_summary = (
                    compose_tagged_markdown(current_ai_tags, current_ai_summary)
                    if summary_enabled
                    else (next_enhanced or clean_content)
                )
                success = db.update_rss_detail_content(
                    article_id,
                    raw_text=clean_content,
                    raw_markdown=clean_content,
                    enhanced_markdown=next_enhanced if sync_enhanced else None,
                    summary=next_summary,
                )
                if success:
                    logger.info(f"RSS 原文已更新: {article_id}")
                    payload = self._build_article_content_payload(
                        article,
                        ai_config,
                        source_type=source_type,
                        summary=next_summary,
                        ai_summary=current_ai_summary,
                        ai_tags=current_ai_tags,
                        raw_text=clean_content,
                        raw_markdown=clean_content,
                        enhanced_markdown=next_enhanced,
                    )
                    return {
                        "status": "success",
                        "message": "原文已更新",
                        **payload,
                    }
                return {"status": "error", "message": "原文更新失败"}

            if mode == "enhanced":
                next_summary = (
                    compose_tagged_markdown(current_ai_tags, current_ai_summary)
                    if summary_enabled
                    else clean_content
                )
                success = db.update_rss_detail_content(
                    article_id,
                    enhanced_markdown=clean_content,
                    summary=next_summary,
                )
                if success:
                    logger.info(f"RSS 增强正文已更新: {article_id}")
                    payload = self._build_article_content_payload(
                        article,
                        ai_config,
                        source_type=source_type,
                        summary=next_summary,
                        ai_summary=current_ai_summary,
                        ai_tags=current_ai_tags,
                        raw_markdown=current_raw,
                        enhanced_markdown=clean_content,
                    )
                    return {
                        "status": "success",
                        "message": "增强正文已更新",
                        **payload,
                    }
                return {"status": "error", "message": "增强正文更新失败"}

            return {"status": "error", "message": "不支持的编辑模式"}
        except Exception as e:
            logger.error(f"更新文章摘要失败: {e}")
            return {"status": "error", "message": str(e)}

    def _normalize_annotation_style_payload(self, style_payload: Any) -> Dict[str, Any]:
        """标准化正文批注样式，避免前后端字段漂移。"""
        payload = style_payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        allowed_colors = {"blueviolet", "cherry", "sand", "teal", "dodger"}
        highlight_color = (
            str(payload.get("highlight_color") or payload.get("highlightColor") or "")
            .strip()
            .lower()
        )
        if highlight_color not in allowed_colors:
            highlight_color = ""

        return {
            "highlight_color": highlight_color,
            "underline": bool(payload.get("underline", False)),
            "strike": bool(
                payload.get(
                    "strike",
                    payload.get("strikethrough", payload.get("lineThrough", False)),
                )
            ),
            "bold": bool(payload.get("bold", False)),
        }

    def _resolve_annotation_view_mode(
        self, article: Dict[str, Any], requested_view_mode: str = "summary"
    ) -> str:
        """解析批注所归属的阅读模式。"""
        if self._resolve_article_source_type(article) != "rss":
            return "summary"

        normalized_mode = str(requested_view_mode or "summary").strip().lower()
        if normalized_mode in {"raw", "enhanced", "summary"}:
            return normalized_mode
        return "summary"

    def _get_annotation_view_modes_to_reset_on_regeneration(
        self,
        article: Dict[str, Any],
        result_kind: str = "default",
    ) -> List[str]:
        """根据重生成结果推导需要清空批注的阅读模式。"""
        if self._resolve_article_source_type(article) != "rss":
            return ["summary"]

        normalized_kind = str(result_kind or "").strip().lower()
        if normalized_kind in {"rss_full", "rss_formatting", "rss_reset"}:
            return ["enhanced", "summary"]
        if normalized_kind == "rss_summary":
            return ["summary"]
        return ["summary"]

    def _serialize_annotation_record(
        self, record: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """统一批注返回结构。"""
        row = dict(record or {})
        return {
            "id": int(row.get("id") or 0),
            "article_id": int(row.get("article_id") or 0),
            "view_mode": str(row.get("view_mode") or "summary").strip() or "summary",
            "anchor_text": str(row.get("anchor_text") or ""),
            "anchor_prefix": str(row.get("anchor_prefix") or ""),
            "anchor_suffix": str(row.get("anchor_suffix") or ""),
            "start_offset": max(int(row.get("start_offset") or 0), 0),
            "end_offset": max(int(row.get("end_offset") or 0), 0),
            "style_payload": self._normalize_annotation_style_payload(
                row.get("style_payload")
            ),
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def get_article_annotations(
        self, article_id: int, view_mode: str = "summary"
    ) -> Dict[str, Any]:
        """读取文章批注。"""
        try:
            article = db.get_article_by_id(article_id)
            if not article:
                return {"status": "error", "message": "文章不存在"}

            resolved_view_mode = self._resolve_annotation_view_mode(article, view_mode)
            annotations = [
                self._serialize_annotation_record(item)
                for item in db.get_article_annotations(article_id, resolved_view_mode)
            ]
            return {
                "status": "success",
                "article_id": int(article_id),
                "view_mode": resolved_view_mode,
                "annotations": annotations,
            }
        except Exception as e:
            logger.error(f"读取文章批注失败: {e}")
            return {"status": "error", "message": str(e)}

    def save_article_annotation(
        self, annotation_payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """保存文章批注。"""
        try:
            payload = annotation_payload if isinstance(annotation_payload, dict) else {}
            article_id = int(payload.get("article_id") or 0)
            if article_id <= 0:
                return {"status": "error", "message": "缺少有效的文章 ID"}

            article = db.get_article_by_id(article_id)
            if not article:
                return {"status": "error", "message": "文章不存在"}

            resolved_view_mode = self._resolve_annotation_view_mode(
                article,
                str(payload.get("view_mode") or "summary"),
            )
            style_payload = self._normalize_annotation_style_payload(
                payload.get("style_payload")
            )
            if not any(
                [
                    style_payload.get("highlight_color"),
                    style_payload.get("underline"),
                    style_payload.get("strike"),
                    style_payload.get("bold"),
                ]
            ):
                return {"status": "error", "message": "批注样式不能为空"}

            start_offset = max(int(payload.get("start_offset") or 0), 0)
            end_offset = max(int(payload.get("end_offset") or 0), start_offset)
            anchor_text = str(payload.get("anchor_text") or "")
            if not anchor_text.strip() or end_offset <= start_offset:
                return {"status": "error", "message": "选区信息无效"}

            annotation_id_raw = payload.get("annotation_id")
            annotation_id = None
            if annotation_id_raw is not None and str(annotation_id_raw).strip():
                annotation_id = int(annotation_id_raw)

            annotation = db.upsert_article_annotation(
                article_id=article_id,
                view_mode=resolved_view_mode,
                anchor_text=anchor_text,
                anchor_prefix=str(payload.get("anchor_prefix") or ""),
                anchor_suffix=str(payload.get("anchor_suffix") or ""),
                start_offset=start_offset,
                end_offset=end_offset,
                style_payload=style_payload,
                annotation_id=annotation_id,
            )
            if not annotation:
                return {"status": "error", "message": "保存批注失败"}

            return {
                "status": "success",
                "article_id": article_id,
                "view_mode": resolved_view_mode,
                "annotation": self._serialize_annotation_record(annotation),
            }
        except Exception as e:
            logger.error(f"保存文章批注失败: {e}")
            return {"status": "error", "message": str(e)}

    def delete_article_annotation(
        self, annotation_id: int, article_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """删除单条文章批注。"""
        try:
            normalized_annotation_id = int(annotation_id or 0)
            if normalized_annotation_id <= 0:
                return {"status": "error", "message": "缺少有效的批注 ID"}

            normalized_article_id = int(article_id) if article_id is not None else None
            success = db.delete_article_annotation(
                normalized_annotation_id,
                article_id=normalized_article_id,
            )
            if not success:
                return {"status": "error", "message": "批注不存在或删除失败"}
            return {
                "status": "success",
                "annotation_id": normalized_annotation_id,
            }
        except Exception as e:
            logger.error(f"删除文章批注失败: {e}")
            return {"status": "error", "message": str(e)}

    def delete_article_annotations(
        self, article_id: int, annotation_ids: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """批量删除文章批注。"""
        try:
            normalized_article_id = int(article_id or 0)
            if normalized_article_id <= 0:
                return {"status": "error", "message": "缺少有效的文章 ID"}

            normalized_ids = [
                int(annotation_id)
                for annotation_id in (annotation_ids or [])
                if str(annotation_id).strip()
            ]
            if not normalized_ids:
                return {"status": "success", "deleted_count": 0}

            deleted_count = db.delete_article_annotations(
                normalized_article_id,
                normalized_ids,
            )
            return {"status": "success", "deleted_count": int(deleted_count or 0)}
        except Exception as e:
            logger.error(f"批量删除文章批注失败: {e}")
            return {"status": "error", "message": str(e)}

    def cancel_ai_tasks(self) -> dict:
        """取消所有待处理的AI任务（用户主动终止）"""
        logger.info("【2】后端 API 已接收到取消指令")
        try:
            logger.info("【2.1】正在调用 scheduler.request_cancel()...")
            self._scheduler.request_cancel()  # 🌟 新增：一脚踩死爬虫抓取线程
            logger.info("【2.2】正在调用 article_processor.request_cancel()...")
            self._article_processor.request_cancel()
            logger.info("【2.3】正在调用 llm.request_cancel()...")
            self._llm.request_cancel()
            logger.info("【2.4】取消指令已发送完毕")
            return {"status": "success", "message": "已请求取消 AI 任务"}
        except Exception as e:
            logger.error(f"取消 AI 任务失败: {e}")
            return {"status": "error", "message": str(e)}

    def cancel_current_ai_summary(
        self, article_id: int = 0, request_token: int = 0
    ) -> dict:
        """取消当前正在进行的 AI 总结"""
        try:
            if article_id and request_token:
                with self._summary_lock:
                    current_token = self._active_summary_tokens.get(article_id, 0)
                if current_token and current_token != request_token:
                    return {
                        "status": "ignored",
                        "message": "当前总结已切换，取消请求已忽略",
                    }
                with self._summary_lock:
                    cancel_event = self._active_summary_events.get(article_id)
                if cancel_event:
                    cancel_event.set()
                    logger.info("已请求取消当前 AI 总结（独立事件）")
                    return {
                        "status": "success",
                        "message": "已请求取消当前 AI 总结",
                    }
                return {
                    "status": "ignored",
                    "message": "当前总结已结束，取消请求已忽略",
                }

            self._llm.request_cancel()
            logger.info("已请求取消当前 AI 总结")
            return {"status": "success", "message": "已请求取消当前 AI 总结"}
        except Exception as e:
            logger.error(f"取消当前 AI 总结失败: {e}")
            return {"status": "error", "message": str(e)}

    def cancel_rss_rule_preview(self, request_token: int = 0) -> dict:
        """取消当前正在进行的 RSS 规则预览"""
        try:
            if not request_token:
                return {"status": "ignored", "message": "缺少 RSS 预览请求标识"}

            with self._rss_preview_lock:
                cancel_event = self._active_rss_preview_events.get(request_token)

            if not cancel_event:
                return {
                    "status": "ignored",
                    "message": "当前 RSS 预览已结束，取消请求已忽略",
                }

            cancel_event.set()
            logger.info(f"已请求取消 RSS 规则预览: {request_token}")
            return {"status": "success", "message": "已请求取消 RSS 规则预览"}
        except Exception as e:
            logger.error(f"取消 RSS 规则预览失败: {e}")
            return {"status": "error", "message": str(e)}

    def clear_ai_cancel(self) -> dict:
        """清除取消标志（新一轮任务开始前调用）"""
        try:
            self._article_processor.clear_cancel()
            self._llm.clear_cancel()
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _clear_residual_ai_cancel_flags(self) -> None:
        """清理上一次手动中断后残留的全局取消标记。"""
        try:
            article_processor = getattr(self, "_article_processor", None)
            if article_processor and hasattr(article_processor, "clear_cancel"):
                article_processor.clear_cancel()
        except Exception as e:
            logger.debug(f"清理 ArticleProcessor 取消标记失败: {e}")

        try:
            llm_service = getattr(self, "_llm", None)
            if llm_service and hasattr(llm_service, "clear_cancel"):
                llm_service.clear_cancel()
        except Exception as e:
            logger.debug(f"清理 LLMService 取消标记失败: {e}")

    def close_app(self):
        """隐藏窗口到后台"""
        if webview.windows:
            webview.windows[0].hide()

    def minimize_app(self):
        """最小化窗口"""
        if webview.windows:
            webview.windows[0].minimize()

    def open_browser(self, url: str):
        """使用默认浏览器打开链接"""
        success = self._system_service.open_browser(url)
        return {"status": "success" if success else "error"}

    def open_in_browser(self, url: str):
        """打开外部链接（用于附件验证码页面）"""
        import webbrowser

        success = webbrowser.open(url)
        logger.info(f"打开浏览器链接: {url} -> {'成功' if success else '失败'}")
        return {"status": "success" if success else "error"}

    def start_daemon(self, interval_minutes: int = 15, debug_seconds: int = None):  # type: ignore
        """启动后台守护线程"""
        debug_mode = debug_seconds is not None

        def on_new_articles(count: int, result: dict):
            """发现新文章时的回调"""
            logger.info(f"守护进程检测到 {count} 篇新文章，正在后台处理...")
            # 🌟 更新托盘未读数量
            self._refresh_tray_status()

        def on_symbolic_ping(title: str, message: str):
            """后台轻提醒回调，不触发重型动作。"""
            try:
                self._enqueue_js(
                    f"if(window.showDaemonHint) window.showDaemonHint({json.dumps(title)}, {json.dumps(message)});"
                )
            except Exception as e:
                logger.debug(f"发送后台轻提醒失败: {e}")

        # 🌟 热重载 Getter：动态读取配置，带安全边界（最小 15 分钟）
        def get_interval_seconds() -> int:
            polling_minutes = self._config_service.get("pollingInterval", 60)
            # 防御性转换：确保最小 900 秒（15 分钟）
            return max(int(polling_minutes) * 60, 900)

        self._daemon_manager.start(
            task_callback=self.check_updates,
            interval_getter=get_interval_seconds,
            on_new_articles=on_new_articles,
            on_symbolic_ping=on_symbolic_ping,
            debug_mode=debug_mode,
        )

    def _refresh_tray_status(self, count: int = None):
        """刷新托盘状态（未读数量和同步时间）

        Args:
            count: 可选的未读数量，如果不传则从数据库重新计算
        """
        try:
            import sys

            # 🌟 关键修复：使用 __main__ 获取真正的主模块，而不是 import main
            # 当 main.py 作为入口点运行时，它的模块名是 __main__，不是 main
            main_mod = sys.modules.get("__main__")
            if main_mod is None:
                logger.warning("无法获取主模块")
                return

            from datetime import datetime

            # 如果没有传入 count，则从数据库获取
            if count is None:
                effective_sources = self._get_effective_sources()
                if effective_sources == []:
                    count = 0
                else:
                    count = db.get_unread_count(source_names=effective_sources)
            # 获取当前时间
            sync_time = datetime.now().strftime("%H:%M")
            logger.debug(
                "_refresh_tray_status: count=%s sync_time=%s",
                count,
                sync_time,
            )
            logger.debug(
                "_refresh_tray_status: main._unread_count=%s",
                getattr(main_mod, "_unread_count", "N/A"),
            )
            logger.debug(
                "_refresh_tray_status: main._status_item=%s",
                getattr(main_mod, "_status_item", "N/A"),
            )
            logger.debug(
                "_refresh_tray_status: main._base_image=%s",
                getattr(main_mod, "_base_image", "N/A"),
            )
            # 更新托盘
            main_mod.update_tray_status(unread=count, sync_time=sync_time)
            logger.debug("_refresh_tray_status: update_tray_status finished")
        except Exception as e:
            logger.exception("_refresh_tray_status failed: %s", e)

    def refresh_tray_mute_status(self, mute_mode: bool) -> Dict[str, Any]:
        """
        刷新托盘勿扰模式状态

        Args:
            mute_mode: 勿扰模式是否开启

        Returns:
            {"status": "success"}
        """
        try:
            import sys

            if "__main__" in sys.modules:
                main_mod = sys.modules["__main__"]
            else:
                import main

                main_mod = main

            # 更新全局勿扰模式状态
            setattr(main_mod, "_mute_mode", mute_mode)

            # 更新托盘菜单中的勿扰模式勾选状态
            main_mod.update_tray_status()

            logger.info(f"🔕 勿扰模式状态已同步到托盘: {mute_mode}")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"刷新托盘勿扰状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def download_attachment(self, url: str, filename: str) -> dict:
        """下载附件（支持后缀智能补全）"""
        return self._download_service.download_attachment(url, filename)

    def open_system_link(self, url: str):
        """调用系统原生应用打开链接"""
        success = self._system_service.open_system_link(url)
        return {"status": "success" if success else "error"}

    def save_snapshot(self, b64_data: str, title: str) -> dict:
        """保存快照图片"""
        return self._download_service.save_snapshot(b64_data, title)

    def copy_image_to_clipboard(self, b64_data: str) -> dict:
        """接收 Base64 图片并调用操作系统底层接口暴力写入剪贴板（无需第三方库）"""
        try:
            # 1. 解码前端传来的 Base64 数据并存入临时文件
            if "," in b64_data:
                b64_data = b64_data.split(",")[1]
            img_data = base64.b64decode(b64_data)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                temp_path = f.name
                f.write(img_data)

            # 2. 根据不同操作系统，调用原生底层脚本注入剪贴板
            system = platform.system().lower()
            if system == "darwin":
                # macOS: 使用内置的 AppleScript (osascript) 强行写入图像
                script = f'set the clipboard to (read (POSIX file "{temp_path}") as TIFF picture)'
                subprocess.run(["osascript", "-e", script], check=True)
            elif system == "windows":
                # Windows: 使用内置的 PowerShell 调用 .NET 的剪贴板接口
                # 隐藏 PowerShell 弹窗黑框
                creation_flags = 0x08000000 if os.name == "nt" else 0
                script = f'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile("{temp_path}"))'
                subprocess.run(
                    ["powershell", "-command", script],
                    check=True,
                    creationflags=creation_flags,
                )
            else:
                return {"status": "error", "message": "当前系统暂不支持一键复制图片"}

            # 3. 延迟一小会儿后删除临时文件，防止剪贴板还没读取完文件就被删了
            time.sleep(0.5)
            os.remove(temp_path)

            return {"status": "success"}
        except Exception as e:
            logger.error(f"写入剪贴板失败: {e}")
            return {"status": "error", "message": str(e)}

    def popup_new_article(self, article_dict: dict):
        """弹窗展示新文章"""
        if not self._window:
            return

        try:
            self._window.restore()
            self._window.show()

            screen = webview.screens[0]
            target_width = 465
            target_height = 750
            x_pos = screen.width - target_width - 20
            y_pos = 40

            self._window.resize(target_width, target_height)
            self._window.move(x_pos, y_pos)

            js_code = f"if(window.showArticleDetailFromBackend) {{ window.showArticleDetailFromBackend({json.dumps(article_dict)}); }}"
            # 🌟 使用队列执行（线程安全）
            self._enqueue_js(js_code)

        except Exception as e:
            logger.warning(f"桌面弹窗展示失败: {e}")

    def _apply_config(self):
        """应用配置到 LLM 服务"""
        config = self._config_service.current
        self._llm.update_config(
            api_key=config.api_key,
            model_name=config.model_name,
            system_prompt=config.prompt,
            base_url=config.base_url,
        )

    def load_config(self) -> dict:
        """暴露给前端：获取配置"""
        config_data = self._config_service.to_dict()
        config_data.pop("updateCooldown", None)
        config_data.pop("max_items", None)
        channel = str(config_data.get("channel", "stable") or "stable").strip().lower()
        if channel not in {"stable", "beta", "internal"}:
            channel = "stable"

        # 🌟 修复：确保所有字段存在（提供默认值），避免设置界面空白
        default_config = {
            "baseUrl": "https://api.deepseek.com/v1",
            "apiKey": "",
            "modelName": "deepseek-chat",
            "prompt": (
                self._llm.system_prompt
                if hasattr(self, "_llm") and self._llm
                else "请帮我总结以下文章内容"
            ),
            "autoStart": False,
            "muteMode": False,
            "trackMode": "continuous",
            "themeAppearance": "snow-frost",
            "fontFamily": "sans-serif",
            "customFontPath": "",
            "customFontName": "",
            "telemetryEnabled": False,
            "telemetryErrorReportsEnabled": False,
            "telemetryConsentStatus": "undecided",
            "telemetryInstallId": "",
            "subscribedSources": [],
            "pollingInterval": 60,
            "isPinned": False,
            "readNoticeTime": "",
            "channel": channel,
            "emailNotifyEnabled": False,
            "smtpHost": "",
            "smtpPort": 465,
            "smtpUser": "",
            "smtpPassword": "",
            "subscriberList": [],
            "secondaryModels": [],
            "body_field": "content",
            "skip_detail": False,
        }
        default_config.update(
            {
                "telemetryEnabled": True,
                "telemetryErrorReportsEnabled": True,
                "telemetryConsentStatus": "enabled",
                "telemetryNoticeShown": False,
            }
        )
        for key, default in default_config.items():
            if key not in config_data or (
                isinstance(default, str) and config_data.get(key) in (None, "")
            ):
                config_data[key] = default

        # 🌟 修复：启动时恢复置顶状态，改为对属性赋值
        if self._window and config_data.get("isPinned", False):
            self._window.on_top = True

        return {"status": "success", "data": config_data}

    def save_config(self, new_config: dict) -> dict:
        """暴露给前端：保存配置"""
        try:
            previous_config = self._config_service.to_dict()
            normalized_config = dict(new_config or {})
            normalized_config.pop("max_items", None)

            # 安全/运行时字段只允许后端维护，避免前端带着旧状态把配置重新锁回去。
            protected_runtime_fields = (
                "updateCooldown",
                "isLocked",
                "apiBalanceOk",
                "configSign",
                "lastCloudSyncTime",
                "deviceId",
            )
            for key in protected_runtime_fields:
                if key in previous_config:
                    normalized_config[key] = previous_config.get(key)
                else:
                    normalized_config.pop(key, None)

            if normalized_config.get("telemetryEnabled") or normalized_config.get(
                "telemetryErrorReportsEnabled"
            ):
                normalized_config["telemetryConsentStatus"] = "enabled"
            else:
                normalized_config["telemetryConsentStatus"] = "disabled"

            # 🌟 检查配置锁定状态
            if self._config_service.current and self._config_service.current.is_locked:
                logger.warning("⚠️ 配置已锁定，拒绝保存操作")
                return {"status": "error", "message": "配置已锁定，无法保存"}

            if not self._config_service.save(normalized_config):
                return {
                    "status": "error",
                    "message": "保存配置文件失败，请检查文件权限",
                }

            # 应用开机自启设置
            self._set_autostart(normalized_config.get("autoStart", False))

            # 热更新 LLM 配置
            self._apply_config()

            tracked_keys = [
                "themeAppearance",
                "fontFamily",
                "trackMode",
                "pollingInterval",
                "autoStart",
                "muteMode",
                "telemetryEnabled",
                "telemetryErrorReportsEnabled",
            ]
            changed_keys = [
                key
                for key in tracked_keys
                if previous_config.get(key) != normalized_config.get(key)
            ]
            if changed_keys:
                self._track_telemetry(
                    "settings_changed",
                    {
                        "changed_keys": changed_keys,
                        "theme": normalized_config.get("themeAppearance", ""),
                        "font_family": normalized_config.get("fontFamily", ""),
                        "track_mode": normalized_config.get("trackMode", ""),
                        "polling_interval": normalized_config.get("pollingInterval", 0),
                        "mute_mode": bool(normalized_config.get("muteMode", False)),
                        "telemetry_enabled": bool(
                            normalized_config.get("telemetryEnabled", False)
                        ),
                        "error_reports_enabled": bool(
                            normalized_config.get(
                                "telemetryErrorReportsEnabled", False
                            )
                        ),
                    },
                )

            logger.info("系统配置已成功保存并热更新")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return {"status": "error", "message": f"保存失败: {str(e)}"}

    def test_ai_connection(
        self,
        api_key: str,
        model_name: str,
        provider: str = "custom",
        base_url: str = "",
    ) -> dict:
        """测试 AI 连通性"""
        logger.info("正在进行 AI 连通性测试...")
        is_ok, msg = self._llm.test_connection(api_key, model_name, base_url)
        if is_ok:
            return {"status": "success"}
        return {"status": "error", "message": msg}

    def track_telemetry_event(
        self,
        event_name: str,
        props: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """供前端调用：记录匿名遥测事件。"""
        try:
            return self._telemetry.track(event_name, props, force=bool(force))
        except Exception as e:
            logger.debug(f"前端记录遥测事件失败 ({event_name}): {e}")
            return {"status": "error", "message": str(e)}

    def report_frontend_error(
        self,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """供前端调用：上报未捕获 JS / Promise 错误。"""
        try:
            return self._telemetry.record_frontend_error(payload)
        except Exception as e:
            logger.debug(f"前端错误上报失败: {e}")
            return {"status": "error", "message": str(e)}

    def flush_telemetry(self, force: bool = False) -> Dict[str, Any]:
        """手动触发一次遥测上报。"""
        try:
            return self._telemetry.flush(force=bool(force))
        except Exception as e:
            logger.debug(f"手动上报遥测失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_telemetry_status(self) -> Dict[str, Any]:
        """获取当前遥测配置与队列状态。"""
        try:
            return self._telemetry.get_status_payload()
        except Exception as e:
            logger.debug(f"读取遥测状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def clear_telemetry_queue(self) -> Dict[str, Any]:
        """清空本地待上报遥测队列。"""
        try:
            return self._telemetry.clear_queue()
        except Exception as e:
            logger.debug(f"清空遥测队列失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_api_balance_status(self) -> dict:
        """获取 API 余额状态"""
        try:
            balance_ok = self._config_service.get_api_balance_ok()
            return {"status": "success", "balance_ok": balance_ok}
        except Exception as e:
            logger.error(f"获取余额状态失败: {e}")
            return {"status": "success", "balance_ok": True}  # 出错时默认正常

    def _get_ai_prereq_signature(self) -> str:
        api_key = str(self._config_service.get("apiKey", "") or "").strip()
        model_name = str(
            self._config_service.get("modelName", "deepseek-chat") or "deepseek-chat"
        ).strip()
        base_url = str(
            self._config_service.get("baseUrl", "https://api.deepseek.com/v1")
            or "https://api.deepseek.com/v1"
        ).strip()
        return "|".join([api_key, model_name, base_url])

    def _get_cached_ai_prereq_result(self) -> Optional[Dict[str, Any]]:
        with self._ai_prereq_cache_lock:
            cache = dict(self._ai_prereq_cache)

        result = cache.get("result")
        if not result or cache.get("signature") != self._get_ai_prereq_signature():
            return None

        checked_at = float(cache.get("checked_at") or 0.0)
        age = time.time() - checked_at
        status = result.get("status")
        stage = result.get("stage")

        if status == "success":
            if (
                age <= self._ai_prereq_success_ttl
                and self._config_service.get_api_balance_ok()
            ):
                return dict(result)
            return None

        if stage == "balance_error":
            if (
                age <= self._ai_prereq_failure_ttl
                and not self._config_service.get_api_balance_ok()
            ):
                return dict(result)
            return None

        if age <= self._ai_prereq_failure_ttl:
            return dict(result)
        return None

    def _set_ai_prereq_cache(self, result: Dict[str, Any]) -> None:
        with self._ai_prereq_cache_lock:
            self._ai_prereq_cache = {
                "signature": self._get_ai_prereq_signature(),
                "result": dict(result),
                "checked_at": time.time(),
            }

    def _warm_ai_prereq_cache(self) -> None:
        try:
            self.validate_ai_prerequisites(use_cache=False)
        except Exception as e:
            logger.debug(f"AI 前置检查预热失败: {e}")

    def validate_ai_prerequisites(self, use_cache: bool = True) -> Dict[str, Any]:
        """
        统一检查 AI 前置条件：
        1. 是否配置 API Key
        2. 是否能够连通 API
        3. 是否处于欠费状态
        """
        try:
            if use_cache:
                cached_result = self._get_cached_ai_prereq_result()
                if cached_result is not None:
                    return cached_result

            with self._ai_prereq_refresh_lock:
                if use_cache:
                    cached_result = self._get_cached_ai_prereq_result()
                    if cached_result is not None:
                        return cached_result

                api_key = str(self._config_service.get("apiKey", "") or "").strip()
                model_name = str(
                    self._config_service.get("modelName", "deepseek-chat")
                    or "deepseek-chat"
                ).strip()
                base_url = str(
                    self._config_service.get("baseUrl", "https://api.deepseek.com/v1")
                    or "https://api.deepseek.com/v1"
                ).strip()

                if not api_key:
                    result = {
                        "status": "error",
                        "stage": "config_missing",
                        "message": "未配置 API Key，请先在设置中配置",
                    }
                    self._set_ai_prereq_cache(result)
                    return result

                is_ok, msg = self._llm.test_connection(api_key, model_name, base_url)
                if not is_ok:
                    result = {
                        "status": "error",
                        "stage": "connection_failed",
                        "message": msg,
                    }
                    self._set_ai_prereq_cache(result)
                    return result

                if not self._config_service.get_api_balance_ok():
                    result = {
                        "status": "error",
                        "stage": "balance_error",
                        "message": "API 余额不足，请先充值后重试",
                    }
                    self._set_ai_prereq_cache(result)
                    return result

                result = {
                    "status": "success",
                    "ready": True,
                    "stage": "ready",
                    "message": "API 就绪",
                }
                self._set_ai_prereq_cache(result)
                return result
        except Exception as e:
            logger.error(f"AI 前置检查失败: {e}")
            result = {
                "status": "error",
                "stage": "validation_failed",
                "message": f"API 前置检查失败：{e}",
            }
            self._set_ai_prereq_cache(result)
            return result

    def clear_api_balance_status(self) -> dict:
        """清除欠费状态（用户充值后调用）"""
        try:
            self._config_service.set_api_balance_ok(True)
            logger.info("用户已清除欠费状态")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"清除欠费状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_cooldown_config(self) -> dict:
        """获取冷却时间配置"""
        cooldown_seconds = self._get_effective_update_cooldown_seconds()
        return {"status": "success", "cooldown_seconds": cooldown_seconds}

    # ==================== 📧 邮件推送 API ====================

    def send_test_email(self, test_email: str = None) -> dict:
        """
        发送测试邮件

        Args:
            test_email: 测试收件人邮箱（可选，默认使用 SMTP 用户名）

        Returns:
            {"status": "success/error", "message": str}
        """
        try:
            # 获取邮件配置
            smtp_host = self._config_service.get("smtpHost", "")
            smtp_port = self._config_service.get("smtpPort", 465)
            smtp_user = self._config_service.get("smtpUser", "")
            smtp_password = self._config_service.get("smtpPassword", "")

            if not smtp_host or not smtp_user or not smtp_password:
                return {"status": "error", "message": "请先配置 SMTP 服务器信息"}

            # 如果未指定测试邮箱，使用 SMTP 用户名
            to_addr = test_email or smtp_user
            if not to_addr:
                return {"status": "error", "message": "请提供测试邮箱地址"}

            # 导入邮件服务
            from src.services.email_service import EmailService

            email_service = EmailService(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
            )

            result = email_service.send_test_email(to_addr)

            if result.get("success"):
                return {"status": "success", "message": f"测试邮件已发送至 {to_addr}"}
            else:
                return {"status": "error", "message": result.get("message", "发送失败")}

        except ImportError as e:
            logger.error(f"邮件服务模块导入失败: {e}")
            return {"status": "error", "message": "邮件服务模块未安装"}
        except Exception as e:
            logger.error(f"发送测试邮件失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_email_config(self) -> dict:
        """获取邮件配置状态"""
        try:
            return {
                "status": "success",
                "emailNotifyEnabled": self._config_service.get(
                    "emailNotifyEnabled", False
                ),
                "smtpHost": self._config_service.get("smtpHost", ""),
                "smtpPort": self._config_service.get("smtpPort", 465),
                "smtpUser": self._config_service.get("smtpUser", ""),
                "hasPassword": bool(self._config_service.get("smtpPassword", "")),
                "subscriberList": self._config_service.get("subscriberList", []),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def diagnose_email_push(self) -> dict:
        """
        诊断邮件推送配置 - 包含真实 SMTP 连通性探测

        Returns:
            {"status": "success/error", "canSend": bool, "message": str, "checks": dict, "issues": list}
        """
        import smtplib
        import socket

        try:
            # 检查配置服务
            if not self._article_processor.config_service:
                return {
                    "status": "error",
                    "message": "ArticleProcessor 缺少 config_service",
                    "canSend": False,
                }

            # 获取各项配置
            enabled = self._config_service.get("emailNotifyEnabled", False)
            subscriber_list = self._config_service.get("subscriberList", [])
            smtp_host = self._config_service.get("smtpHost", "")
            smtp_port = self._config_service.get("smtpPort", 465)
            smtp_user = self._config_service.get("smtpUser", "")
            smtp_password = self._config_service.get("smtpPassword", "")

            # ========== 第一步：基础配置检查 ==========
            issues = []
            warnings = []  # 提示性问题，不阻止诊断
            config_issues = []  # 仅配置问题，不阻止连通性测试

            if not enabled:
                warnings.append("邮件通知未启用")
            if len(subscriber_list) == 0:
                warnings.append("订阅者列表为空，邮件将只发送给自己）")
            if not smtp_host:
                config_issues.append("SMTP 服务器未配置")
            if not smtp_user:
                config_issues.append("SMTP 用户未配置")
            if not smtp_password:
                config_issues.append("SMTP 密码未配置")

            # ========== 第二步：端口范围检查 ==========
            try:
                smtp_port_int = int(smtp_port)
                if not (1 <= smtp_port_int <= 65535):
                    config_issues.append(
                        f"端口号无效 ({smtp_port})，应在 1-65535 范围内"
                    )
            except (ValueError, TypeError):
                config_issues.append(f"端口号格式错误 ({smtp_port})")
                smtp_port_int = 465  # 使用默认值继续

            issues.extend(config_issues)

            # 如果基础配置缺失，直接返回
            if config_issues:
                logger.info(f"📧 邮件推送诊断: 基础配置缺失，跳过连通性测试")
                return {
                    "status": "success",
                    "canSend": False,
                    "checks": {
                        "emailNotifyEnabled": enabled,
                        "subscriberList": subscriber_list,
                        "smtpHost": smtp_host,
                        "smtpPort": (
                            smtp_port_int if "smtp_port_int" in dir() else smtp_port
                        ),
                        "smtpUser": smtp_user,
                        "hasPassword": bool(smtp_password),
                        "connectionTest": "skipped",
                    },
                    "issues": issues,
                    "message": f"配置问题: {', '.join(config_issues)}",
                }

            # ========== 第三步：真实 SMTP 连通性测试 ==========
            connection_result = None
            try:
                logger.info(f"📧 SMTP 连通性测试: {smtp_host}:{smtp_port_int}")

                if smtp_port_int == 465:
                    # SSL 连接
                    server = smtplib.SMTP_SSL(smtp_host, smtp_port_int, timeout=5)
                else:
                    # 普通连接后升级 TLS
                    server = smtplib.SMTP(smtp_host, smtp_port_int, timeout=5)
                    server.starttls()

                # 尝试登录验证
                server.login(smtp_user, smtp_password)
                server.quit()

                connection_result = "success"
                logger.info(f"📧 SMTP 连通性测试成功")

            except smtplib.SMTPAuthenticationError as e:
                connection_result = "auth_failed"
                error_detail = str(e)
                if (
                    "535" in error_detail
                    or "authentication failed" in error_detail.lower()
                ):
                    issues.append("SMTP 认证失败：用户名或授权码错误")
                else:
                    issues.append(f"SMTP 认证失败：{error_detail}")
                logger.warning(f"📧 SMTP 认证失败: {e}")

            except smtplib.SMTPConnectError as e:
                connection_result = "connect_failed"
                issues.append(f"无法连接 SMTP 服务器：请检查服务器地址")
                logger.warning(f"📧 SMTP 连接失败: {e}")

            except socket.timeout:
                connection_result = "timeout"
                issues.append(f"连接超时：服务器 {smtp_host}:{smtp_port_int} 无响应")
                logger.warning(f"📧 SMTP 连接超时")

            except socket.gaierror as e:
                connection_result = "dns_failed"
                issues.append(f"DNS 解析失败：无法解析服务器地址 {smtp_host}")
                logger.warning(f"📧 DNS 解析失败: {e}")

            except ConnectionRefusedError:
                connection_result = "refused"
                issues.append(f"连接被拒绝：端口 {smtp_port_int} 可能未开放")
                logger.warning(f"📧 SMTP 连接被拒绝")

            except Exception as e:
                connection_result = "error"
                error_msg = str(e)
                if "SSL" in error_msg or "certificate" in error_msg.lower():
                    issues.append(f"SSL/TLS 错误：请检查端口配置或服务器证书")
                else:
                    issues.append(f"连接异常：{error_msg}")
                logger.warning(f"📧 SMTP 连接异常: {e}")

            # ========== 第四步：汇总结果 ==========
            # SMTP 连接成功即认为配置正确（订阅者为空不影响测试邮件发送）
            can_send = connection_result == "success"

            # 合并提示信息
            all_hints = issues + warnings

            # 生成消息
            if can_send:
                message = "SMTP 连接测试成功"
                if warnings:
                    message += f"（提示: {', '.join(warnings)}）"
            elif connection_result and connection_result != "success":
                message = issues[-1] if issues else "SMTP 连接测试失败"
            else:
                message = (
                    f"问题: {', '.join(issues)}"
                    if issues
                    else f"提示: {', '.join(warnings)}"
                )

            logger.info(
                f"📧 邮件推送诊断完成: canSend={can_send}, connectionResult={connection_result}"
            )

            return {
                "status": "success",
                "canSend": can_send,
                "checks": {
                    "emailNotifyEnabled": enabled,
                    "subscriberList": subscriber_list,
                    "smtpHost": smtp_host,
                    "smtpPort": smtp_port_int,
                    "smtpUser": smtp_user,
                    "hasPassword": bool(smtp_password),
                    "connectionTest": connection_result,
                },
                "issues": issues,
                "warnings": warnings,
                "message": message,
            }

        except Exception as e:
            logger.error(f"诊断邮件推送失败: {e}")
            return {"status": "error", "message": str(e), "canSend": False}

    def test_email_push_with_latest_article(self) -> dict:
        """
        使用最新一篇文章测试邮件推送流程

        Returns:
            {"status": "success/error", "message": str}
        """
        try:
            # 获取最新一篇文章
            articles = db.get_articles_paged(limit=1, offset=0)
            if not articles or len(articles) == 0:
                return {"status": "error", "message": "数据库中没有文章"}

            article = articles[0]
            logger.info(f"📧 测试邮件推送：使用文章 [{article.get('title', '')[:30]}]")

            # 检查配置
            enabled = self._config_service.get("emailNotifyEnabled", False)
            subscriber_list = self._config_service.get("subscriberList", [])

            if not enabled:
                return {"status": "error", "message": "邮件通知未启用"}
            if not subscriber_list:
                return {"status": "error", "message": "订阅者列表为空"}

            # 🌟 添加 model_name 字段（从配置获取当前模型名称）
            article["model_name"] = self._config_service.get("modelName", "AI")

            # 直接调用 ArticleProcessor 的邮件发送方法
            self._article_processor._send_email_notification(article)

            # 🌟 格式化订阅者显示：1个显示全名，2个及以上显示"...等N个邮箱"
            if len(subscriber_list) == 1:
                recipient_info = subscriber_list[0]
            else:
                recipient_info = f"...等{len(subscriber_list)}个邮箱"

            return {
                "status": "success",
                "message": f"已触发邮件推送，请检查{recipient_info}",
            }

        except Exception as e:
            logger.error(f"测试邮件推送失败: {e}")
            return {"status": "error", "message": str(e)}

    def refresh_snapshot_browser(self) -> dict:
        """
        刷新快照样式（每次截图都会创建新的浏览器实例，无需手动刷新）

        Returns:
            {"status": "success", "message": str}
        """
        # 新实现每次截图都创建新的浏览器实例，样式自动生效
        return {
            "status": "success",
            "message": "样式已自动生效，下次截图将使用最新样式",
        }

    def _set_autostart(self, enabled: bool):
        """设置开机自启"""
        self._system_service.set_autostart(enabled)

    def hide_window(self):
        """隐藏窗口"""
        if self._window:
            self._window.hide()

    def search_articles(self, keyword: str, source_name: str = None, favorites_only: bool = False) -> dict:  # type: ignore
        """全局搜索（支持按来源筛选、按收藏筛选）

        Args:
            keyword: 搜索关键词
            source_name: 单个来源筛选（仅支持指定单个来源）
            favorites_only: 是否只返回收藏的文章

        搜索逻辑：
        - 如果指定了 source_name 且不是"全部"，只搜索该来源
        - 否则，搜索所有有效来源（订阅来源 + 启用的动态来源）
        """
        try:
            # 🌟 统一大门卫：完全由后端定夺
            if source_name and source_name != "全部":
                filter_sources = [source_name]
            else:
                filter_sources = self._get_effective_sources()

            logger.debug(
                f"[DEBUG] search_articles - keyword={keyword}, source_name={source_name}, filter_sources={filter_sources}"
            )

            # 🌟 致命防御：如果什么都没订阅，直接阻断
            if filter_sources == [] and not source_name:
                return {"status": "success", "data": []}

            if not keyword or not keyword.strip():
                # 如果搜索词为空，相当于直接获取分页列表
                articles = self._mark_article_list_records(
                    db.get_articles_paged(
                        limit=20,
                        offset=0,
                        source_names=filter_sources,
                        favorites_only=favorites_only,
                        include_content=False,
                    )
                )
                return {"status": "success", "data": articles}

            data = db.search_articles(
                keyword.strip(),
                limit=50,
                source_names=filter_sources,
                favorites_only=favorites_only,
                include_content=False,
            )
            data = self._mark_article_list_records(data)
            logger.debug(f"[DEBUG] search_articles - 返回结果数: {len(data)}")
            return {"status": "success", "data": data}
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return {"status": "error", "message": "搜索失败"}

    def force_quit(self):
        """彻底退出"""
        logger.info("程序彻底退出")
        self._track_telemetry("app_exit", {"reason": "force_quit"})
        self.is_running = False
        self._daemon_manager.request_stop()
        self._article_processor.shutdown(wait=False)
        try:
            self._telemetry.shutdown(flush=True)
        except Exception:
            pass
        os._exit(0)

    def get_all_sources(self) -> dict:
        """获取所有数据来源列表（按 SPIDER_REGISTRY 定义的顺序返回）"""
        try:
            # 从数据库获取所有存在的来源
            db_sources = set(db.get_all_sources())

            # 按 SPIDER_REGISTRY 的顺序返回（只返回数据库中存在的）
            ordered_sources = [
                spider_cls.SOURCE_NAME
                for spider_cls, _, _, _ in SPIDER_REGISTRY
                if spider_cls.SOURCE_NAME in db_sources
            ]
            # 🌟 追加动态爬虫来源
            dynamic_sources = self._get_active_dynamic_sources()
            for ds in dynamic_sources:
                if ds in db_sources and ds not in ordered_sources:
                    ordered_sources.append(ds)

            return {"status": "success", "data": ordered_sources}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def get_unread_count(self, source_name: str = None) -> dict:
        """获取未读文章数量（直接从数据库统计，不依赖前端分页数据）

        Args:
            source_name: 可选的部门筛选（单个来源）

        Returns:
            {"status": "success", "count": int}
        """
        try:
            # 🌟 统一来源过滤逻辑
            if source_name and source_name != "全部":
                filter_sources = [source_name]
            else:
                filter_sources = self._get_effective_sources()

            logger.debug(f"📊 获取未读数量 - 筛选来源: {filter_sources}")

            # 从数据库直接统计未读数量
            count = db.get_unread_count(source_names=filter_sources)
            logger.debug(f"📊 未读数量统计结果: {count}")

            # 🌟 只有在统计全局未读数时才顺带刷新托盘，避免来源切换时额外占用数据库
            if source_name is None or source_name == "全部":
                try:
                    import threading

                    threading.Thread(
                        target=self._refresh_tray_status, daemon=True
                    ).start()
                except Exception:
                    pass

            return {"status": "success", "count": count}
        except Exception as e:
            logger.error(f"获取未读数量失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "count": 0}

    def get_first_unread_url(self, source_name: str = None) -> dict:
        """获取第一个未读文章的 URL（用于前端定位滚动）

        Args:
            source_name: 可选的部门筛选（单个来源）

        Returns:
            {"status": "success", "url": str, "id": int} 或 {"status": "success", "found": false}
        """
        try:
            # 🌟 统一来源过滤逻辑
            if source_name and source_name != "全部":
                filter_sources = [source_name]
            else:
                filter_sources = self._get_effective_sources()

            article = db.get_first_unread(source_names=filter_sources)

            if article:
                logger.debug(f"📊 找到未读文章: {article['title'][:30]}...")
                return {"status": "success", "url": article["url"], "id": article["id"]}
            else:
                logger.info("📊 没有找到未读文章")
                return {"status": "success", "found": False}
        except Exception as e:
            logger.error(f"获取未读文章失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e), "found": False}

    def update_tray_status(self, unread: int = None, sync_time: str = None) -> dict:
        """更新托盘菜单的状态信息（未读数量、同步时间）

        Args:
            unread: 未读数量
            sync_time: 同步时间字符串

        Returns:
            {"status": "success"}
        """
        try:
            import main

            main.update_tray_status(unread=unread, sync_time=sync_time)
            return {"status": "success"}
        except Exception as e:
            logger.warning(f"更新托盘状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_tray_unread_count(self) -> dict:
        """获取托盘菜单需要的未读数量（用于后台轮询更新）

        Returns:
            {"status": "success", "count": int}
        """
        try:
            # 🌟 统一来源过滤逻辑
            filter_sources = self._get_effective_sources()
            count = db.get_unread_count(source_names=filter_sources)
            return {"status": "success", "count": count}
        except Exception as e:
            logger.error(f"获取托盘未读数量失败: {e}")
            return {"status": "error", "count": 0}

    def _find_matching_rule_for_article(
        self, article: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """按文章上下文匹配对应的规则。"""
        rule_id = str(article.get("rule_id") or "").strip()
        article_url = str(article.get("url") or "").strip()
        source_name = str(article.get("source_name") or "").strip()
        department = str(article.get("department") or "").strip()

        try:
            if rule_id:
                rule = self._rules_manager.get_rule_by_id(rule_id)
                if rule:
                    return normalize_rule_ai_config(rule)

            rules = self._rules_manager.load_custom_rules()
            if article_url:
                for rule in rules:
                    if str(rule.get("url") or "").strip() == article_url:
                        return normalize_rule_ai_config(rule)

            if source_name:
                for rule in rules:
                    if not rule.get("enabled", True):
                        continue
                    if str(rule.get("task_name") or "").strip() == source_name:
                        return normalize_rule_ai_config(rule)

            if department and department != source_name:
                for rule in rules:
                    if not rule.get("enabled", True):
                        continue
                    if str(rule.get("task_name") or "").strip() == department:
                        return normalize_rule_ai_config(rule)
        except Exception as e:
            logger.debug(f"匹配文章规则失败: {e}")

        return None

    def _resolve_article_ai_config(self, article: Dict[str, Any]) -> Dict[str, Any]:
        """按文章上下文解析 AI 配置，兼容新旧规则字段。"""
        source_type = self._resolve_article_source_type(article)
        article_base = {
            **article,
            "source_type": source_type,
            "task_name": article.get("department") or article.get("source_name") or "",
        }
        article_summary_prompt = str(
            article.get("summary_prompt") or article.get("custom_summary_prompt") or ""
        ).strip()
        article_formatting_prompt = str(article.get("formatting_prompt") or "").strip()
        has_explicit_rss_ai_flags = source_type == "rss" and (
            "enable_ai_formatting" in article or "enable_ai_summary" in article
        )
        enable_ai_formatting = (
            bool(article.get("enable_ai_formatting", False))
            if has_explicit_rss_ai_flags
            else bool(
                article.get("enable_ai_formatting", False) or article_formatting_prompt
            )
        )
        enable_ai_summary = (
            bool(
                article.get(
                    "enable_ai_summary", article.get("require_ai_summary", False)
                )
            )
            if has_explicit_rss_ai_flags
            else bool(
                article.get(
                    "enable_ai_summary", article.get("require_ai_summary", False)
                )
                or article_summary_prompt
            )
        )

        if (
            source_type == "rss"
            and article.get("require_ai_summary", False)
            and not has_explicit_rss_ai_flags
        ):
            # 兼容旧 RSS 文章：历史上一个开关会同时驱动排版和摘要。
            enable_ai_formatting = enable_ai_formatting or not (
                "enable_ai_formatting" in article or "formatting_prompt" in article
            )
            enable_ai_summary = True
            if not article_formatting_prompt and article_summary_prompt:
                article_formatting_prompt = article_summary_prompt

        has_article_ai_snapshot = any(
            [
                article_formatting_prompt,
                article_summary_prompt,
                enable_ai_formatting,
                enable_ai_summary,
            ]
        )
        if has_article_ai_snapshot:
            if source_type == "rss":
                strategy = resolve_rss_rule_strategy(article_base)
                article_formatting_prompt = (
                    article_formatting_prompt
                    or str(strategy.get("effective_formatting_prompt") or "").strip()
                )
                article_summary_prompt = (
                    article_summary_prompt
                    or str(strategy.get("effective_summary_prompt") or "").strip()
                )
            return {
                "source_type": source_type,
                "enable_ai_formatting": enable_ai_formatting,
                "enable_ai_summary": enable_ai_summary,
                "formatting_prompt": article_formatting_prompt,
                "summary_prompt": article_summary_prompt,
            }

        matched_rule = self._find_matching_rule_for_article(article)
        if matched_rule:
            if source_type == "rss":
                matched_rule = attach_rss_strategy_metadata(matched_rule)
                strategy = resolve_rss_rule_strategy(matched_rule)
                return {
                    "source_type": source_type,
                    "enable_ai_formatting": bool(
                        matched_rule.get("enable_ai_formatting", False)
                    ),
                    "enable_ai_summary": bool(
                        matched_rule.get(
                            "enable_ai_summary",
                            matched_rule.get("require_ai_summary", False),
                        )
                    ),
                    "formatting_prompt": str(
                        strategy.get("effective_formatting_prompt") or ""
                    ).strip(),
                    "summary_prompt": str(
                        strategy.get("effective_summary_prompt") or ""
                    ).strip(),
                }
            return {
                "source_type": source_type,
                "enable_ai_formatting": bool(
                    matched_rule.get("enable_ai_formatting", False)
                ),
                "enable_ai_summary": bool(
                    matched_rule.get(
                        "enable_ai_summary",
                        matched_rule.get("require_ai_summary", False),
                    )
                ),
                "formatting_prompt": str(
                    matched_rule.get("formatting_prompt") or ""
                ).strip(),
                "summary_prompt": str(
                    matched_rule.get("summary_prompt")
                    or matched_rule.get("custom_summary_prompt")
                    or ""
                ).strip(),
            }

        return {
            "source_type": source_type,
            "enable_ai_formatting": enable_ai_formatting,
            "enable_ai_summary": enable_ai_summary,
            "formatting_prompt": (
                str(
                    resolve_rss_rule_strategy(article_base).get(
                        "effective_formatting_prompt"
                    )
                    or ""
                ).strip()
                if source_type == "rss"
                else article_formatting_prompt
            ),
            "summary_prompt": (
                str(
                    resolve_rss_rule_strategy(article_base).get(
                        "effective_summary_prompt"
                    )
                    or ""
                ).strip()
                if source_type == "rss"
                else article_summary_prompt
            ),
        }

    def _is_system_content_article(self, article: Optional[Dict[str, Any]]) -> bool:
        rule_id = str((article or {}).get("rule_id") or "").strip()
        return bool(rule_id) and rule_id.startswith(self._SYSTEM_CONTENT_RULE_PREFIX)

    def _resolve_article_source_type(self, article: Dict[str, Any]) -> str:
        """按文章上下文解析来源类型（rss/html）"""
        rule_id = str(article.get("rule_id") or "").strip()
        try:
            if rule_id:
                rule = self._rules_manager.get_rule_by_id(rule_id)
                if rule:
                    rule_type = str(rule.get("source_type") or "").strip().lower()
                    if rule_type in {"rss", "html"}:
                        return rule_type
        except Exception as e:
            logger.debug(f"解析文章来源类型失败: {e}")

        article_type = str(article.get("source_type") or "").strip().lower()
        if article_type in {"rss", "html"}:
            return article_type

        return "html"

    def regenerate_summary(
        self, article_id: int, request_token: int = 0, skip_ai_precheck: bool = False
    ) -> Dict[str, Any]:
        """
        重新生成文章的 AI 总结

        Args:
            article_id: 文章 ID

        Returns:
            {"status": "success/error", "summary": str, "message": str}
        """
        try:
            self._clear_residual_ai_cancel_flags()

            # 1. 从数据库获取文章
            article = db.get_article_by_id(article_id)
            if not article:
                return {"status": "error", "message": "文章不存在"}

            if self._is_system_content_article(article):
                payload = self._build_article_content_payload(
                    article,
                    self._resolve_article_ai_config(article),
                )
                return {
                    "status": "success",
                    "message": "系统内容无需重新生成",
                    "result_kind": "system_noop",
                    **payload,
                }

            # 2. 检查是否有原文内容
            raw_text = article.get("raw_text", "")
            title = article.get("title", "未知标题")

            if not raw_text or len(raw_text.strip()) < 10:
                raw_text = resolve_effective_article_text(
                    raw_text=raw_text,
                    raw_markdown=article.get("raw_markdown", ""),
                    body_html=article.get("body_html", ""),
                )
            if not raw_text or len(raw_text.strip()) < 10:
                return {
                    "status": "error",
                    "message": "原文内容过短或缺失，无法生成总结",
                }

            # 3. 🌟 获取文章专属提示词：优先文章自身，其次按规则反查，最后回退默认提示词
            ai_config = self._resolve_article_ai_config(article)
            source_type = ai_config.get("source_type", "html")
            formatting_prompt = str(ai_config.get("formatting_prompt") or "").strip()
            summary_prompt = str(ai_config.get("summary_prompt") or "").strip()
            enable_ai_formatting = bool(ai_config.get("enable_ai_formatting", False))
            enable_ai_summary = bool(ai_config.get("enable_ai_summary", False))

            # 4. 调用 LLM 服务重新生成总结
            logger.info(f"正在重新生成文章总结: {title}")
            if formatting_prompt or summary_prompt:
                logger.info(
                    "使用 RSS 自定义提示词: format=%s / summary=%s",
                    formatting_prompt[:32] if formatting_prompt else "-",
                    summary_prompt[:32] if summary_prompt else "-",
                )

            summary_cancel_event = threading.Event()
            result_kind = "default"
            if request_token:
                with self._summary_lock:
                    self._active_summary_tokens[article_id] = request_token
                    self._active_summary_events[article_id] = summary_cancel_event

            requires_ai_call = (
                source_type != "rss" or enable_ai_formatting or enable_ai_summary
            )
            if requires_ai_call and not skip_ai_precheck:
                ai_gate = self.validate_ai_prerequisites()
                if ai_gate.get("status") != "success":
                    return {
                        "status": "error",
                        "stage": ai_gate.get("stage", "validation_failed"),
                        "message": ai_gate.get("message", "API 前置检查失败"),
                    }

            enhanced_markdown = str(article.get("enhanced_markdown") or "").strip()
            ai_tags: List[str] = []
            ai_summary = ""

            if source_type == "rss":
                if enable_ai_formatting:
                    enhanced_markdown = self._llm.format_rss_article(
                        title,
                        raw_text,
                        formatting_prompt,
                        priority="manual",
                        cancel_event=summary_cancel_event,
                        use_cache=False,
                    )
                    if enhanced_markdown.startswith(
                        "⚠️"
                    ) or enhanced_markdown.startswith("❌"):
                        new_summary = enhanced_markdown
                    else:
                        if not enable_ai_summary:
                            ai_summary = ""
                            ai_tags = []
                            new_summary = ""
                            result_kind = "rss_formatting"
                        else:
                            rss_summary_result = self._llm.summarize_rss_article(
                                title,
                                enhanced_markdown or raw_text,
                                summary_prompt,
                                priority="manual",
                                cancel_event=summary_cancel_event,
                                use_cache=False,
                            )
                            if rss_summary_result.get("status") != "success":
                                new_summary = str(
                                    rss_summary_result.get("message")
                                    or "⚠️ RSS 总结失败"
                                )
                            else:
                                ai_tags = list(rss_summary_result.get("tags") or [])
                                ai_summary = str(
                                    rss_summary_result.get("summary") or ""
                                ).strip()
                                new_summary = compose_tagged_markdown(
                                    ai_tags, ai_summary
                                )
                                result_kind = "rss_full"
                else:
                    enhanced_markdown = raw_text
                    if not enable_ai_summary:
                        ai_summary = ""
                        ai_tags = []
                        new_summary = ""
                        result_kind = "rss_reset"
                    else:
                        rss_summary_result = self._llm.summarize_rss_article(
                            title,
                            raw_text,
                            summary_prompt,
                            priority="manual",
                            cancel_event=summary_cancel_event,
                            use_cache=False,
                        )
                        if rss_summary_result.get("status") != "success":
                            new_summary = str(
                                rss_summary_result.get("message") or "⚠️ RSS 总结失败"
                            )
                        else:
                            ai_tags = list(rss_summary_result.get("tags") or [])
                            ai_summary = str(
                                rss_summary_result.get("summary") or ""
                            ).strip()
                            new_summary = compose_tagged_markdown(ai_tags, ai_summary)
                            result_kind = "rss_summary"
            else:
                new_summary = self._llm.summarize_article(
                    title,
                    raw_text,
                    summary_prompt,
                    priority="manual",
                    cancel_event=summary_cancel_event,
                    content_kind="default",
                    use_cache=False,
                )

            # 5. 检查是否生成成功
            if new_summary.startswith("⚠️") or new_summary.startswith("❌"):
                clean_message = strip_emoji(new_summary)
                if "用户取消" in clean_message:
                    return {
                        "status": "cancelled",
                        "message": "已中断 AI 总结",
                    }
                return {"status": "error", "message": clean_message}

            new_summary = strip_emoji(new_summary)

            # 6. 更新数据库
            if source_type == "rss":
                success = db.update_rss_ai_content(
                    article_id,
                    enhanced_markdown,
                    ai_summary,
                    ai_tags,
                )
            else:
                success = db.update_summary(article_id, new_summary)
            if not success:
                return {"status": "error", "message": "更新数据库失败"}

            cleared_annotation_view_modes = (
                self._get_annotation_view_modes_to_reset_on_regeneration(
                    article,
                    result_kind,
                )
            )
            cleared_annotation_count = 0
            if cleared_annotation_view_modes:
                raw_cleared_annotation_count = (
                    db.delete_article_annotations_by_view_modes(
                        article_id,
                        cleared_annotation_view_modes,
                    )
                )
                if isinstance(raw_cleared_annotation_count, bool):
                    cleared_annotation_count = int(raw_cleared_annotation_count)
                elif isinstance(raw_cleared_annotation_count, (int, float)):
                    cleared_annotation_count = int(raw_cleared_annotation_count)
                else:
                    cleared_annotation_count = 0

            logger.info(f"文章总结重新生成成功: {title}")
            payload = self._build_article_content_payload(
                article,
                ai_config,
                source_type=source_type,
                summary=new_summary,
                ai_summary=ai_summary,
                ai_tags=ai_tags,
                raw_text=str(article.get("raw_text") or raw_text or ""),
                raw_markdown=str(
                    article.get("raw_markdown")
                    or article.get("raw_text")
                    or raw_text
                    or ""
                ),
                enhanced_markdown=enhanced_markdown,
            )
            return {
                "status": "success",
                **payload,
                "ai_tag_items": build_tag_items(payload.get("ai_tags") or []),
                "result_kind": result_kind,
                "cleared_annotation_view_modes": cleared_annotation_view_modes,
                "cleared_annotation_count": cleared_annotation_count,
            }

        except Exception as e:
            logger.error(f"重新生成总结失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if request_token:
                with self._summary_lock:
                    current_token = self._active_summary_tokens.get(article_id)
                    if current_token == request_token:
                        self._active_summary_tokens.pop(article_id, None)
                        self._active_summary_events.pop(article_id, None)

    def import_custom_font(self) -> Dict[str, Any]:
        """呼出系统原生文件选择器，导入并持久化自定义字体"""
        try:
            file_types = ("字体文件 (*.ttf;*.otf;*.woff;*.woff2)", "所有文件 (*.*)")
            # 呼出文件选择对话框
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,  # type: ignore
                allow_multiple=False,
                file_types=file_types,
            )

            if not result:
                return {"status": "cancelled"}

            source_path = result[0]
            ext = os.path.splitext(source_path)[1].lower()
            if ext not in [".ttf", ".otf", ".woff", ".woff2"]:
                return {
                    "status": "error",
                    "message": "不支持的字体格式，仅支持 ttf/otf/woff/woff2",
                }

            # 将字体安全拷贝到 frontend/fonts 目录下，供本地 HTTP 服务器读取
            frontend_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend"
            )
            fonts_dir = os.path.join(frontend_dir, "fonts")
            os.makedirs(fonts_dir, exist_ok=True)

            # 统一重命名避免编码问题
            target_filename = f"custom_font{ext}"
            target_path = os.path.join(fonts_dir, target_filename)

            shutil.copy2(source_path, target_path)

            return {
                "status": "success",
                "font_path": f"fonts/{target_filename}",
                "font_name": os.path.basename(source_path),
            }
        except Exception as e:
            logger.error(f"导入字体失败: {e}")
            return {"status": "error", "message": str(e)}



    def _resolve_platform_download_meta(self, version_data: Dict[str, Any]) -> Dict[str, Any]:
        """解析并返回当前平台对应的下载元数据。"""
        downloads = version_data.get("downloads", {})
        current_platform = platform.system().lower()

        if current_platform == "darwin":
            return downloads.get("macos", {})
        elif current_platform == "windows":
            return downloads.get("windows", {})
        else:
            return {}

    def _system_content_rule_id(self, key: str) -> str:
        return f"{self._SYSTEM_CONTENT_RULE_PREFIX}{str(key or '').strip()}"

    def _system_content_key_from_rule_id(self, rule_id: str) -> str:
        normalized = str(rule_id or "").strip()
        if normalized.startswith(self._SYSTEM_CONTENT_RULE_PREFIX):
            return normalized[len(self._SYSTEM_CONTENT_RULE_PREFIX):]
        return normalized

    def _system_content_internal_url(self, key: str) -> str:
        return f"microflow://system/{str(key or '').strip()}"

    def _build_system_content_storage_url(self, key: str, raw_url: str) -> str:
        """
        为系统内容生成可落库的唯一 URL。

        articles.url 使用唯一索引，而系统公告 / 关于 / 免责声明经常共用同一个
        version.json 地址。直接写入会发生 REPLACE 覆盖，导致系统内容只剩部分条目。
        这里对 http(s) URL 追加稳定 query 参数；项目内部的 URL 规范化会移除 fragment，
        但会保留普通 query，因此 query 方式能稳定穿透到数据库层。
        """
        clean_url = str(raw_url or "").strip()
        if not clean_url:
            return self._system_content_internal_url(key)

        parsed = urlsplit(clean_url)
        if parsed.scheme in {"http", "https"}:
            query_items = list(parse_qsl(parsed.query, keep_blank_values=True))
            normalized_key = str(key or "").strip()
            query_items = [
                (name, value)
                for name, value in query_items
                if str(name or "").strip() != "__microflow_system"
            ]
            query_items.append(("__microflow_system", normalized_key))
            query = urlencode(query_items, doseq=True)
            return urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment)
            )
        return clean_url

    def _normalize_system_publish_time(self, value: Any, fallback: str = "") -> str:
        text = str(value or "").strip()
        if not text:
            text = str(fallback or "").strip()
        if not text:
            text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return f"{text} 00:00:00"
        return text

    def _extract_system_content_text(self, payload: Dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _append_system_content_media_markdown(
        self,
        content: str,
        payload: Dict[str, Any],
        *,
        fallback_alt: str = "",
    ) -> str:
        body = str(content or "").strip()
        image_markdown = self._extract_system_content_text(
            payload, "image_markdown", "media_markdown"
        )
        if image_markdown:
            if image_markdown not in body:
                return f"{body}\n\n{image_markdown}".strip()
            return body

        image_url = self._extract_system_content_text(
            payload,
            "image_url",
            "hero_image_url",
            "cover_image_url",
            "qr_image_url",
            "donate_image_url",
        )
        if not image_url:
            return body
        if image_url in body:
            return body

        image_alt = (
            self._extract_system_content_text(
                payload,
                "image_alt",
                "hero_image_alt",
                "cover_image_alt",
            )
            or str(fallback_alt or "").strip()
            or "系统内容配图"
        )
        image_caption = self._extract_system_content_text(
            payload,
            "image_caption",
            "hero_image_caption",
            "cover_image_caption",
        )
        image_block = f"![{image_alt}]({image_url})"
        if image_caption:
            image_block = f"{image_block}\n\n{image_caption}"
        return f"{body}\n\n{image_block}".strip()

    def _normalize_system_content_tags(
        self, value: Any, fallback_tags: Optional[List[str]] = None
    ) -> List[str]:
        fallback_tags = fallback_tags or []
        if isinstance(value, list):
            tags = [str(item or "").strip() for item in value]
        elif isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    tags = [str(item or "").strip() for item in parsed]
                else:
                    tags = [value.strip()]
            except Exception:
                tags = [
                    part.strip()
                    for part in re.split(r"[、,，|/]+", value)
                    if part.strip()
                ]
        else:
            tags = []

        normalized: List[str] = []
        for tag in [*tags, *fallback_tags]:
            clean_tag = str(tag or "").replace("【", "").replace("】", "").strip()
            if clean_tag and clean_tag not in normalized:
                normalized.append(clean_tag)
        return normalized[:3]

    def _summarize_system_content(self, text: str, fallback: str = "") -> str:
        plain_text = BeautifulSoup(str(text or ""), "html.parser").get_text("\n")
        compact = " ".join(plain_text.split()).strip()
        if compact:
            return compact[:120]
        return str(fallback or "").strip()

    def _build_default_system_content_payloads(
        self, version_data: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        current_version = str(version_data.get("version") or self.CURRENT_VERSION).strip()
        release_date = str(version_data.get("release_date") or "").strip()
        notes = str(version_data.get("notes") or "").strip()
        announcement = version_data.get("announcement", {}) or {}
        download_meta = self._resolve_platform_download_meta(version_data)
        feedback_email = (
            self._extract_system_content_text(version_data, "feedback_email")
            or self._extract_system_content_text(
                version_data.get("feedback", {}) if isinstance(version_data, dict) else {},
                "email",
            )
            or self._SYSTEM_CONTENT_DEFAULT_FEEDBACK_EMAIL
        )
        fallback_publish_time = self._normalize_system_publish_time(
            announcement.get("publish_time") or release_date
        )
        download_url = str(download_meta.get("url") or "").strip()

        changelog_lines = [
            "## 当前版本",
            f"- 版本：{current_version or self.CURRENT_VERSION}",
        ]
        if release_date:
            changelog_lines.append(f"- 发布时间：{release_date}")
        if notes:
            changelog_lines.extend(["", "## 最近更新", notes])
        if download_url:
            changelog_lines.extend(["", "## 下载地址", download_url])

        feedback_lines = [
            f"如需反馈 Bug、提交建议或申请排查，请发送邮件至 <contact>{feedback_email}</contact>。",
            "",
            "建议附上以下信息，便于更快定位问题：",
            "1. 当前版本号",
            "2. 涉及的数据源、文章标题或规则名称",
            "3. 报错信息、截图或录屏",
            "4. 可复现的操作步骤",
        ]

        disclaimer_lines = [
            "MicroFlow 仅作为本地信息聚合与阅读辅助工具，不代表任何官方发布渠道。",
            "",
            "1. 校内通知、公文、RSS 与网页内容的版权、解释权与最终效力归原发布方所有。",
            "2. AI 摘要、标签、排版与增强结果仅用于提升阅读效率，不构成官方结论、法律意见或执行依据。",
            "3. 日期、地点、联系人、附件、原文链接等关键信息，请始终以原始发布页面为准。",
            "4. 如因网络、站点改版、模型限制或第三方服务异常导致内容缺失、延迟或偏差，MicroFlow 不对此承担直接责任。",
        ]

        return {
            "announcement": {
                "title": announcement.get("title") or f"{current_version or 'MicroFlow'} 最新公告",
                "summary": announcement.get("summary") or notes or "欢迎使用 MicroFlow。",
                "content": announcement.get("content")
                or (
                    "欢迎使用 MicroFlow。\n\n"
                    "请先在设置中完成 AI Base URL、API Key 与 Model Name 配置，"
                    "随后即可体验摘要、增强排版、RSS 订阅与自定义数据源等能力。"
                ),
                "publish_time": fallback_publish_time,
                "url": announcement.get("url") or "https://github.com/AmaziiingChen/microflow",
                "category": announcement.get("version") or current_version or "系统公告",
                "tags": announcement.get("tags") or ["系统公告", current_version or "MicroFlow"],
                "department": "系统公告",
                "source_name": "系统通知",
            },
            "changelog": {
                "title": "关于 MicroFlow",
                "summary": notes or "查看当前版本信息与更新日志。",
                "content": "\n".join(changelog_lines).strip(),
                "publish_time": fallback_publish_time,
                "url": announcement.get("url") or download_url or "https://github.com/AmaziiingChen/microflow",
                "category": current_version or "版本更新日志",
                "tags": ["关于", "更新日志", current_version or "MicroFlow"],
                "department": "关于 MicroFlow",
                "source_name": self._SYSTEM_CONTENT_SOURCE_NAME,
            },
            "feedback": {
                "title": "反馈与建议",
                "summary": f"欢迎通过邮件联系：{feedback_email}",
                "content": "\n".join(feedback_lines).strip(),
                "publish_time": fallback_publish_time,
                "url": f"mailto:{feedback_email}",
                "category": "联系方式",
                "tags": ["反馈", "建议", "邮箱"],
                "department": "反馈与建议",
                "source_name": self._SYSTEM_CONTENT_SOURCE_NAME,
                "email": feedback_email,
            },
            "disclaimer": {
                "title": "免责声明",
                "summary": "使用前请阅读免责声明与信息使用边界。",
                "content": "\n".join(disclaimer_lines).strip(),
                "publish_time": fallback_publish_time,
                "url": "https://github.com/AmaziiingChen/microflow",
                "category": "使用说明",
                "tags": ["免责声明", "使用边界"],
                "department": "免责声明",
                "source_name": self._SYSTEM_CONTENT_SOURCE_NAME,
            },
        }

    def _extract_remote_system_content_payloads(
        self, version_data: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        extracted: Dict[str, Dict[str, Any]] = {}
        if not isinstance(version_data, dict):
            return extracted

        alias_map = {
            "announcement": "announcement",
            "changelog": "changelog",
            "about": "changelog",
            "release_notes": "changelog",
            "feedback": "feedback",
            "disclaimer": "disclaimer",
        }

        bundle = version_data.get("system_contents")
        if isinstance(bundle, dict):
            for raw_key, raw_value in bundle.items():
                target_key = alias_map.get(str(raw_key or "").strip())
                if target_key and isinstance(raw_value, dict):
                    extracted[target_key] = dict(raw_value)
        elif isinstance(bundle, list):
            for item in bundle:
                if not isinstance(item, dict):
                    continue
                target_key = alias_map.get(str(item.get("key") or "").strip())
                if target_key:
                    extracted[target_key] = dict(item)

        for raw_key, target_key in alias_map.items():
            payload = version_data.get(raw_key)
            if isinstance(payload, dict):
                extracted[target_key] = dict(payload)

        return extracted

    def _normalize_system_content_entry(
        self,
        key: str,
        default_payload: Dict[str, Any],
        override_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged = {**(default_payload or {})}
        if isinstance(override_payload, dict):
            merged.update(override_payload)

        publish_time = self._normalize_system_publish_time(
            merged.get("publish_time") or merged.get("updated_at")
        )
        content = self._extract_system_content_text(
            merged,
            "content",
            "markdown",
            "body_markdown",
            "body",
        ) or str(default_payload.get("content") or "").strip()
        summary = self._extract_system_content_text(merged, "summary")
        tags = self._normalize_system_content_tags(
            merged.get("tags"),
            default_payload.get("tags") if isinstance(default_payload.get("tags"), list) else [],
        )
        title = self._extract_system_content_text(merged, "title") or str(
            default_payload.get("title") or key
        ).strip()
        content = self._append_system_content_media_markdown(
            content,
            merged,
            fallback_alt=title,
        )
        category = self._extract_system_content_text(
            merged, "category", "version", "label"
        ) or str(default_payload.get("category") or "").strip()
        url = self._extract_system_content_text(merged, "url") or str(
            default_payload.get("url") or self._system_content_internal_url(key)
        ).strip()
        url = self._build_system_content_storage_url(key, url)
        department = self._extract_system_content_text(merged, "department") or str(
            default_payload.get("department") or title
        ).strip()
        source_name = self._extract_system_content_text(merged, "source_name") or str(
            default_payload.get("source_name") or self._SYSTEM_CONTENT_SOURCE_NAME
        ).strip()
        feedback_email = self._extract_system_content_text(merged, "email")

        if not summary:
            summary = self._summarize_system_content(
                content,
                fallback=str(default_payload.get("summary") or "").strip(),
            )

        return {
            "key": key,
            "rule_id": self._system_content_rule_id(key),
            "title": title,
            "summary": summary,
            "content": content,
            "exact_time": publish_time,
            "date": publish_time.split(" ")[0] if publish_time else "",
            "category": category,
            "department": department,
            "source_name": source_name,
            "source_type": "rss",
            "url": url,
            "tags": tags,
            "enable_ai_formatting": True,
            "enable_ai_summary": True,
            "feedback_email": feedback_email,
        }

    def _build_system_content_entries(
        self, version_data: Dict[str, Any]
    ) -> Dict[str, Dict[str, Any]]:
        defaults = self._build_default_system_content_payloads(version_data)
        overrides = self._extract_remote_system_content_payloads(version_data)
        return {
            key: self._normalize_system_content_entry(
                key,
                defaults.get(key, {}),
                overrides.get(key),
            )
            for key in self._SYSTEM_CONTENT_ORDER
        }

    def _system_content_matches_existing(
        self, existing_article: Dict[str, Any], normalized_entry: Dict[str, Any]
    ) -> bool:
        existing_body = str(
            existing_article.get("ai_summary")
            or existing_article.get("enhanced_markdown")
            or existing_article.get("raw_markdown")
            or existing_article.get("raw_text")
            or ""
        ).strip()
        existing_tags = self._extract_article_ai_tags(
            existing_article,
            str(existing_article.get("summary") or "").strip(),
        )
        return (
            str(existing_article.get("title") or "").strip()
            == str(normalized_entry.get("title") or "").strip()
            and str(existing_article.get("exact_time") or "").strip()
            == str(normalized_entry.get("exact_time") or "").strip()
            and str(existing_article.get("category") or "").strip()
            == str(normalized_entry.get("category") or "").strip()
            and str(existing_article.get("department") or "").strip()
            == str(normalized_entry.get("department") or "").strip()
            and str(existing_article.get("source_name") or "").strip()
            == str(normalized_entry.get("source_name") or "").strip()
            and str(existing_article.get("url") or "").strip()
            == str(normalized_entry.get("url") or "").strip()
            and existing_body == str(normalized_entry.get("content") or "").strip()
            and existing_tags == list(normalized_entry.get("tags") or [])
        )

    def _sync_system_content_entries(self, version_data: Optional[Dict[str, Any]]) -> None:
        existing_articles = {
            str(item.get("rule_id") or "").strip(): item
            for item in db.get_articles_by_rule_prefix(self._SYSTEM_CONTENT_RULE_PREFIX)
        }
        has_complete_existing_entries = all(
            self._system_content_rule_id(key) in existing_articles
            for key in self._SYSTEM_CONTENT_ORDER
        )
        if not version_data and has_complete_existing_entries:
            return

        entries = self._build_system_content_entries(version_data or {})

        for key in self._SYSTEM_CONTENT_ORDER:
            entry = entries.get(key)
            if not entry:
                continue
            rule_id = str(entry.get("rule_id") or "").strip()
            existing = existing_articles.get(rule_id)
            if existing and self._system_content_matches_existing(existing, entry):
                continue

            if existing:
                db.delete_articles_by_rule_id(rule_id)

            summary_markdown = (
                compose_tagged_markdown(
                    list(entry.get("tags") or []),
                    str(entry.get("summary") or entry.get("content") or "").strip(),
                )
                or str(entry.get("summary") or entry.get("content") or "").strip()
            )
            body_markdown = str(entry.get("content") or "").strip()

            db.insert_or_update_article_sync(
                title=str(entry.get("title") or "").strip(),
                url=str(entry.get("url") or "").strip(),
                date=str(entry.get("date") or "").strip(),
                exact_time=str(entry.get("exact_time") or "").strip(),
                category=str(entry.get("category") or "").strip(),
                department=str(entry.get("department") or "").strip(),
                attachments="",
                summary=summary_markdown,
                raw_content=body_markdown,
                source_name=str(entry.get("source_name") or self._SYSTEM_CONTENT_SOURCE_NAME).strip(),
                rule_id=rule_id,
                custom_summary_prompt="",
                source_type="rss",
                raw_markdown=body_markdown,
                enhanced_markdown=body_markdown,
                ai_summary=body_markdown,
                ai_tags=list(entry.get("tags") or []),
                formatting_prompt="",
                summary_prompt="",
                enable_ai_formatting=True,
                enable_ai_summary=True,
                content_blocks=[],
                image_assets=[],
            )

    def _get_system_content_index_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        articles = db.get_articles_by_rule_prefix(self._SYSTEM_CONTENT_RULE_PREFIX)
        article_by_rule_id = {
            str(item.get("rule_id") or "").strip(): item for item in articles
        }

        for key in self._SYSTEM_CONTENT_ORDER:
            article = article_by_rule_id.get(self._system_content_rule_id(key))
            if not article:
                continue
            detail_payload = self._build_article_detail_response(article)
            detail_payload["system_content_key"] = key
            detail_payload["is_announcement"] = key == "announcement"
            if key == "feedback":
                url = str(detail_payload.get("url") or "").strip()
                if url.lower().startswith("mailto:"):
                    detail_payload["feedback_email"] = url.split(":", 1)[1]
            payload[key] = detail_payload

        return payload

    def get_system_content_index(self) -> Dict[str, Any]:
        """返回已同步到本地数据库的系统内容索引。"""
        try:
            self._sync_system_content_entries(self._version_info or {})
            return {"status": "success", "data": self._get_system_content_index_payload()}
        except Exception as e:
            logger.error(f"读取系统内容索引失败: {e}")
            return {"status": "error", "message": "读取系统内容失败"}

    def _build_update_payload(self, version_data: Dict[str, Any]) -> Dict[str, Any]:
        """构建统一的软件更新信息结构。"""
        latest_version = str(version_data.get("version", "") or "").strip()
        min_supported_version = str(version_data.get("min_supported_version", "") or "").strip()
        release_date = str(version_data.get("release_date", "") or "").strip()
        platform_download = self._resolve_platform_download_meta(version_data)
        self._sync_system_content_entries(version_data)

        # 🌟 强制更新逻辑：
        # 1. 远程直接指定 force_update
        # 2. 当前版本低于远程指定的最低支持版本
        is_forced = bool(version_data.get("force_update", False))
        if min_supported_version and self.CURRENT_VERSION < min_supported_version:
            is_forced = True

        # 🌟 更新日志/公告逻辑
        announcement = version_data.get("announcement", {})
        note_candidates = [
            str(announcement.get("content", "") or "").strip(),
            str(announcement.get("summary", "") or "").strip(),
            f"发布时间: {release_date}" if release_date else "",
            "有新版本可用",
        ]
        notes = next((item for item in note_candidates if item), "有新版本可用")

        return {
            "has_update": bool(
                latest_version and latest_version > self.CURRENT_VERSION
            ),
            "current_version": self.CURRENT_VERSION,
            "version": latest_version,
            "latest_version": latest_version,
            "release_date": release_date,
            "notes": notes,
            "download_url": str(platform_download.get("url", "") or "").strip(),
            "download_sha256": str(platform_download.get("sha256", "") or "").strip(),
            "download_size": platform_download.get("size"),
            "force_update": is_forced,
            "announcement": announcement,  # 🌟 传递完整的公告对象给前端
            "system_contents": self._get_system_content_index_payload(),
        }

    def check_software_update(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        检查软件更新（使用缓存的版本信息）

        如果缓存为空，则发起网络请求
        """
        # 🌟 优先使用缓存
        data = self._version_info
        if not data or force_refresh:
            res = self.get_version_info(force_refresh=True)
            if res.get("status") == "success":
                data = self._version_info
            else:
                return {
                    "has_update": False,
                    "current_version": self.CURRENT_VERSION,
                    "error": res.get("message", "获取版本失败"),
                }

        if not data:
            return {"has_update": False, "current_version": self.CURRENT_VERSION}

        return self._build_update_payload(data)

    # 🔐 离线存活期常量：7 天（单位：秒）
    OFFLINE_TTL_SECONDS = 7 * 24 * 3600

    def perform_startup_check(self) -> Dict[str, Any]:
        """
        启动时执行的安全检查（唯一请求 version.json 的入口）

        检查步骤：
        1. 检查本地锁定状态（isLocked）和签名验证
        2. 请求远程 version.json，缓存到实例属性
        3. 检查 is_active 字段，如果为 False 执行自毁逻辑
        4. 网络异常时，检查离线 TTL 存活期

        Returns:
            {
                "status": "success" | "locked" | "network_error",
                "reason": str,
                "has_update": bool,
                "version": str,
                "download_url": str,
                "announcement": dict  # 公告信息，供前端使用
            }
        """
        started_at = time.time()

        def finalize(response_payload: Dict[str, Any]) -> Dict[str, Any]:
            response = dict(response_payload or {})
            has_update = bool(response.get("has_update", False))
            force_update = bool(response.get("force_update", False))
            status = str(response.get("status") or "success").strip() or "success"
            mode = str(response.get("mode") or "").strip()
            if mode == "read_only":
                result_label = "read_only"
            else:
                result_label = status
            self._track_telemetry(
                "startup_check_result",
                {
                    "result": result_label,
                    "has_update": has_update,
                    "force_update": force_update,
                    "response_ms": int(max((time.time() - started_at) * 1000, 0)),
                },
            )
            return response

        # 🌟 步骤 1：重新加载本地配置（触发签名验证）
        self._config_service.load()

        # 🌟 步骤 2：请求远程 version.json（云端最高仲裁）
        try:
            response = requests.get(self._get_version_url(), timeout=5)

            # ===== 场景 A：网络请求成功 =====
            if response.status_code != 200:
                logger.warning(
                    f"启动检查：无法获取远程配置 (HTTP {response.status_code})，默认放行"
                )
                # 非正常 HTTP 状态码，视为网络问题，走离线 TTL 检查
                return self._check_offline_ttl()

            data = response.json()

            # 🌟 缓存版本信息（供后续 check_software_update 和前端使用）
            self._version_info = data
            self._refresh_telemetry_remote_config(data)

            # 🌟 步骤 3：检查 is_active 字段（云端最高仲裁）
            is_active = data.get("is_active", True)

            if is_active is False:
                # 🔐 云端标记为不可用，执行只读锁定逻辑
                kill_reason = data.get("kill_message", "该软件已被禁用")
                logger.warning(
                    f"🚫 启动检查：远程配置标记为不可用，进入只读模式。原因: {kill_reason}"
                )
                self._execute_self_destruct(kill_reason)

                # 🌟 优雅降级：返回成功但附加 read_only 模式
                response = self._build_success_response(data)
                response["mode"] = "read_only"
                response["reason"] = kill_reason
                return finalize(response)

            # 🌟 步骤 4：云端验证成功，解除本地锁定状态
            self._unlock_if_needed()

            # 🌟 步骤 5：更新本地同步时间戳
            self._update_cloud_sync_time()

            # 🌟 步骤 6：一切正常，返回成功响应
            return finalize(self._build_success_response(data))

        except requests.exceptions.Timeout:
            logger.warning("启动检查：请求超时，检查离线 TTL")
            return finalize(self._check_offline_ttl())

        except requests.exceptions.RequestException as e:
            logger.warning(f"启动检查：网络请求失败 ({e})，检查离线 TTL")
            return finalize(self._check_offline_ttl())

        except json.JSONDecodeError as e:
            logger.warning(f"启动检查：JSON 解析失败 ({e})，检查离线 TTL")
            return finalize(self._check_offline_ttl())

        except Exception as e:
            logger.error(f"启动检查：未知错误 ({e})，检查离线 TTL")
            return finalize(self._check_offline_ttl())

    def _check_offline_ttl(self) -> Dict[str, Any]:
        """
        检查离线 TTL 存活期（网络异常时调用）

        决策逻辑：
        1. 如果本地配置已锁定，直接返回锁定状态
        2. 如果超过 7 天未同步，触发锁定
        3. 如果在 TTL 期内，放行

        Returns:
            响应字典
        """
        # 步骤 1：检查本地锁定状态
        if self._config_service.current.is_locked:
            logger.warning("🚫 离线检查：配置校验失败或软件已被锁定，进入只读模式")
            response = self._build_success_response({})
            response["mode"] = "read_only"
            response["reason"] = "配置校验失败或软件已被锁定"
            return response

        # 步骤 2：检查 TTL
        last_sync = self._config_service.current.last_cloud_sync_time
        current_time = time.time()
        elapsed_seconds = current_time - last_sync

        logger.info(
            f"🔐 离线 TTL 检查：上次同步时间 = {last_sync:.0f}，已过 {elapsed_seconds / 86400:.1f} 天"
        )

        if elapsed_seconds > self.OFFLINE_TTL_SECONDS:
            # 超过 7 天未连接授权服务器，触发只读锁定
            logger.warning(
                f"🚫 离线检查：已连续 {elapsed_seconds / 86400:.1f} 天未连接授权服务器，进入只读模式"
            )
            self._execute_self_destruct(
                "已连续7天未连接授权服务器，为保障安全已暂停服务"
            )
            response = self._build_success_response({})
            response["mode"] = "read_only"
            response["reason"] = "已连续7天未连接授权服务器，为保障安全已暂停服务"
            return response

        # 步骤 3：在 TTL 期内，放行
        remaining_days = (self.OFFLINE_TTL_SECONDS - elapsed_seconds) / 86400
        logger.info(f"离线检查：TTL 有效，剩余 {remaining_days:.1f} 天")
        return self._build_success_response({})

    def _unlock_if_needed(self) -> bool:
        """
        解除本地锁定状态（云端验证成功后调用）

        当云端 is_active=True 时，解除之前的只读锁定，恢复正常功能。
        """
        if self._config_service.current.is_locked:
            try:
                logger.info("检测到本地锁定状态，尝试解锁...")
                current_config = self._config_service.to_dict()
                current_config["isLocked"] = False
                current_config["lastCloudSyncTime"] = time.time()
                saved = self._config_service.save(current_config)
                if not saved:
                    logger.error("解除锁定失败：配置服务拒绝写回解锁状态")
                    return False
                # 重新加载配置，确保内存中的状态同步
                self._config_service.load()
                logger.info("🔓 云端验证成功，已解除只读锁定，恢复正常模式")
                try:
                    self._enqueue_js(
                        "if(window.onReadOnlyRecovered) window.onReadOnlyRecovered();"
                    )
                except Exception as notify_err:
                    logger.debug(f"通知前端退出只读模式失败: {notify_err}")
                return True
            except Exception as e:
                logger.error(f"解除锁定失败: {e}")
        return False

    def _update_cloud_sync_time(self) -> None:
        """
        更新云端同步时间戳（云端验证成功后调用）
        """
        try:
            current_config = self._config_service.to_dict()
            current_config["lastCloudSyncTime"] = time.time()
            self._config_service.save(current_config)
            logger.info("🔐 已更新云端同步时间戳")
        except Exception as e:
            logger.error(f"更新云端同步时间戳失败: {e}")

    def _execute_self_destruct(self, reason: str) -> None:
        """
        执行自毁逻辑：将配置锁定，禁止软件运行

        Args:
            reason: 禁用原因
        """
        logger.critical(f"🔒 执行自毁逻辑，原因: {reason}")

        # 锁定配置文件
        try:
            current_config = self._config_service.to_dict()
            current_config["isLocked"] = True
            self._config_service.save(current_config)
            logger.info("已将配置标记为锁定")
        except Exception as e:
            logger.error(f"锁定配置失败: {e}")

    def _build_success_response(self, version_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建成功响应，包含版本更新信息和公告

        Args:
            version_data: 从远程获取的版本数据

        Returns:
            成功响应字典，字段与 version.json 结构对齐
        """
        self._sync_system_content_entries(version_data)
        response: Dict[str, Any] = {
            "status": "success",
            "has_update": False,
            "announcement": version_data.get("announcement", {}),
            "current_version": self.CURRENT_VERSION,
            "system_contents": self._get_system_content_index_payload(),
        }

        # 检查版本更新
        response.update(self._build_update_payload(version_data))

        return response

    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计数据"""
        if self._performance_monitor is None:
            return {
                "error": "performance_monitor_unavailable",
                "message": "性能监控依赖未安装，当前功能不可用",
            }
        try:
            return self._performance_monitor.get_current_stats()
        except Exception as e:
            logger.error(f"获取性能统计失败: {e}")
            return {"error": str(e)}

    def reset_performance_stats(self) -> Dict[str, str]:
        """重置性能统计"""
        if self._performance_monitor is None:
            return {
                "status": "error",
                "message": "性能监控依赖未安装，无法重置统计",
            }
        try:
            self._performance_monitor.reset_stats()
            return {"status": "success", "message": "性能统计已重置"}
        except Exception as e:
            logger.error(f"重置性能统计失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_version_info(self, force_refresh: bool = False) -> Dict[str, Any]:
        """获取云端版本信息（支持 ETag 304 缓存协商）"""
        # 1. 优先使用内存缓存（如果不强制刷新）
        if self._version_info and not force_refresh:
            self._refresh_telemetry_remote_config(self._version_info)
            self._sync_system_content_entries(self._version_info)
            return {
                "status": "success",
                **self._version_info,
                "system_contents": self._get_system_content_index_payload(),
            }

        # 2. 发起真实的网络请求（携带 304 缓存协商头）
        headers = {}
        if self._version_etag:
            headers["If-None-Match"] = self._version_etag

        try:
            response = requests.get(self._get_version_url(), timeout=5, headers=headers)

            # 3. 命中 304 缓存，云端文件未变，直接返回内存数据
            if response.status_code == 304:
                logger.debug("🌐 安全心跳：命中 304 缓存，云端配置未变更，免流放行")
                if self._version_info and self._version_info.get("is_active", True):
                    self._unlock_if_needed()
                    self._update_cloud_sync_time()
                self._refresh_telemetry_remote_config(self._version_info)
                self._sync_system_content_entries(self._version_info)
                return {
                    "status": "success",
                    **self._version_info,
                    "system_contents": self._get_system_content_index_payload(),
                }

            # 4. 云端文件有更新，或者首次请求
            if response.status_code == 200:
                data = response.json()
                self._version_info = data
                self._version_etag = response.headers.get("ETag")  # 记录最新 ETag
                if data.get("is_active", True):
                    self._unlock_if_needed()
                    self._update_cloud_sync_time()
                self._refresh_telemetry_remote_config(data)
                self._sync_system_content_entries(data)
                return {
                    "status": "success",
                    **data,
                    "system_contents": self._get_system_content_index_payload(),
                }

            return {
                "status": "error",
                "message": f"请求异常，状态码: {response.status_code}",
            }
        except Exception as e:
            logger.debug(f"获取版本信息网络异常: {e}")
            # 弱网兜底：如果曾经成功获取过，退级使用旧缓存
            if self._version_info:
                self._refresh_telemetry_remote_config(self._version_info)
                self._sync_system_content_entries(self._version_info)
                return {
                    "status": "success",
                    **self._version_info,
                    "system_contents": self._get_system_content_index_payload(),
                }
            return {"status": "error", "message": "无法获取版本信息"}

    def set_window_on_top(self, is_on_top: bool):
        """前端调用：切换窗口置顶状态

        pywebview 的 API 调用本身就在正确的线程中执行，
        直接设置即可，无需额外的线程处理。
        """
        if not self._window:
            return {"status": "error", "message": "窗口未初始化"}

        try:
            # 🌟 Windows 平台特殊处理：使用异步方式避免卡死
            if platform.system() == "Windows":
                import threading
                def _set_on_top():
                    try:
                        self._window.on_top = is_on_top
                        logger.info(f"窗口置顶状态已设置为 {is_on_top}")
                    except Exception as e:
                        logger.error(f"Windows 置顶设置失败: {e}")
                threading.Thread(target=_set_on_top, daemon=True).start()
            else:
                # macOS 直接设置
                self._window.on_top = is_on_top
                logger.info(f"窗口置顶状态已设置为 {is_on_top}")

            return {"status": "success", "is_pinned": is_on_top}

        except Exception as e:
            logger.error(f"设置窗口置顶失败: {e}")
            return {"status": "error", "message": str(e)}

    def ensure_macos_drag_region(self, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """前端加载完成后重装 macOS 原生拖动热区。"""
        if platform.system() != "Darwin":
            return {"status": "ignored", "message": "non-macos"}

        if not self._window:
            return {"status": "error", "message": "窗口未初始化"}

        try:
            main_mod = sys.modules.get("__main__")
            if main_mod is None:
                return {"status": "error", "message": "主模块不可用"}

            layout_mode = "single"
            exclusion_rects = []
            if isinstance(options, dict):
                raw_layout = str(options.get("layout") or "").strip().lower()
                if raw_layout in {"dual", "settings_split"}:
                    layout_mode = raw_layout

                raw_exclusion_rects = options.get("exclusion_rects")
                if isinstance(raw_exclusion_rects, list):
                    for item in raw_exclusion_rects:
                        if not isinstance(item, dict):
                            continue
                        try:
                            rect_x = float(item.get("x", 0.0))
                            rect_y = float(item.get("y", 0.0))
                            rect_w = float(item.get("width", 0.0))
                            rect_h = float(item.get("height", 0.0))
                        except Exception:
                            continue

                        if rect_w <= 0 or rect_h <= 0:
                            continue

                        exclusion_rects.append(
                            {
                                "x": rect_x,
                                "y": rect_y,
                                "width": rect_w,
                                "height": rect_h,
                            }
                        )

            try:
                setattr(self._window, "_macos_drag_layout_mode", layout_mode)
                setattr(self._window, "_macos_drag_exclusion_rects", exclusion_rects)
            except Exception:
                pass

            # 🌟 已移除全尺寸窗口功能，使用系统默认窗口样式
            install_drag_strip = getattr(main_mod, "install_macos_drag_strip", None)

            if callable(install_drag_strip):
                install_drag_strip(self._window)
            else:
                return {"status": "error", "message": "拖动热区安装器不可用"}

            return {"status": "success", "layout": layout_mode}
        except Exception as e:
            logger.error(f"重装 macOS 拖动热区失败: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    def get_local_ai_icon(self, model_name: str) -> Dict[str, Any]:
        """
        🌟 增强版图标检索：支持模糊匹配与打包路径兼容
        """
        def normalize_svg_markup(raw_svg: str) -> str:
            svg_text = str(raw_svg or "").strip()
            if not svg_text:
                return ""

            # 清理 XML 声明 / DOCTYPE / 注释，避免嵌入 HTML 后干扰截图序列化
            svg_text = re.sub(r"<\?xml[^>]*\?>\s*", "", svg_text, flags=re.IGNORECASE)
            svg_text = re.sub(r"<!DOCTYPE[^>]*>\s*", "", svg_text, flags=re.IGNORECASE)
            svg_text = re.sub(r"<!--.*?-->\s*", "", svg_text, flags=re.DOTALL)

            match = re.search(r"<svg\b([^>]*)>", svg_text, flags=re.IGNORECASE)
            if not match:
                return svg_text

            svg_attrs = match.group(1) or ""
            if "width=" not in svg_attrs:
                svg_attrs += ' width="100%"'
            if "height=" not in svg_attrs:
                svg_attrs += ' height="100%"'
            if "preserveAspectRatio=" not in svg_attrs:
                svg_attrs += ' preserveAspectRatio="xMidYMid meet"'
            if "overflow=" not in svg_attrs:
                svg_attrs += ' overflow="visible"'

            normalized_open_tag = f"<svg{svg_attrs}>"
            return re.sub(
                r"<svg\b[^>]*>",
                normalized_open_tag,
                svg_text,
                count=1,
                flags=re.IGNORECASE,
            )

        # 1. 提取品牌关键词
        brand_key = model_name.lower().split("-")[0]

        # 2. 别名映射（可根据需要增删）
        brand_map = {"gpt": "openai", "claude": "anthropic", "gemini": "google"}
        slug = brand_map.get(brand_key, brand_key)

        # 3. 🚀 打包兼容性路径处理
        # 如果是 PyInstaller 打包后的环境，使用 sys._MEIPASS；否则使用当前文件目录
        if getattr(sys, "frozen", False):
            # 生产环境：指向解压后的临时资源目录
            base_path = getattr(sys, "_MEIPASS", "")  # type: ignore[attr-defined]
        else:
            # 开发环境：指向当前项目根目录
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 确保指向 /data/icons 文件夹
        icons_dir = os.path.join(base_path, "data", "icons")

        # 4. 检索逻辑：优先精准匹配，次选模糊匹配
        svg_content = None

        try:
            if os.path.exists(icons_dir):
                files = os.listdir(icons_dir)

                # 策略 A: 精准匹配 (mimo.svg / icon_mimo.svg)
                target_exact = [f"{slug}.svg", f"icon_{slug}.svg", f"{slug}-color.svg"]
                for f in target_exact:
                    if f in files:
                        with open(
                            os.path.join(icons_dir, f), "r", encoding="utf-8"
                        ) as fs:
                            svg_content = fs.read()
                        break

                # 策略 B: 模糊匹配 (匹配 xiaomimimo.svg)
                if not svg_content:
                    for f in files:
                        if f.endswith(".svg") and slug in f.lower():
                            with open(
                                os.path.join(icons_dir, f), "r", encoding="utf-8"
                            ) as fs:
                                svg_content = fs.read()
                            break

            # 5. 回退逻辑：如果本地 800 个都没中，去云端抓取
            if not svg_content:
                url = f"https://unpkg.com/@lobehub/icons-static-svg@latest/icons/{slug}-color.svg"
                resp = requests.get(url, timeout=3)
                if resp.status_code == 200 and resp.text.startswith("<svg"):
                    svg_content = resp.text
                    # 下载后顺手存进本地库，下次就不用下电了
                    os.makedirs(icons_dir, exist_ok=True)
                    with open(
                        os.path.join(icons_dir, f"{slug}.svg"), "w", encoding="utf-8"
                    ) as f:
                        f.write(svg_content)

            svg_content = normalize_svg_markup(svg_content)

            return {"status": "success", "svg_raw": svg_content or ""}

        except Exception as e:
            return {"status": "error", "message": str(e), "svg_raw": ""}

    # ==================== 🕷️ 动态爬虫规则生成 API ====================

    def generate_custom_spider_rule(
        self,
        url: str,
        task_id: str,
        task_name: str,
        target_fields: list,
        require_ai_summary: bool = False,
        task_purpose: str = "",
        custom_summary_prompt: str = "",
        max_items: Optional[int] = None,
        detail_strategy: str = "detail_preferred",
        body_field: str = "",
        skip_detail: bool = False,
        fetch_strategy: str = "requests_first",
        request_method: str = "get",
        request_body: str = "",
        request_headers: Optional[Dict[str, str]] = None,
        cookie_string: str = "",
        existing_rule_id: str = "",
    ) -> Dict[str, Any]:
        """
        生成自定义爬虫规则

        使用 AI 分析网页结构，自动生成 CSS 选择器规则，并进行沙盒测试验证。

        Args:
            url: 目标网页 URL
            task_id: 任务 ID
            task_name: 任务名称（映射到数据库 department 字段）
            target_fields: 用户想要提取的字段列表
            require_ai_summary: 是否需要对抓取内容进行 AI 摘要
            task_purpose: 任务目的/类别（映射到数据库 category 字段）
            custom_summary_prompt: 🌟 专属 AI 提示词（用于定制摘要输出格式）
            max_items: 🌟 单次抓取最大条目数
            detail_strategy: 🌟 HTML 正文抓取策略（list_only / detail_preferred / hybrid）
            body_field: 🌟 正文来源字段（仅 HTML 爬虫有效）
            skip_detail: 🌟 是否跳过详情页抓取（仅 HTML 爬虫有效）
            fetch_strategy: 🌟 页面抓取策略（requests_first / browser_first / requests_only / browser_only）
            request_method: 🌟 HTML 列表页请求方法（get / post）
            request_body: 🌟 HTML 列表页原始请求体（仅 POST 生效）
            request_headers: 🌟 可选，自定义请求头
            cookie_string: 🌟 可选，Cookie 原始字符串
            existing_rule_id: ♻️ 可选，编辑态下用于加载历史健康快照做恢复生成

        Returns:
            {
                "status": "success/error",
                "rule": dict,          # 生成的完整规则
                "sample_data": list    # 沙盒测试提取的前 3 条数据样本
                "recovery_applied": bool
                "recovery_message": str
            }
        """
        try:
            logger.info(f"🕷️ 收到规则生成请求: task_id={task_id}, url={url}")

            # 参数验证
            if not url or not url.startswith(("http://", "https://")):
                return {"status": "error", "message": "无效的 URL"}

            if not target_fields or not isinstance(target_fields, list):
                return {"status": "error", "message": "target_fields 必须是非空列表"}

            # 调用规则生成服务
            recovery_context = None
            normalized_existing_rule_id = str(existing_rule_id or "").strip()
            if normalized_existing_rule_id:
                try:
                    existing_rule = self._rules_manager.get_rule_by_id(
                        normalized_existing_rule_id
                    )
                    if isinstance(existing_rule, dict) and existing_rule:
                        health = (
                            existing_rule.get("health")
                            if isinstance(existing_rule.get("health"), dict)
                            else {}
                        )
                        recovery_context = {
                            "existing_rule_id": normalized_existing_rule_id,
                            "health": health,
                            "last_known_good_snapshot": (
                                health.get("last_known_good_snapshot")
                                if isinstance(
                                    health.get("last_known_good_snapshot"), dict
                                )
                                else {}
                            ),
                            "current_rule_snapshot": existing_rule,
                        }
                except Exception as recovery_error:
                    logger.debug("加载规则恢复上下文失败: %s", recovery_error)

            result = self._rule_generator.generate_and_test_rule(
                task_id=task_id,
                task_name=task_name,
                url=url,
                target_fields=target_fields,
                require_ai_summary=require_ai_summary,
                task_purpose=task_purpose,
                custom_summary_prompt=custom_summary_prompt,
                max_items=max_items,
                detail_strategy=detail_strategy,
                body_field=body_field,
                skip_detail=skip_detail,
                fetch_strategy=fetch_strategy,
                request_method=request_method,
                request_body=request_body,
                request_headers=request_headers,
                cookie_string=cookie_string,
                recovery_context=recovery_context,
            )

            if result.success:
                rule_payload = result.rule.model_dump() if result.rule else None
                result_page_summary = getattr(result, "page_summary", None)
                result_test_snapshot = getattr(result, "test_snapshot", None)
                if isinstance(rule_payload, dict):
                    if isinstance(result_page_summary, dict):
                        rule_payload["page_summary"] = result_page_summary
                    if isinstance(result_test_snapshot, dict):
                        rule_payload["test_snapshot"] = result_test_snapshot
                return {
                    "status": "success",
                    "rule": rule_payload,
                    "sample_data": result.sample_data,
                    "detail_samples": result.detail_samples,
                    "detail_preview_required": result.detail_preview_required,
                    "detail_preview_passed": result.detail_preview_passed,
                    "detail_preview_message": result.detail_preview_message,
                    "recovery_applied": bool(
                        getattr(result, "recovery_applied", False)
                    ),
                    "recovery_message": str(
                        getattr(result, "recovery_message", "") or ""
                    ),
                    "page_summary": (
                        result_page_summary
                        if isinstance(result_page_summary, dict)
                        else None
                    ),
                    "test_snapshot": (
                        result_test_snapshot
                        if isinstance(result_test_snapshot, dict)
                        else None
                    ),
                }
            else:
                return {"status": "error", "message": result.error_message}

        except Exception as e:
            logger.error(f"生成爬虫规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def confirm_and_save_rule(self, rule_dict: dict) -> Dict[str, Any]:
        """
        确认并保存规则

        用户确认沙盒测试结果无误后，将规则写入持久化存储。

        Args:
            rule_dict: 规则字典（SpiderRuleOutput 格式）

        Returns:
            {"status": "success/error", "message": str}
        """
        try:
            logger.info(f"🕷️ 保存规则: rule_id={rule_dict.get('rule_id')}")

            normalized_rule, error_message = self._normalize_rule_payload_for_save(
                rule_dict
            )
            if error_message or not normalized_rule:
                return {
                    "status": "error",
                    "message": error_message or "规则保存校验失败",
                }

            # 保存规则
            success = self._rules_manager.save_custom_rule(normalized_rule)

            if success:
                self._refresh_dynamic_spiders_after_rule_change()
                logger.info(f"规则保存成功: {normalized_rule.get('rule_id')}")
                return {
                    "status": "success",
                    "message": "规则保存成功",
                    "rule_id": normalized_rule.get("rule_id"),
                }
            else:
                return {"status": "error", "message": "规则保存失败"}

        except Exception as e:
            logger.error(f"保存规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def _refresh_dynamic_spiders_after_rule_change(self) -> None:
        """规则变更后尽快刷新运行时动态爬虫，避免继续沿用旧配置。"""
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is None:
            return
        try:
            scheduler.reload_dynamic_spiders(force=True)
        except Exception as e:
            logger.debug(f"规则保存后刷新动态爬虫失败: {e}")

    def validate_and_save_rss_rule(
        self,
        rule_dict: dict,
        preview_only: bool = False,
        request_token: int = 0,
    ) -> Dict[str, Any]:
        """
        验证并直接保存 RSS 规则（无需 AI 生成选择器）

        RSS 订阅规则不需要 CSS 选择器，直接验证 URL 可达性后保存。

        Args:
            rule_dict: 规则字典，包含 url, task_name 等基础字段

        Returns:
            {"status": "success/error", "message": str, "feed_info": dict}
        """
        import feedparser
        from src.spiders.rss_spider import create_rss_spider_from_rule

        class RssPreviewCancelled(Exception):
            """RSS 预览被用户取消"""

        preview_cancel_event: Optional[threading.Event] = None

        def ensure_preview_not_cancelled() -> None:
            if preview_cancel_event and preview_cancel_event.is_set():
                raise RssPreviewCancelled()

        def build_sample_data(
            rule: dict, limit: int = 1
        ) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
            """从 RSS 规则生成代表样本数据，用于前端预览。"""
            sample_rows: List[Dict[str, Any]] = []

            def build_strategy_payload(strategy: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "profile": strategy.get("profile"),
                    "profile_label": strategy.get("profile_label"),
                    "profile_source": strategy.get("profile_source"),
                    "profile_reason": strategy.get("profile_reason"),
                    "template_id": strategy.get("template_id"),
                    "template_name": strategy.get("template_name"),
                    "template_description": strategy.get("template_description"),
                    "default_max_items": strategy.get("default_max_items"),
                    "effective_max_items": strategy.get("effective_max_items"),
                }

            def is_image_attachment(item: Dict[str, Any]) -> bool:
                item_type = str(item.get("type") or "").strip().lower()
                item_url = str(item.get("url") or "").strip().lower()
                return item_type.startswith("image") or item_url.endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".avif")
                )

            def get_ai_preview_success_message(
                formatting_enabled_flag: bool,
                summary_enabled_flag: bool,
            ) -> str:
                if formatting_enabled_flag and summary_enabled_flag:
                    return "已生成 AI 排版与摘要预览。"
                if formatting_enabled_flag:
                    return "已生成 AI 排版预览。"
                if summary_enabled_flag:
                    return "已生成 AI 摘要预览。"
                return "已生成 AI 预览。"

            preview_rule = normalize_rule_ai_config(rule)
            # 预览模式不应受规则启停状态影响，否则编辑已禁用规则时会拿不到样本。
            preview_rule["enabled"] = True
            ensure_preview_not_cancelled()
            spider = create_rss_spider_from_rule(preview_rule)
            if not spider:
                strategy = resolve_rss_rule_strategy(preview_rule)
                return (
                    sample_rows,
                    build_strategy_payload(strategy),
                    attach_rss_strategy_metadata(preview_rule),
                )

            try:
                articles = spider.fetch_list(limit=limit)
            except Exception as exc:
                logger.debug(f"生成 RSS 预览样本失败: {exc}")
                strategy = resolve_rss_rule_strategy(preview_rule)
                return (
                    sample_rows,
                    build_strategy_payload(strategy),
                    attach_rss_strategy_metadata(preview_rule),
                )
            ensure_preview_not_cancelled()

            resolved_preview_rule = attach_rss_strategy_metadata(
                preview_rule,
                sample_articles=articles,
            )
            strategy = resolve_rss_rule_strategy(
                resolved_preview_rule,
                sample_articles=articles,
            )

            formatting_enabled = (
                bool(preview_rule.get("enable_ai_formatting")) and preview_only
            )
            summary_enabled = (
                bool(preview_rule.get("enable_ai_summary")) and preview_only
            )
            formatting_prompt = str(
                strategy.get("effective_formatting_prompt") or ""
            ).strip()
            summary_prompt = str(strategy.get("effective_summary_prompt") or "").strip()
            ai_enabled = formatting_enabled or summary_enabled
            ai_preview_ready = False
            ai_preview_error = ""
            if ai_enabled:
                ensure_preview_not_cancelled()
                ai_gate = self.validate_ai_prerequisites()
                ensure_preview_not_cancelled()
                ai_preview_ready = ai_gate.get("status") == "success"
                if not ai_preview_ready:
                    ai_preview_error = str(ai_gate.get("message") or "AI 不可用")

            for index, article in enumerate(articles[:limit]):
                ensure_preview_not_cancelled()
                body_text = strip_emoji(str(article.get("body_text") or "")).strip()
                attachments = article.get("attachments") or []
                image_assets = article.get("image_assets") or []
                asset_image_urls = {
                    str(asset.get("url") or "").strip()
                    for asset in image_assets
                    if str(asset.get("url") or "").strip()
                }
                image_urls = set(asset_image_urls)
                image_category_counts = {
                    "cover": 0,
                    "body": 0,
                    "attachment": 0,
                    "external": 0,
                }
                for asset in image_assets:
                    category = str(asset.get("category") or "").strip().lower()
                    if category in image_category_counts:
                        image_category_counts[category] += 1
                for att in attachments:
                    if not is_image_attachment(att):
                        continue
                    clean_url = str(att.get("url") or "").strip()
                    if clean_url:
                        image_urls.add(clean_url)
                        if clean_url not in asset_image_urls:
                            image_category_counts["attachment"] += 1

                preview_signals = analyze_rss_preview_content(
                    body_text,
                    image_count=len(image_urls),
                    attachment_count=len(attachments),
                )
                body_preview = str(preview_signals.get("body_plain_text") or "")
                if len(body_preview) > 180:
                    body_preview = body_preview[:180].rstrip() + "..."
                sample_item = {
                    "title": strip_emoji(str(article.get("title") or "")).strip(),
                    "date": str(article.get("date") or "").strip(),
                    "url": str(article.get("url") or "").strip(),
                    "content_preview": body_preview or "（无正文）",
                    "raw_markdown": body_text,
                    "image_count": len(image_urls),
                    "image_asset_count": len(image_urls),
                    "cover_image_count": image_category_counts["cover"],
                    "body_image_count": image_category_counts["body"],
                    "attachment_image_count": image_category_counts["attachment"],
                    "external_image_count": image_category_counts["external"],
                    "attachment_count": len(attachments),
                    "attachments": attachments,
                    "ai_enabled": ai_enabled,
                    "ai_formatting_enabled": formatting_enabled,
                    "ai_summary_enabled": summary_enabled,
                    "ai_preview_ready": ai_preview_ready,
                    "has_ai_preview": False,
                    **preview_signals,
                }

                if not ai_enabled:
                    sample_item["ai_preview_status"] = "disabled"
                    sample_item["ai_preview_message"] = (
                        "已关闭 AI 预览，本次仅展示结构化解析结果。"
                    )
                elif not ai_preview_ready:
                    sample_item["ai_preview_status"] = "unavailable"
                    sample_item["ai_preview_message"] = (
                        ai_preview_error or "AI 当前不可用，已跳过预览。"
                    )
                elif not sample_item["has_body"]:
                    sample_item["ai_preview_status"] = "skipped"
                    sample_item["ai_preview_message"] = (
                        "未识别到可读正文，已跳过 AI 预览。"
                    )
                else:
                    sample_item["ai_preview_status"] = "pending"
                    sample_item["ai_preview_message"] = "正在生成 AI 预览。"

                if ai_enabled and ai_preview_ready and body_text:
                    ensure_preview_not_cancelled()
                    enhanced_markdown = body_text
                    if formatting_enabled:
                        enhanced_markdown = self._llm.format_rss_article(
                            sample_item["title"],
                            body_text,
                            custom_prompt=formatting_prompt,
                            priority="manual",
                            cancel_event=preview_cancel_event,
                        )
                    if enhanced_markdown.startswith(("⚠️", "❌")):
                        if "用户取消" in enhanced_markdown:
                            raise RssPreviewCancelled()
                        sample_item["ai_preview_error"] = enhanced_markdown
                        sample_item["ai_preview_status"] = "unavailable"
                        sample_item["ai_preview_message"] = enhanced_markdown
                    else:
                        if formatting_enabled:
                            sample_item["enhanced_markdown"] = enhanced_markdown
                            sample_item["has_ai_preview"] = True
                        if summary_enabled:
                            summary_result = self._llm.summarize_rss_article(
                                sample_item["title"],
                                enhanced_markdown or body_text,
                                custom_prompt=summary_prompt,
                                priority="manual",
                                cancel_event=preview_cancel_event,
                            )
                            if summary_result.get("status") == "success":
                                sample_item["ai_summary"] = str(
                                    summary_result.get("summary") or ""
                                ).strip()
                                sample_item["ai_tags"] = list(
                                    summary_result.get("tags") or []
                                )
                                sample_item["ai_tag_items"] = list(
                                    summary_result.get("tag_items")
                                    or build_tag_items(sample_item["ai_tags"])
                                )
                                sample_item["has_ai_preview"] = bool(
                                    sample_item.get("has_ai_preview")
                                    or sample_item["ai_summary"]
                                    or sample_item["ai_tags"]
                                )
                            else:
                                if "用户取消" in str(
                                    summary_result.get("message") or ""
                                ):
                                    raise RssPreviewCancelled()
                                sample_item["ai_preview_error"] = str(
                                    summary_result.get("message") or "AI 预览失败"
                                )
                        if sample_item.get("ai_preview_error") and sample_item.get(
                            "has_ai_preview"
                        ):
                            sample_item["ai_preview_status"] = "warning"
                            sample_item["ai_preview_message"] = (
                                f"AI 预览部分生成：{sample_item['ai_preview_error']}"
                            )
                        elif sample_item.get("ai_preview_error"):
                            sample_item["ai_preview_status"] = "unavailable"
                            sample_item["ai_preview_message"] = str(
                                sample_item["ai_preview_error"]
                            )
                        elif sample_item.get("has_ai_preview"):
                            sample_item["ai_preview_status"] = "ready"
                            sample_item["ai_preview_message"] = (
                                get_ai_preview_success_message(
                                    formatting_enabled,
                                    summary_enabled,
                                )
                            )
                        else:
                            sample_item["ai_preview_status"] = "skipped"
                            sample_item["ai_preview_message"] = (
                                "AI 已启用，但当前样本没有生成可展示结果。"
                            )
                elif ai_enabled and not ai_preview_ready:
                    sample_item["ai_preview_error"] = ai_preview_error

                sample_rows.append(sample_item)

            return sample_rows, build_strategy_payload(strategy), resolved_preview_rule

        try:
            logger.info(f"📡 追加 RSS 规则: {rule_dict.get('task_name', 'unknown')}")
            normalized_rule, error_message = self._normalize_rule_payload_for_save(
                rule_dict,
                expected_source_type="rss",
            )
            if error_message or not normalized_rule:
                return {
                    "status": "error",
                    "message": error_message or "RSS 规则校验失败",
                }
            rule_dict = normalized_rule
            if preview_only and request_token:
                preview_cancel_event = threading.Event()
                with self._rss_preview_lock:
                    self._active_rss_preview_events[request_token] = (
                        preview_cancel_event
                    )

            url = rule_dict.get("url", "")
            if not url:
                return {"status": "error", "message": "RSS URL 不能为空"}

            # 鋰前验证：尝试解析 RSS
            logger.info(f"📡 正在验证 RSS: {url}")
            feed = feedparser.parse(url)
            ensure_preview_not_cancelled()

            # 检查是否为有效的 RSS
            if feed.bozo and not feed.entries:
                error_detail = (
                    str(feed.bozo_exception) if feed.bozo_exception else "未知错误"
                )
                logger.error(f"📡 RSS 验证失败: {error_detail}")
                return {
                    "status": "error",
                    "message": f"无效的 RSS 订阅地址: {error_detail}",
                }

            # 声成成功：获取 feed 信息
            feed_title = getattr(feed.feed, "title", url)
            feed_link = getattr(feed.feed, "link", url)
            entry_count = len(feed.entries)

            logger.info(f"📡 RSS 验证成功: {feed_title} ({entry_count} 条目)")

            sample_data, strategy_info, resolved_rule_dict = build_sample_data(
                rule_dict, limit=1
            )

            if preview_only:
                return {
                    "status": "success",
                    "message": "RSS 规则预览成功",
                    "rule": resolved_rule_dict,
                    "feed_info": {
                        "title": feed_title,
                        "link": feed_link,
                        "entry_count": entry_count,
                        "strategy": strategy_info,
                    },
                    "sample_data": sample_data,
                }

            # 填充默认值（RSS 规则不需要 HTML 选择器字段）
            rule_dict.setdefault("source_type", "rss")
            # 🌟 不再填充冗余的 HTML 选择器字段，由 save_custom_rule 统一处理
            rule_dict = attach_rss_strategy_metadata(
                normalize_rule_ai_config(resolved_rule_dict),
                sample_articles=None,
            )
            rule_dict.setdefault("enabled", True)

            # 保存规则
            success = self._rules_manager.save_custom_rule(rule_dict)

            if success:
                synced_articles = db.sync_rss_rule_ai_config(
                    str(rule_dict.get("rule_id") or "").strip(),
                    formatting_prompt=str(
                        rule_dict.get("formatting_prompt") or ""
                    ).strip(),
                    summary_prompt=str(rule_dict.get("summary_prompt") or "").strip(),
                    custom_summary_prompt=str(
                        rule_dict.get("custom_summary_prompt")
                        or rule_dict.get("summary_prompt")
                        or ""
                    ).strip(),
                    enable_ai_formatting=bool(
                        rule_dict.get("enable_ai_formatting", False)
                    ),
                    enable_ai_summary=bool(rule_dict.get("enable_ai_summary", False)),
                )
                logger.info(f"RSS 规则保存成功: {rule_dict.get('rule_id')}")
                return {
                    "status": "success",
                    "message": "RSS 规则保存成功",
                    "rule_id": rule_dict.get("rule_id"),
                    "rule": rule_dict,
                    "synced_articles": synced_articles,
                    "feed_info": {
                        "title": feed_title,
                        "link": feed_link,
                        "entry_count": entry_count,
                        "strategy": strategy_info,
                    },
                    "sample_data": sample_data,
                }
            else:
                return {"status": "error", "message": "规则保存失败"}

        except ImportError:
            logger.error("feedparser 未安装")
            return {
                "status": "error",
                "message": "feedparser 模块未安装，请检查 requirements.txt",
            }
        except RssPreviewCancelled:
            logger.info("RSS 规则预览已取消")
            return {"status": "cancelled", "message": "已取消 RSS 规则预览"}
        except Exception as e:
            logger.error(f"保存 RSS 规则失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            if preview_only and request_token:
                with self._rss_preview_lock:
                    self._active_rss_preview_events.pop(request_token, None)

    def get_custom_spider_rules(self) -> Dict[str, Any]:
        """
        获取所有自定义爬虫规则

        Returns:
            {"status": "success", "rules": list}
        """
        try:
            rules = self._rules_manager.load_custom_rules()
            return {
                "status": "success",
                "rules": rules,
                "rss_strategy_catalog": get_rss_strategy_catalog(),
                "rss_health_summary": self._build_rss_health_summary(rules),
                "html_health_summary": self._build_html_health_summary(rules),
            }
        except Exception as e:
            logger.error(f"获取规则列表失败: {e}")
            return {"status": "error", "message": str(e)}

    def _build_rule_health_summary(
        self, rules: List[Dict[str, Any]], source_type: str
    ) -> Dict[str, Any]:
        """按来源类型汇总规则健康状态，供前端做轻量提示。"""
        filtered_rules = [
            rule
            for rule in (rules or [])
            if str(rule.get("source_type") or "").strip().lower()
            == str(source_type or "").strip().lower()
        ]

        summary = {
            "total_rules": len(filtered_rules),
            "healthy_count": 0,
            "empty_count": 0,
            "error_count": 0,
            "attention_count": 0,
            "attention_rules": [],
        }

        if not filtered_rules:
            return summary

        attention_rules: List[Dict[str, Any]] = []
        for rule in filtered_rules:
            health = rule.get("health") if isinstance(rule.get("health"), dict) else {}
            status = str(health.get("status") or "").strip().lower()
            if status == "healthy":
                summary["healthy_count"] += 1
            elif status == "empty":
                summary["empty_count"] += 1
            elif status == "error":
                summary["error_count"] += 1

            consecutive_failures = int(health.get("consecutive_failures") or 0)
            is_alerting = bool(health.get("is_alerting"))
            if status == "error" or consecutive_failures >= 2 or is_alerting:
                attention_rules.append(
                    {
                        "rule_id": str(rule.get("rule_id") or "").strip(),
                        "task_name": str(rule.get("task_name") or "").strip(),
                        "status": status or "unknown",
                        "status_detail": str(health.get("status_detail") or "").strip(),
                        "consecutive_failures": consecutive_failures,
                        "last_checked_at": str(
                            health.get("last_checked_at") or ""
                        ).strip(),
                        "last_success_at": str(
                            health.get("last_success_at") or ""
                        ).strip(),
                        "last_failure_at": str(
                            health.get("last_failure_at") or ""
                        ).strip(),
                        "last_error_message": str(
                            health.get("last_error_message") or ""
                        ).strip(),
                        "is_alerting": is_alerting,
                        "field_alerts": (
                            list(health.get("field_alerts") or [])
                            if isinstance(health.get("field_alerts"), list)
                            else []
                        ),
                    }
                )

        attention_rules.sort(
            key=lambda item: (
                -int(item.get("consecutive_failures") or 0),
                item.get("last_checked_at") or "",
            )
        )
        summary["attention_rules"] = attention_rules[:5]
        summary["attention_count"] = len(attention_rules)
        return summary

    def _build_rss_health_summary(self, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """汇总 RSS 规则健康状态，供前端做轻量提示。"""
        return self._build_rule_health_summary(rules, "rss")

    def _build_html_health_summary(self, rules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """汇总 HTML 规则健康状态，供前端做轻量提示。"""
        return self._build_rule_health_summary(rules, "html")

    def get_custom_spider_rule_by_id(self, rule_id: str) -> Dict[str, Any]:
        """
        根据 ID 获取规则

        Args:
            rule_id: 规则 ID

        Returns:
            {"status": "success", "rule": dict}
        """
        try:
            rule = self._rules_manager.get_rule_by_id(rule_id)
            if rule:
                return {"status": "success", "rule": rule}
            else:
                return {"status": "error", "message": "规则不存在"}
        except Exception as e:
            logger.error(f"获取规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def get_custom_spider_rule_versions(self, rule_id: str) -> Dict[str, Any]:
        """
        获取指定规则的历史版本列表。

        Args:
            rule_id: 规则 ID

        Returns:
            {"status": "success", "versions": list}
        """
        try:
            normalized_rule_id = str(rule_id or "").strip()
            if not normalized_rule_id:
                return {"status": "error", "message": "规则 ID 不能为空"}

            rule = self._rules_manager.get_rule_by_id(normalized_rule_id)
            if not rule:
                return {"status": "error", "message": "规则不存在"}

            versions = self._rules_manager.get_rule_versions(normalized_rule_id)
            return {
                "status": "success",
                "rule_id": normalized_rule_id,
                "task_name": str(rule.get("task_name") or "").strip(),
                "versions": versions,
                "current_snapshot": {
                    "updated_at": str(rule.get("updated_at") or "").strip(),
                    "source_type": str(rule.get("source_type") or "").strip(),
                },
            }
        except Exception as e:
            logger.error(f"获取规则历史版本失败: {e}")
            return {"status": "error", "message": str(e)}

    def rollback_custom_spider_rule_version(
        self, rule_id: str, version_id: str
    ) -> Dict[str, Any]:
        """
        将指定规则回滚到某个历史版本。

        Args:
            rule_id: 规则 ID
            version_id: 历史版本 ID

        Returns:
            {"status": "success", "rule": dict}
        """
        try:
            normalized_rule_id = str(rule_id or "").strip()
            normalized_version_id = str(version_id or "").strip()
            if not normalized_rule_id:
                return {"status": "error", "message": "规则 ID 不能为空"}
            if not normalized_version_id:
                return {"status": "error", "message": "历史版本 ID 不能为空"}

            rolled_back_rule = self._rules_manager.rollback_rule_to_version(
                normalized_rule_id,
                normalized_version_id,
            )
            if not rolled_back_rule:
                return {"status": "error", "message": "回滚失败，未找到对应版本"}

            return {
                "status": "success",
                "message": "规则已回滚到指定历史版本",
                "rule_id": normalized_rule_id,
                "version_id": normalized_version_id,
                "rule": rolled_back_rule,
            }
        except Exception as e:
            logger.error(f"回滚规则历史版本失败: {e}")
            return {"status": "error", "message": str(e)}

    def export_custom_spider_rules(
        self, rule_ids: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        导出自定义规则到 JSON 文件。

        Args:
            rule_ids: 可选，仅导出指定规则

        Returns:
            {"status": "success/cancelled/error", ...}
        """
        try:
            payload = self._rules_manager.build_rules_export_payload(rule_ids)
            export_rules = payload.get("rules") or []
            if not export_rules:
                return {"status": "error", "message": "暂无可导出的规则"}

            default_filename = (
                f"MicroFlow_custom_rules_{time.strftime('%Y%m%d_%H%M%S')}.json"
            )
            dialog_result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG,  # type: ignore
                directory="",
                save_filename=default_filename,
                file_types=("JSON 文件 (*.json)", "所有文件 (*.*)"),
            )
            if not dialog_result:
                return {"status": "cancelled"}

            target_path = (
                dialog_result[0]
                if isinstance(dialog_result, (list, tuple))
                else dialog_result
            )
            target_path = str(target_path or "").strip()
            if not target_path or target_path.lower() == "none":
                return {"status": "cancelled"}
            if not target_path.lower().endswith(".json"):
                target_path = f"{target_path}.json"

            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            return {
                "status": "success",
                "message": f"已导出 {len(export_rules)} 条规则",
                "path": target_path,
                "exported_count": len(export_rules),
            }
        except Exception as e:
            logger.error(f"导出规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def import_custom_spider_rules(self) -> Dict[str, Any]:
        """
        从 JSON 文件导入自定义规则。

        Returns:
            {"status": "success/partial/cancelled/error", ...}
        """
        try:
            dialog_result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,  # type: ignore
                allow_multiple=False,
                file_types=("JSON 文件 (*.json)", "所有文件 (*.*)"),
            )
            if not dialog_result:
                return {"status": "cancelled"}

            source_path = (
                dialog_result[0]
                if isinstance(dialog_result, (list, tuple))
                else dialog_result
            )
            source_path = str(source_path or "").strip()
            if not source_path or source_path.lower() == "none":
                return {"status": "cancelled"}

            with open(source_path, "r", encoding="utf-8") as f:
                payload = json.load(f)

            result = self._rules_manager.import_rules_payload(payload)
            return {
                **result,
                "path": source_path,
            }
        except json.JSONDecodeError:
            logger.error("导入规则失败: JSON 解析失败")
            return {"status": "error", "message": "导入文件不是有效的 JSON"}
        except Exception as e:
            logger.error(f"导入规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def delete_custom_spider_rule(self, rule_id: str) -> Dict[str, Any]:
        """
        删除规则

        Args:
            rule_id: 规则 ID

        Returns:
            {"status": "success/error"}
        """
        try:
            rule = self._rules_manager.get_rule_by_id(rule_id)
            if not rule:
                return {"status": "error", "message": "删除失败或规则不存在"}

            success = self._rules_manager.delete_rule(rule_id)
            if success:
                deleted_articles_count = db.delete_articles_by_rule_id(
                    rule_id, hard_delete=True
                )
                return {
                    "status": "success",
                    "message": "规则已删除",
                    "deleted_articles_count": deleted_articles_count,
                }
            else:
                return {"status": "error", "message": "删除失败或规则不存在"}
        except Exception as e:
            logger.error(f"删除规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def toggle_custom_spider_rule(self, rule_id: str, enabled: bool) -> Dict[str, Any]:
        """
        切换规则启用状态

        Args:
            rule_id: 规则 ID
            enabled: 是否启用

        Returns:
            {"status": "success/error"}
        """
        try:
            success = self._rules_manager.update_rule_status(rule_id, enabled)
            if success:
                status = "启用" if enabled else "禁用"
                return {"status": "success", "message": f"规则已{status}"}
            else:
                return {"status": "error", "message": "更新失败或规则不存在"}
        except Exception as e:
            logger.error(f"切换规则状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def test_custom_spider_rule(self, rule_dict: dict) -> Dict[str, Any]:
        """
        测试已有规则

        Args:
            rule_dict: 规则字典

        Returns:
            {"status": "success", "sample_data": list}
        """
        try:
            from src.models.spider_rule import SpiderRuleOutput

            # 构建规则对象
            rule = SpiderRuleOutput(**rule_dict)

            preview_bundle = self._rule_generator.build_rule_preview_bundle(
                rule,
                max_items=1,
            )
            sample_data = preview_bundle.get("sample_data") or []
            fetch_error = str(preview_bundle.get("fetch_error") or "").strip()
            result_status = "error" if (not sample_data and fetch_error) else "success"

            return {
                "status": result_status,
                "message": fetch_error if result_status == "error" else "",
                "sample_data": sample_data,
                "count": len(sample_data),
                "detail_samples": preview_bundle.get("detail_samples") or [],
                "detail_preview_required": bool(
                    preview_bundle.get("detail_preview_required", False)
                ),
                "detail_preview_passed": bool(
                    preview_bundle.get("detail_preview_passed", False)
                ),
                "detail_preview_message": str(
                    preview_bundle.get("detail_preview_message") or ""
                ),
                "page_summary": (
                    preview_bundle.get("page_summary")
                    if isinstance(preview_bundle.get("page_summary"), dict)
                    else None
                ),
                "test_snapshot": (
                    preview_bundle.get("test_snapshot")
                    if isinstance(preview_bundle.get("test_snapshot"), dict)
                    else None
                ),
            }
        except Exception as e:
            logger.error(f"测试规则失败: {e}")
            return {"status": "error", "message": str(e)}

    def manual_retest_custom_spider_rule(self, rule_id: str) -> Dict[str, Any]:
        """
        对已保存的自定义规则执行一次真实抓取复测，并回写健康状态。

        说明：
        - 与编辑态的 `test_custom_spider_rule` 不同，这里使用已保存规则创建运行时爬虫，
          走与调度器一致的抓取链路。
        - 当前主要用于 HTML 规则健康监控的一键复测入口。
        """
        try:
            normalized_rule_id = str(rule_id or "").strip()
            if not normalized_rule_id:
                return {"status": "error", "message": "规则 ID 不能为空"}

            rule = self._rules_manager.get_rule_by_id(normalized_rule_id)
            if not rule:
                return {"status": "error", "message": "规则不存在或已被删除"}

            source_type = str(rule.get("source_type") or "html").strip().lower()
            if source_type == "rss":
                from src.spiders.rss_spider import create_rss_spider_from_rule

                spider = create_rss_spider_from_rule(rule)
            else:
                from src.spiders.dynamic_spider import create_dynamic_spider_from_rule

                spider = create_dynamic_spider_from_rule(rule)

            if not spider:
                self._rules_manager.update_rule_health(
                    normalized_rule_id,
                    status="error",
                    error_message="无法根据当前规则创建爬虫实例",
                    fetched_count=0,
                    field_hit_stats=None,
                )
                return {
                    "status": "error",
                    "message": "无法根据当前规则创建爬虫实例",
                }

            sample_articles = spider.fetch_list(limit=5)
            fetch_status = (
                str(getattr(spider, "last_fetch_status", "") or "").strip().lower()
                or "error"
            )
            fetch_error = str(getattr(spider, "last_fetch_error", "") or "").strip()
            fetched_count = int(getattr(spider, "last_fetched_count", 0) or 0)

            self._rules_manager.update_rule_health(
                normalized_rule_id,
                status=fetch_status,
                error_message=fetch_error,
                fetched_count=fetched_count,
                field_hit_stats=(
                    getattr(spider, "last_field_hit_stats", None)
                    if isinstance(getattr(spider, "last_field_hit_stats", None), dict)
                    else None
                ),
            )
            refreshed_rule = (
                self._rules_manager.get_rule_by_id(normalized_rule_id) or rule
            )

            if fetch_status == "error":
                return {
                    "status": "error",
                    "message": fetch_error or "规则复测失败",
                    "rule_id": normalized_rule_id,
                    "sample_data": sample_articles or [],
                    "count": len(sample_articles or []),
                    "health": refreshed_rule.get("health") or {},
                }

            message = (
                f"复测成功，提取到 {fetched_count} 条样本数据"
                if fetch_status == "healthy"
                else "复测完成，当前暂无新内容"
            )
            return {
                "status": "success",
                "message": message,
                "rule_id": normalized_rule_id,
                "sample_data": sample_articles or [],
                "count": len(sample_articles or []),
                "health": refreshed_rule.get("health") or {},
            }
        except Exception as e:
            logger.error(f"手动复测规则失败: {e}")
            try:
                normalized_rule_id = str(rule_id or "").strip()
                if normalized_rule_id:
                    self._rules_manager.update_rule_health(
                        normalized_rule_id,
                        status="error",
                        error_message=str(e),
                        fetched_count=0,
                        field_hit_stats=None,
                    )
            except Exception:
                pass
            return {"status": "error", "message": str(e)}
