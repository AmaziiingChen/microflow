"""
路径管理模块 - 统一管理所有持久化数据目录

解决 PyInstaller 打包后路径丢失的问题：
- Windows: %APPDATA%/MicroFlow/data
- macOS: ~/Library/Application Support/MicroFlow/data
"""

import os
import platform
from pathlib import Path


APP_NAME = "MicroFlow"


def get_app_data_dir() -> Path:
    """
    获取应用程序持久化数据目录

    Returns:
        Path: 系统级持久化数据目录
    """
    system = platform.system().lower()
    app_name = "MicroFlow"

    if system == "windows":
        # Windows: %APPDATA%/MicroFlow/data
        base_dir = os.environ.get('APPDATA', '')
        if not base_dir:
            # 回退到用户主目录
            base_dir = os.path.expanduser('~')
        data_dir = Path(base_dir) / app_name / "data"
    elif system == "darwin":
        # macOS: ~/Library/Application Support/MicroFlow/data
        base_dir = os.path.expanduser('~')
        data_dir = Path(base_dir) / "Library" / "Application Support" / app_name / "data"
    else:
        # Linux 及其他: ~/.local/share/MicroFlow/data
        base_dir = os.environ.get('XDG_DATA_HOME', '')
        if not base_dir:
            base_dir = os.path.expanduser('~/.local/share')
        data_dir = Path(base_dir) / app_name / "data"

    return data_dir


def ensure_data_dir_exists() -> Path:
    """
    确保数据目录存在，如果不存在则创建

    Returns:
        Path: 数据目录路径
    """
    data_dir = get_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# 全局常量：数据目录路径
DATA_DIR: Path = ensure_data_dir_exists()

# 全局常量：配置文件路径
CONFIG_PATH: Path = DATA_DIR / "config.json"

# 全局常量：数据库路径
DB_PATH: Path = DATA_DIR / "MicroFlow.db"

# 全局常量：日志文件路径
LOG_PATH: Path = DATA_DIR / "microflow.log"

# 全局常量：最后抓取时间文件路径
LAST_FETCH_TIME_PATH: Path = DATA_DIR / ".last_fetch_time"
