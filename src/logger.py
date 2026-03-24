"""
全局日志配置模块 - 统一管理日志输出

特性：
1. 同时输出到控制台和文件
2. 文件日志自动轮转（单文件最大 5MB，保留 3 个备份）
3. 统一格式化输出
4. 文件只记录重要日志（WARNING及以上），控制台可查看详细日志
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.core.paths import LOG_PATH

# 日志格式
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# 是否已初始化
_initialized = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    配置全局日志系统

    Args:
        level: 控制台日志级别，默认 INFO
    """
    global _initialized

    if _initialized:
        return

    # 确保日志目录存在
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 获取根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根日志器设为最低级别，由各 handler 自行控制

    # 清除已有的 handlers（防止重复添加）
    root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # 1. 控制台处理器 - 显示 INFO 及以上
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. 文件处理器 - 只记录 WARNING 及以上（减少日志文件大小）
    file_handler = RotatingFileHandler(
        str(LOG_PATH),
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.WARNING)  # 文件只记录警告和错误
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _initialized = True

    # 记录启动日志（只显示在控制台）
    logging.info("MicroFlow 日志系统已启动")


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志器

    Args:
        name: 日志器名称（通常使用 __name__）

    Returns:
        Logger 实例
    """
    return logging.getLogger(name)
