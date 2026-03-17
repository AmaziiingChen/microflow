# src/notifier.py
"""
跨平台通知模块 - 支持常驻与覆盖模式

架构说明：
- 单例模式管理通知实例
- 新通知自动覆盖旧通知（避免桌面堆叠）
- macOS: 使用 osascript display dialog（常驻，需用户关闭）
- Windows: 使用 plyer（支持 timeout 控制）
"""
import sys
import subprocess
import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)


class NotificationManager:
    """
    通知管理器 - 单例模式，支持常驻与覆盖
    """
    _instance: Optional['NotificationManager'] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._last_notification_time = 0
        self._notification_process: Optional[subprocess.Popen] = None
        self._notification_lock = threading.Lock()

    def send_sticky_notification(self, title: str, message: str, subtitle: str = ""):
        """
        发送常驻通知（覆盖上一条）

        Args:
            title: 通知标题
            message: 通知内容
            subtitle: 副标题（可选）
        """
        with self._notification_lock:
            # 先关闭上一条通知（如果有）
            self._dismiss_previous()

            try:
                # 防注入转义
                escaped_title = title.replace('"', '\\"').replace("'", "'\\''")
                escaped_message = message.replace('"', '\\"').replace("'", "'\\''")
                if subtitle:
                    escaped_subtitle = subtitle.replace('"', '\\"').replace("'", "'\\''")
                else:
                    escaped_subtitle = ""

                if sys.platform == "darwin":
                    self._send_macos_sticky(escaped_title, escaped_message, escaped_subtitle)
                else:
                    self._send_windows_sticky(title, message)

            except Exception as e:
                logger.error(f"发送系统通知失败: {e}")

    def _dismiss_previous(self):
        """关闭上一条通知"""
        if sys.platform == "darwin":
            # macOS: 通过关闭通知中心的进程来清除通知
            try:
                # 方法1: 关闭通知中心通知（需要 AppleScript）
                dismiss_script = '''
                tell application "System Events"
                    try
                        click UI element "Close" of every window of process "Notification Center"
                    end try
                end tell
                '''
                subprocess.run(
                    ["osascript", "-e", dismiss_script],
                    capture_output=True,
                    timeout=2
                )
            except Exception:
                pass  # 忽略关闭失败，继续发送新通知

        # Windows: plyer 的通知会自动超时消失，无需手动关闭

    def _send_macos_sticky(self, title: str, message: str, subtitle: str):
        """
        macOS 常驻通知实现

        使用 display notification 配合 sound 实现醒目效果
        注意：macOS 原生通知不支持真正的"常驻"，但可以通过以下方式增强：
        1. 使用声音提醒
        2. 设置较长的显示时间（系统控制）
        """
        try:
            # 方案1: 使用 display notification（推荐，更原生）
            # 添加声音提醒，让用户注意到
            if subtitle:
                apple_script = f'''
                display notification "{message}" with title "{title}" subtitle "{subtitle}" sound name "Glass"
                '''
            else:
                apple_script = f'''
                display notification "{message}" with title "{title}" sound name "Glass"
                '''

            # 使用 Popen 不等待，让通知在后台显示
            self._notification_process = subprocess.Popen(
                ["osascript", "-e", apple_script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            logger.debug(f"macOS 通知已发送: {title}")

        except Exception as e:
            logger.error(f"macOS 通知发送失败: {e}")
            # 降级方案：尝试使用 terminal-notifier（如果已安装）
            self._try_terminal_notifier(title, message, subtitle)

    def _try_terminal_notifier(self, title: str, message: str, subtitle: str):
        """尝试使用 terminal-notifier（第三方工具）"""
        try:
            cmd = ["terminal-notifier", "-title", title, "-message", message]
            if subtitle:
                cmd.extend(["-subtitle", subtitle])
            # -timeout 0 表示常驻（需要用户点击）
            cmd.extend(["-timeout", "0"])

            subprocess.run(cmd, capture_output=True, timeout=5)
        except FileNotFoundError:
            logger.debug("terminal-notifier 未安装，使用默认通知")
        except Exception as e:
            logger.debug(f"terminal-notifier 失败: {e}")

    def _send_windows_sticky(self, title: str, message: str):
        """
        Windows 常驻通知实现

        使用 plyer 的 notification，设置超长 timeout
        """
        try:
            from plyer import notification
            notification.notify(
                title=title,
                message=message,
                app_name="公文通助手",
                timeout=300  # 5分钟超时（足够长，模拟常驻）
            )#type: ignore
            logger.debug(f"Windows 通知已发送: {title}")
        except ImportError:
            logger.warning("plyer 未安装，无法发送 Windows 通知")
        except Exception as e:
            logger.error(f"Windows 通知发送失败: {e}")


# 全局单例实例
_manager = NotificationManager()


def send_notification(title: str, message: str):
    """
    发送普通系统通知（向后兼容接口）
    """
    _manager.send_sticky_notification(title, message)


def send_sticky_notification(title: str, message: str, subtitle: str = ""):
    """
    发送常驻通知（覆盖上一条）

    Args:
        title: 通知标题
        message: 通知内容
        subtitle: 副标题（如来源名称）
    """
    _manager.send_sticky_notification(title, message, subtitle)
