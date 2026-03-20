"""配置管理服务 - 负责配置的加载、保存和验证"""

import json
import os
import logging
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
    is_locked: bool = False  # 🌟 新增：配置锁定状态（用于防止修改）
    articles_per_section_limit: int = 10  # 🌟 新增：每个板块处理的文章上限
    api_balance_ok: bool = True  # 🌟 新增：API 余额状态（默认正常）


class ConfigService:
    """
    配置管理服务 - 单一职责：配置的持久化与热更新

    使用方式：
        config_service = ConfigService(config_path, default_prompt)
        config = config_service.load()
        config_service.save(new_config)
    """

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

        # 确保配置目录存在
        config_dir = os.path.dirname(config_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir, exist_ok=True)

    def load(self) -> AppConfig:
        """
        从文件加载配置

        Returns:
            AppConfig 配置对象
        """
        default = AppConfig(prompt=self._default_prompt)

        if not os.path.exists(self.config_path):
            self._config = default
            return self._config

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

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
                is_locked=data.get('isLocked', default.is_locked),  # 🌟 新增：配置锁定状态
                articles_per_section_limit=data.get('articlesPerSectionLimit', default.articles_per_section_limit),  # 🌟 新增
                api_balance_ok=data.get('apiBalanceOk', default.api_balance_ok),  # 🌟 新增：API 余额状态
            )
            return self._config

        except Exception as e:
            logger.error(f"读取配置文件失败: {e}")
            self._config = default
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
            # 确保目录存在
            config_dir = os.path.dirname(self.config_path)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)

            # 写入文件
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, ensure_ascii=False, indent=4)

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
                is_locked=config_dict.get('isLocked', False),  # 🌟 新增：配置锁定状态
                articles_per_section_limit=config_dict.get('articlesPerSectionLimit', 10),  # 🌟 新增
                api_balance_ok=config_dict.get('apiBalanceOk', True),  # 🌟 新增：API 余额状态
            )

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
            "isLocked": config.is_locked,  # 🌟 新增：配置锁定状态
            "articlesPerSectionLimit": config.articles_per_section_limit,  # 🌟 新增
            "apiBalanceOk": config.api_balance_ok,  # 🌟 新增：API 余额状态
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
            'isLocked': 'is_locked',  # 🌟 新增：配置锁定状态
            'articlesPerSectionLimit': 'articles_per_section_limit',  # 🌟 新增
            'apiBalanceOk': 'api_balance_ok',  # 🌟 新增：API 余额状态
        }

        # 转换键名
        attr_name = key_mapping.get(key, key)

        return getattr(config, attr_name, default)

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
            # 读取当前配置文件内容
            config_dict = {}
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config_dict = json.load(f)

            # 更新余额状态
            config_dict['apiBalanceOk'] = ok

            # 保存回文件
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, ensure_ascii=False, indent=4)

            # 更新内存中的配置
            if self._config:
                self._config.api_balance_ok = ok

            logger.info(f"API 余额状态已更新: {ok}")
            return True
        except Exception as e:
            logger.error(f"设置 API 余额状态失败: {e}")
            return False