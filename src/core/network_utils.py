"""网络环境探测模块 - 检测网络状态并智能路由爬虫"""

import logging
import socket
import urllib.request
import urllib.error
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

_SRUN_PORTAL_URLS = (
    "http://172.19.0.5/srun_portal_pc?ac_id=18&theme=ctcc",
    "http://172.19.0.5/srun_portal_pc",
)

_SRUN_PORTAL_MARKERS = (
    "网络准入认证",
    "srun_portal",
    "login-account",
    "self-service",
    "用户名",
    "账号",
    "密码",
    "记住密码",
    "忘记密码",
    "自助服务",
    "注销",
    "已用流量",
    "已用时长",
)


class NetworkStatus(Enum):
    """网络状态枚举"""
    NO_NETWORK = "NO_NETWORK"                    # 无网络连接
    PUBLIC_ONLY = "PUBLIC_ONLY"                  # 仅公网（校外）
    PUBLIC_AND_INTRANET = "PUBLIC_AND_INTRANET"  # 公网+校园网（校内）


def check_network_status(
    public_timeout: float = 3.0,
    intranet_timeout: float = 5.0
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


def _check_intranet(timeout: float = 3.0) -> bool:
    """
    测试校园网内网连通性

    判定策略：
    1. 优先识别深澜认证页/已认证页特征
    2. 再尝试访问公文通固定入口
    3. 任一条件满足，即认为是校园网环境

    Args:
        timeout: 超时时间（秒），默认 3 秒

    Returns:
        True 如果能访问公文通网站
    """
    # 1) 深澜认证页：更稳定的校园网特征
    for portal_url in _SRUN_PORTAL_URLS:
        if _probe_url_for_portal(portal_url, timeout=timeout):
            logger.debug(f"内网测试成功：识别到深澜认证页特征 ({portal_url})")
            return True

    # 2) 公文通网站：仅校园网环境可稳定访问
    intranet_url = "https://nbw.sztu.edu.cn/list.jsp?urltype=tree.TreeTempUrl&wbtreeid=1029"
    if _probe_url_for_access(intranet_url, timeout=timeout):
        logger.debug("内网测试成功：能访问公文通网站")
        return True

    return False


def _build_request(url: str) -> urllib.request.Request:
    request = urllib.request.Request(
        url,
        method="GET",
    )
    request.add_header(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    )
    request.add_header("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    request.add_header("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
    return request


def _read_response_text(
    response: urllib.response.addinfourl, limit: int = 65536
) -> tuple[str, str, int]:
    body = response.read(limit)
    status_code = response.status if hasattr(response, "status") else response.getcode()
    final_url = getattr(response, "url", "") or response.geturl()
    try:
        text = body.decode("utf-8", errors="ignore")
    except Exception:
        text = body.decode("latin-1", errors="ignore")
    return text, final_url, status_code


def _probe_url_for_portal(url: str, timeout: float = 3.0) -> bool:
    """
    探测 URL 是否表现为深澜认证页/认证门户。

    仅要出现稳定指纹即可判定，不要求登录成功。
    """
    try:
        with urllib.request.urlopen(_build_request(url), timeout=timeout) as response:
            text, final_url, status_code = _read_response_text(response)

            if status_code not in (200, 301, 302, 303, 307, 308):
                return False

            haystack = f"{final_url}\n{text}".lower()
            if any(marker.lower() in haystack for marker in _SRUN_PORTAL_MARKERS):
                return True

            # 某些版本会直接展示“已认证/自助服务”面板，带这些关键结构也可判定
            if "panel-login" in haystack or "change-lang" in haystack:
                return True

            return False
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            location = getattr(e, "headers", {}).get("Location", "") or ""
            haystack = f"{url}\n{location}".lower()
            return any(marker.lower() in haystack for marker in _SRUN_PORTAL_MARKERS)
        return False
    except Exception:
        return False


def _probe_url_for_access(url: str, timeout: float = 3.0) -> bool:
    """
    探测目标 URL 是否可正常访问。

    只要拿到有效响应即可视为校内连通特征之一。
    """
    try:
        with urllib.request.urlopen(_build_request(url), timeout=timeout) as response:
            status_code = response.status if hasattr(response, "status") else response.getcode()
            final_url = getattr(response, "url", "") or response.geturl()
            if status_code == 200:
                return True
            if status_code in (301, 302, 303, 307, 308):
                return True
            if "srun_portal" in final_url.lower():
                return True
            return False
    except urllib.error.HTTPError as e:
        # 某些情况下 302 重定向会触发 HTTPError，但说明服务器响应了
        if e.code in (301, 302, 303, 307, 308):
            return True
        return False
    except urllib.error.URLError as e:
        return False
    except socket.timeout:
        return False
    except OSError as e:
        return False
    except Exception as e:
        logger.debug(f"内网测试未知错误: {e}")
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
