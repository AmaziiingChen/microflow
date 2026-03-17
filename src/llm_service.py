import logging
import time
from openai import OpenAI
import os
import random
from typing import Optional
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class LLMService:
    """
    AI 处理层：支持任何兼容 OpenAI 标准的 API服务
    """

    # 重试配置
    MAX_RETRIES = 5
    BASE_DELAY = 1.0  # 初始延迟 1 秒
    MAX_DELAY = 32.0  # 最大延迟 32 秒

    def __init__(self, api_key: Optional[str] = None, base_url: str = 'https://api.deepseek.com/v1'):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        self.base_url = base_url
        self.model_name = 'deepseek-chat'

        self.client = None

        self.system_prompt = """# Role: 高校公文首席分析师

## Profile
你是一位资深的高校公文分析专家。你的任务是仔细阅读原文，精准识别公文类型，提取核心要素，并采用最适合该公文逻辑的结构进行高度概括的总结。

## 严格执行规则 (Rules)

### 1. 实体标记规则 (XML标签包裹)
请扫视全文（包括标题、正文及落款），对以下三类核心实体进行无遗漏的标签包裹：
- **时间与期限**：遇到具体时间、日期、截止期限，必须用 `<date>` 包裹。例：`<date>3月15日</date>`。
- **物理空间**：遇到任何实体建筑、楼层、教室编号、会议室、集会地点等，必须用 `<loc>` 包裹。例：`<loc>行政楼804室</loc>`。
- **联系方式**：遇到电话号码、手机号、邮箱、微信号等，必须用 `<contact>` 包裹。例：`<contact>138xxxxxxx</contact>`。

### 2. 视觉与排版铁律 (Markdown规范)
- **禁用代码块**：输出内容严禁使用 ` ``` ` 包裹为代码块。
- **标题层级**：正文一级板块必须使用 `### ` (三级标题) 开头，按需可使用 `#### ` 作为子板块。
- **列表化表达**：凡涉及多个并列项（如要求、步骤、材料清单等），一律使用无序列表 `- ` 或有序列表 `1. `。
- **高亮强调**：对关键信息（金额、实体标签等）需进行加粗强调。**注意**：为了确保 Markdown 渲染成功，加粗符号 `**` 与其前后的非加粗文字之间**必须保留一个半角空格**（例如：地点设在 ** `<loc>大礼堂</loc>` ** 举行）。
- **信息缺失处理**：若原文未提及时间、地点或截止日期，请忽略该字段或备注"详见原文"，**绝对禁止编造或推理**信息。不必列举发文单位和发文日期。

## 输出格式规范 (Output Format)

【标签1】【标签2】【标签3】（注意，对于第一个标签，强制使用四个字进行总结，后续的不做要求）
（此处必须空一行）禁止输出任何排版解释性文字（如『此处空一行』）
### [自定义板块标题1]
- 内容详情...
### [自定义板块标题2]
- 内容详情...

**标签要求**：
1. 必须放在第一行，最多4个，每个标签用 `【】` 包裹。
2. 内容必须是精炼的关键词，严禁使用完整句子。
3. 【标签1】必须是公文性质（如：通知、公告、申请、报告、方案、总结等），需从文中提取明确线索，不可臆断。
4. 后续标签应涵盖：[受众群体]、[核心动作] 等符合信息传递核心的关键词。

**正文要求**：
1. 不要套用固定模板，请根据公文的内在逻辑自由创建板块（例如："报名详情"、"评审流程"、"注意事项"等），目标是让读者一眼抓住核心。
2. 次要说明、背景信息、附件链接等细节可放在最后的板块中。

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
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )
            logger.info(f"已初始化 AI 客户端，模型: {self.model_name}，地址: {self.base_url}")
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
            "429",                 # Too Many Requests
            "rate_limit",          # 速率限制
            "rate limit",
            "overloaded",          # 服务端过载
            "timeout",             # 超时
            "timed out",
            "connection",          # 连接问题
            "503",                 # Service Unavailable
            "502",                 # Bad Gateway
            "500",                 # Internal Server Error
        ]
        error_lower = error_msg.lower()
        return any(pattern in error_lower for pattern in retryable_patterns)

    def _calculate_delay(self, attempt: int) -> float:
        """
        计算指数退避延迟时间

        Args:
            attempt: 当前重试次数（从 0 开始）

        Returns:
            延迟秒数
        """
        # 指数退避：1s, 2s, 4s, 8s, 16s...
        delay = self.BASE_DELAY * (2 ** attempt)
        # 添加 10% 的随机抖动，避免雷群效应
        jitter = delay * 0.1 * random.random()
        return min(delay + jitter, self.MAX_DELAY)

    def summarize_article(self, title: str, raw_text: str) -> str:
        """
        调用大模型对公文正文进行结构化总结（带指数退避重试）

        Args:
            title: 文章标题
            raw_text: 原始文本

        Returns:
            摘要内容或错误标识字符串
        """
        if not self.client:
            return "⚠️ 系统未配置 API Key 或大模型尚未初始化。请在设置中配置。"

        if not raw_text or len(raw_text.strip()) < 10:
            return "⚠️ 原文内容过短或抓取失败，无法进行有效的 AI 总结。"

        logger.info(f"正在调用 AI 分析公文: {title}")
        user_content = f"以下是公文《{title}》的正文内容，请按照系统设定的规范进行总结：\n\n{raw_text}"

        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.3,
                    max_tokens=3000,
                    top_p=0.9,
                    timeout=60.0
                )

                content = response.choices[0].message.content
                if content:
                    return content.strip()
                else:
                    return "⚠️ AI 返回了空内容或非文本数据。"

            except Exception as e:
                last_error = str(e)
                error_lower = last_error.lower()

                # 不可重试的错误：直接返回
                if "insufficient_quota" in error_lower or "401" in error_lower:
                    logger.error(f"AI 调用失败（不可重试）({title}): {last_error}")
                    return f"❌ API 余额不足或认证失败：{last_error}"

                # 可重试的错误：执行指数退避
                if self._is_retryable_error(last_error):
                    if attempt < self.MAX_RETRIES - 1:
                        delay = self._calculate_delay(attempt)
                        logger.warning(
                            f"AI 调用失败（第 {attempt + 1} 次），{delay:.1f}秒后重试 ({title}): {last_error}"
                        )
                        time.sleep(delay)
                        continue
                    else:
                        logger.error(f"AI 调用失败（已达最大重试次数 {self.MAX_RETRIES}）({title}): {last_error}")
                        return f"⚠️ AI 服务暂时不可用，已重试 {self.MAX_RETRIES} 次仍失败：{last_error}"

                # 其他未知错误
                logger.error(f"AI 总结失败 ({title}): {last_error}")
                return f"⚠️ AI 处理遇到未知错误：{last_error}"

        # 理论上不会到达这里，但作为安全保护
        return f"⚠️ AI 处理失败：{last_error}"

    def update_config(self,
                  api_key: Optional[str],
                  model_name: Optional[str],
                  system_prompt: Optional[str],
                  base_url: Optional[str] = None):
        """
        热更新配置：前端点击保存时触发，立刻重置客户端而无需重启应用。
        """
        # 1. 处理 API Key
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")

        # 2. 处理 Model Name
        self.model_name = str(model_name or 'deepseek-chat')

        # 3. 处理 Base URL
        if base_url and base_url.strip():
            self.base_url = base_url
        else:
            self.base_url = 'https://api.deepseek.com/v1'

        # 4. 处理 System Prompt
        if system_prompt and system_prompt.strip():
            self.system_prompt = system_prompt

        # 5. 初始化客户端
        if self.api_key:
            self._init_client()
        else:
            self.client = None
            logger.warning("大模型 API Key 为空，客户端已重置为 None。")

    def test_connection(self,
                        api_key: Optional[str],
                        model_name: Optional[str],
                        base_url: Optional[str] = None) -> tuple[bool, str]:
        """
        连通性测试：发起一次极小消耗的请求来验证 Key 是否有效。
        """
        if not api_key:
            return False, "API Key 不能为空"

        # 确定最终的 Base URL
        final_base_url = str(base_url or 'https://api.deepseek.com/v1')

        # 确定最终的 Model Name
        final_model = str(model_name or 'deepseek-chat')

        try:
            # 使用临时客户端进行测试，不影响全局 client 状态
            temp_client = OpenAI(api_key=api_key, base_url=final_base_url)

            # 发起极简请求
            temp_client.chat.completions.create(
                model=final_model,
                messages=[{"role": "user", "content": "1"}],
                max_tokens=5,
                temperature=0.1,
                timeout=10 # 测试时使用较短的超时
            )
            return True, "连接成功"

        except Exception as e:
            error_msg = str(e)
            # 增强型错误识别
            if "Authentication" in error_msg or "401" in error_msg:
                return False, "API Key 无效或认证失败"
            elif "insufficient_quota" in error_msg:
                return False, "API 余额不足或额度超限"
            elif "timeout" in error_msg.lower():
                return False, "连接超时，请检查网络环境或代理设置"
            elif "404" in error_msg:
                return False, f"模型路径错误(404)，请检查 Base URL 或模型名称: {final_model}"

            return False, f"连接失败: {error_msg}"