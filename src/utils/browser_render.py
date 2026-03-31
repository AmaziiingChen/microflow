"""
浏览器渲染与抓取策略工具。

为 HTML 自定义数据源提供：
1. requests 抓取
2. Headless 浏览器渲染抓取
3. 站点级抓取策略编排
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import platform
import shutil
import subprocess
import time
from typing import Any, Dict, Iterable, Optional

import requests
from urllib.parse import urlparse

from src.utils.http_rule_config import (
    ensure_body_content_type,
    normalize_request_body,
    normalize_request_method,
)


logger = logging.getLogger(__name__)

DEFAULT_FETCH_STRATEGY = "requests_first"
SUPPORTED_FETCH_STRATEGIES = {
    "requests_first",
    "browser_first",
    "requests_only",
    "browser_only",
}


@dataclass
class HtmlFetchResult:
    success: bool
    html: str = ""
    engine: str = ""
    status_code: int = 0
    error_code: str = ""
    error_message: str = ""


def normalize_fetch_strategy(strategy: Any) -> str:
    normalized = str(strategy or DEFAULT_FETCH_STRATEGY).strip().lower()
    if normalized not in SUPPORTED_FETCH_STRATEGIES:
        return DEFAULT_FETCH_STRATEGY
    return normalized


def _trim_error_message(message: str, limit: int = 220) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _find_browser_executable() -> str:
    env_path = str(os.getenv("MICROFLOW_BROWSER_PATH") or "").strip()
    if env_path and os.path.exists(env_path):
        return env_path

    system = platform.system()
    candidates: list[str] = []

    if system == "Darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "google-chrome",
                "chromium",
                "chromium-browser",
                "microsoft-edge",
            ]
        )
    elif system == "Windows":
        program_files = [
            os.getenv("PROGRAMFILES", ""),
            os.getenv("PROGRAMFILES(X86)", ""),
            os.getenv("LOCALAPPDATA", ""),
        ]
        for base in filter(None, program_files):
            candidates.extend(
                [
                    os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"),
                    os.path.join(
                        base, "Microsoft", "Edge", "Application", "msedge.exe"
                    ),
                    os.path.join(base, "Chromium", "Application", "chrome.exe"),
                ]
            )
        candidates.extend(["chrome", "msedge", "chromium"])
    else:
        candidates.extend(
            [
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
                "microsoft-edge",
            ]
        )

    for candidate in candidates:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    return ""


def _build_browser_command(
    executable: str,
    url: str,
    browser_wait_ms: int,
    headless_flag: str = "--headless=new",
) -> list[str]:
    virtual_time_budget = max(int(browser_wait_ms or 0), 0) + 1500
    command = [
        executable,
        headless_flag,
        "--disable-gpu",
        "--hide-scrollbars",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        "--disable-renderer-backgrounding",
        f"--virtual-time-budget={virtual_time_budget}",
        "--dump-dom",
        url,
    ]

    if platform.system() != "Windows":
        command.insert(2, "--no-sandbox")

    return command


def _run_subprocess_with_deadline(
    command: list[str],
    timeout_seconds: int,
    cancel_event: Any = None,
) -> HtmlFetchResult:
    process = None
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.monotonic() + max(int(timeout_seconds or 0), 1)

        while True:
            if cancel_event is not None and getattr(cancel_event, "is_set", None):
                if cancel_event.is_set():
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    return HtmlFetchResult(
                        success=False,
                        engine="browser",
                        error_code="cancelled",
                        error_message="浏览器渲染已取消",
                    )

            return_code = process.poll()
            if return_code is not None:
                stdout, stderr = process.communicate(timeout=1)
                html = str(stdout or "").strip()
                if return_code == 0 and html:
                    return HtmlFetchResult(
                        success=True,
                        html=html,
                        engine="browser",
                        status_code=200,
                    )
                return HtmlFetchResult(
                    success=False,
                    engine="browser",
                    error_code="browser_failed",
                    error_message=_trim_error_message(
                        stderr or stdout or f"浏览器返回退出码 {return_code}"
                    ),
                )

            if time.monotonic() >= deadline:
                process.kill()
                stdout, stderr = process.communicate(timeout=1)
                return HtmlFetchResult(
                    success=False,
                    engine="browser",
                    error_code="timeout",
                    error_message=_trim_error_message(
                        stderr or stdout or "浏览器渲染超时"
                    ),
                )

            time.sleep(0.1)
    except FileNotFoundError:
        return HtmlFetchResult(
            success=False,
            engine="browser",
            error_code="browser_not_found",
            error_message="未检测到可用的 Chrome / Edge / Chromium 浏览器",
        )
    except Exception as exc:
        if process:
            try:
                process.kill()
            except Exception:
                pass
        return HtmlFetchResult(
            success=False,
            engine="browser",
            error_code="browser_exception",
            error_message=_trim_error_message(str(exc) or "浏览器渲染异常"),
        )


def _serialize_playwright_page(page: Any) -> str:
    """Serialize Playwright page HTML and inline open shadow-root content."""
    try:
        html = page.evaluate(
            """
            () => {
              const VOID_TAGS = new Set([
                "area","base","br","col","embed","hr","img","input",
                "link","meta","param","source","track","wbr"
              ]);

              const escapeHtml = (text) =>
                String(text ?? "")
                  .replace(/&/g, "&amp;")
                  .replace(/</g, "&lt;")
                  .replace(/>/g, "&gt;");

              const escapeAttr = (text) =>
                escapeHtml(text).replace(/"/g, "&quot;");

              const serializeNode = (node) => {
                if (!node) return "";

                if (node.nodeType === Node.TEXT_NODE || node.nodeType === Node.CDATA_SECTION_NODE) {
                  return escapeHtml(node.textContent || "");
                }

                if (node.nodeType === Node.DOCUMENT_TYPE_NODE) {
                  return `<!DOCTYPE ${node.name || "html"}>`;
                }

                if (node.nodeType === Node.DOCUMENT_FRAGMENT_NODE) {
                  return Array.from(node.childNodes || []).map(serializeNode).join("");
                }

                if (node.nodeType !== Node.ELEMENT_NODE) {
                  return "";
                }

                const tagName = String(node.tagName || "").toLowerCase();
                let attrs = "";
                for (const attr of Array.from(node.attributes || [])) {
                  attrs += ` ${attr.name}="${escapeAttr(attr.value)}"`;
                }

                let children = "";
                if (tagName === "slot" && typeof node.assignedNodes === "function") {
                  const assigned = node.assignedNodes({ flatten: true }) || [];
                  if (assigned.length > 0) {
                    children += assigned.map(serializeNode).join("");
                  } else {
                    children += Array.from(node.childNodes || []).map(serializeNode).join("");
                  }
                } else {
                  children += Array.from(node.childNodes || []).map(serializeNode).join("");
                }

                if (node.shadowRoot) {
                  const shadowChildren = Array.from(node.shadowRoot.childNodes || [])
                    .map(serializeNode)
                    .join("");
                  if (shadowChildren.trim()) {
                    children += `<div data-microflow-shadow-root="open">${shadowChildren}</div>`;
                  }
                }

                if (VOID_TAGS.has(tagName)) {
                  return `<${tagName}${attrs}>${children}`;
                }
                return `<${tagName}${attrs}>${children}</${tagName}>`;
              };

              const doctype = document.doctype ? serializeNode(document.doctype) : "<!DOCTYPE html>";
              const root = document.documentElement ? serializeNode(document.documentElement) : "";
              return `${doctype}${root}`.trim();
            }
            """
        )
        serialized = str(html or "").strip()
        if serialized:
            return serialized
    except Exception:
        pass

    return str(page.content() or "").strip()


def _render_html_in_browser(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    request_method: str = "get",
    request_body: str = "",
    browser_timeout_seconds: int = 20,
    browser_wait_ms: int = 1200,
    cancel_event: Any = None,
) -> HtmlFetchResult:
    normalized_method = normalize_request_method(request_method)
    normalized_body = normalize_request_body(request_body)
    normalized_headers = {
        str(key).strip(): str(value).strip()
        for key, value in ensure_body_content_type(
            headers or {},
            normalized_method,
            normalized_body,
        ).items()
        if str(key).strip() and str(value).strip()
    }
    normalized_cookies = {
        str(key).strip(): str(value).strip()
        for key, value in (cookies or {}).items()
        if str(key).strip()
    }
    requires_browser_context = bool(
        normalized_headers or normalized_cookies or normalized_method != "get"
    )

    try:
        from playwright.sync_api import sync_playwright
        playwright_available = True
    except ImportError:
        sync_playwright = None
        playwright_available = False

    executable = _find_browser_executable()
    cli_failures: list[HtmlFetchResult] = []
    if executable and not requires_browser_context and not playwright_available:
        for headless_flag in ("--headless=new", "--headless"):
            result = _run_subprocess_with_deadline(
                _build_browser_command(
                    executable,
                    url,
                    browser_wait_ms=browser_wait_ms,
                    headless_flag=headless_flag,
                ),
                timeout_seconds=browser_timeout_seconds,
                cancel_event=cancel_event,
            )
            if result.success:
                return result
            cli_failures.append(result)
            message = str(result.error_message or "").lower()
            if "headless" not in message and "unknown" not in message:
                break

    if cli_failures:
        last_cli_failure = cli_failures[-1]
    elif executable:
        last_cli_failure = HtmlFetchResult(
            success=False,
            engine="browser",
            error_code="browser_failed",
            error_message="浏览器渲染失败",
        )
    else:
        last_cli_failure = HtmlFetchResult(
            success=False,
            engine="browser",
            error_code="browser_not_found",
            error_message="未检测到可用的 Chrome / Edge / Chromium 浏览器",
        )

    if not playwright_available:
        if requires_browser_context:
            return HtmlFetchResult(
                success=False,
                engine="browser",
                error_code="playwright_required",
                error_message=(
                    "当前规则配置了 POST 请求、自定义请求头或 Cookie，"
                    "需安装 Playwright 才能走浏览器抓取；"
                    "Shadow DOM 站点也建议安装 Playwright"
                ),
            )
        return last_cli_failure

    playwright = None
    browser = None
    context = None
    page = None
    try:
        if cancel_event is not None and getattr(cancel_event, "is_set", None):
            if cancel_event.is_set():
                return HtmlFetchResult(
                    success=False,
                    engine="browser",
                    error_code="cancelled",
                    error_message="浏览器渲染已取消",
                )

        playwright = sync_playwright().start()
        if normalized_method == "post":
            request_context_kwargs: Dict[str, Any] = {}
            extra_http_headers: Dict[str, str] = {}
            for key, value in normalized_headers.items():
                if key.lower() == "user-agent":
                    request_context_kwargs["user_agent"] = value
                else:
                    extra_http_headers[key] = value
            if extra_http_headers:
                request_context_kwargs["extra_http_headers"] = extra_http_headers
            if normalized_cookies:
                parsed_url = urlparse(url)
                cookie_domain = str(parsed_url.hostname or "").strip()
                secure = parsed_url.scheme.lower() == "https"
                if cookie_domain:
                    request_context_kwargs["storage_state"] = {
                        "cookies": [
                            {
                                "name": name,
                                "value": value,
                                "domain": cookie_domain,
                                "path": "/",
                                "secure": secure,
                                "httpOnly": False,
                            }
                            for name, value in normalized_cookies.items()
                        ],
                        "origins": [],
                    }
            api_context = playwright.request.new_context(**request_context_kwargs)
            try:
                response = api_context.fetch(
                    url,
                    method="POST",
                    data=normalized_body or None,
                    timeout=max(int(browser_timeout_seconds or 0), 1) * 1000,
                )
                status_code = int(getattr(response, "status", 0) or 0)
                if not getattr(response, "ok", False):
                    response_text = ""
                    try:
                        response_text = str(response.text() or "").strip()
                    except Exception:
                        response_text = ""
                    return HtmlFetchResult(
                        success=False,
                        engine="playwright_request",
                        status_code=status_code,
                        error_code="http_error",
                        error_message=_trim_error_message(
                            response_text
                            or f"POST 请求失败，状态码 {status_code or 'unknown'}"
                        ),
                    )
                html = str(response.text() or "").strip()
                if not html:
                    return HtmlFetchResult(
                        success=False,
                        engine="playwright_request",
                        status_code=status_code,
                        error_code="browser_empty",
                        error_message="POST 请求成功，但未返回可用 HTML",
                    )
                return HtmlFetchResult(
                    success=True,
                    html=html,
                    engine="playwright_request",
                    status_code=status_code or 200,
                )
            finally:
                try:
                    api_context.dispose()
                except Exception:
                    pass
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context_kwargs: Dict[str, Any] = {}
        extra_http_headers: Dict[str, str] = {}
        for key, value in normalized_headers.items():
            if key.lower() == "user-agent":
                context_kwargs["user_agent"] = value
            else:
                extra_http_headers[key] = value
        if extra_http_headers:
            context_kwargs["extra_http_headers"] = extra_http_headers

        context = browser.new_context(**context_kwargs)
        if normalized_cookies:
            parsed_url = urlparse(url)
            cookie_domain = str(parsed_url.hostname or "").strip()
            secure = parsed_url.scheme.lower() == "https"
            if cookie_domain:
                context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": value,
                            "domain": cookie_domain,
                            "path": "/",
                            "secure": secure,
                            "httpOnly": False,
                        }
                        for name, value in normalized_cookies.items()
                    ]
                )
        page = context.new_page()
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=max(int(browser_timeout_seconds or 0), 1) * 1000,
        )
        if browser_wait_ms > 0:
            page.wait_for_timeout(int(browser_wait_ms))
        html = _serialize_playwright_page(page)
        if not html:
            return HtmlFetchResult(
                success=False,
                engine="playwright",
                error_code="browser_empty",
                error_message="浏览器已打开页面，但未返回可用 HTML",
            )
        return HtmlFetchResult(
            success=True,
            html=html,
            engine="playwright",
            status_code=200,
        )
    except Exception as exc:
        if executable and not requires_browser_context:
            cli_failures = []
            for headless_flag in ("--headless=new", "--headless"):
                result = _run_subprocess_with_deadline(
                    _build_browser_command(
                        executable,
                        url,
                        browser_wait_ms=browser_wait_ms,
                        headless_flag=headless_flag,
                    ),
                    timeout_seconds=browser_timeout_seconds,
                    cancel_event=cancel_event,
                )
                if result.success:
                    return result
                cli_failures.append(result)
                message = str(result.error_message or "").lower()
                if "headless" not in message and "unknown" not in message:
                    break
            if cli_failures:
                last_cli_failure = cli_failures[-1]

        message = _trim_error_message(str(exc) or "Playwright 渲染失败")
        if last_cli_failure and last_cli_failure.error_message:
            message = _trim_error_message(
                f"{last_cli_failure.error_message}；Playwright 兜底失败：{message}"
            )
        return HtmlFetchResult(
            success=False,
            engine="playwright",
            error_code="browser_exception",
            error_message=message,
        )
    finally:
        try:
            if page:
                page.close()
        except Exception:
            pass
        try:
            if context:
                context.close()
        except Exception:
            pass
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass


def _fetch_html_via_requests(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    request_method: str = "get",
    request_body: str = "",
    timeout_seconds: int = 15,
    request_kwargs: Optional[Dict[str, Any]] = None,
) -> HtmlFetchResult:
    client = session or requests.Session()
    kwargs = dict(request_kwargs or {})
    normalized_method = normalize_request_method(request_method)
    normalized_body = normalize_request_body(request_body)
    normalized_headers = ensure_body_content_type(
        headers or {},
        normalized_method,
        normalized_body,
    )
    if normalized_headers:
        kwargs["headers"] = normalized_headers
    if cookies:
        kwargs["cookies"] = cookies
    if (
        normalized_method == "post"
        and normalized_body
        and "data" not in kwargs
        and "json" not in kwargs
    ):
        kwargs["data"] = normalized_body

    try:
        response = client.request(
            normalized_method.upper(),
            url,
            timeout=max(int(timeout_seconds or 0), 1),
            **kwargs,
        )
        response.raise_for_status()
        if response.encoding is None or response.encoding == "ISO-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        html = str(response.text or "").strip()
        if not html:
            return HtmlFetchResult(
                success=False,
                engine="requests",
                status_code=int(getattr(response, "status_code", 0) or 0),
                error_code="empty",
                error_message="请求成功，但返回内容为空",
            )
        return HtmlFetchResult(
            success=True,
            html=html,
            engine="requests",
            status_code=int(getattr(response, "status_code", 200) or 200),
        )
    except requests.exceptions.Timeout:
        return HtmlFetchResult(
            success=False,
            engine="requests",
            error_code="timeout",
            error_message="请求网页超时",
        )
    except requests.exceptions.RequestException as exc:
        return HtmlFetchResult(
            success=False,
            engine="requests",
            error_code="request_error",
            error_message=_trim_error_message(str(exc) or "请求网页失败"),
        )


def _strategy_order(strategy: str) -> Iterable[str]:
    normalized = normalize_fetch_strategy(strategy)
    if normalized == "browser_first":
        return ("browser", "requests")
    if normalized == "browser_only":
        return ("browser",)
    if normalized == "requests_only":
        return ("requests",)
    return ("requests", "browser")


def fetch_html_with_strategy(
    url: str,
    *,
    strategy: Any = DEFAULT_FETCH_STRATEGY,
    session: Optional[requests.Session] = None,
    headers: Optional[Dict[str, str]] = None,
    browser_headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    request_method: str = "get",
    request_body: str = "",
    request_timeout_seconds: int = 15,
    browser_timeout_seconds: int = 20,
    browser_wait_ms: int = 1200,
    request_kwargs: Optional[Dict[str, Any]] = None,
    cancel_event: Any = None,
) -> HtmlFetchResult:
    errors: list[str] = []
    last_failure = HtmlFetchResult(
        success=False,
        engine="",
        error_code="unknown",
        error_message="抓取失败",
    )

    for engine in _strategy_order(strategy):
        if engine == "requests":
            result = _fetch_html_via_requests(
                url,
                session=session,
                headers=headers,
                cookies=cookies,
                request_method=request_method,
                request_body=request_body,
                timeout_seconds=request_timeout_seconds,
                request_kwargs=request_kwargs,
            )
        else:
            result = _render_html_in_browser(
                url,
                headers=browser_headers,
                cookies=cookies,
                request_method=request_method,
                request_body=request_body,
                browser_timeout_seconds=browser_timeout_seconds,
                browser_wait_ms=browser_wait_ms,
                cancel_event=cancel_event,
            )

        if result.success:
            return result

        last_failure = result
        if result.error_message:
            label = "请求抓取" if engine == "requests" else "浏览器渲染"
            errors.append(f"{label}失败：{result.error_message}")

    if errors:
        last_failure.error_message = _trim_error_message("；".join(errors), limit=360)
    return last_failure


__all__ = [
    "HtmlFetchResult",
    "DEFAULT_FETCH_STRATEGY",
    "SUPPORTED_FETCH_STRATEGIES",
    "normalize_fetch_strategy",
    "fetch_html_with_strategy",
]
