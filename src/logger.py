"""
全局日志配置模块 - 统一管理日志输出

特性：
1. 同时输出到控制台和文件
2. 文件日志自动轮转（单文件最大 5MB，保留 3 个备份）
3. 统一格式化输出
"""

import logging
import os
from logging.handlers import RotatingFileHandler

# 日志文件路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, 'data', 'microflow.log')

# 日志格式
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# 是否已初始化
_initialized = False


def setup_logging(level: int = logging.INFO) -> None:
    """
    配置全局日志系统

    Args:
        level: 日志级别，默认 INFO
    """
    global _initialized

    if _initialized:
        return

    # 确保日志目录存在
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    # 获取根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # 清除已有的 handlers（防止重复添加）
    root_logger.handlers.clear()

    # 创建格式化器
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    # 1. 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 2. 文件处理器（轮转）
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    _initialized = True

    # 记录启动日志
    logging.info("=" * 50)
    logging.info("MicroFlow 日志系统已启动")
    logging.info(f"日志文件: {LOG_FILE}")
    logging.info("=" * 50)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志器

    Args:
        name: 日志器名称（通常使用 __name__）

    Returns:
        Logger 实例
    """
    return logging.getLogger(name)
