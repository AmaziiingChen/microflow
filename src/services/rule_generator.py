# pyright: reportOptionalMemberAccess=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportArgumentType=false
"""
动态爬虫规则生成服务 - 使用 ScrapeGraphAI 生成 CSS 选择器规则

核心功能：
1. 使用 AI 分析网页结构，自动生成 CSS 选择器规则
2. 沙盒测试验证规则有效性
3. 支持自定义 LLM 提供者（DeepSeek、OpenAI 等）
"""

import json
import logging
import uuid
import requests
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime

from bs4 import BeautifulSoup

from src.models.spider_rule import (
    SpiderRuleSchema,
    SpiderRuleOutput,
    RuleGenerationResult,
)

logger = logging.getLogger(__name__)


def _normalize_selector(selector: str) -> str:
    """
    规范化 CSS 选择器，修复常见的 AI 生成错误

    将非法的 JS 属性名转换为合法的 BeautifulSoup4 CSS 选择器格式。

    Args:
        selector: 原始选择器字符串

    Returns:
        规范化后的选择器
    """
    if not selector:
        return selector

    selector = selector.strip()
    original = selector  # 保存原始值用于日志

    # 1. 纯 JS 属性名 -> 转换为 ::attr() 或 ::text
    js_text_props = ["textcontent", "innertext", "text"]
    js_attr_props = {
        "href": "::attr(href)",
        "src": "::attr(src)",
    }

    selector_lower = selector.lower()

    # 处理纯 JS 文本属性
    if selector_lower in js_text_props:
        selector = "::text"
        logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
        return selector

    # 处理纯 JS 链接/图片属性
    if selector_lower in js_attr_props:
        selector = js_attr_props[selector_lower]
        logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
        return selector

    # 2. 处理带标签但用了 JS 属性的情况 (如 'a.href', 'span.textContent')
    if "." in selector and "::" not in selector:
        parts = selector.rsplit(".", 1)
        if len(parts) == 2:
            tag_part, attr_part = parts
            attr_lower = attr_part.lower()

            if attr_lower in js_text_props:
                selector = f"{tag_part}::text"
                logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
                return selector

            if attr_lower in js_attr_props:
                selector = f"{tag_part}{js_attr_props[attr_lower]}"
                logger.debug(f"选择器规范化: '{original}' -> '{selector}'")
                return selector

    return selector


