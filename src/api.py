"""
V2 版本后端调度引擎 - 多源数据订阅架构

核心改动：
1. 废弃原有 TongWenScraper，引入 GwtSpider 和 NmneSpider
2. 维护爬虫实例列表，遍历抓取所有数据源
3. 支持 source_name 字段，实现多来源聚合
"""
import sys
import logging
import webview
import os
import json
import shutil
import requests
import urllib3
import tempfile
import platform
import subprocess
import base64
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from typing import Dict, Any, Optional
import time
from src.database import db
from src.llm_service import LLMService
from src.services import SystemService, DownloadService, ConfigService
from src.core import DaemonManager, SpiderScheduler, ArticleProcessor
from src.core.scheduler import SPIDER_REGISTRY
from src.core.network_utils import check_network_status, NetworkStatus
from src.core.paths import CONFIG_PATH, ensure_data_dir_exists
from src.version import __version__

logger = logging.getLogger(__name__)

# ============================================================
# 🌟 全局常量：云端配置 URL（单一真相源）
# ============================================================
VERSION_URL = "https://microflow-1412347033.cos.ap-guangzhou.myqcloud.com/version.json"

# 确保数据目录存在
ensure_data_dir_exists()


class Api:
    """V2 多源调度引擎 - 门面层"""

    def __init__(self):
        # 当前软件版本号（从 version.py 统一导入）
        self.CURRENT_VERSION = __version__

        # 🌟 云端版本信息缓存（启动时由 perform_startup_check 填充）
        self._version_info: Dict[str, Any] = {}

        # 大模型服务
        self.llm = LLMService()

        # 🌟 服务层：系统交互、文件下载、配置管理
        self.system_service = SystemService()
        self.download_service = DownloadService()
        self.config_service = ConfigService(str(CONFIG_PATH), self.llm.system_prompt)

        # 🌟 核心组件：文章处理器（传入回调函数用于唤醒窗口）
        self.article_processor = ArticleProcessor(
            self.llm, db,
            on_article_processed=self._on_article_processed,
            on_progress=self._push_ai_progress  # 🌟 新增：AI 进度回调
        )
        self.scheduler = SpiderScheduler(
            article_processor=self.article_processor,
            progress_callback=self._push_progress,
            config_service=self.config_service
        )

        # 🌟 核心组件：守护进程管理器
        self.daemon_manager = DaemonManager()

        # 线程控制
        self.is_running = True
        self.window: Optional[webview.Window] = None

        # 加载配置并应用
        self._apply_config()

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
            safe_title = current_title.replace("'", "\\'").replace('"', '\\"') if current_title else ""
            js_code = f"if(window.updatePyProgress) window.updatePyProgress({completed}, {total}, '{safe_title}');"
            webview.windows[0].evaluate_js(js_code)
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
            webview.windows[0].evaluate_js(js_code)
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
            safe_title = current_title.replace("'", "\\'").replace('"', '\\"') if current_title else ""
            js_code = f"if(window.updatePyProgress) window.updatePyProgress({completed}, {total}, '{safe_title}');"
            webview.windows[0].evaluate_js(js_code)
            logger.debug(f"AI 进度推送: {completed}/{total} - {current_title[:20] if current_title else ''}")
        except Exception as e:
            logger.warning(f"AI 进度推送失败: {e}")

    def _on_article_processed(self, article_data: dict):
        """
        单篇文章处理完成后的回调：根据静音模式决定唤醒窗口或显示托盘红点

        Args:
            article_data: 完整的文章数据字典
        """
        if not self.window:
            return

        try:
            # 检查静音模式
            mute_mode = self.config_service.get('muteMode', False)
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
                self.window.evaluate_js(js_code)
                logger.info(f"🔔 静音模式：已显示托盘红点 - {article_data.get('title', '未知标题')}")
            else:
                # 强提醒模式：唤醒窗口并弹出详情
                self.window.restore()
                self.window.show()
                js_code = f"if(window.openArticleDetail) window.openArticleDetail({json_data});"
                self.window.evaluate_js(js_code)
                logger.info(f"🔔 已唤醒窗口并推送文章: {article_data.get('title', '未知标题')}")

        except Exception as e:
            logger.warning(f"文章处理回调执行失败: {e}")

    def check_updates(self, is_manual: bool = False) -> Dict[str, Any]:
        """
        触发爬虫检查更新（异步提交到处理队列）

        Args:
            is_manual: 是否为用户手动触发

        Returns:
            {"status": "success/error", "submitted_count": int, "queue_size": int, "data": list, "cooldown_remaining": int}
        """
        # 🌟 终极防御：如果前端的锁失效了，后端在这里强行拦截
        if is_manual:
            last_fetch = self.daemon_manager._get_last_fetch_time()
            remaining = 120 - (time.time() - last_fetch)
            if remaining > 0:
                logger.warning(f"拦截高频手动刷新，剩余冷却 {remaining:.1f} 秒")
                return {
                    "status": "cooldown",
                    "remaining": int(remaining),
                    "message": f"刷新太快啦，请等待 {int(remaining)} 秒",
                    "cooldown_remaining": int(remaining)
                }

        # 🌟 无论是否手动，都先记录时间戳，防止守护线程重复执行
        self.daemon_manager.record_manual_update()

        mode = self.config_service.get('trackMode', 'continuous')

        # 获取用户订阅的来源列表
        subscribed_sources = self.config_service.get('subscribedSources', None)

        # 🌟 核心：在执行爬虫之前，立即通知前端开始加载
        # 无论是手动还是自动，都要通知前端显示进度条和冷却状态
        try:
            if webview.windows:
                # 如果是后台自动执行，合并通知（同时显示进度条和提示消息）
                if not is_manual:
                    webview.windows[0].evaluate_js("""
                        if (window.onStartFetching) {
                            window.onStartFetching();
                        }
                        if (window.showAutoFetchNotice) {
                            window.showAutoFetchNotice();
                        }
                    """)
                else:
                    # 手动触发只显示进度条
                    webview.windows[0].evaluate_js("""
                        if (window.onStartFetching) {
                            window.onStartFetching();
                        }
                    """)
                # 🌟 关键修复：给浏览器一个处理事件循环的机会
                # 确保通知在爬虫执行之前被渲染到 UI
                time.sleep(0.05)
        except Exception as e:
            logger.debug(f"通知前端开始执行失败: {e}")

        # 委托给调度器执行（异步提交）
        result = self.scheduler.run_all_spiders(
            mode=mode,
            is_manual=is_manual,
            wait_for_completion=False,  # 不等待处理完成，立即返回
            enabled_sources=subscribed_sources,
            spider_progress_callback=self._push_spider_progress   # 🌟 新增
        )

        # 如果调度器返回错误，直接返回（带冷却时间）
        if result.get("status") == "error" and result.get("message"):
            return {
                **result,
                "cooldown_remaining": self.daemon_manager.get_cooldown_remaining()
            }

        # 🌟 关键修复：爬虫阶段完成后，通知前端切换状态
        submitted_count = result.get("submitted_count", 0)
        try:
            if webview.windows:
                if submitted_count > 0:
                    # 有新文章，切换到 AI 阶段
                    webview.windows[0].evaluate_js("""
                        if (window.onSpiderComplete) {
                            window.onSpiderComplete(true);
                        }
                    """)
                    logger.debug(f"爬虫完成，已通知前端切换到 AI 阶段（{submitted_count} 篇新文章）")
                else:
                    # 无新文章，直接关闭加载状态
                    webview.windows[0].evaluate_js("""
                        if (window.onSpiderComplete) {
                            window.onSpiderComplete(false);
                        }
                    """)
                    logger.debug("爬虫完成，已通知前端关闭加载状态（无新文章）")
        except Exception as e:
            logger.debug(f"通知前端爬虫完成失败: {e}")

        # 🌟 手动更新成功后，记录时间戳（与守护进程共享冷却状态）
        if is_manual and result.get("status") == "success":
            self.daemon_manager.record_manual_update()

        # 获取最新数据
        all_articles = db.get_articles_paged(limit=20, offset=0)

        # 🌟 获取冷却剩余时间
        cooldown_remaining = self.daemon_manager.get_cooldown_remaining()

        return {
            "status": "success",
            "submitted_count": result.get("submitted_count", 0),
            "queue_size": result.get("queue_size", 0),
            "data": all_articles,
            "warnings": result.get("warnings"),
            "cooldown_remaining": cooldown_remaining
        }

    def get_update_cooldown(self) -> Dict[str, Any]:
        """获取真实的剩余更新冷却时间（秒）"""
        remaining = self.daemon_manager.get_cooldown_remaining()

        if remaining > 0:
            return {"status": "cooling", "remaining": remaining}
        return {"status": "ready", "remaining": 0}

    def get_processing_stats(self) -> Dict[str, Any]:
        """获取后台处理任务的统计信息"""
        stats = self.scheduler.get_processor_stats()
        return {"status": "success", "data": stats}

    # ===== 以下是保留的兼容方法 =====

    def get_history(self) -> Dict[str, Any]:
        """前端初始化：读取第一页数据"""
        try:
            articles = db.get_articles_paged(limit=20, offset=0)
            return {"status": "success", "data": articles}
        except Exception as e:
            logger.error(f"读取历史记录失败: {e}")
            return {"status": "error", "message": "读取本地数据库失败"}

    def get_history_paged(self, page: int = 1, page_size: int = 20, source_name: str = None, source_names: list = None) -> Dict[str, Any]:# type: ignore
        """分页获取历史记录，支持按来源筛选

        Args:
            page: 页码
            page_size: 每页数量
            source_name: 单个来源筛选
            source_names: 多个来源筛选列表（优先级高于 source_name）
        """
        try:
            offset = (page - 1) * page_size
            articles = db.get_articles_paged(limit=page_size, offset=offset, source_name=source_name, source_names=source_names)
            return {"status": "success", "data": articles}
        except Exception as e:
            logger.error(f"分页读取失败: {e}")
            return {"status": "error", "message": "读取本地数据库失败"}

    def mark_as_read(self, url: str) -> Dict[str, Any]:
        """标记文章为已读"""
        try:
            db.mark_as_read(url)
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def cancel_ai_tasks(self) -> dict:
        """取消所有待处理的AI任务（用户主动终止）"""
        logger.info("【2】后端 API 已接收到取消指令")
        try:
            logger.info("【2.1】正在调用 scheduler.request_cancel()...")
            self.scheduler.request_cancel()         # 🌟 新增：一脚踩死爬虫抓取线程
            logger.info("【2.2】正在调用 article_processor.request_cancel()...")
            self.article_processor.request_cancel()
            logger.info("【2.3】取消指令已发送完毕")
            return {"status": "success", "message": "已请求取消 AI 任务"}
        except Exception as e:
            logger.error(f"取消 AI 任务失败: {e}")
            return {"status": "error", "message": str(e)}

    def clear_ai_cancel(self) -> dict:
        """清除取消标志（新一轮任务开始前调用）"""
        try:
            self.article_processor.clear_cancel()
            return {"status": "success"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

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
        success = self.system_service.open_browser(url)
        return {"status": "success" if success else "error"}

    def open_in_browser(self, url: str):
        """打开外部链接（用于附件验证码页面）"""
        import webbrowser
        success = webbrowser.open(url)
        logger.info(f"打开浏览器链接: {url} -> {'成功' if success else '失败'}")
        return {"status": "success" if success else "error"}

    def start_daemon(self, interval_minutes: int = 15, debug_seconds: int = None):# type: ignore
        """启动后台守护线程"""
        debug_mode = debug_seconds is not None

        def on_new_articles(count: int, result: dict):
            """发现新文章时的回调（已废弃系统通知，由 ArticleProcessor 回调处理弹窗）"""
            # 单篇文章处理完成后会自动通过 _on_article_processed 回调唤醒窗口
            # 这里只做日志记录
            logger.info(f"守护进程检测到 {count} 篇新文章，正在后台处理...")

        # 🌟 热重载 Getter：动态读取配置，带安全边界（最小 15 分钟）
        def get_interval_seconds() -> int:
            polling_minutes = self.config_service.get('pollingInterval', 60)
            # 防御性转换：确保最小 900 秒（15 分钟）
            return max(int(polling_minutes) * 60, 900)

        self.daemon_manager.start(
            task_callback=self.check_updates,
            interval_getter=get_interval_seconds,
            on_new_articles=on_new_articles,
            debug_mode=debug_mode
        )

    def download_attachment(self, url: str, filename: str) -> dict:
        """下载附件（支持后缀智能补全）"""
        return self.download_service.download_attachment(url, filename)

    def open_system_link(self, url: str):
        """调用系统原生应用打开链接"""
        return self.system_service.open_system_link(url)

    def save_snapshot(self, b64_data: str, title: str) -> dict:
        """保存快照图片"""
        return self.download_service.save_snapshot(b64_data, title)

    def copy_image_to_clipboard(self, b64_data: str) -> dict:
        """接收 Base64 图片并调用操作系统底层接口暴力写入剪贴板（无需第三方库）"""
        try:
            # 1. 解码前端传来的 Base64 数据并存入临时文件
            if ',' in b64_data:
                b64_data = b64_data.split(',')[1]
            img_data = base64.b64decode(b64_data)

            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                temp_path = f.name
                f.write(img_data)

            # 2. 根据不同操作系统，调用原生底层脚本注入剪贴板
            system = platform.system().lower()
            if system == 'darwin':
                # macOS: 使用内置的 AppleScript (osascript) 强行写入图像
                script = f'set the clipboard to (read (POSIX file "{temp_path}") as TIFF picture)'
                subprocess.run(['osascript', '-e', script], check=True)
            elif system == 'windows':
                # Windows: 使用内置的 PowerShell 调用 .NET 的剪贴板接口
                # 隐藏 PowerShell 弹窗黑框
                creation_flags = 0x08000000 if os.name == 'nt' else 0
                script = f'Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile("{temp_path}"))'
                subprocess.run(["powershell", "-command", script], check=True, creationflags=creation_flags)
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
        if not self.window:
            return

        try:
            self.window.restore()
            self.window.show()

            screen = webview.screens[0]
            target_width = 465
            target_height = 750
            x_pos = screen.width - target_width - 20
            y_pos = 40

            self.window.resize(target_width, target_height)
            self.window.move(x_pos, y_pos)

            js_code = f"if(window.showArticleDetailFromBackend) {{ window.showArticleDetailFromBackend({json.dumps(article_dict)}); }}"
            self.window.evaluate_js(js_code)

        except Exception as e:
            logger.warning(f"桌面弹窗展示失败: {e}")

    def _apply_config(self):
        """应用配置到 LLM 服务"""
        config = self.config_service.current
        self.llm.update_config(
            api_key=config.api_key,
            model_name=config.model_name,
            system_prompt=config.prompt,
            base_url=config.base_url
        )

    def load_config(self) -> dict:
        """暴露给前端：获取配置"""
        config_data = self.config_service.to_dict()
        
        # 🌟 修复：启动时恢复置顶状态，改为对属性赋值
        if self.window and config_data.get('isPinned', False):
            self.window.on_top = True
            
        return {"status": "success", "data": config_data}

    def save_config(self, new_config: dict) -> dict:
        """暴露给前端：保存配置"""
        try:
            if not self.config_service.save(new_config):
                return {"status": "error", "message": "保存配置文件失败"}

            # 应用开机自启设置
            self._set_autostart(new_config.get('autoStart', False))

            # 热更新 LLM 配置
            self._apply_config()

            logger.info("系统配置已成功保存并热更新")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return {"status": "error", "message": str(e)}

    def test_ai_connection(self, api_key: str, model_name: str, provider: str = 'custom', base_url: str = '') -> dict:
        """测试 AI 连通性"""
        logger.info("正在进行 AI 连通性测试...")
        is_ok, msg = self.llm.test_connection(api_key, model_name, base_url)
        if is_ok:
            return {"status": "success"}
        return {"status": "error", "message": msg}

    def get_api_balance_status(self) -> dict:
        """获取 API 余额状态"""
        try:
            balance_ok = self.config_service.get_api_balance_ok()
            return {"status": "success", "balance_ok": balance_ok}
        except Exception as e:
            logger.error(f"获取余额状态失败: {e}")
            return {"status": "success", "balance_ok": True}  # 出错时默认正常

    def clear_api_balance_status(self) -> dict:
        """清除欠费状态（用户充值后调用）"""
        try:
            self.config_service.set_api_balance_ok(True)
            logger.info("用户已清除欠费状态")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"清除欠费状态失败: {e}")
            return {"status": "error", "message": str(e)}

    def _set_autostart(self, enabled: bool):
        """设置开机自启"""
        self.system_service.set_autostart(enabled)

    def hide_window(self):
        """隐藏窗口"""
        if self.window:
            self.window.hide()

    def search_articles(self, keyword: str, source_name: str = None) -> dict: #type: ignore
        """全局搜索（支持按来源筛选）"""
        try:
            if not keyword or not keyword.strip():
                # 如果搜索词为空，相当于直接获取分页列表
                articles = db.get_articles_paged(limit=20, offset=0, source_name=source_name)
                return {"status": "success", "data": articles}

            # 将 source_name 传递给底层的数据库方法
            data = db.search_articles(keyword.strip(), limit=50, source_name=source_name)
            return {"status": "success", "data": data}
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            return {"status": "error", "message": "搜索失败"}

    def force_quit(self):
        """彻底退出"""
        logger.info("程序彻底退出")
        self.is_running = False
        self.daemon_manager.request_stop()
        self.article_processor.shutdown(wait=False)
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

            return {"status": "success", "data": ordered_sources}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def regenerate_summary(self, article_id: int) -> Dict[str, Any]:
        """
        重新生成文章的 AI 总结

        Args:
            article_id: 文章 ID

        Returns:
            {"status": "success/error", "summary": str, "message": str}
        """
        try:
            # 1. 从数据库获取文章
            article = db.get_article_by_id(article_id)
            if not article:
                return {"status": "error", "message": "文章不存在"}

            # 2. 检查是否有原文内容
            raw_text = article.get('raw_text', '')
            title = article.get('title', '未知标题')

            if not raw_text or len(raw_text.strip()) < 10:
                return {"status": "error", "message": "原文内容过短或缺失，无法生成总结"}

            # 3. 调用 LLM 服务重新生成总结
            logger.info(f"正在重新生成文章总结: {title}")
            new_summary = self.llm.summarize_article(title, raw_text)

            # 4. 检查是否生成成功
            if new_summary.startswith("⚠️") or new_summary.startswith("❌"):
                return {"status": "error", "message": new_summary}

            # 5. 更新数据库
            success = db.update_summary(article_id, new_summary)
            if not success:
                return {"status": "error", "message": "更新数据库失败"}

            logger.info(f"文章总结重新生成成功: {title}")
            return {"status": "success", "summary": new_summary}

        except Exception as e:
            logger.error(f"重新生成总结失败: {e}")
            return {"status": "error", "message": str(e)}

    def import_custom_font(self) -> Dict[str, Any]:
        """呼出系统原生文件选择器，导入并持久化自定义字体"""
        try:
            file_types = ('字体文件 (*.ttf;*.otf;*.woff;*.woff2)', '所有文件 (*.*)')
            # 呼出文件选择对话框
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, #type: ignore
                allow_multiple=False,
                file_types=file_types
            )

            if not result:
                return {"status": "cancelled"}

            source_path = result[0]
            ext = os.path.splitext(source_path)[1].lower()
            if ext not in ['.ttf', '.otf', '.woff', '.woff2']:
                return {"status": "error", "message": "不支持的字体格式，仅支持 ttf/otf/woff/woff2"}

            # 将字体安全拷贝到 frontend/fonts 目录下，供本地 HTTP 服务器读取
            frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend')
            fonts_dir = os.path.join(frontend_dir, 'fonts')
            os.makedirs(fonts_dir, exist_ok=True)

            # 统一重命名避免编码问题
            target_filename = f"custom_font{ext}"
            target_path = os.path.join(fonts_dir, target_filename)

            shutil.copy2(source_path, target_path)

            return {
                "status": "success",
                "font_path": f"fonts/{target_filename}",
                "font_name": os.path.basename(source_path)
            }
        except Exception as e:
            logger.error(f"导入字体失败: {e}")
            return {"status": "error", "message": str(e)}

    def check_software_update(self) -> Dict[str, Any]:
        """
        检查软件更新（使用缓存的版本信息）

        如果缓存为空，则发起网络请求
        """
        import platform

        # 🌟 优先使用缓存
        data = self._version_info

        if not data:
            # 缓存为空，发起网络请求
            try:
                response = requests.get(VERSION_URL, timeout=5, verify=False)
                if response.status_code == 200:
                    data = response.json()
                    self._version_info = data  # 缓存结果
            except Exception as e:
                logger.error(f"检查更新失败: {e}")
                return {"has_update": False, "error": str(e)}

        if not data:
            return {"has_update": False}

        latest_version = data.get("version", "")

        # 简单的版本号字符串比对 (例如 "v1.1.0" > "v1.0.0")
        if latest_version and latest_version > self.CURRENT_VERSION:
            # 根据当前系统选择对应的下载链接
            downloads = data.get("downloads", {})
            current_system = platform.system().lower()

            if current_system == "windows":
                download_url = downloads.get("windows", "")
            elif current_system == "darwin":
                download_url = downloads.get("macos", "")
            else:
                download_url = ""

            release_date = data.get("release_date", "")
            notes = f"发布时间: {release_date}" if release_date else "有新版本可用"

            return {
                "has_update": True,
                "version": latest_version,
                "notes": notes,
                "download_url": download_url
            }

        return {"has_update": False}

    def perform_startup_check(self) -> Dict[str, Any]:
        """
        启动时执行的安全检查（唯一请求 version.json 的入口）

        检查步骤：
        1. 检查本地锁定状态（isLocked）
        2. 检查网络环境（必须是校园网）
        3. 请求远程 version.json，缓存到实例属性
        4. 检查 is_active 字段，如果为 False 执行自毁逻辑

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
        # 🌟 步骤 1：检查本地锁定状态
        if self.config_service.get('isLocked', False):
            logger.warning("🚫 启动检查：软件已被锁定")
            return {
                "status": "locked",
                "reason": "该软件已被永久禁用"
            }

        # # 🌟 步骤 2：检查网络环境 [已禁用 - 允许公网环境运行]
        # network_status = check_network_status()
        # if network_status != NetworkStatus.PUBLIC_AND_INTRANET:
        #     logger.warning(f"🚫 启动检查：网络环境不符合要求 ({network_status.value})")
        #     return {
        #         "status": "network_error",
        #         "reason": "请连接深圳技术大学校园网后使用"
        #     }

        # 🌟 步骤 3：请求远程 version.json（唯一请求点）
        try:
            response = requests.get(VERSION_URL, timeout=5, verify=False)

            if response.status_code != 200:
                logger.warning(f"启动检查：无法获取远程配置 (HTTP {response.status_code})，默认放行")
                return self._build_success_response({})

            data = response.json()

            # 🌟 缓存版本信息（供后续 check_software_update 和前端使用）
            self._version_info = data

            # 🌟 步骤 4：检查 is_active 字段
            is_active = data.get('is_active', True)

            if is_active is False:
                kill_reason = data.get('kill_message', '该软件已被禁用')
                logger.warning(f"🚫 启动检查：远程配置标记为不可用，原因: {kill_reason}")
                self._execute_self_destruct(kill_reason)
                return {
                    "status": "locked",
                    "reason": kill_reason
                }

            # 🌟 步骤 5：一切正常，返回成功响应
            return self._build_success_response(data)

        except requests.exceptions.Timeout:
            logger.warning("启动检查：请求超时，默认放行")
            return self._build_success_response({})

        except requests.exceptions.RequestException as e:
            logger.warning(f"启动检查：网络请求失败 ({e})，默认放行")
            return self._build_success_response({})

        except json.JSONDecodeError as e:
            logger.warning(f"启动检查：JSON 解析失败 ({e})，默认放行")
            return self._build_success_response({})

        except Exception as e:
            logger.error(f"启动检查：未知错误 ({e})，默认放行")
            return self._build_success_response({})

    def _execute_self_destruct(self, reason: str) -> None:
        """
        执行自毁逻辑：将配置锁定，禁止软件运行

        Args:
            reason: 禁用原因
        """
        logger.critical(f"🔒 执行自毁逻辑，原因: {reason}")

        # 锁定配置文件
        try:
            current_config = self.config_service.to_dict()
            current_config['isLocked'] = True
            self.config_service.save(current_config)
            logger.info("✅ 已将配置标记为锁定")
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
        import platform

        response: Dict[str, Any] = {
            "status": "success",
            "has_update": False,
            "announcement": version_data.get("announcement", {})
        }

        # 检查版本更新
        latest_version = version_data.get("version", "")

        if latest_version and latest_version > self.CURRENT_VERSION:
            downloads = version_data.get("downloads", {})
            current_system = platform.system().lower()

            if current_system == "windows":
                download_url = downloads.get("windows", "")
            elif current_system == "darwin":
                download_url = downloads.get("macos", "")
            else:
                download_url = ""

            release_date = version_data.get("release_date", "")
            notes = f"发布时间: {release_date}" if release_date else "有新版本可用"

            response["has_update"] = True
            response["version"] = latest_version
            response["download_url"] = download_url
            response["notes"] = notes

        return response

    def get_version_info(self) -> Dict[str, Any]:
        """
        获取云端版本信息（供前端调用）

        如果缓存为空，则发起网络请求

        Returns:
            {
                "status": "success" | "error",
                "version": str,
                "announcement": dict,
                "downloads": dict,
                "release_date": str,
                "is_active": bool
            }
        """
        # 🌟 优先返回缓存
        if self._version_info:
            return {
                "status": "success",
                **self._version_info
            }

        # 缓存为空，发起网络请求
        try:
            response = requests.get(VERSION_URL, timeout=5, verify=False)
            if response.status_code == 200:
                data = response.json()
                self._version_info = data  # 缓存结果
                return {
                    "status": "success",
                    **data
                }
        except Exception as e:
            logger.error(f"获取版本信息失败: {e}")

        return {"status": "error", "message": "无法获取版本信息"}
    

    def set_window_on_top(self, is_on_top: bool):
        """前端调用：切换窗口置顶状态"""
        if self.window:
            # 🌟 修复：改为对属性进行赋值
            self.window.on_top = is_on_top
            return {"status": "success", "is_pinned": is_on_top}
        return {"status": "error"}

    def get_local_ai_icon(self, model_name: str) -> Dict[str, Any]:
        """
        🌟 增强版图标检索：支持模糊匹配与打包路径兼容
        """
        # 1. 提取品牌关键词
        brand_key = model_name.lower().split('-')[0]
        
        # 2. 别名映射（可根据需要增删）
        brand_map = {'gpt': 'openai', 'claude': 'anthropic', 'gemini': 'google'}
        slug = brand_map.get(brand_key, brand_key)

        # 3. 🚀 打包兼容性路径处理
        # 如果是 PyInstaller 打包后的环境，使用 sys._MEIPASS；否则使用当前文件目录
        if getattr(sys, 'frozen', False):
            # 生产环境：指向解压后的临时资源目录
            base_path = getattr(sys, '_MEIPASS', '')  # type: ignore[attr-defined]
        else:
            # 开发环境：指向当前项目根目录
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        # 确保指向 /data/icons 文件夹
        icons_dir = os.path.join(base_path, 'data', 'icons')
        
        # 4. 检索逻辑：优先精准匹配，次选模糊匹配
        svg_content = None
        
        try:
            if os.path.exists(icons_dir):
                files = os.listdir(icons_dir)
                
                # 策略 A: 精准匹配 (mimo.svg / icon_mimo.svg)
                target_exact = [f"{slug}.svg", f"icon_{slug}.svg", f"{slug}-color.svg"]
                for f in target_exact:
                    if f in files:
                        with open(os.path.join(icons_dir, f), 'r', encoding='utf-8') as fs:
                            svg_content = fs.read()
                        break
                
                # 策略 B: 模糊匹配 (匹配 xiaomimimo.svg)
                if not svg_content:
                    for f in files:
                        if f.endswith('.svg') and slug in f.lower():
                            with open(os.path.join(icons_dir, f), 'r', encoding='utf-8') as fs:
                                svg_content = fs.read()
                            break

            # 5. 回退逻辑：如果本地 800 个都没中，去云端抓取
            if not svg_content:
                url = f"https://unpkg.com/@lobehub/icons-static-svg@latest/icons/{slug}-color.svg"
                resp = requests.get(url, timeout=3, verify=False)
                if resp.status_code == 200 and resp.text.startswith('<svg'):
                    svg_content = resp.text
                    # 下载后顺手存进本地库，下次就不用下电了
                    os.makedirs(icons_dir, exist_ok=True)
                    with open(os.path.join(icons_dir, f"{slug}.svg"), 'w', encoding='utf-8') as f:
                        f.write(svg_content)

            # 6. 尺寸加固：确保截图必现
            if svg_content and 'width=' not in svg_content:
                svg_content = svg_content.replace('<svg', '<svg width="100%" height="100%"')

            return {"status": "success", "svg_raw": svg_content or ""}
            
        except Exception as e:
            return {"status": "error", "message": str(e), "svg_raw": ""}