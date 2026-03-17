"""系统交互服务 - 负责开机自启、系统链接等操作系统层面的交互"""

import os
import sys
import platform
import webbrowser
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SystemService:
    """系统交互服务 - 单一职责：操作系统层面的交互"""

    def set_autostart(self, enabled: bool) -> bool:
        """
        设置开机自启

        Args:
            enabled: True 启用开机自启，False 禁用

        Returns:
            是否设置成功
        """
        is_frozen = getattr(sys, 'frozen', False)
        python_exe = sys.executable

        if is_frozen:
            full_command = f'"{python_exe}" --minimized'
        else:
            main_script = os.path.abspath(sys.argv[0])
            full_command = f'"{python_exe}" "{main_script}" --minimized'

        try:
            if platform.system() == "Windows":
                return self._set_autostart_windows(enabled, full_command)
            elif platform.system() == "Darwin":
                return self._set_autostart_macos(enabled, python_exe, is_frozen)
            else:
                logger.warning(f"不支持的平台: {platform.system()}")
                return False
        except Exception as e:
            logger.error(f"设置开机自启失败: {e}")
            return False

    def _set_autostart_windows(self, enabled: bool, full_command: str) -> bool:
        """Windows 开机自启"""
        import winreg  # type: ignore

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)

        if enabled:
            winreg.SetValueEx(key, "TongwenMonitor", 0, winreg.REG_SZ, full_command)
        else:
            try:
                winreg.DeleteValue(key, "TongwenMonitor")
            except FileNotFoundError:
                pass

        winreg.CloseKey(key)
        logger.info(f"Windows 开机自启已{'启用' if enabled else '禁用'}")
        return True

    def _set_autostart_macos(self, enabled: bool, python_exe: str, is_frozen: bool) -> bool:
        """macOS 开机自启"""
        plist_path = os.path.expanduser("~/Library/LaunchAgents/com.tongwen.monitor.plist")

        if enabled:
            args_list = [python_exe]
            if not is_frozen:
                args_list.append(os.path.abspath(sys.argv[0]))
            args_list.append("--minimized")

            xml_args = "".join([f"<string>{arg}</string>" for arg in args_list])
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tongwen.monitor</string>
    <key>ProgramArguments</key>
    <array>{xml_args}</array>
    <key>RunAtLoad</key>
    <true/>
    <key>WorkingDirectory</key>
    <string>{os.path.dirname(os.path.abspath(sys.argv[0]))}</string>
</dict>
</plist>"""

            os.makedirs(os.path.dirname(plist_path), exist_ok=True)
            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(plist_content)
            logger.info("macOS 开机自启已启用")
        elif os.path.exists(plist_path):
            os.remove(plist_path)
            logger.info("macOS 开机自启已禁用")

        return True

    def open_browser(self, url: str) -> bool:
        """
        使用默认浏览器打开链接

        Args:
            url: 要打开的 URL

        Returns:
            是否成功打开
        """
        try:
            webbrowser.open(url)
            return True
        except Exception as e:
            logger.warning(f"浏览器打开失败: {e}")
            return False

    def open_system_link(self, url: str) -> bool:
        """
        调用系统原生应用打开链接（支持 mailto:, tel: 等协议）

        Args:
            url: 要打开的 URL

        Returns:
            是否成功打开
        """
        try:
            if url.startswith("mailto:") or url.startswith("tel:"):
                if sys.platform == "darwin":
                    subprocess.run(["open", url])
                elif sys.platform == "win32":
                    os.startfile(url)
                else:
                    webbrowser.open(url)
            else:
                webbrowser.open(url)
            return True
        except Exception as e:
            logger.warning(f"系统应用唤起失败: {e}")
            return False