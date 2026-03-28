"""配置管理服务 - 负责配置的加载、保存和验证"""

import json
import os
import logging
import hmac
import hashlib
import uuid
import time
import shutil
import tempfile
from typing import Dict, Any, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """应用配置数据类"""
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model_name: str = "deepseek-chat"
    prompt: str = ""
    auto_start: bool = False
    mute_mode: bool = False
    track_mode: str = "continuous"
    font_family: str = "sans-serif"  # 🌟 新增：字体风格
    custom_font_path: str = ""  # 🌟 新增：外部字体路径
    custom_font_name: str = ""  # 🌟 新增：外部字体原始名称
    subscribed_sources: list = field(default_factory=list)  # 🌟 新增：订阅的来源列表
    polling_interval: int = 900  # 🌟 新增：守护进程轮询间隔（秒），默认 15 分钟
    update_cooldown: int = 60  # 🌟 新增：手动更新冷却时间（秒），默认 60 秒
    is_locked: bool = False  # 🌟 新增：配置锁定状态（用于防止修改）
    api_balance_ok: bool = True  # 🌟 新增：API 余额状态（默认正常）
    # 🔐 安全字段：防篡改签名
    config_sign: str = ""  # HMAC-SHA256 签名
    last_cloud_sync_time: float = 0.0  # 最后一次成功获取云端授权的时间戳
    device_id: str = ""  # 设备唯一标识（基于 MAC 地址）
    # 📧 邮件推送配置
    email_notify_enabled: bool = False  # 是否启用邮件通知
    smtp_host: str = ""  # SMTP 服务器地址
    smtp_port: int = 465  # SMTP 服务器端口
    smtp_user: str = ""  # SMTP 用户名
    smtp_password: str = ""  # SMTP 密码/授权码
    subscriber_list: list = field(default_factory=list)  # 订阅者邮箱列表
    # 🤖 多模型配置（用于 AI 爬虫多模型投票）
    secondary_models: list = field(default_factory=list)  # 备选模型列表 [{baseUrl, apiKey, modelName}]


