"""
自定义爬虫规则管理器 - 线程安全的 JSON 持久化层

提供自定义爬虫规则的读写操作，支持并发访问。
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


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
            # 🌟 更新文件修改时间记录
            self._last_mtime = self._rules_path.stat().st_mtime
            logger.info(f"[DEBUG] 规则文件加载成功，规则数: {len(self._rules_cache.get('rules', []))}")
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

    def save_custom_rule(self, rule_dict: Dict[str, Any]) -> bool:
        """
        保存自定义规则

        Args:
            rule_dict: 规则字典（SpiderRuleOutput 格式）

        Returns:
            是否保存成功
        """
        with self._lock:
            try:
                rules_data = self._load_rules()

                # 添加时间戳
                now = datetime.now().isoformat()
                rule_dict['created_at'] = rule_dict.get('created_at') or now
                rule_dict['updated_at'] = now

                # 🌟 清理冗余字段：RSS 规则不需要 HTML 选择器
                if rule_dict.get('source_type') == 'rss':
                    # 移除 HTML 专用字段（避免存储空值）
                    rule_dict.pop('list_container', None)
                    rule_dict.pop('item_selector', None)
                    rule_dict.pop('field_selectors', None)
                    logger.debug(f"RSS 规则清理：移除 HTML 选择器字段")

                # 检查是否已存在相同 rule_id 的规则
                rule_id = rule_dict.get('rule_id')
                existing_index = None

                for i, existing_rule in enumerate(rules_data.get('rules', [])):
                    if existing_rule.get('rule_id') == rule_id:
                        existing_index = i
                        break

                if existing_index is not None:
                    # 更新现有规则
                    rules_data['rules'][existing_index] = rule_dict
                    logger.info(f"更新规则: {rule_id}")
                else:
                    # 添加新规则
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
