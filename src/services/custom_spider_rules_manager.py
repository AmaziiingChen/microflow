"""
自定义爬虫规则管理器 - 线程安全的 JSON 持久化层

提供自定义爬虫规则的读写操作，支持并发访问。
"""

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

from src.utils.rule_ai_config import normalize_rule_ai_config
from src.utils.rss_strategy import attach_rss_strategy_metadata

logger = logging.getLogger(__name__)

_FIELD_DRIFT_MIN_BASELINE_RATE = 0.6
_FIELD_DRIFT_DROP_THRESHOLD = 0.5
_EMPTY_ALERT_THRESHOLD = 3
_RULE_VERSION_HISTORY_LIMIT = 20
_RULE_SNAPSHOT_FIELDS = (
    "rule_id",
    "task_id",
    "task_name",
    "task_purpose",
    "url",
    "source_type",
    "enabled",
    "fetch_strategy",
    "request_method",
    "request_body",
    "request_headers",
    "cookie_string",
    "list_container",
    "item_selector",
    "field_selectors",
    "pagination_mode",
    "next_page_selector",
    "page_url_template",
    "page_start",
    "max_pages",
    "incremental_max_pages",
    "load_more_selector",
    "body_field",
    "detail_strategy",
    "detail_body_selector",
    "detail_time_selector",
    "detail_attachment_selector",
    "detail_image_selector",
    "skip_detail",
    "require_ai_summary",
    "custom_summary_prompt",
    "enable_ai_formatting",
    "enable_ai_summary",
    "formatting_prompt",
    "summary_prompt",
    "source_profile_source",
    "source_template_id",
    "max_items",
)
_RULE_HTML_ONLY_FIELDS = (
    "list_container",
    "item_selector",
    "field_selectors",
    "fetch_strategy",
    "request_method",
    "request_body",
    "request_headers",
    "cookie_string",
    "pagination_mode",
    "next_page_selector",
    "page_url_template",
    "page_start",
    "max_pages",
    "incremental_max_pages",
    "load_more_selector",
    "body_field",
    "detail_strategy",
    "detail_body_selector",
    "detail_time_selector",
    "detail_attachment_selector",
    "detail_image_selector",
    "skip_detail",
)
_RULE_RUNTIME_METADATA_FIELDS = (
    "health",
    "page_summary",
    "test_snapshot",
)
_RULE_EXPORT_FORMAT = "microflow_custom_rules_export"
_RULE_EXPORT_VERSION = "1.0"


def _classify_rule_health_detail(status: str, error_message: str = "") -> str:
    normalized_status = str(status or "").strip().lower()
    if normalized_status == "healthy":
        return "healthy"
    if normalized_status == "empty":
        return "empty"

    message = str(error_message or "").strip().lower()
    if not message:
        return "unknown_error"

    if "未找到列表容器" in message:
        return "list_container_drift"

    if "未找到列表项" in message:
        return "item_selector_drift"

    if any(keyword in message for keyword in ("cancelled", "canceled", "已取消", "取消")):
        return "cancelled"

    if any(
        keyword in message
        for keyword in (
            "未找到列表容器",
            "未找到列表项",
            "selector",
            "选择器",
        )
    ):
        return "selector_error"

    if any(
        keyword in message
        for keyword in (
            "timeout",
            "timed out",
            "超时",
            "network",
            "连接",
            "connection",
            "dns",
            "ssl",
            "证书",
            "proxy",
            "403",
            "404",
            "429",
            "500",
            "502",
            "503",
            "504",
            "请求网页",
            "浏览器验证",
        )
    ):
        return "network_error"

    return "unknown_error"


def _build_rule_snapshot(rule: Dict[str, Any]) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {}
    for field_name in _RULE_SNAPSHOT_FIELDS:
        if field_name not in rule:
            continue
        value = rule.get(field_name)
        if isinstance(value, dict):
            snapshot[field_name] = deepcopy(value)
        elif isinstance(value, list):
            snapshot[field_name] = deepcopy(value)
        else:
            snapshot[field_name] = value
    return snapshot


def _generate_rule_version_id() -> str:
    return f"ver_{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{uuid4().hex[:6]}"


