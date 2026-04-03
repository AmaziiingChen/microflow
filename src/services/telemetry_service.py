"""匿名遥测服务 - 本地队列、后台 flush 与错误上报。"""

from __future__ import annotations

import hashlib
import json
import locale
import logging
import platform
import random
import threading
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class TelemetryService:
    """负责匿名事件入队、脱敏、批量上报与后台重试。"""

    SCHEMA_VERSION = "1"
    STARTUP_FLUSH_DELAY_SEC = 30
    FLUSH_WAKE_INTERVAL_SEC = 5
    QUEUE_FLUSH_THRESHOLD = 20
    DEFAULT_BATCH_SIZE = 30
    DEFAULT_FLUSH_INTERVAL_SEC = 1800
    DEFAULT_SAMPLE_RATE_STABLE = 0.3
    DEFAULT_SAMPLE_RATE_FULL = 1.0
    SENT_RETENTION_SECONDS = 7 * 24 * 3600
    MAX_PROP_TEXT_LENGTH = 240
    MAX_ERROR_TEXT_LENGTH = 360
    SECRET_KEYS = {
        "api_key",
        "apikey",
        "password",
        "smtp_password",
        "smtppassword",
        "prompt",
        "summary_prompt",
        "formatting_prompt",
        "raw_text",
        "rawtext",
        "raw_markdown",
        "rawmarkdown",
        "markdown",
        "body",
        "content",
        "html",
        "selector",
        "selectors",
        "note",
        "note_text",
    }
    HASH_URL_KEYS = {
        "url",
        "download_url",
        "target_url",
        "feed_url",
        "page_url",
        "source_url",
    }
    CRITICAL_EVENTS = {
        "app_launch",
        "app_exit",
        "startup_check_result",
        "update_check_result",
        "update_available",
        "update_download_click",
        "source_fetch_result",
        "ai_summary_result",
        "ai_regenerate_request",
        "ai_regenerate_result",
        "custom_rule_test_result",
        "custom_rule_save_result",
    }

    def __init__(self, config_service, database, app_version: str):
        self._config_service = config_service
        self._db = database
        self._app_version = str(app_version or "").strip() or "v1.0.0"
        self._session_id = str(uuid.uuid4())
        self._install_id = ""
        self._flush_lock = threading.Lock()
        self._flush_event = threading.Event()
        self._stop_event = threading.Event()
        self._last_flush_at = 0.0
        self._remote_config: Dict[str, Any] = {
            "enabled": None,
            "endpoint": "",
            "batch_size": self.DEFAULT_BATCH_SIZE,
            "flush_interval_sec": self.DEFAULT_FLUSH_INTERVAL_SEC,
            "sample_rate": self._default_sample_rate(),
        }

        self._install_id = self._ensure_install_id()

        self._worker = threading.Thread(
            target=self._flush_loop,
            daemon=True,
            name="TelemetryFlushWorker",
        )
        self._worker.start()

    def _default_sample_rate(self) -> float:
        channel = str(self._config_service.get("channel", "stable") or "stable").strip()
        if channel == "stable":
            return self.DEFAULT_SAMPLE_RATE_STABLE
        return self.DEFAULT_SAMPLE_RATE_FULL

    def _normalize_remote_config(self, remote_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = remote_config if isinstance(remote_config, dict) else {}
        enabled = data.get("enabled")
        normalized_enabled = None if enabled is None else bool(enabled)
        batch_size = max(int(data.get("batch_size") or self.DEFAULT_BATCH_SIZE), 1)
        flush_interval_sec = max(
            int(data.get("flush_interval_sec") or self.DEFAULT_FLUSH_INTERVAL_SEC),
            60,
        )
        try:
            sample_rate = float(data.get("sample_rate", self._default_sample_rate()))
        except (TypeError, ValueError):
            sample_rate = self._default_sample_rate()
        sample_rate = min(max(sample_rate, 0.0), 1.0)
        endpoint = str(data.get("endpoint") or "").strip()
        return {
            "enabled": normalized_enabled,
            "endpoint": endpoint,
            "batch_size": batch_size,
            "flush_interval_sec": flush_interval_sec,
            "sample_rate": sample_rate,
        }

    def update_remote_config(self, remote_config: Optional[Dict[str, Any]]) -> None:
        self._remote_config = self._normalize_remote_config(remote_config)
        self.request_flush()

    def _ensure_install_id(self) -> str:
        current = str(
            self._config_service.get("telemetryInstallId", "") or ""
        ).strip()
        if current:
            return current

        generated = str(uuid.uuid4())
        try:
            config_dict = self._config_service.to_dict()
            config_dict["telemetryInstallId"] = generated
            if not config_dict.get("telemetryConsentStatus"):
                config_dict["telemetryConsentStatus"] = "undecided"
            self._config_service.save(config_dict)
            return generated
        except Exception as e:
            logger.warning(f"写入 telemetryInstallId 失败，退化为当前会话标识: {e}")
            return generated

    @staticmethod
    def _hash_text(value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            return ""
        return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]

    def _hash_url_domain(self, value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        hostname = str(parsed.hostname or "").strip().lower()
        if not hostname:
            return ""
        return self._hash_text(hostname)

    def _sanitize_props(self, event_name: str, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        payload = dict(props or {})
        sanitized: Dict[str, Any] = {}

        is_custom_source = bool(payload.get("is_custom_source"))
        source_type = str(payload.get("source_type") or "").strip().lower()
        if source_type == "rss":
            is_custom_source = True
        if str(payload.get("rule_id") or "").strip():
            is_custom_source = True

        for raw_key, raw_value in payload.items():
            key = str(raw_key or "").strip()
            if not key:
                continue

            lower_key = key.lower()
            if lower_key in self.SECRET_KEYS:
                continue

            if raw_value is None:
                continue

            if lower_key in self.HASH_URL_KEYS or lower_key.endswith("_url"):
                domain_hash = self._hash_url_domain(str(raw_value))
                if domain_hash:
                    sanitized[f"{key}DomainHash"] = domain_hash
                continue

            if key == "source_name" and is_custom_source:
                hashed = self._hash_text(str(raw_value))
                if hashed:
                    sanitized["sourceNameHash"] = hashed
                continue

            if isinstance(raw_value, bool):
                sanitized[key] = raw_value
                continue

            if isinstance(raw_value, (int, float)):
                sanitized[key] = raw_value
                continue

            if isinstance(raw_value, list):
                sanitized[key] = [
                    item
                    for item in (
                        self._sanitize_props(event_name, {"item": value}).get("item")
                        for value in raw_value[:12]
                    )
                    if item not in (None, "", [])
                ]
                continue

            if isinstance(raw_value, dict):
                nested = self._sanitize_props(event_name, raw_value)
                if nested:
                    sanitized[key] = nested
                continue

            text = str(raw_value).strip()
            if not text:
                continue
            max_len = (
                self.MAX_ERROR_TEXT_LENGTH
                if event_name.startswith("error_")
                else self.MAX_PROP_TEXT_LENGTH
            )
            if len(text) > max_len:
                text = f"{text[:max_len].rstrip()}..."
            sanitized[key] = text

        if is_custom_source:
            sanitized["is_custom_source"] = True
        return sanitized

    def _is_consent_enabled(self) -> bool:
        return (
            str(
                self._config_service.get("telemetryConsentStatus", "undecided")
                or "undecided"
            ).strip()
            == "enabled"
        )

    def _is_usage_enabled(self) -> bool:
        return bool(self._config_service.get("telemetryEnabled", False))

    def _is_error_enabled(self) -> bool:
        return bool(self._config_service.get("telemetryErrorReportsEnabled", False))

    def _is_event_allowed(self, event_name: str) -> bool:
        remote_enabled = self._remote_config.get("enabled")
        if remote_enabled is False:
            return False

        if event_name.startswith("error_"):
            return self._is_consent_enabled() and self._is_error_enabled()

        return self._is_consent_enabled() and self._is_usage_enabled()

    def _should_sample_event(self, event_name: str) -> bool:
        if event_name.startswith("error_") or event_name in self.CRITICAL_EVENTS:
            return True
        sample_rate = float(self._remote_config.get("sample_rate") or self._default_sample_rate())
        if sample_rate >= 1.0:
            return True
        if sample_rate <= 0:
            return False
        return random.random() <= sample_rate

    def _build_event_payload(self, event_name: str, props: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        try:
            locale_info = locale.getlocale()
            locale_name = (
                locale_info[0]
                if isinstance(locale_info, tuple) and locale_info and locale_info[0]
                else "unknown"
            )
        except Exception:
            locale_name = "unknown"
        return {
            "event_id": str(uuid.uuid4()),
            "event": event_name,
            "ts": int(time.time()),
            "install_id": self._install_id,
            "session_id": self._session_id,
            "app_version": self._app_version,
            "channel": str(self._config_service.get("channel", "stable") or "stable"),
            "platform": platform.system().lower(),
            "platform_version": platform.version(),
            "arch": platform.machine(),
            "locale": locale_name,
            "props": self._sanitize_props(event_name, props),
        }

    def track(
        self,
        event_name: str,
        props: Optional[Dict[str, Any]] = None,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        safe_event_name = str(event_name or "").strip()
        if not safe_event_name:
            return {"status": "error", "message": "缺少事件名"}

        if not force and not self._is_event_allowed(safe_event_name):
            return {"status": "skipped", "reason": "disabled"}

        if not force and not self._should_sample_event(safe_event_name):
            return {"status": "skipped", "reason": "sampled_out"}

        payload = self._build_event_payload(safe_event_name, props)
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        saved = self._db.enqueue_telemetry_event(
            event_id=payload["event_id"],
            event_name=safe_event_name,
            payload_json=serialized,
            created_at=payload["ts"],
            next_retry_at=payload["ts"],
        )
        if not saved:
            return {"status": "error", "message": "事件入队失败"}

        stats = self._db.get_telemetry_queue_stats()
        if stats.get("ready_count", 0) >= self.QUEUE_FLUSH_THRESHOLD:
            self.request_flush()
        return {"status": "queued", "event_id": payload["event_id"]}

    def request_flush(self) -> None:
        self._flush_event.set()

    def _build_retry_delay(self, retry_count: int) -> int:
        if retry_count <= 1:
            return 60
        if retry_count == 2:
            return 5 * 60
        if retry_count == 3:
            return 30 * 60
        return 12 * 3600

    def _flush_loop(self) -> None:
        startup_deadline = time.time() + self.STARTUP_FLUSH_DELAY_SEC
        while not self._stop_event.is_set():
            now = time.time()
            periodic_interval = max(
                int(self._remote_config.get("flush_interval_sec") or self.DEFAULT_FLUSH_INTERVAL_SEC),
                60,
            )
            should_periodic_flush = (
                now >= startup_deadline
                and (now - self._last_flush_at) >= periodic_interval
            )
            if self._flush_event.is_set() or should_periodic_flush:
                self._flush_event.clear()
                try:
                    self.flush()
                except Exception as e:
                    logger.debug(f"后台 flush telemetry 失败: {e}")
            self._stop_event.wait(self.FLUSH_WAKE_INTERVAL_SEC)

    def flush(self, *, force: bool = False) -> Dict[str, Any]:
        if not self._flush_lock.acquire(blocking=False):
            return {"status": "busy", "message": "正在上报"}

        try:
            if not force and not (
                self._is_consent_enabled()
                and (self._is_usage_enabled() or self._is_error_enabled())
            ):
                return {"status": "skipped", "message": "遥测已关闭"}

            endpoint = str(self._remote_config.get("endpoint") or "").strip()
            if not endpoint:
                return {"status": "skipped", "message": "未配置上报地址"}

            batch_size = max(
                int(self._remote_config.get("batch_size") or self.DEFAULT_BATCH_SIZE),
                1,
            )
            rows = self._db.get_pending_telemetry_events(limit=batch_size)
            if not rows:
                return {"status": "empty", "accepted": 0}

            events = []
            for row in rows:
                try:
                    events.append(json.loads(row.get("payload_json") or "{}"))
                except Exception:
                    continue

            if not events:
                stale_ids = [str(row.get("event_id") or "").strip() for row in rows]
                self._db.mark_telemetry_events_sent(stale_ids)
                return {"status": "success", "accepted": 0}

            request_payload = {
                "schema_version": self.SCHEMA_VERSION,
                "channel": str(self._config_service.get("channel", "stable") or "stable"),
                "events": events,
            }

            response = requests.post(endpoint, json=request_payload, timeout=8)
            if response.status_code < 200 or response.status_code >= 300:
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text[:200]}".strip()
                )

            accepted_ids = [
                str(row.get("event_id") or "").strip()
                for row in rows
                if str(row.get("event_id") or "").strip()
            ]
            accepted_count = self._db.mark_telemetry_events_sent(accepted_ids)
            self._last_flush_at = time.time()
            self._db.prune_sent_telemetry_events(
                int(time.time()) - self.SENT_RETENTION_SECONDS
            )
            return {"status": "success", "accepted": accepted_count}
        except Exception as e:
            now_ts = int(time.time())
            failure_items = []
            for row in rows if "rows" in locals() else []:
                retry_count = int(row.get("retry_count") or 0) + 1
                failure_items.append(
                    {
                        "event_id": str(row.get("event_id") or "").strip(),
                        "retry_count": retry_count,
                        "next_retry_at": now_ts + self._build_retry_delay(retry_count),
                        "last_error": str(e)[: self.MAX_ERROR_TEXT_LENGTH],
                    }
                )
            if failure_items:
                self._db.mark_telemetry_events_failed(failure_items)
            logger.debug(f"遥测事件上报失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            self._flush_lock.release()

    def get_status_payload(self) -> Dict[str, Any]:
        queue_stats = self._db.get_telemetry_queue_stats()
        return {
            "status": "success",
            "enabled": self._is_usage_enabled(),
            "error_reports_enabled": self._is_error_enabled(),
            "consent_status": str(
                self._config_service.get("telemetryConsentStatus", "undecided")
                or "undecided"
            ),
            "install_id_ready": bool(self._install_id),
            "session_id": self._session_id,
            "remote_enabled": self._remote_config.get("enabled"),
            "endpoint_configured": bool(self._remote_config.get("endpoint")),
            "batch_size": int(self._remote_config.get("batch_size") or self.DEFAULT_BATCH_SIZE),
            "flush_interval_sec": int(
                self._remote_config.get("flush_interval_sec")
                or self.DEFAULT_FLUSH_INTERVAL_SEC
            ),
            "sample_rate": float(
                self._remote_config.get("sample_rate") or self._default_sample_rate()
            ),
            "queue": queue_stats,
        }

    def clear_queue(self) -> Dict[str, Any]:
        deleted = self._db.clear_telemetry_events()
        return {"status": "success", "deleted_count": int(deleted or 0)}

    def record_python_error(
        self,
        exc_type: Any,
        exc_value: BaseException,
        exc_traceback: Any,
        *,
        is_fatal: bool = False,
        thread_name: str = "",
    ) -> Dict[str, Any]:
        stack_text = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )
        return self.track(
            "error_python",
            {
                "module": getattr(exc_type, "__module__", ""),
                "error_type": getattr(exc_type, "__name__", str(exc_type)),
                "error_message_short": str(exc_value or "")[: self.MAX_ERROR_TEXT_LENGTH],
                "stack_hash": self._hash_text(stack_text),
                "is_fatal": bool(is_fatal),
                "thread_name": thread_name,
            },
        )

    def record_frontend_error(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        data = payload if isinstance(payload, dict) else {}
        stack = str(data.get("stack") or "").strip()
        return self.track(
            "error_js",
            {
                "page": str(data.get("page") or "main").strip() or "main",
                "error_type": str(data.get("error_type") or data.get("type") or "js_error"),
                "message": str(data.get("message") or "")[: self.MAX_ERROR_TEXT_LENGTH],
                "line": int(data.get("line") or 0),
                "column": int(data.get("column") or 0),
                "stack_hash": self._hash_text(stack),
            },
        )

    def shutdown(self, *, flush: bool = True) -> None:
        if flush:
            try:
                self.flush(force=True)
            except Exception:
                pass
        self._stop_event.set()
        self._flush_event.set()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
