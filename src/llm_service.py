import logging
import time
import threading
import hashlib
import json
import re
from openai import OpenAI
import os
import random
from contextlib import contextmanager
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from src.utils.text_cleaner import strip_emoji
from src.utils.ai_markdown import build_tag_items, extract_leading_tags, normalize_tags
from src.utils.llm_safety import sanitize_llm_provider_risk_text

load_dotenv()
logger = logging.getLogger(__name__)

# 全局配置服务实例（延迟初始化）
_config_service = None
_database = None


def _get_config_service():
    """获取配置服务实例（延迟导入避免循环依赖）"""
    global _config_service
    if _config_service is None:
        from src.services.config_service import ConfigService
        from src.core.paths import CONFIG_PATH  # 🌟 使用正确的配置路径

        _config_service = ConfigService(str(CONFIG_PATH))
    return _config_service


def _get_database():
    """获取数据库实例（延迟导入避免循环依赖）"""
    global _database
    if _database is None:
        from src.database import db

        _database = db
    return _database


class LLMService:
    """
    AI 处理层：支持任何兼容 OpenAI 标准的 API服务
    """

    # 重试配置
    MAX_RETRIES = 3
    BASE_DELAY = 1.0  # 初始延迟 1 秒
    MAX_DELAY = 32.0  # 最大延迟 32 秒

    # 🌟 平衡优化：批量总结2路，手动总结1路，加快AI处理
    MAX_BATCH_CONCURRENCY = 2
    MAX_MANUAL_CONCURRENCY = 1
    RSS_CHUNK_TRIGGER_LENGTH = 7000
    RSS_CHUNK_TARGET_LENGTH = 3600
    RSS_CHUNK_HARD_LIMIT = 4600
    AI_RESULT_CACHE_ENABLED = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com/v1",
    ):
        # 只认显式传入的配置，不再从环境变量回退，便于测试阶段排查配置来源
        self.api_key = api_key or ""
        self.base_url = base_url
        self.model_name = "deepseek-chat"

        self.client = None

        # 🌟 取消事件（用于中断正在进行的 AI 调用）
        self._cancel_event = threading.Event()

        # 🌟 受控并发：批量总结与手动总结分通道
        self._batch_summary_slots = threading.BoundedSemaphore(
            self.MAX_BATCH_CONCURRENCY
        )
        self._manual_summary_slots = threading.BoundedSemaphore(
            self.MAX_MANUAL_CONCURRENCY
        )

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

        self.rss_system_prompt = """# Role: RSS 阅读排版编辑器

## 任务目标
你要把 RSS/Atom 订阅内容整理成适合卡片阅读的 Markdown 文本，同时保留原文事实，不要编造。

## 输出要求
1. 第一行必须输出 3 个彩色标签，格式固定为：【标签1】【标签2】【标签3】
2. 标签要简洁有力，每个不超过 15 个字，优先概括主题、对象、动作。
3. 正文必须使用 Markdown 排版，优先使用 ###/#### 标题、- 列表、** 加粗。
4. 如果原文包含图片或链接，请尽量保留对应的 Markdown 形式，不能删除关键信息。
5. 如果内容较长，请先提炼结构，再按段落重排，保持阅读顺畅。
6. 不要使用 emoji，不要输出多余的客套话。

## 排版策略
- 标题要清晰，避免一大段平铺直叙。
- 关键词可适度加粗。
- 图片尽量放在对应段落附近。
- 对纯摘要型内容，输出更短、更密度高的总结。

## Input:
{raw_text}
"""

        self.rss_format_system_prompt = """# Role: RSS Markdown 排版编辑器

## 任务目标
将 RSS/Atom 原文整理成更适合阅读的 Markdown 正文，但不要额外编造事实，也不要加入总结标签行。

## 输出要求
1. 只输出 Markdown 正文，不要输出解释，不要输出 JSON。
2. 不要输出任何 `【标签】` 行。
3. 优先使用 `###` / `####` 标题、列表、加粗整理结构。
4. 尽量保留原文中的图片、链接、引用和列表。
5. 如果原文已经结构清晰，做轻度润色即可，不要过度改写。
6. 不要使用 emoji，不要加“以下为整理后内容”等提示语。
"""

        self.rss_summary_system_prompt = """# Role: RSS 摘要与标签生成器

## 任务目标
基于 RSS 正文输出一个适合卡片阅读的精简摘要，并生成 3 个彩色标签。

## 输出格式
1. 第一行必须是 3 个标签，格式固定：`【标签1】【标签2】【标签3】`
2. 第二行开始输出 Markdown 摘要正文。

## 约束
1. 每个标签不超过 15 个字。
2. 标签优先覆盖：主题、对象、动作。
3. 标签不要和日期、来源名、任务名称做简单重复。
4. 摘要正文必须使用 Markdown，可使用 `###` / `-` / `**`。
5. 摘要要突出最值得读的内容，不要照搬整篇全文。
6. 不要输出 JSON，不要输出客套话，不要使用 emoji。
"""

        self.rss_chunk_summary_system_prompt = """# Role: RSS 长文分段提炼器

## 任务目标
你将收到一篇 RSS 长文中的一个分段，请提炼这一段最值得保留的信息，供后续汇总使用。

## 输出要求
1. 只输出 Markdown，不要输出标签行，不要输出 JSON。
2. 优先保留该分段的核心观点、事实、步骤、结论。
3. 可以使用 `###`、`-`、`**`，但要简洁，不要复写原文。
4. 不要编造，不要加入客套话，不要使用 emoji。
"""

        self.rss_summary_synthesis_system_prompt = """# Role: RSS 长文汇总编辑器

## 任务目标
你会收到一篇 RSS 长文的多段摘要，请将它们汇总成最终的阅读摘要，并输出 3 个标签。

## 输出格式
1. 第一行必须是 3 个标签，格式固定：`【标签1】【标签2】【标签3】`
2. 第二行开始输出 Markdown 摘要正文。

## 约束
1. 每个标签不超过 15 个字。
2. 标签优先覆盖主题、对象、动作，并按重要性排序。
3. 摘要要整合多段信息，避免重复。
4. 不要输出 JSON，不要解释过程，不要使用 emoji。
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
                base_url=self.base_url,
                max_retries=0,
                timeout=45.0,
            )
            logger.info(
                f"已初始化 AI 客户端，模型: {self.model_name}，地址: {self.base_url}"
            )
        else:
            self.client = None
            logger.warning("API Key 为空，客户端未初始化")

    def _normalize_openai_error(self, error_msg: str) -> str:
        """
        将 OpenAI SDK / 兼容接口返回的底层错误归一化成更可读的提示。
        """
        raw = str(error_msg or "").strip()
        if not raw:
            return "未知错误"

        lowered = raw.lower()

        if self._is_content_risk_error(raw):
            return f"内容触发模型风控：{raw}"

        if "401" in lowered or "authentication" in lowered or "invalid api key" in lowered:
            return "API Key 无效或认证失败"

        if "403" in lowered or "permission" in lowered or "forbidden" in lowered:
            return "当前 API Key 没有该模型或接口的访问权限"

        if "404" in lowered or "not found" in lowered:
            return "接口路径或模型名称错误，请检查 Base URL 与模型名称"

        if "400" in lowered or "bad request" in lowered:
            return f"请求参数无效：{raw}"

        if (
            "insufficient_quota" in lowered
            or "insufficient_balance" in lowered
            or "quota exceeded" in lowered
            or "balance" in lowered
            or "402" in lowered
        ):
            return f"API 余额不足或额度超限：{raw}"

        if "timeout" in lowered or "timed out" in lowered:
            return "请求超时，长内容可能需要更久，请稍后重试"

        if (
            "connection error" in lowered
            or "connecterror" in lowered
            or "apiconnectionerror" in lowered
            or "connection" in lowered
            or "dns" in lowered
            or "name or service not known" in lowered
            or "nodename nor servname provided" in lowered
            or "failed to establish a new connection" in lowered
        ):
            return "无法连接到 AI 服务，请检查 Base URL、网络环境或代理设置"

        if "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered:
            return f"请求过于频繁或服务限流：{raw}"

        if any(code in lowered for code in ("500", "502", "503", "504", "overloaded")):
            return f"AI 服务暂时不可用：{raw}"

        return raw

    def _is_content_risk_error(self, error_msg: str) -> bool:
        raw = str(error_msg or "").strip().lower()
        if not raw:
            return False
        risk_patterns = [
            "content exists risk",
            "content risk",
            "sensitive",
            "safety system",
        ]
        return any(pattern in raw for pattern in risk_patterns)

    def _build_content_risk_retry_payload(self, user_content: str) -> Optional[Dict[str, Any]]:
        sanitized_text, replacements = sanitize_llm_provider_risk_text(user_content)
        if not replacements or sanitized_text == str(user_content or ""):
            return None

        summary = ", ".join(
            f"{item['label']} x{item['count']}" for item in replacements[:6]
        )
        return {
            "user_content": sanitized_text,
            "replacements": replacements,
            "summary": summary,
        }

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

    def _is_timeout_error(self, error_msg: str) -> bool:
        error_lower = str(error_msg or "").lower()
        return "timeout" in error_lower or "timed out" in error_lower

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

    def _error_result(self, message: str, prefix: str = "❌") -> str:
        """统一构造可被上层识别的失败结果"""
        return f"{prefix} {message}".strip()

    def _build_ai_cache_payload(
        self,
        *,
        cache_scope: str,
        system_prompt: str,
        user_content: str,
    ) -> Dict[str, str]:
        base_url = str(self.base_url or "").strip()
        model_name = str(self.model_name or "").strip()
        content_hash = hashlib.sha256(user_content.encode("utf-8")).hexdigest()
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "scope": cache_scope,
                    "model": model_name,
                    "base_url": base_url,
                    "content_hash": content_hash,
                    "prompt_hash": prompt_hash,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "cache_key": cache_key,
            "cache_scope": cache_scope,
            "content_hash": content_hash,
            "prompt_hash": prompt_hash,
            "model_name": model_name,
            "base_url": base_url,
        }

    def _read_cached_result(self, cache_payload: Dict[str, str]) -> Optional[str]:
        if not self.AI_RESULT_CACHE_ENABLED:
            return None
        try:
            cache_entry = _get_database().get_ai_result_cache(
                cache_payload.get("cache_key", "")
            )
        except Exception as e:
            logger.debug(f"读取 AI 缓存失败: {e}")
            return None

        if not cache_entry:
            return None

        result_text = str(cache_entry.get("result_text") or "").strip()
        if not result_text:
            return None

        logger.info(
            "命中 AI 缓存: scope=%s, model=%s",
            cache_payload.get("cache_scope", ""),
            cache_payload.get("model_name", ""),
        )
        return result_text

    def _write_cached_result(
        self,
        cache_payload: Dict[str, str],
        result_text: str,
    ) -> None:
        if not self.AI_RESULT_CACHE_ENABLED:
            return
        cleaned_result = str(result_text or "").strip()
        if not cleaned_result or cleaned_result.startswith(("⚠️", "❌")):
            return

        try:
            _get_database().upsert_ai_result_cache(
                cache_key=cache_payload.get("cache_key", ""),
                cache_scope=cache_payload.get("cache_scope", ""),
                content_hash=cache_payload.get("content_hash", ""),
                prompt_hash=cache_payload.get("prompt_hash", ""),
                model_name=cache_payload.get("model_name", ""),
                base_url=cache_payload.get("base_url", ""),
                result_text=cleaned_result,
            )
        except Exception as e:
            logger.debug(f"写入 AI 缓存失败: {e}")

    def _generate_cached_with_retry(
        self,
        *,
        cache_scope: str,
        title: str,
        raw_text: str,
        system_prompt: str,
        user_content: str,
        priority: str,
        cancel_event: Optional[threading.Event],
        target_label: str,
        use_cache: bool = True,
    ) -> str:
        cache_enabled = bool(use_cache and self.AI_RESULT_CACHE_ENABLED)
        cache_payload: Optional[Dict[str, str]] = None
        if cache_enabled:
            cache_payload = self._build_ai_cache_payload(
                cache_scope=cache_scope,
                system_prompt=system_prompt,
                user_content=user_content,
            )
            cached_result = self._read_cached_result(cache_payload)
            if cached_result is not None:
                return cached_result

        result = self._generate_with_retry(
            title=title,
            raw_text=raw_text,
            system_prompt=system_prompt,
            user_content=user_content,
            priority=priority,
            cancel_event=cancel_event,
            target_label=target_label,
        )
        if cache_enabled and cache_payload is not None:
            self._write_cached_result(cache_payload, result)
        return result

    def _split_rss_markdown_chunks(self, markdown_text: str) -> List[str]:
        text = str(markdown_text or "").strip()
        if not text:
            return []

        raw_blocks = [
            block.strip()
            for block in re.split(r"\n\s*\n", text)
            if block and block.strip()
        ]
        if not raw_blocks:
            return [text]

        normalized_blocks: List[str] = []
        for block in raw_blocks:
            if len(block) <= self.RSS_CHUNK_HARD_LIMIT:
                normalized_blocks.append(block)
                continue

            lines = [line.rstrip() for line in block.splitlines() if line.strip()]
            current_lines: List[str] = []
            current_length = 0
            for line in lines:
                line_length = len(line) + 1
                if (
                    current_lines
                    and current_length + line_length > self.RSS_CHUNK_HARD_LIMIT
                ):
                    normalized_blocks.append("\n".join(current_lines).strip())
                    current_lines = [line]
                    current_length = line_length
                else:
                    current_lines.append(line)
                    current_length += line_length

            if current_lines:
                normalized_blocks.append("\n".join(current_lines).strip())

        chunks: List[str] = []
        current_blocks: List[str] = []
        current_length = 0

        for block in normalized_blocks:
            block_length = len(block)
            separator_length = 2 if current_blocks else 0
            projected_length = current_length + separator_length + block_length

            if (
                current_blocks
                and projected_length > self.RSS_CHUNK_HARD_LIMIT
            ):
                chunks.append("\n\n".join(current_blocks).strip())
                current_blocks = [block]
                current_length = block_length
                continue

            if (
                current_blocks
                and current_length >= self.RSS_CHUNK_TARGET_LENGTH
                and block.startswith("#")
            ):
                chunks.append("\n\n".join(current_blocks).strip())
                current_blocks = [block]
                current_length = block_length
                continue

            current_blocks.append(block)
            current_length = projected_length

        if current_blocks:
            chunks.append("\n\n".join(current_blocks).strip())

        return [chunk for chunk in chunks if chunk]

    def _should_use_chunked_rss_summary(self, raw_text: str) -> bool:
        text = str(raw_text or "").strip()
        if len(text) < self.RSS_CHUNK_TRIGGER_LENGTH:
            return False
        return len(self._split_rss_markdown_chunks(text)) > 1

    def _summarize_rss_article_chunked(
        self,
        *,
        title: str,
        raw_text: str,
        custom_prompt: str,
        priority: str,
        cancel_event: Optional[threading.Event],
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        chunks = self._split_rss_markdown_chunks(raw_text)
        if len(chunks) <= 1:
            return self._summarize_rss_article_single_pass(
                title=title,
                raw_text=raw_text,
                custom_prompt=custom_prompt,
                priority=priority,
                cancel_event=cancel_event,
                use_cache=use_cache,
            )

        logger.info("RSS 长文启用分块摘要: title=%s, chunks=%d", title, len(chunks))
        chunk_summaries: List[str] = []
        extra_instruction = (
            f"\n\n源级额外要求：{custom_prompt.strip()}"
            if custom_prompt and custom_prompt.strip()
            else ""
        )

        for index, chunk in enumerate(chunks, start=1):
            chunk_user_content = (
                f"以下是 RSS 长文《{title}》的第 {index}/{len(chunks)} 个分段。"
                f"{extra_instruction}\n\n---\n\n{chunk}"
            )
            chunk_result = self._generate_cached_with_retry(
                cache_scope="rss_summary_chunk",
                title=f"{title} - chunk {index}",
                raw_text=chunk,
                system_prompt=self.rss_chunk_summary_system_prompt,
                user_content=chunk_user_content,
                priority=priority,
                cancel_event=cancel_event,
                target_label="RSS 分块摘要",
                use_cache=use_cache,
            )
            if chunk_result.startswith(("⚠️", "❌")):
                return {
                    "status": "error",
                    "message": chunk_result,
                    "markdown": chunk_result,
                    "summary": "",
                    "tags": [],
                }
            chunk_summaries.append(f"### 分段 {index}\n{chunk_result.strip()}")

        synthesis_instruction = (
            f"\n\n源级额外要求：{custom_prompt.strip()}"
            if custom_prompt and custom_prompt.strip()
            else ""
        )
        synthesis_source = "\n\n".join(chunk_summaries)
        synthesis_user_content = (
            f"以下是 RSS 长文《{title}》的分段摘要，请整合为最终摘要。"
            f"{synthesis_instruction}\n\n---\n\n{synthesis_source}"
        )
        markdown = self._generate_cached_with_retry(
            cache_scope="rss_summary_final",
            title=title,
            raw_text=synthesis_source,
            system_prompt=self.rss_summary_synthesis_system_prompt,
            user_content=synthesis_user_content,
            priority=priority,
            cancel_event=cancel_event,
            target_label="RSS 摘要汇总",
            use_cache=use_cache,
        )
        if markdown.startswith(("⚠️", "❌")):
            return {
                "status": "error",
                "message": markdown,
                "markdown": markdown,
                "summary": "",
                "tags": [],
            }

        tags, summary = extract_leading_tags(markdown)
        return {
            "status": "success",
            "markdown": markdown,
            "summary": summary.strip(),
            "tags": normalize_tags(tags),
            "tag_items": build_tag_items(tags),
        }

    def _summarize_rss_article_single_pass(
        self,
        *,
        title: str,
        raw_text: str,
        custom_prompt: str,
        priority: str,
        cancel_event: Optional[threading.Event],
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        extra_instruction = (
            f"\n\n源级额外要求：{custom_prompt.strip()}"
            if custom_prompt and custom_prompt.strip()
            else ""
        )
        user_content = (
            f"以下是 RSS 订阅内容《{title}》的正文，请输出三枚标签和结构化 Markdown 摘要。"
            f"{extra_instruction}\n\n---\n\n{raw_text}"
        )
        markdown = self._generate_cached_with_retry(
            cache_scope="rss_summary",
            title=title,
            raw_text=raw_text,
            system_prompt=self.rss_summary_system_prompt,
            user_content=user_content,
            priority=priority,
            cancel_event=cancel_event,
            target_label="RSS 摘要生成",
            use_cache=use_cache,
        )
        if markdown.startswith(("⚠️", "❌")):
            return {
                "status": "error",
                "message": markdown,
                "markdown": markdown,
                "summary": "",
                "tags": [],
            }

        tags, summary = extract_leading_tags(markdown)
        return {
            "status": "success",
            "markdown": markdown,
            "summary": summary.strip(),
            "tags": normalize_tags(tags),
            "tag_items": build_tag_items(tags),
        }

    def _resolve_request_timeout(
        self,
        raw_text: str,
        target_label: str,
        priority: str,
    ) -> float:
        text = str(raw_text or "").strip()
        content_length = len(text)
        label_lower = str(target_label or "").lower()

        timeout = 60.0
        if "rss" in label_lower:
            timeout = 90.0
        if priority == "manual":
            timeout += 15.0

        if content_length > 4000:
            timeout += 15.0
        if content_length > 8000:
            timeout += 20.0
        if content_length > 12000:
            timeout += 25.0
        if content_length > 20000:
            timeout += 30.0
        if content_length > 32000:
            timeout += 30.0

        timeout = min(max(timeout, 45.0), 210.0)
        logger.debug(
            "AI 请求超时阈值已调整为 %.1fs (%s, length=%d, priority=%s)",
            timeout,
            target_label,
            content_length,
            priority,
        )
        return timeout

    @contextmanager
    def _summary_slot(self, priority: str):
        """
        获取摘要并发槽位。

        priority:
            - "manual": 手动重总结，独占高优先级槽位
            - 其他：批量更新总结，走批量槽位
        """
        slot_type = "manual" if priority == "manual" else "batch"
        semaphore = (
            self._manual_summary_slots
            if slot_type == "manual"
            else self._batch_summary_slots
        )

        logger.debug(f"等待 {slot_type} AI 总结槽位...")
        semaphore.acquire()
        try:
            logger.debug(f"已获取 {slot_type} AI 总结槽位")
            yield
        finally:
            semaphore.release()
            logger.debug(f"已释放 {slot_type} AI 总结槽位")

    def summarize_article(
        self,
        title: str,
        raw_text: str,
        custom_prompt: str = None,
        priority: str = "batch",
        cancel_event: Optional[threading.Event] = None,
        content_kind: str = "default",
        use_cache: bool = True,
    ) -> str:
        """
        调用大模型对公文正文进行结构化总结（带指数退避重试和可中断机制）

        Args:
            title: 文章标题
            raw_text: 原始文本
            custom_prompt: 🌟 专属 AI 提示词（用于定制摘要输出格式）
            priority: 并发优先级，manual 为手动高优先级通道
            cancel_event: 可选的独立取消事件；不传则使用全局取消事件
            content_kind: 内容类型，rss 会使用 RSS 专用排版系统提示词

        Returns:
            摘要内容或错误标识字符串
        """
        system_prompt = (
            self.rss_system_prompt if content_kind == "rss" else self.system_prompt
        )
        if content_kind == "rss":
            if custom_prompt and custom_prompt.strip():
                user_content = (
                    f"以下是 RSS 订阅内容《{title}》的正文。\n\n"
                    f"专属指令：{custom_prompt}\n\n---\n\n{raw_text}"
                )
            else:
                user_content = (
                    f"以下是 RSS 订阅内容《{title}》的正文，请按照系统设定的 Markdown 排版规范进行整理：\n\n"
                    f"{raw_text}"
                )
        elif custom_prompt and custom_prompt.strip():
            user_content = f"以下是文章《{title}》的正文内容。\n\n专属指令：{custom_prompt}\n\n---\n\n{raw_text}"
        else:
            user_content = f"以下是公文《{title}》的正文内容，请按照系统设定的规范进行总结：\n\n{raw_text}"

        target_label = "RSS 订阅内容" if content_kind == "rss" else "公文"
        cache_scope = "rss_article_summary" if content_kind == "rss" else "article_summary"
        return self._generate_cached_with_retry(
            cache_scope=cache_scope,
            title=title,
            raw_text=raw_text,
            system_prompt=system_prompt,
            user_content=user_content,
            priority=priority,
            cancel_event=cancel_event,
            target_label=target_label,
            use_cache=use_cache,
        )

    def format_rss_article(
        self,
        title: str,
        raw_text: str,
        custom_prompt: str = None,
        priority: str = "batch",
        cancel_event: Optional[threading.Event] = None,
        use_cache: bool = True,
    ) -> str:
        extra_instruction = (
            f"\n\n源级额外要求：{custom_prompt.strip()}"
            if custom_prompt and custom_prompt.strip()
            else ""
        )
        user_content = (
            f"以下是 RSS 订阅内容《{title}》的原始 Markdown/正文。"
            f"{extra_instruction}\n\n---\n\n{raw_text}"
        )
        return self._generate_cached_with_retry(
            cache_scope="rss_formatting",
            title=title,
            raw_text=raw_text,
            system_prompt=self.rss_format_system_prompt,
            user_content=user_content,
            priority=priority,
            cancel_event=cancel_event,
            target_label="RSS 排版增强",
            use_cache=use_cache,
        )

    def summarize_rss_article(
        self,
        title: str,
        raw_text: str,
        custom_prompt: str = None,
        priority: str = "batch",
        cancel_event: Optional[threading.Event] = None,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        if self._should_use_chunked_rss_summary(raw_text):
            return self._summarize_rss_article_chunked(
                title=title,
                raw_text=raw_text,
                custom_prompt=custom_prompt or "",
                priority=priority,
                cancel_event=cancel_event,
                use_cache=use_cache,
            )

        return self._summarize_rss_article_single_pass(
            title=title,
            raw_text=raw_text,
            custom_prompt=custom_prompt or "",
            priority=priority,
            cancel_event=cancel_event,
            use_cache=use_cache,
        )

    def _generate_with_retry(
        self,
        *,
        title: str,
        raw_text: str,
        system_prompt: str,
        user_content: str,
        priority: str,
        cancel_event: Optional[threading.Event],
        target_label: str,
    ) -> str:
        active_cancel_event = cancel_event or self._cancel_event

        if cancel_event is None:
            active_cancel_event.clear()

        with self._summary_slot(priority):
            if not self.client:
                return self._error_result(
                    "系统未配置 API Key 或大模型尚未初始化。请在设置中配置。"
                )

            if not raw_text or len(raw_text.strip()) < 10:
                return self._error_result(
                    "原文内容过短或抓取失败，无法进行有效的 AI 处理。"
                )

            if active_cancel_event.is_set():
                return self._error_result("用户取消", prefix="⚠️")

            try:
                config_service = _get_config_service()
                if not config_service.get_api_balance_ok():
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

            logger.info(f"正在调用 AI 分析{target_label}: {title}")
            last_error = None
            effective_user_content = user_content
            content_risk_retry_used = False
            request_timeout = self._resolve_request_timeout(
                raw_text=raw_text,
                target_label=target_label,
                priority=priority,
            )

            for attempt in range(self.MAX_RETRIES):
                if active_cancel_event.is_set():
                    logger.info(f"AI 调用被用户取消（尝试 {attempt + 1}）: {title}")
                    return "⚠️ 用户取消"

                try:
                    result_container = {"content": None, "error": None}
                    call_completed = threading.Event()

                    def _api_call():
                        try:
                            response = self.client.chat.completions.create(
                                model=self.model_name,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": effective_user_content},
                                ],
                                temperature=0.3,
                                max_tokens=3000,
                                top_p=0.9,
                                timeout=request_timeout,
                            )
                            result_container["content"] = response.choices[
                                0
                            ].message.content
                        except Exception as e:
                            result_container["error"] = e
                        finally:
                            call_completed.set()

                    api_thread = threading.Thread(target=_api_call, daemon=True)
                    api_thread.start()

                    while not call_completed.wait(timeout=0.5):
                        if active_cancel_event.is_set():
                            logger.info(f"用户取消，等待 API 线程结束: {title}")
                            call_completed.wait(timeout=2.0)
                            return "⚠️ 用户取消"

                    error = result_container["error"]
                    if error is not None:
                        if isinstance(error, Exception):
                            raise error
                        raise RuntimeError(str(error))

                    content = result_container["content"]
                    if content:
                        cleaned_content = strip_emoji(content.strip())
                        if cleaned_content:
                            return cleaned_content
                        return "⚠️ AI 返回了空内容或非文本数据。"
                    return "⚠️ AI 返回了空内容或非文本数据。"

                except Exception as e:
                    last_error = str(e)
                    error_lower = last_error.lower()
                    normalized_error = self._normalize_openai_error(last_error)

                    if active_cancel_event.is_set():
                        return "⚠️ 用户取消"

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
                        return f"❌ {normalized_error}"

                    if "401" in error_lower:
                        logger.error(f"AI 调用失败（不可重试）({title}): {last_error}")
                        return f"❌ {normalized_error}"

                    if "403" in error_lower or "404" in error_lower:
                        logger.error(f"AI 调用失败（不可重试）({title}): {last_error}")
                        return f"❌ {normalized_error}"

                    if self._is_content_risk_error(last_error):
                        if not content_risk_retry_used:
                            retry_payload = self._build_content_risk_retry_payload(
                                effective_user_content
                            )
                            if retry_payload:
                                effective_user_content = str(
                                    retry_payload.get("user_content") or effective_user_content
                                )
                                content_risk_retry_used = True
                                logger.warning(
                                    "AI 调用触发内容风控，已启用内置降险重试 (%s): %s",
                                    title,
                                    retry_payload.get("summary") or "已替换敏感表述",
                                )
                                continue

                        logger.error(f"AI 调用失败（内容风控）({title}): {last_error}")
                        return (
                            "⚠️ 当前内容触发模型风控，系统已自动做过一次降险重试仍被拦截。"
                            "建议切换模型，或在设置里调整提示词后重试。"
                        )

                    if self._is_retryable_error(last_error):
                        if attempt < self.MAX_RETRIES - 1:
                            delay = self._calculate_delay(attempt)
                            logger.warning(
                                f"AI 调用失败（第 {attempt + 1} 次），{delay:.1f}秒后重试 ({title}): {normalized_error}"
                            )
                            for _ in range(int(delay * 10)):
                                if active_cancel_event.is_set():
                                    return "⚠️ 用户取消"
                                time.sleep(0.1)
                            continue
                        logger.error(
                            f"AI 调用失败（已达最大重试次数 {self.MAX_RETRIES}）({title}): {last_error}"
                        )
                        if self._is_timeout_error(last_error):
                            return (
                                "⚠️ AI 请求超时，"
                                f"已重试 {self.MAX_RETRIES} 次仍未完成。"
                                f"本次已将等待时间放宽到约 {request_timeout:.0f} 秒，"
                                "长内容请稍后重试。"
                            )
                        return f"⚠️ AI 服务暂时不可用，已重试 {self.MAX_RETRIES} 次仍失败：{normalized_error}"

                    logger.error(f"AI 总结失败 ({title}): {last_error}")
                    return f"⚠️ AI 处理遇到错误：{normalized_error}"

            return f"⚠️ AI 处理失败：{self._normalize_openai_error(last_error)}"

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
        # 只认显式传入的配置，不再从环境变量回退
        self.api_key = api_key or ""

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
            temp_client = OpenAI(
                api_key=api_key,
                base_url=final_base_url,
                max_retries=0,
                timeout=10,
            )

            # 发起极简请求
            temp_client.chat.completions.create(
                model=final_model,
                messages=[{"role": "user", "content": "1"}],
                max_tokens=5,
                temperature=0.1,
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
            normalized_error = self._normalize_openai_error(error_msg)
            # 增强型错误识别
            if "Authentication" in error_msg or "401" in error_msg:
                return False, "API Key 无效或认证失败"
            elif (
                "insufficient_quota" in error_msg or "insufficient_balance" in error_msg
            ):
                return False, "API 余额不足或额度超限"
            elif "timeout" in error_msg.lower():
                return False, normalized_error
            elif "404" in error_msg:
                return False, f"{normalized_error}：{final_model}"

            return False, f"连接失败: {normalized_error}"
