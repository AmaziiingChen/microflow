"""核心模块 - 提供爬虫调度、守护进程、文章处理等核心功能"""

from .daemon import DaemonManager
from .scheduler import SpiderScheduler, SPIDER_REGISTRY
from .article_processor import ArticleProcessor, ArticleContext
from .network_utils import check_network_status, NetworkStatus, get_network_description

__all__ = [
    'DaemonManager',
    'SpiderScheduler',
    'ArticleProcessor',
    'ArticleContext',
    'SPIDER_REGISTRY',
    'check_network_status',
    'NetworkStatus',
    'get_network_description'
]