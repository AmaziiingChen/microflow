import logging
import time
import threading
from openai import OpenAI
import os
import random
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# 全局配置服务实例（延迟初始化）
_config_service = None


def _get_config_service():
    """获取配置服务实例（延迟导入避免循环依赖）"""
    global _config_service
    if _config_service is None:
        from src.services.config_service import ConfigService
        from src.core.paths import CONFIG_PATH  # 🌟 使用正确的配置路径

        _config_service = ConfigService(str(CONFIG_PATH))
    return _config_service


class LLMService:
    """
    AI 处理层：支持任何兼容 OpenAI 标准的 API服务
    """

    # 重试配置
    MAX_RETRIES = 5
    BASE_DELAY = 1.0  # 初始延迟 1 秒
    MAX_DELAY = 32.0  # 最大延迟 32 秒

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com/v1",
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = base_url
        self.model_name = "deepseek-chat"

        self.client = None

        # 🌟 取消事件（用于中断正在进行的 AI 调用）
        self._cancel_event = threading.Event()

        self.system_prompt = """# Role: 高校公文首席分析师

## Profile
你是一位资深的高校公文分析专家。你的任务是仔细阅读原文，精准识别公文类型，提取核心要素，并采用最适合该公文逻辑的结构进行高度概括的总结。

## 核心实体处理规则 (最高优先级)

请扫视全文，对以下四类核心信息进行严格的格式化处理，**绝对不能混淆**：

1. **时间与期限 (XML包裹)**：遇到具体时间、日期、截止期限，必须用 `<date>` 包裹。例：`<date>3月15日</date>`。
2. **物理空间 (XML包裹)**：遇到实体建筑、楼层、教室编号等实体地点，必须用 `<loc>` 包裹。例：`<loc>行政楼804室</loc>`。
3. **联系方式 (XML包裹)**：遇到电话号码、手机号、邮箱、微信号等**人际沟通方式**，必须用 `<contact>` 包裹。例：`<contact>138xxxxxxx</contact>`。
4. **网页链接 (Markdown强制转换)**：遇到任何 `http` 或 `https` 开头的网址、报名链接、系统入口等，**绝对禁止使用 `<contact>` 包裹**！必须强制转换为标准的 Markdown 超链接格式 `[链接描述](URL)`。
   - 错误示范：`<contact>http://grants.nsfc.gov.cn</contact>`
   - 错误示范：请登录系统（`http://grants.nsfc.gov.cn`）
   - 正确示范：请登录 [科学基金网络信息系统](http://grants.nsfc.gov.cn)

## 视觉与排版铁律 (Markdown规范)
- **禁用代码块**：输出内容严禁使用 ``` 包裹为代码块。
- **标题层级**：正文一级板块必须使用 `### ` (三级标题) 开头。
- **列表化表达**：凡涉及多个并列项，一律使用无序列表 `- ` 或有序列表 `1. `。列表禁止使用任何emoji表情符号。
- **高亮强调**：对关键信息需进行加粗强调。注意：加粗符号与其前后的非加粗文字之间必须保留一个半角空格（例如：地点设在 ** <loc>大礼堂</loc> ** 举行）。
- **信息缺失处理**：若原文未提及时间/地点，请忽略或备注"详见原文"，绝对禁止编造。

本系统的前端已通过原生代码自动渲染了【发文单位】、【发文日期】以及【文末文档附件列表（如 pdf/docx 下载）】。为避免画面冗余：
1. **禁止输出外围信息**：总结中**绝对禁止**重复输出"发文单位"、"落款日期"，也严禁出现"附件详见文末"、"请下载附件查看"等废话提示语。
2. **转移注意力（深化细节）**：请将原本用于总结外围信息的算力，100% 转移到"正文高价值细节"的深挖上。例如：具体的办理步骤、严苛的审核条件、处罚机制、学分折算细则等。
3. **链接特例区分**：普通的文档附件需忽略，但**"外部系统的网页操作入口 / 报名问卷网址"**必须严格按照前述的 `[描述](URL)` 格式强制保留在正文中！

## 输出格式规范

【标签1】【标签2】【标签3】
（注意：第一个标签强制使用2个中文汉字总结公文性质，如：通知、公告、总结。后续标签涵盖受众、核心动作等，最多4个标签）

### [自定义板块标题1] 
- 内容详情...
### [自定义板块标题2] 
- 内容详情...

正文要求：
1. 不要套用固定模板，请根据公文内在逻辑自由创建板块标题，目标是让读者一眼抓住核心。


## Input:
{raw_text}
"""

        if not self.api_key:
            logger.warning("未检测到 API Key，AI 总结功能将无法工作！")
            return

        self._init_client()

    def _init_client(self):
        """初始化或重新初始化 OpenAI 客户端"""
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            logger.info(
                f"已初始化 AI 客户端，模型: {self.model_name}，地址: {self.base_url}"
            )
        else:
            self.client = None
            logger.warning("API Key 为空，客户端未初始化")

    def _is_retryable_error(self, error_msg: str) -> bool:
        """
        判断是否为可重试的错误

        Args:
            error_msg: 错误信息字符串

        Returns:
            是否应该重试
        """
        retryable_patterns = [
            "429",  # Too Many Requests
            "rate_limit",  # 速率限制
            "rate limit",
            "overloaded",  # 服务端过载
            "timeout",  # 超时
            "timed out",
            "connection",  # 连接问题
            "503",  # Service Unavailable
            "502",  # Bad Gateway
            "500",  # Internal Server Error
        ]
        error_lower = error_msg.lower()
        return any(pattern in error_lower for pattern in retryable_patterns)

    # ==================== 取消控制 ====================

    def request_cancel(self) -> None:
        """请求取消当前正在进行的 AI 调用"""
        self._cancel_event.set()
        logger.info("🛑 LLMService: 已请求取消 AI 调用")

    def clear_cancel(self) -> None:
        """清除取消标志（用于新的一轮调用）"""
        self._cancel_event.clear()

    def is_cancelled(self) -> bool:
        """检查是否已请求取消"""
        return self._cancel_event.is_set()

    def _calculate_delay(self, attempt: int) -> float:
        """
        计算指数退避延迟时间

        Args:
            attempt: 当前重试次数（从 0 开始）

        Returns:
            延迟秒数
        """
        # 指数退避：1s, 2s, 4s, 8s, 16s...
        delay = self.BASE_DELAY * (2**attempt)
        # 添加 10% 的随机抖动，避免雷群效应
        jitter = delay * 0.1 * random.random()
        return min(delay + jitter, self.MAX_DELAY)

    def summarize_article(self, title: str, raw_text: str, custom_prompt: str = None) -> str:
        '''
        调用大模型对公文正文进行结构化总结（带指数退避重试和可中断机制）

        Args:
            title: 文章标题
            raw_text: 原始文本
            custom_prompt: 🌟 专属 AI 提示词（用于定制摘要输出格式）

        Returns:
            摘要内容或错误标识字符串
        '''
        # 🌟 清除之前的取消状态（开始新任务）
        self._cancel_event.clear()

        if not self.client:
            return "⚠️ 系统未配置 API Key 或大模型尚未初始化。请在设置中配置。"

        if not raw_text or len(raw_text.strip()) < 10:
            return "⚠️ 原文内容过短或抓取失败，无法进行有效的 AI 总结。"

        # 🌟 检查取消状态
        if self._cancel_event.is_set():
            return "⚠️ 用户取消"

        # 🌟 检查余额状态：如果欠费，直接返回提示
        try:
            config_service = _get_config_service()
            if not config_service.get_api_balance_ok():
                # 🌟 通知前端显示欠费卡片（用户可能之前点击了"不再提醒"）
                try:
                    import webview

                    if webview.windows:
                        webview.windows[0].evaluate_js(
                            """
                            if (window.updateApiBalanceStatus) {
                                window.updateApiBalanceStatus(false);
                            }
                        """
                        )
                except Exception as notify_err:
                    logger.debug(f"通知前端失败: {notify_err}")
                return "⚠️【欠费提醒】您的 API 账户余额不足，AI 总结功能已暂停。请充值或更换密钥后点击「我已充值」恢复。"
        except Exception as e:
            logger.warning(f"检查余额状态失败: {e}")

        logger.info(f"正在调用 AI 分析公文: {title}")

        # 🌟 构建用户消息：支持自定义提示词
        if custom_prompt and custom_prompt.strip():
            user_content = f"以下是文章《{title}》的正文内容。\n\n📋 **专属指令**：{custom_prompt}\n\n---\n\n{raw_text}"
            logger.info(f"使用自定义提示词: {custom_prompt[:50]}...")
        else:
            user_content = f"以下是公文《{title}》的正文内容，请按照系统设定的规范进行总结：\n\n{raw_text}"

        last_error = None

        for attempt in range(self.MAX_RETRIES):
            # 🌟 在每次尝试前检查取消状态
            if self._cancel_event.is_set():
                logger.info(f"AI 调用被用户取消（尝试 {attempt + 1}）: {title}")
                return "⚠️ 用户取消"

            try:
                # 🌟 使用线程执行 API 调用，支持中断
                result_container = {"content": None, "error": None}
                call_completed = threading.Event()

                def _api_call():
                    try:
                        response = self.client.chat.completions.create(
                            model=self.model_name,
                            messages=[
                                {"role": "system", "content": self.system_prompt},
                                {"role": "user", "content": user_content},
                            ],
                            temperature=0.3,
                            max_tokens=3000,
                            top_p=0.9,
                            timeout=45.0,  # 🌟 缩短超时：60 -> 45 秒
                        )
                        result_container["content"] = response.choices[0].message.content
                    except Exception as e:
                        result_container["error"] = e
                    finally:
                        call_completed.set()

                # 启动 API 调用线程
                api_thread = threading.Thread(target=_api_call, daemon=True)
                api_thread.start()

                # 🌟 等待 API 调用完成或被取消（每 0.5 秒检查一次取消状态）
                while not call_completed.wait(timeout=0.5):
                    if self._cancel_event.is_set():
                        logger.info(f"用户取消，等待 API 线程结束: {title}")
                        # 等待线程结束（最多再等 2 秒）
                        call_completed.wait(timeout=2.0)
                        return "⚠️ 用户取消"

                # 检查结果
                error = result_container["error"]
                if error is not None:
                    if isinstance(error, Exception):
                        raise error
                    else:
                        raise RuntimeError(str(error))


                content = result_container["content"]
                if content:
                    return content.strip()
                else:
                    return "⚠️ AI 返回了空内容或非文本数据。"

            except Exception as e:
                last_error = str(e)
                error_lower = last_error.lower()

                # 🌟 如果是取消导致的异常，直接返回
                if self._cancel_event.is_set():
                    return "⚠️ 用户取消"

                # 🌟 检测余额不足错误并更新状态
                balance_error_patterns = [
                    "insufficient_quota",
                    "insufficient_balance",
                    "402",
                    "余额不足",
                    "balance",
                    "quota exceeded",
                    "额度",
                ]
                if any(pattern in error_lower for pattern in balance_error_patterns):
                    logger.error(f"AI 调用失败（余额不足）({title}): {last_error}")
                    try:
                        config_service = _get_config_service()
                        config_service.set_api_balance_ok(False)
                        logger.info("已更新欠费状态到配置文件")

                        # 🌟 主动通知前端更新余额状态
                        try:
                            import webview

                            if webview.windows:
                                webview.windows[0].evaluate_js(
                                    """
                                    if (window.updateApiBalanceStatus) {
                                        window.updateApiBalanceStatus(false);
                                    }
                                """
                                )
                        except Exception as notify_err:
                            logger.debug(f"通知前端失败: {notify_err}")
                    except Exception as config_err:
                        logger.warning(f"更新欠费状态失败: {config_err}")
                    return f"❌ API 余额不足：{last_error}"

                # 不可重试的错误：直接返回
                if "401" in error_lower:
                    logger.error(f"AI 调用失败（不可重试）({title}): {last_error}")
                    return f"❌ API 认证失败：{last_error}"

                # 可重试的错误：执行指数退避
                if self._is_retryable_error(last_error):
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self._calculate_delay(attempt)
                        logger.warning(
                            f"AI 调用失败（第 {attempt + 1} 次），{delay:.1f}秒后重试 ({title}): {last_error}"
                        )
                        # 🌟 在退避等待期间也检查取消状态
                        for _ in range(int(delay * 10)):
                            if self._cancel_event.is_set():
                                return "⚠️ 用户取消"
                            time.sleep(0.1)
                        continue
                    else:
                        logger.error(
                            f"AI 调用失败（已达最大重试次数 {self.MAX_RETRIES}）({title}): {last_error}"
                        )
                        return f"⚠️ AI 服务暂时不可用，已重试 {self.MAX_RETRIES} 次仍失败：{last_error}"

                # 其他未知错误
                logger.error(f"AI 总结失败 ({title}): {last_error}")
                return f"⚠️ AI 处理遇到未知错误：{last_error}"

        # 理论上不会到达这里，但作为安全保护
        return f"⚠️ AI 处理失败：{last_error}"

    def update_config(
        self,
        api_key: Optional[str],
        model_name: Optional[str],
        system_prompt: Optional[str],
        base_url: Optional[str] = None,
    ):
        """
        热更新配置：前端点击保存时触发，立刻重置客户端而无需重启应用。
        """
        # 1. 处理 API Key
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")

        # 2. 处理 Model Name
        self.model_name = str(model_name or "deepseek-chat")

        # 3. 处理 Base URL
        if base_url and base_url.strip():
            self.base_url = base_url
        else:
            self.base_url = "https://api.deepseek.com/v1"

        # 4. 处理 System Prompt
        if system_prompt and system_prompt.strip():
            self.system_prompt = system_prompt

        # 5. 初始化客户端
        if self.api_key:
            self._init_client()
        else:
            self.client = None
            logger.warning("大模型 API Key 为空，客户端已重置为 None。")

    def test_connection(
        self,
        api_key: Optional[str],
        model_name: Optional[str],
        base_url: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        连通性测试：发起一次极小消耗的请求来验证 Key 是否有效。
        """
        if not api_key:
            return False, "API Key 不能为空"

        # 确定最终的 Base URL
        final_base_url = str(base_url or "https://api.deepseek.com/v1")

        # 确定最终的 Model Name
        final_model = str(model_name or "deepseek-chat")

        try:
            # 使用临时客户端进行测试，不影响全局 client 状态
            temp_client = OpenAI(api_key=api_key, base_url=final_base_url)

            # 发起极简请求
            temp_client.chat.completions.create(
                model=final_model,
                messages=[{"role": "user", "content": "1"}],
                max_tokens=5,
                temperature=0.1,
                timeout=10,  # 测试时使用较短的超时
            )

            # 🌟 测试成功，清除欠费状态
            try:
                config_service = _get_config_service()
                config_service.set_api_balance_ok(True)
                logger.info("API 连接测试成功，已清除欠费状态")

                # 🌟 主动通知前端更新余额状态
                try:
                    import webview

                    if webview.windows:
                        webview.windows[0].evaluate_js(
                            """
                            if (window.updateApiBalanceStatus) {
                                window.updateApiBalanceStatus(true);
                            }
                        """
                        )
                except Exception as notify_err:
                    logger.debug(f"通知前端失败: {notify_err}")
            except Exception as e:
                logger.warning(f"清除欠费状态失败: {e}")

            return True, "连接成功"

        except Exception as e:
            error_msg = str(e)
            # 增强型错误识别
            if "Authentication" in error_msg or "401" in error_msg:
                return False, "API Key 无效或认证失败"
            elif (
                "insufficient_quota" in error_msg or "insufficient_balance" in error_msg
            ):
                return False, "API 余额不足或额度超限"
            elif "timeout" in error_msg.lower():
                return False, "连接超时，请检查网络环境或代理设置"
            elif "404" in error_msg:
                return (
                    False,
                    f"模型路径错误(404)，请检查 Base URL 或模型名称: {final_model}",
                )

            return False, f"连接失败: {error_msg}"