def _normalize_rule_version_history(history: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    if not isinstance(history, list):
        return normalized

    for raw_entry in history:
        if not isinstance(raw_entry, dict):
            continue

        snapshot = _build_rule_snapshot(raw_entry.get("snapshot") or {})
        if not snapshot:
            continue

        saved_at = (
            str(raw_entry.get("saved_at") or "").strip()
            or datetime.now().isoformat()
        )
        version_id = (
            str(raw_entry.get("version_id") or "").strip()
            or _generate_rule_version_id()
        )
        reason = str(raw_entry.get("reason") or "save").strip().lower()
        if reason not in {"save", "rollback"}:
            reason = "save"

        normalized.append(
            {
                "version_id": version_id,
                "saved_at": saved_at,
                "reason": reason,
                "snapshot": snapshot,
            }
        )

    normalized.sort(
        key=lambda item: (
            str(item.get("saved_at") or ""),
            str(item.get("version_id") or ""),
        ),
        reverse=True,
    )
    return normalized[:_RULE_VERSION_HISTORY_LIMIT]


def _build_rule_version_entry(
    rule: Dict[str, Any],
    *,
    reason: str,
    saved_at: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    snapshot = _build_rule_snapshot(rule)
    if not snapshot:
        return None
    return {
        "version_id": _generate_rule_version_id(),
        "saved_at": str(saved_at or datetime.now().isoformat()).strip(),
        "reason": (
            "rollback"
            if str(reason or "").strip().lower() == "rollback"
            else "save"
        ),
        "snapshot": snapshot,
    }


def _cleanup_rule_storage_fields(rule: Dict[str, Any]) -> Dict[str, Any]:
    if str(rule.get("source_type") or "").strip().lower() == "rss":
        for field_name in _RULE_HTML_ONLY_FIELDS:
            rule.pop(field_name, None)
        logger.debug("RSS 规则清理：移除 HTML 选择器字段")

    if isinstance(rule.get("page_summary"), dict):
        rule["page_summary"] = deepcopy(rule.get("page_summary") or {})
    else:
        rule.pop("page_summary", None)

    if isinstance(rule.get("test_snapshot"), dict):
        rule["test_snapshot"] = deepcopy(rule.get("test_snapshot") or {})
    else:
        rule.pop("test_snapshot", None)

    rule["version_history"] = _normalize_rule_version_history(rule.get("version_history"))
    return rule


def _normalize_field_hit_stats(field_hit_stats: Any) -> Dict[str, Dict[str, Any]]:
    normalized: Dict[str, Dict[str, Any]] = {}
    if not isinstance(field_hit_stats, dict):
        return normalized

    for field_name, raw_item in field_hit_stats.items():
        if not isinstance(raw_item, dict):
            continue
        clean_name = str(field_name or "").strip()
        if not clean_name:
            continue
        try:
            total_count = max(int(raw_item.get("total_count") or 0), 0)
        except (TypeError, ValueError):
            total_count = 0
        try:
            hit_count = max(int(raw_item.get("hit_count") or 0), 0)
        except (TypeError, ValueError):
            hit_count = 0
        raw_rate = raw_item.get("hit_rate")
        try:
            hit_rate = float(raw_rate)
        except (TypeError, ValueError):
            hit_rate = (hit_count / total_count) if total_count > 0 else 0.0

        normalized[clean_name] = {
            "hit_count": hit_count,
            "total_count": total_count,
            "hit_rate": round(max(min(hit_rate, 1.0), 0.0), 4),
            "selector": str(raw_item.get("selector") or "").strip(),
        }

    return normalized


def _detect_field_drift(
    baseline_stats: Dict[str, Dict[str, Any]],
    current_stats: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    for field_name, baseline_item in baseline_stats.items():
        current_item = current_stats.get(field_name)
        if not current_item:
            continue

        baseline_rate = float(baseline_item.get("hit_rate") or 0.0)
        current_rate = float(current_item.get("hit_rate") or 0.0)
        drop = round(baseline_rate - current_rate, 4)

        if baseline_rate < _FIELD_DRIFT_MIN_BASELINE_RATE:
            continue
        if current_rate <= 0.0:
            alerts.append(
                {
                    "field": field_name,
                    "baseline_rate": baseline_rate,
                    "current_rate": current_rate,
                    "drop": drop,
                    "reason": "field_missing",
                }
            )
            continue
        if drop >= _FIELD_DRIFT_DROP_THRESHOLD and current_rate < baseline_rate:
            alerts.append(
                {
                    "field": field_name,
                    "baseline_rate": baseline_rate,
                    "current_rate": current_rate,
                    "drop": drop,
                    "reason": "hit_rate_drop",
                }
            )
    return alerts


class CustomSpiderRulesManager:
    """
    自定义爬虫规则管理器 - 单例模式

    提供线程安全的规则读写操作，使用文件锁防止并发冲突。

    Attributes:
        rules_path: 规则文件路径
        _lock: 线程锁
        _rules_cache: 内存中的规则缓存
    """

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls, rules_path: Path = None):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, rules_path: Path = None):
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return

        # 默认使用全局路径
        if rules_path is None:
            from src.core.paths import CUSTOM_SPIDERS_RULES_PATH
            rules_path = CUSTOM_SPIDERS_RULES_PATH

        self._rules_path = Path(rules_path)
        self._lock = threading.RLock()
        self._rules_cache: Optional[Dict[str, Any]] = None
        self._last_mtime: float = 0  # 🌟 新增：记录文件最后修改时间

        # 确保目录存在
        self._rules_path.parent.mkdir(parents=True, exist_ok=True)

        self._initialized = True
        logger.info(f"🕷️ CustomSpiderRulesManager 初始化完成，规则文件: {self._rules_path}")

    def _load_rules(self) -> Dict[str, Any]:
        """
        从文件加载规则（带缓存和热重载）

        Returns:
            规则字典
        """
        # 🌟 热重载：检查文件是否有变更
        if self._rules_cache is not None and self._rules_path.exists():
            try:
                current_mtime = self._rules_path.stat().st_mtime
                if current_mtime != self._last_mtime:
                    # 文件有变更，清除缓存
                    logger.info(f"[热重载] 检测到规则文件变更，重新加载")
                    self._rules_cache = None
            except Exception as e:
                logger.debug(f"检查规则文件修改时间失败: {e}")

        if self._rules_cache is not None:
            return self._rules_cache

        if not self._rules_path.exists():
            # 🌟 修复：不自动创建空文件，直接返回内存中的空规则结构
            # 文件将在第一次实际保存规则时创建
            empty_rules = {
                "version": "1.0",
                "rules": [],
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            self._rules_cache = empty_rules
            logger.debug(f"规则文件不存在，使用内存中的空规则结构")
            return empty_rules

        try:
            with open(self._rules_path, 'r', encoding='utf-8') as f:
                self._rules_cache = json.load(f)
            if isinstance(self._rules_cache.get("rules"), list):
                self._rules_cache["rules"] = [
                    _cleanup_rule_storage_fields(
                        attach_rss_strategy_metadata(normalize_rule_ai_config(rule))
                    )
                    for rule in self._rules_cache.get("rules", [])
                    if isinstance(rule, dict)
                ]
            # 🌟 更新文件修改时间记录
            self._last_mtime = self._rules_path.stat().st_mtime
            logger.info(
                "规则文件加载成功，规则数: %s",
                len(self._rules_cache.get("rules", [])),
            )
            return self._rules_cache
        except Exception as e:
            logger.error(f"加载规则文件失败: {e}")
            return {"version": "1.0", "rules": []}

    def _save_to_file(self, rules_data: Dict[str, Any]) -> bool:
        """
        保存规则到文件

        Args:
            rules_data: 规则数据

        Returns:
            是否保存成功
        """
        try:
            with open(self._rules_path, 'w', encoding='utf-8') as f:
                json.dump(rules_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            logger.error(f"保存规则文件失败: {e}")
            return False

    def save_custom_rule(
        self,
        rule_dict: Dict[str, Any],
        *,
        track_version: bool = True,
    ) -> bool:
        """
        保存自定义规则

        Args:
            rule_dict: 规则字典（SpiderRuleOutput 格式）

        Returns:
            是否保存成功
        """
        with self._lock:
            try:
                rule_dict = _cleanup_rule_storage_fields(
                    attach_rss_strategy_metadata(normalize_rule_ai_config(rule_dict))
                )
                rules_data = self._load_rules()

                # 添加时间戳
                now = datetime.now().isoformat()
                rule_dict['created_at'] = rule_dict.get('created_at') or now
                rule_dict['updated_at'] = now

                # 检查是否已存在相同 rule_id 的规则
                rule_id = rule_dict.get('rule_id')
                existing_index = None

                for i, existing_rule in enumerate(rules_data.get('rules', [])):
                    if existing_rule.get('rule_id') == rule_id:
                        existing_index = i
                        break

                if existing_index is not None:
                    existing_rule = rules_data['rules'][existing_index]
                    existing_history = _normalize_rule_version_history(
                        existing_rule.get("version_history")
                    )
                    existing_snapshot = _build_rule_snapshot(existing_rule)
                    next_snapshot = _build_rule_snapshot(rule_dict)
                    version_entry = None
                    if (
                        track_version
                        and existing_snapshot
                        and existing_snapshot != next_snapshot
                    ):
                        version_entry = _build_rule_version_entry(
                            existing_rule,
                            reason="save",
                            saved_at=now,
                        )
                    if version_entry:
                        existing_history.append(version_entry)
                    history_source = (
                        existing_history
                        if track_version
                        else (
                            rule_dict.get("version_history")
                            if "version_history" in rule_dict
                            else existing_rule.get("version_history")
                        )
                    )
                    rule_dict["version_history"] = _normalize_rule_version_history(
                        history_source
                    )
                    if "health" in existing_rule and "health" not in rule_dict:
                        rule_dict["health"] = deepcopy(existing_rule.get("health"))
                    for field_name in _RULE_RUNTIME_METADATA_FIELDS:
                        if (
                            field_name in existing_rule
                            and field_name not in rule_dict
                            and isinstance(existing_rule.get(field_name), dict)
                        ):
                            rule_dict[field_name] = deepcopy(
                                existing_rule.get(field_name)
                            )
                    if existing_rule.get("created_at"):
                        rule_dict["created_at"] = existing_rule.get("created_at")
                    # 更新现有规则
                    rules_data['rules'][existing_index] = rule_dict
                    logger.info(f"更新规则: {rule_id}")
                else:
                    # 添加新规则
                    rule_dict["version_history"] = _normalize_rule_version_history(
                        rule_dict.get("version_history")
                    )
                    rules_data.setdefault('rules', []).append(rule_dict)
                    logger.info(f"添加新规则: {rule_id}")

                # 更新文件时间戳
                rules_data['updated_at'] = now

                # 保存到文件
                success = self._save_to_file(rules_data)

                # 清除缓存，下次加载时重新读取
                if success:
                    self._rules_cache = None

                return success

            except Exception as e:
                logger.error(f"保存规则失败: {e}")
                return False

    def load_custom_rules(self) -> List[Dict[str, Any]]:
        """
        加载所有自定义规则

        Returns:
            规则列表
        """
        with self._lock:
            try:
                rules_data = self._load_rules()
                return rules_data.get('rules', [])
            except Exception as e:
                logger.error(f"加载规则失败: {e}")
                return []

    def get_rule_by_id(self, rule_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取规则

        Args:
            rule_id: 规则 ID

        Returns:
            规则字典，如果不存在则返回 None
        """
        rules = self.load_custom_rules()
        for rule in rules:
            if rule.get('rule_id') == rule_id:
                return rule
        return None

    def get_rule_versions(self, rule_id: str) -> List[Dict[str, Any]]:
        """
        获取规则的历史版本列表（按时间倒序）。

        Args:
            rule_id: 规则 ID

        Returns:
            历史版本列表
        """
        rule = self.get_rule_by_id(rule_id)
        if not rule:
            return []
        return _normalize_rule_version_history(rule.get("version_history"))

    def build_rules_export_payload(
        self,
        rule_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        构建规则导出 payload。

        Args:
            rule_ids: 可选，仅导出指定 rule_id 列表

        Returns:
            可序列化的导出字典
        """
        selected_rule_ids = {
            str(rule_id or "").strip()
            for rule_id in (rule_ids or [])
            if str(rule_id or "").strip()
        }

        rules = self.load_custom_rules()
        export_rules = [
            deepcopy(rule)
            for rule in rules
            if not selected_rule_ids
            or str(rule.get("rule_id") or "").strip() in selected_rule_ids
        ]

        return {
            "format": _RULE_EXPORT_FORMAT,
            "version": _RULE_EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(),
            "rule_count": len(export_rules),
            "rules": export_rules,
        }

    def get_rule_by_task_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        根据任务 ID 获取规则

        Args:
            task_id: 任务 ID

        Returns:
            规则字典，如果不存在则返回 None
        """
        rules = self.load_custom_rules()
        for rule in rules:
            if rule.get('task_id') == task_id:
                return rule
        return None

    def import_rules_payload(
        self,
        payload: Any,
        *,
        overwrite_existing: bool = True,
    ) -> Dict[str, Any]:
        """
        导入规则 payload。

        Args:
            payload: JSON 解析后的 payload，支持 {rules:[...]} 或直接 list
            overwrite_existing: 已存在规则是否覆盖

        Returns:
            导入结果统计
        """
        if isinstance(payload, dict):
            raw_rules = payload.get("rules")
        elif isinstance(payload, list):
            raw_rules = payload
        else:
            raw_rules = None

        if not isinstance(raw_rules, list):
            return {
                "status": "error",
                "message": "导入文件格式不正确，未找到 rules 列表",
                "imported_count": 0,
                "added_count": 0,
                "updated_count": 0,
                "skipped_count": 0,
                "errors": [],
            }

        result = {
            "status": "success",
            "message": "",
            "imported_count": 0,
            "added_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "errors": [],
            "rule_ids": [],
        }

        required_fields = ("rule_id", "task_id", "task_name", "url")
        for index, raw_rule in enumerate(raw_rules, start=1):
            if not isinstance(raw_rule, dict):
                result["skipped_count"] += 1
                result["errors"].append(f"第 {index} 条规则不是对象，已跳过")
                continue

            normalized_rule = _cleanup_rule_storage_fields(
                attach_rss_strategy_metadata(normalize_rule_ai_config(raw_rule))
            )
            missing_fields = [
                field_name
                for field_name in required_fields
                if not str(normalized_rule.get(field_name) or "").strip()
            ]
            if missing_fields:
                result["skipped_count"] += 1
                result["errors"].append(
                    f"第 {index} 条规则缺少必要字段: {', '.join(missing_fields)}"
                )
                continue

            rule_id = str(normalized_rule.get("rule_id") or "").strip()
            existing_rule = self.get_rule_by_id(rule_id)
            if existing_rule and not overwrite_existing:
                result["skipped_count"] += 1
                result["errors"].append(f"规则 {rule_id} 已存在，已跳过")
                continue

            saved = self.save_custom_rule(
                normalized_rule,
                track_version=False,
            )
            if not saved:
                result["skipped_count"] += 1
                result["errors"].append(f"规则 {rule_id} 保存失败")
                continue

            result["imported_count"] += 1
            if existing_rule:
                result["updated_count"] += 1
            else:
                result["added_count"] += 1
            result["rule_ids"].append(rule_id)

        if result["errors"]:
            if result["imported_count"] > 0:
                result["status"] = "partial"
                result["message"] = (
                    f"成功导入 {result['imported_count']} 条规则，"
                    f"跳过 {result['skipped_count']} 条"
                )
            else:
                result["status"] = "error"
                result["message"] = "没有成功导入任何规则"
        else:
            result["message"] = f"成功导入 {result['imported_count']} 条规则"

        return result

    def delete_rule(self, rule_id: str) -> bool:
        """
        删除规则

        Args:
            rule_id: 规则 ID

        Returns:
            是否删除成功
        """
        with self._lock:
            try:
                rules_data = self._load_rules()
                original_count = len(rules_data.get('rules', []))

                # 过滤掉要删除的规则
                rules_data['rules'] = [
                    rule for rule in rules_data.get('rules', [])
                    if rule.get('rule_id') != rule_id
                ]

                if len(rules_data['rules']) < original_count:
                    # 更新时间戳
                    rules_data['updated_at'] = datetime.now().isoformat()

                    # 保存到文件
                    success = self._save_to_file(rules_data)

                    # 清除缓存
                    if success:
                        self._rules_cache = None

                    logger.info(f"删除规则: {rule_id}")
                    return success
                else:
                    logger.warning(f"未找到要删除的规则: {rule_id}")
                    return False

            except Exception as e:
                logger.error(f"删除规则失败: {e}")
                return False

    def rollback_rule_to_version(
        self, rule_id: str, version_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        将规则回滚到指定历史版本。

        Args:
            rule_id: 规则 ID
            version_id: 历史版本 ID

        Returns:
            回滚后的规则字典；失败时返回 None
        """
        normalized_rule_id = str(rule_id or "").strip()
        normalized_version_id = str(version_id or "").strip()
        if not normalized_rule_id or not normalized_version_id:
            return None

        with self._lock:
            try:
                rules_data = self._load_rules()
                now = datetime.now().isoformat()

                for index, rule in enumerate(rules_data.get("rules", [])):
                    if str(rule.get("rule_id") or "").strip() != normalized_rule_id:
                        continue

                    history = _normalize_rule_version_history(rule.get("version_history"))
                    target_entry = next(
                        (
                            item
                            for item in history
                            if str(item.get("version_id") or "").strip()
                            == normalized_version_id
                        ),
                        None,
                    )
                    if not target_entry:
                        logger.warning(
                            "未找到规则历史版本: rule_id=%s, version_id=%s",
                            normalized_rule_id,
                            normalized_version_id,
                        )
                        return None

                    current_version_entry = _build_rule_version_entry(
                        rule,
                        reason="rollback",
                        saved_at=now,
                    )
                    if current_version_entry:
                        history.append(current_version_entry)

                    rolled_back_rule = deepcopy(rule)
                    for field_name in _RULE_SNAPSHOT_FIELDS:
                        rolled_back_rule.pop(field_name, None)

                    rolled_back_rule.update(
                        deepcopy(target_entry.get("snapshot") or {})
                    )
                    rolled_back_rule["created_at"] = rule.get("created_at") or now
                    rolled_back_rule["updated_at"] = now
                    rolled_back_rule["version_history"] = _normalize_rule_version_history(
                        history
                    )
                    rolled_back_rule = _cleanup_rule_storage_fields(
                        attach_rss_strategy_metadata(
                            normalize_rule_ai_config(rolled_back_rule)
                        )
                    )

                    rules_data["rules"][index] = rolled_back_rule
                    rules_data["updated_at"] = now
                    success = self._save_to_file(rules_data)
                    if success:
                        self._rules_cache = None
                        logger.info(
                            "规则已回滚: rule_id=%s, version_id=%s",
                            normalized_rule_id,
                            normalized_version_id,
                        )
                        return rolled_back_rule
                    return None

                logger.warning(f"未找到规则，无法回滚: {normalized_rule_id}")
                return None
            except Exception as e:
                logger.error(f"回滚规则失败: {e}")
                return None

    def update_rule_status(self, rule_id: str, enabled: bool) -> bool:
        """
        更新规则启用状态

        Args:
            rule_id: 规则 ID
            enabled: 是否启用

        Returns:
            是否更新成功
        """
        with self._lock:
            try:
                rules_data = self._load_rules()

                for rule in rules_data.get('rules', []):
                    if rule.get('rule_id') == rule_id:
                        rule['enabled'] = enabled
                        rule['updated_at'] = datetime.now().isoformat()
                        break
                else:
                    logger.warning(f"未找到规则: {rule_id}")
                    return False

                rules_data['updated_at'] = datetime.now().isoformat()
                success = self._save_to_file(rules_data)

                if success:
                    self._rules_cache = None

                return success

            except Exception as e:
                logger.error(f"更新规则状态失败: {e}")
                return False

    def update_rule_health(
        self,
        rule_id: str,
        *,
        status: str,
        error_message: str = "",
        fetched_count: Optional[int] = None,
        field_hit_stats: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> bool:
        """更新 RSS 规则健康状态。"""
        normalized_status = str(status or "").strip().lower()
        if normalized_status not in {"healthy", "empty", "error"}:
            normalized_status = "error"

        with self._lock:
            try:
                rules_data = self._load_rules()
                now = datetime.now().isoformat()

                for rule in rules_data.get("rules", []):
                    if rule.get("rule_id") != rule_id:
                        continue

                    health = (
                        dict(rule.get("health"))
                        if isinstance(rule.get("health"), dict)
                        else {}
                    )
                    previous_failures = int(health.get("consecutive_failures") or 0)
                    previous_empties = int(health.get("consecutive_empties") or 0)
                    normalized_field_stats = _normalize_field_hit_stats(field_hit_stats)

                    health["status"] = normalized_status
                    health["status_detail"] = _classify_rule_health_detail(
                        normalized_status,
                        error_message,
                    )
                    health["last_checked_at"] = now
                    if fetched_count is not None:
                        try:
                            health["last_fetched_count"] = max(int(fetched_count), 0)
                        except (TypeError, ValueError):
                            pass
                    if normalized_field_stats:
                        health["field_hit_stats"] = normalized_field_stats

                    if normalized_status == "healthy":
                        health["last_success_at"] = now
                        health["consecutive_failures"] = 0
                        health["consecutive_empties"] = 0
                        health["last_error_message"] = ""
                        health["last_known_good_snapshot"] = _build_rule_snapshot(rule)
                        health["last_known_good_at"] = now

                        baseline_stats = _normalize_field_hit_stats(
                            health.get("baseline_field_hit_stats")
                        )
                        drift_alerts = (
                            _detect_field_drift(baseline_stats, normalized_field_stats)
                            if baseline_stats and normalized_field_stats
                            else []
                        )
                        if drift_alerts:
                            health["status_detail"] = "field_drift"
                            health["field_alerts"] = drift_alerts
                            health["is_alerting"] = True
                        else:
                            if normalized_field_stats:
                                health["baseline_field_hit_stats"] = normalized_field_stats
                            health["field_alerts"] = []
                            health["is_alerting"] = False
                    elif normalized_status == "empty":
                        health["last_success_at"] = now
                        health["consecutive_failures"] = 0
                        health["consecutive_empties"] = previous_empties + 1
                        health["last_error_message"] = ""
                        health["field_alerts"] = []
                        if health["consecutive_empties"] >= _EMPTY_ALERT_THRESHOLD:
                            health["status_detail"] = "stale_empty"
                            health["is_alerting"] = True
                        else:
                            health["is_alerting"] = False
                    else:
                        health["last_failure_at"] = now
                        health["consecutive_failures"] = previous_failures + 1
                        health["consecutive_empties"] = 0
                        clean_error = str(error_message or "").strip()
                        if clean_error:
                            health["last_error_message"] = clean_error[:240]
                        health["is_alerting"] = health["consecutive_failures"] >= 2

                    rule["health"] = health
                    rule["updated_at"] = now
                    break
                else:
                    logger.warning(f"未找到规则，无法更新健康状态: {rule_id}")
                    return False

                rules_data["updated_at"] = now
                success = self._save_to_file(rules_data)
                if success:
                    self._rules_cache = None
                return success

            except Exception as e:
                logger.error(f"更新规则健康状态失败: {e}")
                return False

    def clear_cache(self):
        """清除内存缓存"""
        self._rules_cache = None


# 延迟初始化单例
_rules_manager_instance: Optional[CustomSpiderRulesManager] = None
_rules_manager_lock = threading.Lock()


def get_rules_manager() -> CustomSpiderRulesManager:
    """
    获取规则管理器单例（延迟初始化）

    Returns:
        CustomSpiderRulesManager 实例
    """
    global _rules_manager_instance
    if _rules_manager_instance is None:
        with _rules_manager_lock:
            if _rules_manager_instance is None:
                _rules_manager_instance = CustomSpiderRulesManager()
    return _rules_manager_instance