class RuleGeneratorService:
    """
    动态爬虫规则生成服务

    使用 ScrapeGraphAI 的 SmartScraperGraph 分析网页结构，
    自动生成 CSS 选择器规则，并进行沙盒测试验证。

    Example:
        >>> service = RuleGeneratorService(config_service)
        >>> result = service.generate_and_test_rule(
        ...     task_id="task_001",
        ...     task_name="新闻列表",
        ...     url="https://example.com/news",
        ...     target_fields=["title", "date", "url"],
        ...     require_ai_summary=True
        ... )
    """

    # 高压 Prompt 模板
    RULE_GENERATION_PROMPT = """你是一个资深前端架构师和网页结构分析专家。

请仔细分析该网页的 HTML 结构，目标是提取一个**列表类型**的内容（如新闻列表、公告列表、文章列表等）。

你需要返回以下信息：
1. **list_container**: 包含所有列表项的父级 CSS 选择器（例如 'ul.news-list' 或 'div.article-list'）
2. **item_selector**: 单个列表项的 CSS 选择器（相对于 list_container，例如 'li' 或 'div.item'）
3. **field_selectors**: 一个字典，键为字段名，值为该字段在 item_selector 内部的相对 CSS 选择器

**用户想要提取的字段**: {target_fields}

================================================================================
【⚠️ CSS 选择器格式铁律 - 必须严格遵守】
================================================================================

你必须返回合法的 BeautifulSoup4 CSS 选择器。**绝对禁止返回 JS 属性名！**

❌ 以下格式是非法的（会导致提取失败）：
   - 'textContent' 或 'innerText' → 这是 JS 属性，不是 CSS 选择器！
   - 'href' 或 'src' → 这是 JS 属性，不是 CSS 选择器！
   - 'a.href' → 仍然是 JS 属性语法，非法！

✅ 正确格式如下：

1. **提取文本内容**：在选择器末尾加 ::text
   - 从子节点提取文本：'span.date::text' 或 'a.title::text'
   - 从当前 item 节点本身提取文本：'::text'（前面没有标签）

2. **提取属性值**：使用 ::attr(属性名)
   - 提取链接 href：'a::attr(href)'
   - 如果 item 本身就是 a 标签，直接用 '::attr(href)'
   - 提取图片 src：'img::attr(src)'

【示例对比】
| 字段用途 | ❌ 错误写法      | ✅ 正确写法              |
|---------|-----------------|-------------------------|
| 标题文本 | 'textContent'   | 'a.title::text' 或 '::text' |
| 链接地址 | 'href'          | 'a::attr(href)' 或 '::attr(href)' |
| 日期文本 | 'innerText'     | 'span.date::text'       |

================================================================================

**重要提示**：
- 选择器必须精准且稳定，避免使用动态生成的类名或索引
- 优先使用语义化的选择器（如 article, section, h1-h6）
- 对于链接字段，必须使用 '::attr(href)' 格式提取 href 属性
- 对于日期字段，请找到包含日期信息的元素并加 '::text'
- 如果字段可能是嵌套在其他元素中，请使用后代选择器

================================================================================
【⚠️ 极其重要的 CSS 选择器生成铁律 - 防止选择器失效】
================================================================================

在生成 CSS 选择器时，必须严格遵守以下规则：

❌ **绝对禁止使用的 Class 类型**：
1. 表现层 Class：如 flex, text-center, mt-4, w-full, p-2, grid, hidden
2. 打包工具生成的动态哈希 Class：如 css-1y3b6, style-A3x, sc-bdVaJa
3. 框架生成的随机 Class：如 emotion-xxx, styled-xxx, jss-xxx

✅ **必须优先使用的选择器类型**：
1. 具有业务语义的属性：id, data-testid, data-id, name, aria-label
2. 具有结构语义的标签：article, section, nav, main, aside, ul>li, ol>li
3. 具有明确意义的 class 命名：如 .news-list, .article-item, .post-title
4. 基于层级关系的稳定结构：如 div.container > div.content > article

【选择器稳定性示例】
| 场景 | ❌ 不稳定写法 | ✅ 稳定写法 |
|------|--------------|------------|
| 列表容器 | div.css-1k2x | ul.news-list 或 section#news |
| 列表项 | div.flex | article 或 li.news-item |
| 标题 | h3.text-lg | h3.post-title 或 a[data-testid="title"] |
| 日期 | span.mt-2 | time.datetime 或 span.publish-date |

**保证选择器的普适性与长效稳定性！网站改版后仍应有效！**

请严格按照指定的输出 Schema 返回结果。"""

    def __init__(self, config_service):
        """
        初始化规则生成服务

        Args:
            config_service: 配置服务实例，用于获取 LLM 配置
        """
        self.config_service = config_service
        self._generation_lock = threading.Lock()
        logger.info("🔧 RuleGeneratorService 初始化完成")

    def _get_llm_config(self) -> Dict[str, Any]:
        """
        从配置服务获取 LLM 配置

        Returns:
            LLM 配置字典
        """
        api_key = self.config_service.get("apiKey", "")
        base_url = self.config_service.get("baseUrl", "https://api.deepseek.com/v1")
        model_name = self.config_service.get("modelName", "deepseek-chat")

        return {"api_key": api_key, "base_url": base_url, "model_name": model_name}

    def _build_scrapegraph_config(self, llm_config: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建 ScrapeGraphAI 配置

        使用 model_instance 参数传入自定义 LLM 实例，支持任意兼容 OpenAI 格式的模型。

        Args:
            llm_config: LLM 配置

        Returns:
            ScrapeGraphAI 配置字典
        """
        from langchain_openai import ChatOpenAI

        # 创建兼容 OpenAI 格式的 LLM 实例
        llm_instance = ChatOpenAI(
            model=llm_config["model_name"],
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
            temperature=0.1,  # 低温度以获得更稳定的选择器
        )

        return {
            "llm": {
                "model_instance": llm_instance,
                "model_tokens": 8192,  # 默认 token 数量
            },
            "verbose": False,
            "headless": True,  # 无头模式
        }

    def _fetch_html_content(self, url: str, timeout: int = 30) -> Optional[str]:
        """
        获取网页 HTML 内容

        Args:
            url: 目标 URL
            timeout: 超时时间（秒）

        Returns:
            HTML 内容字符串，失败返回 None
        """
        try:
            # 🌟 强力伪装：模拟真实浏览器行为，绕过基础反爬
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "max-age=0",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            }

            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()

            # 处理编码
            if response.encoding is None or response.encoding == "ISO-8859-1":
                response.encoding = response.apparent_encoding or "utf-8"

            return response.text

        except requests.exceptions.Timeout:
            logger.error(f"获取网页超时: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"获取网页失败: {url}, 错误: {e}")
            return None

    def _test_rule_with_beautifulsoup(
        self, html_content: str, rule: SpiderRuleSchema, max_items: int = 3
    ) -> List[Dict[str, str]]:
        """
        使用 BeautifulSoup 测试规则

        支持以下选择器格式：
        - 普通选择器：'span.date', 'a.title'
        - 文本提取：'span.date::text', '::text'（从当前节点提取）
        - 属性提取：'a::attr(href)', '::attr(href)'（从当前节点提取）

        Args:
            html_content: HTML 内容
            rule: 生成的规则
            max_items: 最大测试项数

        Returns:
            提取的样本数据列表
        """
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            # 查找列表容器
            container = soup.select_one(rule.list_container)
            if not container:
                logger.warning(f"未找到列表容器: {rule.list_container}")
                return []

            # 查找所有列表项
            items = container.select(rule.item_selector)[:max_items]
            if not items:
                logger.warning(f"未找到列表项: {rule.item_selector}")
                return []

            # 提取字段
            results = []
            for item in items:
                item_data = {}
                for field_name, raw_selector in rule.field_selectors.items():
                    try:
                        # 🌟 规范化选择器（修复 AI 生成的非法格式）
                        selector = _normalize_selector(raw_selector)

                        # 🌟 解析选择器格式
                        value = self._extract_field_value(item, selector, field_name)
                        item_data[field_name] = value

                    except Exception as e:
                        logger.warning(f"提取字段 {field_name} 失败: {e}")
                        item_data[field_name] = ""

                results.append(item_data)

            return results

        except Exception as e:
            logger.error(f"沙盒测试失败: {e}")
            return []

    def _extract_field_value(self, item, selector: str, field_name: str) -> str:
        """
        从列表项中提取字段值

        支持三种选择器格式：
        1. 普通选择器：'span.date' -> 获取元素的文本
        2. 文本提取：'span.date::text' 或 '::text' -> 获取文本
        3. 属性提取：'a::attr(href)' 或 '::attr(href)' -> 获取属性值

        Args:
            item: BeautifulSoup 元素（列表项）
            selector: CSS 选择器
            field_name: 字段名（用于智能判断）

        Returns:
            提取的值字符串
        """
        # 解析选择器中的 ::text 或 ::attr() 后缀
        attr_name = None
        extract_text = False
        css_selector = selector

        if "::attr(" in selector:
            # 提取属性：'a::attr(href)' -> css_selector='a', attr_name='href'
            import re

            match = re.match(r"^(.+?)::attr\(([^)]+)\)$", selector)
            if match:
                css_selector = match.group(1).strip()
                attr_name = match.group(2).strip()
            else:
                # 纯属性提取：'::attr(href)' -> css_selector='', attr_name='href'
                match = re.match(r"^::attr\(([^)]+)\)$", selector)
                if match:
                    css_selector = ""
                    attr_name = match.group(1).strip()
        elif "::text" in selector:
            # 提取文本：'span.date::text' 或 '::text'
            css_selector = selector.replace("::text", "").strip()
            extract_text = True

        # 根据选择器类型确定目标元素
        if css_selector:
            # 有具体选择器，从子节点中查找
            element = item.select_one(css_selector)
            if not element:
                return ""
        else:
            # 纯 '::text' 或 '::attr(href)'，直接操作当前 item
            element = item

        # 提取值
        if attr_name:
            # 提取属性
            value = element.get(attr_name, "") or ""
            if isinstance(value, list):
                value = value[0] if value else ""
            return str(value).strip()
        elif extract_text:
            # 提取文本
            return element.get_text(strip=True)
        else:
            # 默认行为：根据字段名智能判断
            if field_name.lower() in ["url", "link", "href"]:
                # 链接字段，优先取 href
                return element.get("href", "") or element.get_text(strip=True)
            else:
                # 其他字段，取文本
                return element.get_text(strip=True)

    def generate_and_test_rule(
        self,
        task_id: str,
        task_name: str,
        url: str,
        target_fields: List[str],
        require_ai_summary: bool = False,
        task_purpose: str = "",
    ) -> RuleGenerationResult:
        """
        生成并测试爬虫规则

        核心方法：使用 AI 分析网页结构，生成 CSS 选择器规则，并进行沙盒测试。

        Args:
            task_id: 任务 ID
            task_name: 任务名称（映射到数据库 department 字段）
            url: 目标网页 URL
            target_fields: 用户想要提取的字段列表
            require_ai_summary: 是否需要对抓取内容进行 AI 摘要
            task_purpose: 任务目的/类别（映射到数据库 category 字段）

        Returns:
            RuleGenerationResult 包含生成的规则和沙盒测试数据
        """
        with self._generation_lock:
            try:
                logger.info(f"🕷️ 开始生成规则: task_id={task_id}, url={url}")

                # 1. 检查 LLM 配置
                llm_config = self._get_llm_config()
                if not llm_config["api_key"]:
                    return RuleGenerationResult(
                        success=False,
                        error_message="未配置 API Key，请先在设置中配置 LLM",
                    )

                # 2. 导入 ScrapeGraphAI
                try:
                    from scrapegraphai.graphs import SmartScraperGraph
                except ImportError:
                    return RuleGenerationResult(
                        success=False,
                        error_message="ScrapeGraphAI 未安装，请运行 pip install scrapegraphai",
                    )

                # 3. 构建配置和 Prompt
                graph_config = self._build_scrapegraph_config(llm_config)
                prompt = self.RULE_GENERATION_PROMPT.format(
                    target_fields=", ".join(target_fields)
                )

                logger.info(f"📝 Prompt: {prompt[:100]}...")
                logger.info(
                    f"🔧 LLM Config: base_url={llm_config['base_url']}, model={llm_config['model_name']}"
                )

                # 4. 创建并执行 SmartScraperGraph
                try:
                    smart_scraper = SmartScraperGraph(
                        prompt=prompt,
                        source=url,
                        config=graph_config,
                        schema=SpiderRuleSchema,
                    )

                    result = smart_scraper.run()
                    logger.info(f"🤖 AI 生成结果: {result}")

                except Exception as e:
                    error_msg = str(e)
                    logger.error(f"AI 规则生成失败: {error_msg}")

                    # 提供更友好的错误信息
                    if (
                        "api_key" in error_msg.lower()
                        or "authentication" in error_msg.lower()
                    ):
                        error_msg = "API Key 无效或已过期，请检查配置"
                    elif (
                        "connection" in error_msg.lower()
                        or "timeout" in error_msg.lower()
                    ):
                        error_msg = "网络连接失败，请检查网络或 base_url 配置"
                    elif "rate limit" in error_msg.lower():
                        error_msg = "API 请求频率超限，请稍后重试"

                    return RuleGenerationResult(
                        success=False, error_message=f"AI 规则生成失败: {error_msg}"
                    )

                # 5. 解析结果
                if not isinstance(result, dict):
                    # 如果返回的是 Pydantic 模型，转换为字典
                    if hasattr(result, "model_dump"):
                        result = result.model_dump()
                    else:
                        return RuleGenerationResult(
                            success=False,
                            error_message=f"AI 返回格式异常: {type(result)}",
                        )

                # 6. 构建规则对象
                try:
                    rule_schema = SpiderRuleSchema(**result)
                except Exception as e:
                    logger.error(f"规则验证失败: {e}, 原始数据: {result}")
                    return RuleGenerationResult(
                        success=False, error_message=f"生成的规则格式不正确: {e}"
                    )

                # 7. 生成完整规则
                rule_id = f"rule_{uuid.uuid4().hex[:8]}"
                now = datetime.now().isoformat()

                rule_output = SpiderRuleOutput(
                    rule_id=rule_id,
                    task_id=task_id,
                    task_name=task_name,
                    task_purpose=task_purpose,
                    url=url,
                    list_container=rule_schema.list_container,
                    item_selector=rule_schema.item_selector,
                    field_selectors=rule_schema.field_selectors,
                    require_ai_summary=require_ai_summary,
                    created_at=now,
                    updated_at=now,
                    enabled=True,
                )

                # 8. 沙盒测试
                logger.info(f"🧪 开始沙盒测试: {url}")
                html_content = self._fetch_html_content(url)

                sample_data = []
                if html_content:
                    sample_data = self._test_rule_with_beautifulsoup(
                        html_content, rule_schema, max_items=3
                    )
                    logger.info(f"🧪 沙盒测试结果: 提取到 {len(sample_data)} 条数据")
                else:
                    logger.warning("无法获取网页内容，跳过沙盒测试")

                # 9. 返回结果
                return RuleGenerationResult(
                    success=True, rule=rule_output, sample_data=sample_data
                )

            except Exception as e:
                logger.error(f"规则生成过程发生异常: {e}", exc_info=True)
                return RuleGenerationResult(
                    success=False, error_message=f"规则生成过程发生异常: {str(e)}"
                )

    def test_existing_rule(
        self, rule: SpiderRuleOutput, max_items: int = 3
    ) -> List[Dict[str, str]]:
        """
        测试已有规则

        Args:
            rule: 已有的规则
            max_items: 最大测试项数

        Returns:
            提取的样本数据列表
        """
        try:
            # 获取 HTML
            html_content = self._fetch_html_content(rule.url)
            if not html_content:
                return []

            # 构建规则 Schema
            rule_schema = SpiderRuleSchema(
                list_container=rule.list_container,
                item_selector=rule.item_selector,
                field_selectors=rule.field_selectors,
            )

            # 测试
            return self._test_rule_with_beautifulsoup(
                html_content, rule_schema, max_items
            )

        except Exception as e:
            logger.error(f"测试规则失败: {e}")
            return []