class ConfigService:
    """
    配置管理服务 - 单一职责：配置的持久化与热更新

    使用方式：
        config_service = ConfigService(config_path, default_prompt)
        config = config_service.load()
        config_service.save(new_config)
    """

    # 🔐 安全常量：用于 HMAC 签名的加盐密钥
    _SECRET_KEY = b"MicroFlow_Secure_Salt_2026"

    def __init__(self, config_path: str, default_prompt: str = ""):
        """
        初始化配置服务

        Args:
            config_path: 配置文件路径
            default_prompt: 默认的 AI 提示词
        """
        self.config_path = config_path
        self._default_prompt = default_prompt
        self._config: Optional[AppConfig] = None
        self._last_load_failed = False
        self._has_loaded_successfully = False

        # 确保配置目录存在
        config_dir = os.path.dirname(config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)

    def _get_device_fingerprint(self) -> str:
        """
        获取设备唯一指纹（基于 MAC 地址）

        Returns:
            设备指纹字符串
        """
        try:
            # uuid.getnode() 返回设备的 MAC 地址（整数形式）
            mac = uuid.getnode()
            return f"device_{mac:x}"
        except Exception as e:
            logger.warning(f"获取设备指纹失败: {e}")
            return "device_unknown"

    def _generate_signature(self, config_dict: Dict[str, Any]) -> str:
        """
        生成配置的 HMAC-SHA256 签名

        签名算法：将关键字段（isLocked, last_cloud_sync_time, device_id）拼接后
        使用 HMAC-SHA256 进行签名，确保配置不被篡改。

        Args:
            config_dict: 配置字典（前端格式）

        Returns:
            签名的 hex 字符串
        """
        try:
            # 提取用于签名的关键字段
            is_locked = str(config_dict.get('isLocked', False))
            sync_time = str(config_dict.get('lastCloudSyncTime', 0.0))
            device_id = str(config_dict.get('deviceId', ''))

            # 拼接签名字符串
            sign_payload = f"{is_locked}|{sync_time}|{device_id}"

            # 计算 HMAC-SHA256 签名
            signature = hmac.new(
                self._SECRET_KEY,
                sign_payload.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()

            return signature
        except Exception as e:
            logger.error(f"生成签名失败: {e}")
            return ""

    @staticmethod
    def _normalize_email_list(emails: Any) -> list:
        """
        规范化邮箱列表：去空、去重、按大小写不敏感方式合并。

        保留首个出现的原始写法，便于界面展示。
        """
        if not isinstance(emails, list):
            return []

        normalized = []
        seen = set()
        for email in emails:
            cleaned = str(email).strip() if email is not None else ""
            if not cleaned:
                continue

            key = cleaned.lower()
            if key in seen:
                continue

            seen.add(key)
            normalized.append(cleaned)

        return normalized

    def load(self) -> AppConfig:
        """
        从文件加载配置

        Returns:
            AppConfig 配置对象
        """
        default = AppConfig(prompt=self._default_prompt)

        if not os.path.exists(self.config_path):
            self._config = default
            self._last_load_failed = False
            self._has_loaded_successfully = True
            return self._config

        backup_path = f"{self.config_path}.bak"

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 🔐 安全检查：验证签名
            stored_sign = data.get('configSign', '')
            is_locked_from_file = data.get('isLocked', False)

            # 🔐 向后兼容：如果缺少签名字段，视为旧版首次升级，自动放行
            if not stored_sign:
                logger.info("检测到旧版配置文件（无签名），自动升级为新版签名机制")
                # 不强制锁定，后续 save() 会自动添加签名
                is_locked = is_locked_from_file
            else:
                # 重新计算签名并验证
                expected_sign = self._generate_signature(data)

                if stored_sign != expected_sign:
                    logger.warning(f"🔐 配置签名校验失败！文件可能被篡改。")
                    logger.warning(f"  - 存储签名: {stored_sign[:16]}...")
                    logger.warning(f"  - 计算签名: {expected_sign[:16]}...")
                    # 🔐 强制锁定：签名不匹配，视为被篡改
                    is_locked = True
                else:
                    is_locked = is_locked_from_file
                    logger.debug("🔐 配置签名校验通过")

            self._config = AppConfig(
                base_url=data.get('baseUrl', default.base_url),
                api_key=data.get('apiKey', default.api_key),
                model_name=data.get('modelName', default.model_name),
                prompt=data.get('prompt', default.prompt),
                auto_start=data.get('autoStart', default.auto_start),
                mute_mode=data.get('muteMode', default.mute_mode),
                track_mode=data.get('trackMode', default.track_mode),
                font_family=data.get('fontFamily', default.font_family),  # 🌟 新增
                custom_font_path=data.get('customFontPath', default.custom_font_path),  # 🌟 新增
                custom_font_name=data.get('customFontName', default.custom_font_name),  # 🌟 新增
                subscribed_sources=data.get('subscribedSources', default.subscribed_sources),  # 🌟 新增
                polling_interval=data.get('pollingInterval', default.polling_interval),  # 🌟 新增
                update_cooldown=data.get('updateCooldown', default.update_cooldown),  # 🌟 新增：冷却时间
                is_locked=is_locked,  # 🔐 使用签名验证后的锁定状态
                api_balance_ok=data.get('apiBalanceOk', default.api_balance_ok),  # 🌟 新增：API 余额状态
                # 🔐 安全字段
                config_sign=data.get('configSign', ''),
                last_cloud_sync_time=data.get('lastCloudSyncTime', 0.0),
                device_id=data.get('deviceId', ''),
                # 📧 邮件推送配置
                email_notify_enabled=data.get('emailNotifyEnabled', False),
                smtp_host=data.get('smtpHost', ''),
                smtp_port=data.get('smtpPort', 465),
                smtp_user=data.get('smtpUser', ''),
                smtp_password=data.get('smtpPassword', ''),
                subscriber_list=self._normalize_email_list(data.get('subscriberList', [])),
                # 🤖 多模型配置（用于 AI 爬虫多模型投票）
                secondary_models=data.get('secondaryModels', []),
            )
            self._last_load_failed = False
            self._has_loaded_successfully = True
            return self._config

        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            try:
                if os.path.exists(backup_path):
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._config = AppConfig(
                        base_url=data.get('baseUrl', default.base_url),
                        api_key=data.get('apiKey', default.api_key),
                        model_name=data.get('modelName', default.model_name),
                        prompt=data.get('prompt', default.prompt),
                        auto_start=data.get('autoStart', default.auto_start),
                        mute_mode=data.get('muteMode', default.mute_mode),
                        track_mode=data.get('trackMode', default.track_mode),
                        font_family=data.get('fontFamily', default.font_family),
                        custom_font_path=data.get('customFontPath', default.custom_font_path),
                        custom_font_name=data.get('customFontName', default.custom_font_name),
                        subscribed_sources=data.get('subscribedSources', default.subscribed_sources),
                        polling_interval=data.get('pollingInterval', default.polling_interval),
                        update_cooldown=data.get('updateCooldown', default.update_cooldown),
                        is_locked=data.get('isLocked', False),
                        api_balance_ok=data.get('apiBalanceOk', default.api_balance_ok),
                        config_sign=data.get('configSign', ''),
                        last_cloud_sync_time=data.get('lastCloudSyncTime', 0.0),
                        device_id=data.get('deviceId', ''),
                        email_notify_enabled=data.get('emailNotifyEnabled', False),
                        smtp_host=data.get('smtpHost', ''),
                        smtp_port=data.get('smtpPort', 465),
                        smtp_user=data.get('smtpUser', ''),
                        smtp_password=data.get('smtpPassword', ''),
                        subscriber_list=self._normalize_email_list(data.get('subscriberList', [])),
                        secondary_models=data.get('secondaryModels', []),
                    )
                    self._last_load_failed = False
                    self._has_loaded_successfully = True
                    logger.warning("配置文件读取失败，已从备份恢复")
                    return self._config
            except Exception as backup_err:
                logger.warning(f"配置备份恢复失败: {backup_err}")

            if self._config is None:
                self._config = default
            self._last_load_failed = True
            self._has_loaded_successfully = False
            return self._config

    def save(self, config_dict: Dict[str, Any]) -> bool:
        """
        保存配置到文件

        Args:
            config_dict: 配置字典（前端格式）

        Returns:
            是否保存成功
        """
        try:
            # 🔐 安全步骤 0：检查锁定状态
            if self._config and self._config.is_locked:
                logger.warning("⚠️ 配置已锁定，拒绝保存操作")
                return False

            # 🌟 类型转换：确保数值字段是正确的类型
            if 'max_items' in config_dict and config_dict['max_items'] is not None:
                try:
                    config_dict['max_items'] = int(config_dict['max_items'])
                except (ValueError, TypeError):
                    config_dict['max_items'] = 20

            if 'pollingInterval' in config_dict and config_dict['pollingInterval'] is not None:
                try:
                    config_dict['pollingInterval'] = int(config_dict['pollingInterval'])
                except (ValueError, TypeError):
                    config_dict['pollingInterval'] = 60

            if 'smtpPort' in config_dict and config_dict['smtpPort'] is not None:
                try:
                    config_dict['smtpPort'] = int(config_dict['smtpPort'])
                except (ValueError, TypeError):
                    config_dict['smtpPort'] = 465

            if 'subscriberList' in config_dict:
                config_dict['subscriberList'] = self._normalize_email_list(
                    config_dict.get('subscriberList', [])
                )

            # 如果配置从未成功加载过，避免把前端初始空白状态写回磁盘。
            # 这种情况下优先保留内存中的已知配置，防止把有效设置覆盖成空值。
            if self._config is not None and not self._has_loaded_successfully:
                current = self._config

                def keep_existing(key: str, current_value: Any) -> None:
                    value = config_dict.get(key)
                    if value in (None, "", []):
                        if current_value not in (None, "", []):
                            config_dict[key] = current_value

                keep_existing('baseUrl', current.base_url)
                keep_existing('apiKey', current.api_key)
                keep_existing('modelName', current.model_name)
                keep_existing('prompt', current.prompt)
                keep_existing('fontFamily', current.font_family)
                keep_existing('customFontPath', current.custom_font_path)
                keep_existing('customFontName', current.custom_font_name)
                keep_existing('subscribedSources', current.subscribed_sources)
                keep_existing('secondaryModels', current.secondary_models)
                keep_existing('smtpHost', current.smtp_host)
                keep_existing('smtpUser', current.smtp_user)
                keep_existing('smtpPassword', current.smtp_password)
                keep_existing('subscriberList', current.subscriber_list)

            # 确保目录存在
            config_dir = os.path.dirname(self.config_path)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)

            # 🔐 安全步骤 1：自动更新设备指纹
            device_id = self._get_device_fingerprint()
            config_dict['deviceId'] = device_id

            # 🔐 安全步骤 2：确保时间戳字段存在（如果未设置则使用当前时间）
            if 'lastCloudSyncTime' not in config_dict or config_dict['lastCloudSyncTime'] == 0:
                # 尝试保留旧值
                old_sync_time = 0.0
                if self._config and hasattr(self._config, 'last_cloud_sync_time'):
                    old_sync_time = self._config.last_cloud_sync_time
                config_dict['lastCloudSyncTime'] = old_sync_time

            # 🔐 安全步骤 3：计算并添加签名
            signature = self._generate_signature(config_dict)
            config_dict['configSign'] = signature

            logger.debug(f"🔐 已生成配置签名: {signature[:16]}... (设备: {device_id})")

            # 📧 邮件配置日志
            logger.debug(f"📧 邮件配置保存: emailNotifyEnabled={config_dict.get('emailNotifyEnabled', False)}")
            logger.debug(f"📧 邮件配置保存: smtpHost={config_dict.get('smtpHost', '')}")
            logger.debug(f"📧 邮件配置保存: smtpUser={config_dict.get('smtpUser', '')}")
            logger.debug(f"📧 邮件配置保存: subscriberList={config_dict.get('subscriberList', [])}")

            # 写入文件：先写临时文件，再原子替换，避免并发读到半截 JSON
            backup_path = f"{self.config_path}.bak"
            fd, tmp_path = tempfile.mkstemp(
                dir=config_dir or None,
                prefix=os.path.basename(self.config_path) + ".",
                suffix=".tmp",
            )
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(config_dict, f, ensure_ascii=False, indent=4)
                    f.flush()
                    os.fsync(f.fileno())

                if os.path.exists(self.config_path):
                    try:
                        shutil.copy2(self.config_path, backup_path)
                    except Exception as copy_err:
                        logger.debug(f"更新配置备份失败: {copy_err}")

                os.replace(tmp_path, self.config_path)
            finally:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

            # 更新内存中的配置
            self._config = AppConfig(
                base_url=config_dict.get('baseUrl', self._config.base_url if self._config else ""),
                api_key=config_dict.get('apiKey', ''),
                model_name=config_dict.get('modelName', 'deepseek-chat'),
                prompt=config_dict.get('prompt', self._default_prompt),
                auto_start=config_dict.get('autoStart', False),
                mute_mode=config_dict.get('muteMode', False),
                track_mode=config_dict.get('trackMode', 'continuous'),
                font_family=config_dict.get('fontFamily', 'sans-serif'),  # 🌟 新增
                custom_font_path=config_dict.get('customFontPath', ''),  # 🌟 新增
                custom_font_name=config_dict.get('customFontName', ''),  # 🌟 新增
                subscribed_sources=config_dict.get('subscribedSources', []),  # 🌟 新增
                polling_interval=config_dict.get('pollingInterval', 900),  # 🌟 新增
                update_cooldown=config_dict.get('updateCooldown', 60),  # 🌟 新增：冷却时间
                is_locked=config_dict.get('isLocked', False),  # 🌟 新增：配置锁定状态
                api_balance_ok=config_dict.get('apiBalanceOk', True),  # 🌟 新增：API 余额状态
                # 🔐 安全字段
                config_sign=signature,
                last_cloud_sync_time=config_dict.get('lastCloudSyncTime', 0.0),
                device_id=device_id,
                # 📧 邮件推送配置
                email_notify_enabled=config_dict.get('emailNotifyEnabled', False),
                smtp_host=config_dict.get('smtpHost', ''),
                smtp_port=config_dict.get('smtpPort', 465),
                smtp_user=config_dict.get('smtpUser', ''),
                smtp_password=config_dict.get('smtpPassword', ''),
                subscriber_list=self._normalize_email_list(config_dict.get('subscriberList', [])),
                # 🤖 多模型配置（用于 AI 爬虫多模型投票）
                secondary_models=config_dict.get('secondaryModels', []),
            )
            self._last_load_failed = False
            self._has_loaded_successfully = True

            logger.info("配置已成功保存")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    @property
    def current(self) -> AppConfig:
        """获取当前配置（如果未加载则先加载）"""
        if self._config is None:
            self.load()
        return self._config # type: ignore

    def to_dict(self) -> Dict[str, Any]:
        """
        转换为前端期望的字典格式

        Returns:
            前端格式的配置字典
        """
        config = self.current
        return {
            "baseUrl": config.base_url,
            "apiKey": config.api_key,
            "modelName": config.model_name,
            "prompt": config.prompt,
            "autoStart": config.auto_start,
            "muteMode": config.mute_mode,
            "trackMode": config.track_mode,
            "fontFamily": config.font_family,  # 🌟 新增
            "customFontPath": config.custom_font_path,  # 🌟 新增
            "customFontName": config.custom_font_name,  # 🌟 新增
            "subscribedSources": config.subscribed_sources,  # 🌟 新增
            "pollingInterval": config.polling_interval,  # 🌟 新增
            "updateCooldown": config.update_cooldown,  # 🌟 新增：冷却时间
            "isLocked": config.is_locked,  # 🌟 新增：配置锁定状态
            "apiBalanceOk": config.api_balance_ok,  # 🌟 新增：API 余额状态
            # 🔐 安全字段
            "configSign": config.config_sign,
            "lastCloudSyncTime": config.last_cloud_sync_time,
            "deviceId": config.device_id,
            # 📧 邮件推送配置
            "emailNotifyEnabled": config.email_notify_enabled,
            "smtpHost": config.smtp_host,
            "smtpPort": config.smtp_port,
            "smtpUser": config.smtp_user,
            "smtpPassword": config.smtp_password,
            "subscriberList": config.subscriber_list,
            # 🤖 多模型配置（用于 AI 爬虫多模型投票）
            "secondaryModels": config.secondary_models,
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取单个配置项（支持热重载）

        Args:
            key: 配置键名（支持前端格式如 'apiKey' 或 Python 格式如 'api_key'）
            default: 默认值

        Returns:
            配置值
        """
        # 🌟 热重载：每次读取时重新从文件加载配置
        if os.path.exists(self.config_path):
            try:
                file_mtime = os.path.getmtime(self.config_path)
                if not hasattr(self, '_last_mtime') or file_mtime != self._last_mtime:
                    self._last_mtime = file_mtime
                    self.load()  # 文件有变更时重新加载
            except Exception:
                pass

        config = self.current

        # 支持两种格式的键名
        key_mapping = {
            'baseUrl': 'base_url',
            'apiKey': 'api_key',
            'modelName': 'model_name',
            'autoStart': 'auto_start',
            'muteMode': 'mute_mode',
            'trackMode': 'track_mode',
            'fontFamily': 'font_family',  # 🌟 新增
            'customFontPath': 'custom_font_path',  # 🌟 新增
            'customFontName': 'custom_font_name',  # 🌟 新增
            'subscribedSources': 'subscribed_sources',  # 🌟 新增
            'pollingInterval': 'polling_interval',  # 🌟 新增
            'updateCooldown': 'update_cooldown',  # 🌟 新增：冷却时间
            'isLocked': 'is_locked',  # 🌟 新增：配置锁定状态
            'apiBalanceOk': 'api_balance_ok',  # 🌟 新增：API 余额状态
            # 🔐 安全字段
            'configSign': 'config_sign',
            'lastCloudSyncTime': 'last_cloud_sync_time',
            'deviceId': 'device_id',
            # 📧 邮件推送配置
            'emailNotifyEnabled': 'email_notify_enabled',
            'smtpHost': 'smtp_host',
            'smtpPort': 'smtp_port',
            'smtpUser': 'smtp_user',
            'smtpPassword': 'smtp_password',
            'subscriberList': 'subscriber_list',
            # 🤖 多模型配置（用于 AI 爬虫多模型投票）
            'secondaryModels': 'secondary_models',
        }

        # 转换键名
        attr_name = key_mapping.get(key, key)

        return getattr(config, attr_name, default)

    @property
    def last_load_failed(self) -> bool:
        return self._last_load_failed

    @property
    def has_loaded_successfully(self) -> bool:
        return self._has_loaded_successfully

    def reload(self) -> bool:
        """
        强制重新加载配置文件（用于外部修改后的手动刷新）

        Returns:
            是否成功加载
        """
        try:
            self.load()
            logger.info("配置已热重载")
            return True
        except Exception as e:
            logger.error(f"配置热重载失败: {e}")
            return False

    def get_api_balance_ok(self) -> bool:
        """获取 API 余额是否正常（默认 True）"""
        return self.get('apiBalanceOk', True)

    def set_api_balance_ok(self, ok: bool) -> bool:
        """
        设置 API 余额状态

        Args:
            ok: True 表示余额正常，False 表示欠费

        Returns:
            是否设置成功
        """
        try:
            # 使用 to_dict() 获取当前完整配置，确保签名一致性
            config_dict = self.to_dict()

            # 更新余额状态
            config_dict['apiBalanceOk'] = ok

            # 调用 save() 方法，自动更新签名
            return self.save(config_dict)

        except Exception as e:
            logger.error(f"设置 API 余额状态失败: {e}")
            return False
