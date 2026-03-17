"""网络环境探测模块 - 检测网络状态并智能路由爬虫"""

import logging
import socket
import urllib.request
import urllib.error
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class NetworkStatus(Enum):
    """网络状态枚举"""
    NO_NETWORK = "NO_NETWORK"                    # 无网络连接
    PUBLIC_ONLY = "PUBLIC_ONLY"                  # 仅公网（校外）
    PUBLIC_AND_INTRANET = "PUBLIC_AND_INTRANET"  # 公网+校园网（校内）


def check_network_status(
    public_timeout: float = 3.0,
    intranet_timeout: float = 2.0
) -> NetworkStatus:
    """
    检测当前网络环境状态

    检测逻辑：
    1. 先测试公网连通性（DNS 114.114.114.114 或 baidu.com）
    2. 若公网不通，返回 NO_NETWORK
    3. 若公网通，再测试校园网内网（公文通网站）
    4. 内网通则返回 PUBLIC_AND_INTRANET，否则返回 PUBLIC_ONLY

    Args:
        public_timeout: 公网测试超时时间（秒）
        intranet_timeout: 内网测试超时时间（秒）

    Returns:
        NetworkStatus 枚举值
    """
    # 第一步：测试公网连通性
    if not _check_public_network(timeout=public_timeout):
        logger.warning("🌐 公网不可达，判定为无网络连接")
        return NetworkStatus.NO_NETWORK

    # 第二步：测试校园网内网
    if _check_intranet(timeout=intranet_timeout):
        logger.info("🌐 检测到校园网环境（公网+内网）")
        return NetworkStatus.PUBLIC_AND_INTRANET
    else:
        logger.info("🌐 检测到公网环境（仅公网）")
        return NetworkStatus.PUBLIC_ONLY


def _check_public_network(timeout: float = 3.0) -> bool:
    """
    测试公网连通性

    策略：
    1. 尝试 DNS 解析 baidu.com
    2. 若失败，尝试 TCP 连接 114.114.114.114:53

    Args:
        timeout: 超时时间（秒）

    Returns:
        True 如果公网可达
    """
    # 方法1：DNS 解析测试
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo("www.baidu.com", 80)
        return True
    except (socket.timeout, socket.gaierror, OSError):
        pass

    # 方法2：TCP 连接测试（备用）
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("114.114.114.114", 53))
        sock.close()
        return True
    except (socket.timeout, OSError):
        return False


def _check_intranet(timeout: float = 2.0) -> bool:
    """
    测试校园网内网连通性

    通过 HTTP HEAD 请求公文通网站判断是否在校园网内

    Args:
        timeout: 超时时间（秒）

    Returns:
        True 如果内网可达
    """
    # 公文通网站（仅校园网可访问）
    intranet_url = "https://nbw.sztu.edu.cn"

    try:
        request = urllib.request.Request(
            intranet_url,
            method="HEAD"
        )
        request.add_header("User-Agent", "Mozilla/5.0")

        with urllib.request.urlopen(request, timeout=timeout) as response:
            # 任何响应（包括重定向）都说明内网可达
            return response.status in (200, 301, 302, 303, 307, 308)

    except (urllib.error.URLError, urllib.error.HTTPError, socket.timeout, OSError) as e:
        logger.debug(f"内网测试失败: {e}")
        return False


def get_network_description(status: NetworkStatus) -> str:
    """
    获取网络状态的人类可读描述

    Args:
        status: 网络状态枚举

    Returns:
        中文描述字符串
    """
    descriptions = {
        NetworkStatus.NO_NETWORK: "无网络连接",
        NetworkStatus.PUBLIC_ONLY: "仅公网（校外）",
        NetworkStatus.PUBLIC_AND_INTRANET: "公网+校园网（校内）"
    }
    return descriptions.get(status, "未知网络状态")
