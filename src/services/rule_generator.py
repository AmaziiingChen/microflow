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
import threading
from copy import deepcopy
from typing import Dict, List, Optional, Any
from datetime import datetime

from bs4 import BeautifulSoup, FeatureNotFound

from src.models.spider_rule import (
    SpiderRuleSchema,
    SpiderRuleOutput,
    RuleGenerationResult,
)
from src.services.html_template_library import (
    build_site_profile,
    match_template_candidates,
)
from src.utils.browser_render import fetch_html_with_strategy, normalize_fetch_strategy
from src.utils.html_rule_strategy import normalize_detail_strategy
from src.utils.http_rule_config import (
    normalize_request_headers,
    normalize_cookie_string,
    parse_cookie_string,
    normalize_request_method,
    normalize_request_body,
)

logger = logging.getLogger(__name__)


def _create_soup(html: str) -> BeautifulSoup:
    """优先使用 lxml，缺失时自动回退到内置 html.parser。"""
    try:
        return BeautifulSoup(html, "lxml")
    except FeatureNotFound:
        logger.debug("lxml 解析器不可用，回退到 html.parser")
        return BeautifulSoup(html, "html.parser")


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


def score_selector_stability(selector: str) -> float:
    """
    评估 CSS 选择器稳定性得分 (0-100)

    根据选择器的特征评估其在网站改版后的存活概率。
    分数越高表示选择器越稳定，越不容易因网站更新而失效。

    Args:
        selector: CSS 选择器字符串

    Returns:
        稳定性得分 (0-100)
    """
    if not selector:
        return 0.0

    score = 60.0  # 基础分

    # 1. 检查是否使用 ID 选择器（+20 分）
    if selector.startswith('#') or '[id=' in selector:
        score += 20

    # 2. 检查是否使用语义标签（+15 分）
    semantic_tags = ['article', 'section', 'main', 'nav', 'aside', 'ul', 'ol', 'li', 'time', 'header', 'footer']
    if any(tag in selector.lower() for tag in semantic_tags):
        score += 15

    # 3. 检查是否使用 data-* 属性（+25 分）- 最稳定的标识符
    if 'data-' in selector or '[data-' in selector:
        score += 25

    # 4. 检查是否使用 aria-label 等无障碍属性（+15 分）
    if 'aria-' in selector or '[aria-' in selector:
        score += 15

    # 5. 检查是否使用有意义的 class 名称（+10 分）
    meaningful_patterns = ['news', 'article', 'list', 'item', 'post', 'content', 'title', 'date']
    if any(pattern in selector.lower() for pattern in meaningful_patterns):
        score += 10

    # 6. 检查是否包含动态 class（-30 分）
    import re
    dynamic_patterns = [
        r'css-[a-z0-9]+',      # CSS-in-JS 生成
        r'style-[a-z0-9]+',     # Styled Components
        r'sc-[a-zA-Z]+',        # Styled Components
        r'_[a-f0-9]{5,}',       # Webpack hash
        r'[a-z]+-\d+[a-z]*$',   # Tailwind 如 mt-4, px-2
    ]
    for pattern in dynamic_patterns:
        if re.search(pattern, selector):
            score -= 30
            break

    # 7. 检查选择器深度（每多一层 -3 分）
    depth = selector.count('>') + selector.count(' ')
    score -= min(depth * 3, 15)  # 最多扣 15 分

    # 8. 检查是否使用索引选择器（-25 分）
    if ':nth-child' in selector or ':nth-of-type' in selector or ':first-child' in selector or ':last-child' in selector:
        score -= 25

    # 9. 检查是否使用通配符（-20 分）
    if '*' in selector:
        score -= 20

    # 10. 检查是否过于依赖特定标签嵌套（-10 分）
    if selector.count('>') >= 3:
        score -= 10

    return max(0.0, min(100.0, score))


def get_stability_rating(score: float) -> str:
    """
    根据得分返回稳定性评级

    Args:
        score: 稳定性得分 (0-100)

    Returns:
        评级字符串
    """
    if score >= 80:
        return "🟢 优秀"
    elif score >= 60:
        return "🟡 良好"
    elif score >= 40:
        return "🟠 一般"
    else:
        return "🔴 风险"


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
        self._last_html_fetch_error = ""
        logger.info("🔧 RuleGeneratorService 初始化完成")

    # 🌟 网站类型识别策略
    WEBSITE_TYPE_STRATEGIES = {
        "edu_gov": {
            "keywords": [".edu.cn", ".gov.cn", "edu.cn", "gov.cn", "university", "college", "school"],
            "hints": """【学校/政府网站特征】
- 通常使用传统的 HTML 结构，表格布局或 ul/li 列表
- class 命名较为规范，如 .news-list, .article-list, .content-list
- 常见标签：ul.list-gl, dl.article-list, table.news
- 优先查找：ul > li, dl > dt, table tr""",
            "priority_selectors": ["ul", "ol", "dl", "table", ".list", ".content"]
        },
        "news_portal": {
            "keywords": ["news", "xinwen", "sina", "163", "qq.com", "sohu", "toutiao", "ifeng"],
            "hints": """【新闻门户特征】
- 通常使用 article 标签或语义化结构
- class 命名如 .news-item, .article-item, .post
- 常见标签：article, section.main, div.news-list
- 优先查找：article, .news-item, .post-item""",
            "priority_selectors": ["article", "section", ".news-list", ".article-list"]
        },
        "blog_forum": {
            "keywords": ["blog", "forum", "community", "zhihu", "weibo", "tieba", "discuz"],
            "hints": """【博客/论坛特征】
- 用户生成内容，结构可能较复杂
- class 命名如 .post, .topic, .thread, .comment
- 常见标签：div.post, div.topic-item, li.thread
- 优先查找：.post, .topic, .thread, .comment-item""",
            "priority_selectors": [".post", ".topic", ".thread", "article"]
        },
        "ecommerce": {
            "keywords": ["shop", "store", "mall", "taobao", "jd.com", "amazon", "ebay"],
            "hints": """【电商网站特征】
- 商品列表通常使用 grid 布局
- class 命名如 .product-item, .goods-card, .item
- 常见标签：div.product, li.goods, a.product-link
- 优先查找：.product-item, .goods-card, .item""",
            "priority_selectors": [".product", ".goods", ".item-card"]
        }
    }

    def _identify_website_type(self, url: str, html_content: str) -> tuple:
        """
        识别网站类型，返回 (类型名称, 策略提示)

        Args:
            url: 目标 URL
            html_content: HTML 内容

        Returns:
            (网站类型, 策略提示字典)
        """
        url_lower = url.lower()

        # 基于域名匹配
        for site_type, strategy in self.WEBSITE_TYPE_STRATEGIES.items():
            for keyword in strategy["keywords"]:
                if keyword in url_lower:
                    logger.info(f"🌐 网站类型识别: {site_type} (匹配关键词: {keyword})")
                    return site_type, strategy

        # 默认策略
        default_strategy = {
            "hints": """【通用网站】
- 分析 HTML 结构，寻找重复出现的列表项模式
- 优先使用语义化标签和有意义的 class 名称
- 避免使用动态生成的 class""",
            "priority_selectors": ["ul", "ol", "div.list", "article"]
        }
        return "general", default_strategy

    def _build_enhanced_prompt(self, target_fields: List[str], website_type: str, strategy: dict) -> str:
        """
        构建增强型 Prompt，注入网站类型特定的策略提示

        Args:
            target_fields: 目标字段列表
            website_type: 网站类型
            strategy: 网站策略字典

        Returns:
            增强后的 Prompt
        """
        base_prompt = self.RULE_GENERATION_PROMPT.format(target_fields=", ".join(target_fields))

        # 注入网站类型特定的提示
        type_hint = f"""

================================================================================
【🌐 网站类型识别: {website_type}】
================================================================================
{strategy.get('hints', '')}

【优先尝试的选择器类型】
{', '.join(strategy.get('priority_selectors', []))}
"""

        return base_prompt + type_hint

    def _build_recovery_prompt_hint(
        self,
        recovery_context: Optional[Dict[str, Any]],
    ) -> str:
        if not isinstance(recovery_context, dict):
            return ""

        health = (
            recovery_context.get("health")
            if isinstance(recovery_context.get("health"), dict)
            else {}
        )
        snapshot = (
            recovery_context.get("last_known_good_snapshot")
            if isinstance(recovery_context.get("last_known_good_snapshot"), dict)
            else {}
        )
        current_rule = (
            recovery_context.get("current_rule_snapshot")
            if isinstance(recovery_context.get("current_rule_snapshot"), dict)
            else {}
        )

        lines = [
            "",
            "================================================================================",
            "【♻️ 规则恢复上下文】",
            "================================================================================",
        ]

        status_detail = str(health.get("status_detail") or "").strip().lower()
        error_message = str(health.get("last_error_message") or "").strip()
        if status_detail == "list_container_drift":
            lines.append("- 上次失败点：旧的列表容器选择器已失效，请优先重新定位承载重复列表项的父容器。")
        elif status_detail == "item_selector_drift":
            lines.append("- 上次失败点：列表项选择器已失效，请优先重新定位单条重复项。")
        elif status_detail == "field_drift":
            lines.append("- 上次失败点：字段命中率明显下降，请尽量保留仍稳定的字段，仅修复失效字段。")
        elif status_detail == "stale_empty":
            lines.append("- 上次失败点：规则持续空抓，请检查站点列表结构是否改版或内容入口已迁移。")

        if error_message:
            lines.append(f"- 最近错误：{error_message}")

        if snapshot:
            lines.append("- 最近一次健康快照可作为优先参考，请尽量在其基础上最小改动：")
            if snapshot.get("list_container"):
                lines.append(f"  - list_container: {snapshot.get('list_container')}")
            if snapshot.get("item_selector"):
                lines.append(f"  - item_selector: {snapshot.get('item_selector')}")
            field_selectors = snapshot.get("field_selectors")
            if isinstance(field_selectors, dict) and field_selectors:
                lines.append(
                    f"  - field_selectors: {json.dumps(field_selectors, ensure_ascii=False)}"
                )

        if current_rule:
            current_fields = current_rule.get("field_selectors")
            if isinstance(current_fields, dict) and current_fields:
                lines.append(
                    f"- 当前待修复规则字段：{json.dumps(current_fields, ensure_ascii=False)}"
                )

        lines.append("- 输出目标：给出一套尽量稳健、语义化、可维护的新版规则。")
        return "\n".join(lines).strip()

    def _build_template_prompt_hint(
        self,
        template_candidates: Optional[List[Dict[str, Any]]],
        target_fields: Optional[List[str]] = None,
    ) -> str:
        """将模板库推荐转成 AI 可理解的补充提示。"""
        candidates = [
            item for item in (template_candidates or []) if isinstance(item, dict)
        ][:2]
        if not candidates:
            return ""

        normalized_targets = [
            str(field or "").strip()
            for field in (target_fields or [])
            if str(field or "").strip()
        ]
        lines = [
            "",
            "================================================================================",
            "【📚 模板库候选】",
            "================================================================================",
            "以下模板为当前网页的高相似度候选，请优先参考其结构，再输出最终规则：",
        ]
        for index, candidate in enumerate(candidates, start=1):
            field_selectors = (
                candidate.get("field_selectors")
                if isinstance(candidate.get("field_selectors"), dict)
                else {}
            )
            compact_fields = {
                field: field_selectors.get(field)
                for field in normalized_targets
                if str(field_selectors.get(field) or "").strip()
            }
            if not compact_fields:
                compact_fields = {
                    key: value
                    for key, value in list(field_selectors.items())[:4]
                    if str(key or "").strip() and str(value or "").strip()
                }
            lines.append(
                f"{index}. {candidate.get('name')} / {candidate.get('profile_label')} / {candidate.get('confidence_label')}"
            )
            if candidate.get("reason"):
                lines.append(f"   - 匹配原因：{candidate.get('reason')}")
            if candidate.get("list_container"):
                lines.append(f"   - list_container: {candidate.get('list_container')}")
            if candidate.get("item_selector"):
                lines.append(f"   - item_selector: {candidate.get('item_selector')}")
            if compact_fields:
                lines.append(
                    f"   - field_selectors: {json.dumps(compact_fields, ensure_ascii=False)}"
                )
        lines.append("请保留模板中真正稳定的结构特征，不要机械照抄不适用的选择器。")
        return "\n".join(lines).strip()

    def _build_recovery_schema(
        self,
        recovery_context: Optional[Dict[str, Any]],
        target_fields: List[str],
    ) -> Optional[SpiderRuleSchema]:
        if not isinstance(recovery_context, dict):
            return None

        snapshot = (
            recovery_context.get("last_known_good_snapshot")
            if isinstance(recovery_context.get("last_known_good_snapshot"), dict)
            else {}
        )
        if not snapshot:
            return None

        list_container = str(snapshot.get("list_container") or "").strip()
        item_selector = str(snapshot.get("item_selector") or "").strip()
        field_selectors = (
            snapshot.get("field_selectors")
            if isinstance(snapshot.get("field_selectors"), dict)
            else {}
        )
        if not list_container or not item_selector or not field_selectors:
            return None

        normalized_fields: Dict[str, str] = {}
        preferred_fields = [str(field or "").strip() for field in target_fields if str(field or "").strip()]
        for field_name in preferred_fields:
            selector = str(field_selectors.get(field_name) or "").strip()
            if selector:
                normalized_fields[field_name] = selector

        if not normalized_fields:
            normalized_fields = {
                str(key): str(value).strip()
                for key, value in field_selectors.items()
                if str(key or "").strip() and str(value or "").strip()
            }

        if not normalized_fields:
            return None

        try:
            return SpiderRuleSchema(
                list_container=list_container,
                item_selector=item_selector,
                field_selectors=normalized_fields,
            )
        except Exception as e:
            logger.debug("恢复快照构建 SpiderRuleSchema 失败: %s", e)
            return None

    def _sample_data_has_signal(
        self,
        sample_data: Optional[List[Dict[str, Any]]],
        target_fields: List[str],
    ) -> bool:
        if not sample_data:
            return False

        preferred_fields = [
            field
            for field in target_fields
            if str(field or "").strip().lower() in {"title", "url", "date", "time"}
        ]
        inspect_fields = preferred_fields or [str(field or "").strip() for field in target_fields if str(field or "").strip()]

        for item in sample_data:
            if not isinstance(item, dict):
                continue
            for field_name in inspect_fields:
                if str(item.get(field_name) or "").strip():
                    return True
        return False

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

    def _get_secondary_llm_configs(self) -> List[Dict[str, Any]]:
        """
        获取备选 LLM 配置（用于多模型投票）

        从配置中读取备选模型，用于多模型投票机制。

        Returns:
            备选 LLM 配置列表
        """
        secondary_configs = []

        # 从统一的 secondaryModels 字段读取
        secondary_models = self.config_service.get("secondaryModels", [])

        for model_config in secondary_models:
            # 每个元素是 {"baseUrl": "...", "apiKey": "...", "modelName": "..."}
            if isinstance(model_config, dict):
                base_url = model_config.get("baseUrl", "")
                api_key = model_config.get("apiKey", "")
                model_name = model_config.get("modelName", "")

                # 只有三个字段都存在才添加
                if base_url and api_key and model_name:
                    secondary_configs.append({
                        "api_key": api_key,
                        "base_url": base_url,
                        "model_name": model_name
                    })

        return secondary_configs

    def _vote_best_rule(
        self,
        results: List[tuple],
        raw_html: str
    ) -> tuple:
        """
        多模型投票：选择最佳规则

        根据沙盒测试结果和稳定性评分选择最佳规则。

        Args:
            results: [(rule_schema, sample_data, stability_score), ...] 列表
            raw_html: 原始 HTML（用于验证）

        Returns:
            (最佳规则, 样本数据, 稳定性评分, 投票信息)
        """
        if not results:
            return None, [], 0.0, "无有效结果"

        if len(results) == 1:
            return results[0][0], results[0][1], results[0][2], "单模型"

        # 评分每个结果
        scored_results = []
        for rule_schema, sample_data, stability_score in results:
            score = 0.0

            # 1. 数据完整性评分（最多 40 分）
            if sample_data:
                data_count = len(sample_data)
                score += min(data_count * 10, 40)  # 每条数据 10 分，最多 40 分

                # 检查字段完整性
                if sample_data:
                    total_fields = len(sample_data[0]) if sample_data else 0
                    filled_fields = sum(
                        1 for v in sample_data[0].values() if v and v.strip()
                    ) if sample_data else 0
                    if total_fields > 0:
                        score += (filled_fields / total_fields) * 20  # 最多 20 分

            # 2. 稳定性评分（最多 40 分）
            score += stability_score * 0.4

            scored_results.append((rule_schema, sample_data, stability_score, score))

        # 按分数排序
        scored_results.sort(key=lambda x: x[3], reverse=True)

        best = scored_results[0]
        vote_info = f"多模型投票: {len(results)} 个模型参与，最佳得分 {best[3]:.1f} 分"

        logger.info(f"🗳️ {vote_info}")
        for i, (rule, data, stab, score) in enumerate(scored_results):
            logger.info(f"  模型 {i+1}: {len(data)} 条数据, 稳定性 {stab:.1f}, 总分 {score:.1f}")

        return best[0], best[1], best[2], vote_info

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
            "loader_kwargs": {
                "command": "playwright",
            },
        }

    def _fetch_html_content(
        self,
        url: str,
        timeout: int = 30,
        fetch_strategy: str = "requests_first",
        request_method: str = "get",
        request_body: str = "",
        request_headers: Optional[Dict[str, str]] = None,
        cookie_string: str = "",
    ) -> Optional[str]:
        """
        获取网页 HTML 内容

        Args:
            url: 目标 URL
            timeout: 超时时间（秒）

        Returns:
            HTML 内容字符串，失败返回 None
        """
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

        normalized_strategy = normalize_fetch_strategy(fetch_strategy)
        normalized_request_method = normalize_request_method(request_method)
        normalized_request_body = normalize_request_body(request_body)
        normalized_request_headers = normalize_request_headers(request_headers)
        normalized_cookie_string = normalize_cookie_string(cookie_string)
        merged_headers = {
            **headers,
            **normalized_request_headers,
        }
        result = fetch_html_with_strategy(
            url,
            strategy=normalized_strategy,
            headers=merged_headers,
            browser_headers=normalized_request_headers,
            cookies=parse_cookie_string(normalized_cookie_string),
            request_method=normalized_request_method,
            request_body=normalized_request_body,
            request_timeout_seconds=timeout,
            browser_timeout_seconds=max(timeout, 20),
            browser_wait_ms=1200,
        )
        if result.success:
            self._last_html_fetch_error = ""
            logger.info(
                "网页抓取成功: strategy=%s, engine=%s, url=%s",
                normalized_strategy,
                result.engine or "unknown",
                url,
            )
            return result.html

        self._last_html_fetch_error = str(
            result.error_message or f"获取网页失败: {url}"
        ).strip()
        logger.error(
            "获取网页失败: strategy=%s, url=%s, 错误=%s",
            normalized_strategy,
            url,
            self._last_html_fetch_error,
        )
        return None

    def _prune_html(self, raw_html: str) -> str:
        """
        DOM 降噪预处理：剔除干扰 AI 分析的无效节点

        通过移除无意义标签、简化属性、处理原子化 CSS，
        大幅减少 HTML 体积，提升 AI 分析精准度。

        Args:
            raw_html: 原始 HTML 内容

        Returns:
            清洗后的精简 HTML
        """
        from bs4 import Comment

        soup = _create_soup(raw_html)

        # 1. 移除无意义的标签（保留语义标签如 article, section, nav）
        for tag in soup(['script', 'style', 'noscript', 'iframe', 'canvas', 'svg', 'path']):
            tag.decompose()

        # 2. 移除所有注释
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # 3. 简化属性（保留对选择器生成有意义的属性）
        ALLOWED_ATTRS = {'id', 'class', 'href', 'title', 'name', 'data-testid', 'aria-label', 'role'}
        for tag in soup.find_all(True):
            attrs = dict(tag.attrs)
            for attr in list(attrs.keys()):
                if attr not in ALLOWED_ATTRS:
                    del tag[attr]

            # 4. 处理原子化 CSS（Tailwind 等）：class 过长则清空
            class_list = tag.get('class', [])
            if class_list and (len(class_list) > 6 or any(len(c) > 25 for c in class_list)):
                del tag['class']

        return str(soup)

    def _extract_main_content_region(self, raw_html: str) -> Optional[Dict[str, Any]]:
        """
        使用 trafilatura 提取主内容区域信息

        分析 HTML 结构，识别主要内容区域，为 AI 提供重点分析范围。

        Args:
            raw_html: 原始 HTML 内容

        Returns:
            包含主内容区域信息的字典，或 None（如果提取失败）
        """
        try:
            import trafilatura

            # 1. 提取正文内容（用于判断页面类型）
            text_content = trafilatura.extract(
                raw_html,
                include_comments=False,
                include_tables=True,
                no_fallback=False
            )

            if not text_content:
                logger.warning("trafilatura 无法提取正文内容")
                return None

            # 2. 分析 HTML 结构，识别主内容区域
            soup = _create_soup(raw_html)

            # 3. 常见的主内容区域标签
            main_content_tags = [
                ('main', None),
                ('article', None),
                ('section', {'class': lambda x: x and any(k in ' '.join(x) for k in ['content', 'main', 'article', 'post', 'news', 'list'])}),
                ('div', {'id': lambda x: x and any(k in x.lower() for k in ['content', 'main', 'article', 'post', 'news', 'list'])}),
                ('div', {'class': lambda x: x and any(k in ' '.join(x).lower() for k in ['content', 'main', 'article', 'post', 'news', 'list'])}),
            ]

            detected_regions = []
            for tag_name, attrs in main_content_tags:
                elements = soup.find_all(tag_name, attrs) if attrs else soup.find_all(tag_name)
                for elem in elements:
                    # 计算元素内的链接数量（列表页通常有多个链接）
                    links = elem.find_all('a', href=True)
                    if len(links) >= 3:  # 至少有3个链接才算可能是列表区域
                        detected_regions.append({
                            'tag': tag_name,
                            'id': elem.get('id', ''),
                            'class': ' '.join(elem.get('class', [])),
                            'link_count': len(links),
                            'text_length': len(elem.get_text(strip=True)),
                        })

            # 4. 按链接数量排序，选择最可能是列表区域的前3个
            detected_regions.sort(key=lambda x: x['link_count'], reverse=True)
            top_regions = detected_regions[:3]

            if top_regions:
                logger.info(f"🎯 检测到 {len(top_regions)} 个潜在列表区域")
                for i, region in enumerate(top_regions):
                    logger.info(f"  区域 {i+1}: {region['tag']}#{region['id']}.{region['class'][:30]} ({region['link_count']} 个链接)")

            return {
                'text_content': text_content[:500] if text_content else '',  # 前500字符用于上下文
                'detected_regions': top_regions,
                'total_text_length': len(text_content) if text_content else 0,
            }

        except ImportError:
            logger.warning("trafilatura 未安装，跳过主内容区域提取。运行 pip install trafilatura 安装")
            return None
        except Exception as e:
            logger.warning(f"主内容区域提取失败: {e}")
            return None

    def _build_page_summary(
        self,
        *,
        url: str,
        raw_html: str,
        pruned_html: str,
        website_type: str = "",
        fetch_strategy: str = "requests_first",
        request_method: str = "get",
        content_region: Optional[Dict[str, Any]] = None,
        site_profile: Optional[Dict[str, Any]] = None,
        template_candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """构建轻量页面摘要，供规则调试与持久化保存。"""
        soup = _create_soup(raw_html)
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        meta_description = ""
        for attrs in (
            {"name": "description"},
            {"property": "og:description"},
            {"name": "Description"},
        ):
            meta = soup.find("meta", attrs=attrs)
            content = str(meta.get("content") or "").strip() if meta else ""
            if content:
                meta_description = content
                break

        canonical_link = ""
        canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
        if canonical_tag:
            canonical_link = str(canonical_tag.get("href") or "").strip()

        headings = []
        for heading in soup.find_all(["h1", "h2", "h3"], limit=8):
            text = heading.get_text(" ", strip=True)
            if not text:
                continue
            headings.append(
                {
                    "level": heading.name.lower(),
                    "text": text[:120],
                }
            )

        signal_counts = {
            "link_count": len(soup.find_all("a", href=True)),
            "list_count": len(soup.find_all(["ul", "ol"])),
            "table_count": len(soup.find_all("table")),
            "article_count": len(soup.find_all("article")),
            "heading_count": len(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])),
        }

        detected_regions = []
        if isinstance(content_region, dict):
            detected_regions = (
                content_region.get("detected_regions")
                if isinstance(content_region.get("detected_regions"), list)
                else []
            )

        compression_ratio = 0.0
        if raw_html:
            compression_ratio = round(len(pruned_html) / len(raw_html) * 100, 2)

        return {
            "url": str(url or "").strip(),
            "title": title,
            "description": meta_description[:240],
            "canonical_url": canonical_link,
            "website_type": str(website_type or "").strip(),
            "fetch_strategy": str(fetch_strategy or "").strip(),
            "request_method": str(request_method or "").strip().lower(),
            "fetched_at": datetime.now().isoformat(),
            "html_length": len(raw_html or ""),
            "pruned_html_length": len(pruned_html or ""),
            "compression_ratio": compression_ratio,
            "signal_counts": signal_counts,
            "heading_outline": headings,
            "detected_regions": detected_regions[:3],
            "main_text_excerpt": str(
                (content_region or {}).get("text_content") or ""
            ).strip()[:240],
            "site_profile": (
                deepcopy(site_profile) if isinstance(site_profile, dict) else None
            ),
            "template_recommendations": (
                deepcopy(template_candidates[:3])
                if isinstance(template_candidates, list)
                else []
            ),
            "matched_template": None,
        }

    def _build_test_snapshot(
        self,
        *,
        sample_data: Optional[List[Dict[str, Any]]] = None,
        detail_samples: Optional[List[Dict[str, Any]]] = None,
        detail_preview_required: bool = False,
        detail_preview_passed: bool = False,
        detail_preview_message: str = "",
        stability_score: Optional[float] = None,
        stability_rating: Optional[str] = None,
    ) -> Dict[str, Any]:
        """构建最近一次规则测试快照。"""
        return {
            "tested_at": datetime.now().isoformat(),
            "sample_count": len(sample_data or []),
            "sample_data": deepcopy(sample_data or []),
            "detail_sample_count": len(detail_samples or []),
            "detail_samples": deepcopy(detail_samples or []),
            "detail_preview_required": bool(detail_preview_required),
            "detail_preview_passed": bool(detail_preview_passed),
            "detail_preview_message": str(detail_preview_message or "").strip(),
            "stability_score": (
                round(float(stability_score), 1)
                if stability_score is not None
                else None
            ),
            "stability_rating": str(stability_rating or "").strip(),
        }

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
            soup = _create_soup(html_content)

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
        custom_summary_prompt: str = "",
        max_items: Optional[int] = None,
        detail_strategy: str = "detail_preferred",
        body_field: str = "",
        skip_detail: bool = False,
        fetch_strategy: str = "requests_first",
        request_method: str = "get",
        request_body: str = "",
        request_headers: Optional[Dict[str, str]] = None,
        cookie_string: str = "",
        recovery_context: Optional[Dict[str, Any]] = None,
    ) -> RuleGenerationResult:
        """
        生成并测试爬虫规则（带 AI 自我反思与自愈机制）

        核心方法：使用 AI 分析网页结构，生成 CSS 选择器规则，并进行沙盒测试。
        如果提取数据为空，会触发 AI 反思机制，自动分析失败原因并重试。

        Args:
            task_id: 任务 ID
            task_name: 任务名称（映射到数据库 department 字段）
            url: 目标网页 URL
            target_fields: 用户想要提取的字段列表
            require_ai_summary: 是否需要对抓取内容进行 AI 摘要
            task_purpose: 任务目的/类别（映射到数据库 category 字段）
            custom_summary_prompt: 🌟 专属 AI 提示词（用于定制摘要输出格式）
            max_items: 🌟 单次抓取最大条目数
            detail_strategy: 🌟 HTML 正文抓取策略（list_only / detail_preferred / hybrid）
            body_field: 🌟 正文来源字段（仅 HTML 爬虫有效）
            skip_detail: 🌟 是否跳过详情页抓取（仅 HTML 爬虫有效）
            request_headers: 🌟 可选，自定义请求头
            cookie_string: 🌟 可选，Cookie 原始字符串

        Returns:
            RuleGenerationResult 包含生成的规则和沙盒测试数据
        """
        # 🌟 AI 自我反思机制：最大重试次数
        MAX_RETRIES = 2
        attempt = 0
        ai_hint = ""  # AI 分析师的修复建议
        recovery_applied = False
        recovery_message = ""

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

                # 3. 构建配置
                graph_config = self._build_scrapegraph_config(llm_config)

                # 🌟 4. 获取并清洗 HTML（DOM 降噪预处理）
                normalized_fetch_strategy = normalize_fetch_strategy(fetch_strategy)
                normalized_request_method = normalize_request_method(
                    request_method
                )
                normalized_request_body = normalize_request_body(request_body)
                normalized_detail_strategy = normalize_detail_strategy(
                    detail_strategy,
                    skip_detail=skip_detail,
                )
                raw_html = self._fetch_html_content(
                    url,
                    fetch_strategy=normalized_fetch_strategy,
                    request_method=normalized_request_method,
                    request_body=normalized_request_body,
                    request_headers=request_headers,
                    cookie_string=cookie_string,
                )
                if not raw_html:
                    return RuleGenerationResult(
                        success=False,
                        error_message=self._last_html_fetch_error
                        or "无法获取网页内容，请检查 URL 是否可访问",
                    )

                pruned_html = self._prune_html(raw_html)
                compression_ratio = (
                    len(pruned_html) / len(raw_html) * 100 if raw_html else 0
                )
                logger.info(
                    f"🔧 HTML 降噪: {len(raw_html)} -> {len(pruned_html)} 字符, 压缩率 {compression_ratio:.1f}%"
                )

                website_type, website_strategy = self._identify_website_type(
                    url, raw_html
                )
                logger.info(f"🌐 网站类型: {website_type}")

                content_region = self._extract_main_content_region(raw_html)
                if content_region and content_region.get('detected_regions'):
                    # 将检测到的区域信息注入策略
                    region_hints = "\n\n【🎯 自动检测到的主内容区域】\n"
                    for i, region in enumerate(content_region['detected_regions'][:3]):
                        region_hints += f"- {region['tag']}"
                        if region['id']:
                            region_hints += f"#{region['id']}"
                        if region['class']:
                            region_hints += f".{region['class'][:50]}"
                        region_hints += f" (包含 {region['link_count']} 个链接)\n"
                    website_strategy['hints'] = website_strategy.get('hints', '') + region_hints
                    logger.info(f"📍 检测到 {len(content_region['detected_regions'])} 个潜在列表区域")

                site_profile = build_site_profile(
                    url,
                    raw_html,
                    target_fields=target_fields,
                )
                template_candidates = match_template_candidates(
                    url,
                    raw_html,
                    target_fields=target_fields,
                    limit=3,
                )
                template_prompt_hint = self._build_template_prompt_hint(
                    template_candidates,
                    target_fields,
                )

                page_summary = self._build_page_summary(
                    url=url,
                    raw_html=raw_html,
                    pruned_html=pruned_html,
                    website_type=website_type,
                    fetch_strategy=normalized_fetch_strategy,
                    request_method=normalized_request_method,
                    content_region=content_region,
                    site_profile=site_profile,
                    template_candidates=template_candidates,
                )

                def build_success_result(
                    *,
                    rule_output: SpiderRuleOutput,
                    sample_rows: List[Dict[str, Any]],
                    detail_rows: List[Dict[str, Any]],
                    detail_required: bool,
                    detail_passed: bool,
                    detail_message: str,
                    stability_score: Optional[float],
                    stability_rating: Optional[str],
                    matched_template: Optional[Dict[str, Any]] = None,
                ) -> RuleGenerationResult:
                    result_page_summary = deepcopy(page_summary)
                    if isinstance(result_page_summary, dict):
                        result_page_summary["matched_template"] = (
                            deepcopy(matched_template)
                            if isinstance(matched_template, dict)
                            else None
                        )
                    return RuleGenerationResult(
                        success=True,
                        rule=rule_output,
                        sample_data=sample_rows,
                        detail_samples=detail_rows,
                        detail_preview_required=detail_required,
                        detail_preview_passed=detail_passed,
                        detail_preview_message=detail_message,
                        recovery_applied=recovery_applied,
                        recovery_message=recovery_message or None,
                        page_summary=result_page_summary,
                        test_snapshot=self._build_test_snapshot(
                            sample_data=sample_rows,
                            detail_samples=detail_rows,
                            detail_preview_required=detail_required,
                            detail_preview_passed=detail_passed,
                            detail_preview_message=detail_message,
                            stability_score=stability_score,
                            stability_rating=stability_rating,
                        ),
                        stability_score=(
                            round(float(stability_score), 1)
                            if stability_score is not None
                            else None
                        ),
                        stability_rating=stability_rating,
                    )

                recovery_prompt_hint = self._build_recovery_prompt_hint(
                    recovery_context
                )
                if recovery_prompt_hint:
                    recovery_applied = True
                    recovery_message = "已基于历史健康快照和失败上下文辅助重生成。"
                recovery_schema = self._build_recovery_schema(
                    recovery_context,
                    target_fields,
                )
                if recovery_schema:
                    try:
                        recovery_sample_data = self._test_rule_with_beautifulsoup(
                            raw_html,
                            recovery_schema,
                            max_items=3,
                        )
                        if self._sample_data_has_signal(
                            recovery_sample_data,
                            target_fields,
                        ):
                            logger.info("♻️ 最近健康快照仍可用，直接复用恢复规则")
                            rule_id = (
                                str(
                                    (
                                        recovery_context or {}
                                    ).get("existing_rule_id")
                                    or f"rule_{uuid.uuid4().hex[:8]}"
                                ).strip()
                                or f"rule_{uuid.uuid4().hex[:8]}"
                            )
                            now = datetime.now().isoformat()
                            rule_output = SpiderRuleOutput(
                                rule_id=rule_id,
                                task_id=task_id,
                                task_name=task_name,
                                task_purpose=task_purpose,
                                url=url,
                                list_container=recovery_schema.list_container,
                                item_selector=recovery_schema.item_selector,
                                field_selectors=recovery_schema.field_selectors,
                                require_ai_summary=require_ai_summary,
                                enable_ai_summary=require_ai_summary,
                                custom_summary_prompt=custom_summary_prompt,
                                summary_prompt=custom_summary_prompt,
                                max_items=max_items,
                                detail_strategy=normalized_detail_strategy,
                                body_field=body_field,
                                skip_detail=skip_detail,
                                fetch_strategy=normalized_fetch_strategy,
                                request_method=normalized_request_method,
                                request_body=normalized_request_body,
                                request_headers=normalize_request_headers(
                                    request_headers
                                ),
                                cookie_string=normalize_cookie_string(
                                    cookie_string
                                ),
                                created_at=now,
                                updated_at=now,
                                enabled=True,
                            )
                            (
                                detail_samples,
                                detail_preview_required,
                                detail_preview_passed,
                                detail_preview_message,
                            ) = self._build_html_detail_preview(
                                rule_output,
                                max_items=2,
                            )
                            recovery_applied = True
                            recovery_message = "已直接复用最近一次健康规则快照。"
                            return build_success_result(
                                rule_output=rule_output,
                                sample_rows=recovery_sample_data,
                                detail_rows=detail_samples,
                                detail_required=detail_preview_required,
                                detail_passed=detail_preview_passed,
                                detail_message=detail_preview_message,
                                stability_score=75.0,
                                stability_rating="🟢 优秀 (健康快照恢复)",
                            )
                        recovery_applied = True
                        recovery_message = "已加载最近一次健康快照作为恢复参考。"
                    except Exception as e:
                        logger.warning("恢复快照测试失败，转入 AI 重生成: %s", e)

                # 🌟 5. 模板库候选优先
                if template_candidates:
                    logger.info(
                        "📚 模板库命中 %s 个候选，最高分 %s",
                        len(template_candidates),
                        template_candidates[0].get("score"),
                    )
                for candidate in template_candidates:
                    try:
                        template_schema = SpiderRuleSchema(
                            list_container=str(candidate.get("list_container") or ""),
                            item_selector=str(candidate.get("item_selector") or ""),
                            field_selectors=(
                                candidate.get("field_selectors")
                                if isinstance(candidate.get("field_selectors"), dict)
                                else {}
                            ),
                        )
                        sample_data = self._test_rule_with_beautifulsoup(
                            raw_html,
                            template_schema,
                            max_items=3,
                        )
                        if not self._sample_data_has_signal(
                            sample_data,
                            target_fields,
                        ):
                            continue

                        logger.info(
                            "✅ 模板候选命中成功：%s（%s）",
                            candidate.get("name"),
                            candidate.get("confidence_label"),
                        )
                        rule_id = f"rule_{uuid.uuid4().hex[:8]}"
                        now = datetime.now().isoformat()
                        rule_output = SpiderRuleOutput(
                            rule_id=rule_id,
                            task_id=task_id,
                            task_name=task_name,
                            task_purpose=task_purpose,
                            url=url,
                            list_container=template_schema.list_container,
                            item_selector=template_schema.item_selector,
                            field_selectors=template_schema.field_selectors,
                            require_ai_summary=require_ai_summary,
                            enable_ai_summary=require_ai_summary,
                            custom_summary_prompt=custom_summary_prompt,
                            summary_prompt=custom_summary_prompt,
                            max_items=max_items,
                            detail_strategy=normalized_detail_strategy,
                            body_field=body_field,
                            skip_detail=skip_detail,
                            fetch_strategy=normalized_fetch_strategy,
                            request_method=normalized_request_method,
                            request_body=normalized_request_body,
                            request_headers=normalize_request_headers(
                                request_headers
                            ),
                            cookie_string=normalize_cookie_string(
                                cookie_string
                            ),
                            detail_body_selector=str(
                                candidate.get("detail_body_selector") or ""
                            ),
                            detail_time_selector=str(
                                candidate.get("detail_time_selector") or ""
                            ),
                            detail_attachment_selector=str(
                                candidate.get("detail_attachment_selector") or ""
                            ),
                            detail_image_selector=str(
                                candidate.get("detail_image_selector") or ""
                            ),
                            created_at=now,
                            updated_at=now,
                            enabled=True,
                        )
                        (
                            detail_samples,
                            detail_preview_required,
                            detail_preview_passed,
                            detail_preview_message,
                        ) = self._build_html_detail_preview(
                            rule_output,
                            max_items=2,
                        )
                        candidate_score = float(candidate.get("score") or 80.0)
                        return build_success_result(
                            rule_output=rule_output,
                            sample_rows=sample_data,
                            detail_rows=detail_samples,
                            detail_required=detail_preview_required,
                            detail_passed=detail_preview_passed,
                            detail_message=detail_preview_message,
                            stability_score=max(candidate_score, 80.0),
                            stability_rating=(
                                f"🟢 优秀 (模板库：{candidate.get('name')})"
                            ),
                            matched_template=candidate,
                        )
                    except Exception as e:
                        logger.warning("模板候选测试失败，回退到 AI 生成: %s", e)

                # 🌟 ========== 自我反思循环 ==========
                last_error = None
                rule_schema = None
                sample_data = []
                raw_html_for_test = raw_html  # 保留原始 HTML 用于沙盒测试

                while attempt <= MAX_RETRIES:
                    attempt += 1
                    logger.info(f"🔄 规则生成尝试 {attempt}/{MAX_RETRIES + 1}")

                    # 🌟 动态构建 Prompt（注入网站类型策略 + AI 分析师的建议）
                    prompt = self._build_enhanced_prompt(target_fields, website_type, website_strategy)
                    if recovery_prompt_hint:
                        prompt = f"{prompt}\n\n{recovery_prompt_hint}"
                    if template_prompt_hint:
                        prompt = f"{prompt}\n\n{template_prompt_hint}"
                    if ai_hint:
                        prompt = f"{prompt}\n\n⚠️ 上次提取失败，AI 分析师的修复建议：{ai_hint}"
                        logger.info(f"💡 注入 AI 修复建议: {ai_hint[:100]}...")

                    logger.info(f"📝 Prompt 长度: {len(prompt)} 字符")
                    logger.info(
                        f"🔧 LLM Config: base_url={llm_config['base_url']}, model={llm_config['model_name']}"
                    )

                    # 5. 创建并执行 SmartScraperGraph（使用清洗后的 HTML）
                    try:
                        smart_scraper = SmartScraperGraph(
                            prompt=prompt,
                            source=pruned_html,  # 🌟 传入清洗后的 HTML
                            config=graph_config,
                            schema=SpiderRuleSchema,
                        )

                        result = smart_scraper.run()
                        logger.info(f"🤖 AI 生成结果: {result}")

                    except Exception as e:
                        error_msg = str(e)
                        logger.error(f"AI 规则生成失败: {error_msg}")
                        last_error = error_msg

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

                        # 🌟 对于 API 错误，不重试，直接返回
                        if attempt >= MAX_RETRIES or "api_key" in str(e).lower():
                            return RuleGenerationResult(
                                success=False, error_message=f"AI 规则生成失败: {error_msg}"
                            )
                        continue

                    # 5. 解析结果
                    if not isinstance(result, dict):
                        if hasattr(result, "model_dump"):
                            result = result.model_dump()
                        else:
                            last_error = f"AI 返回格式异常: {type(result)}"
                            if attempt >= MAX_RETRIES:
                                return RuleGenerationResult(
                                    success=False,
                                    error_message=last_error,
                                )
                            continue

                    # 6. 构建规则对象
                    try:
                        rule_schema = SpiderRuleSchema(**result)
                    except Exception as e:
                        logger.error(f"规则验证失败: {e}, 原始数据: {result}")
                        last_error = f"生成的规则格式不正确: {e}"
                        if attempt >= MAX_RETRIES:
                            return RuleGenerationResult(
                                success=False, error_message=last_error
                            )
                        continue

                    # 7. 沙盒测试（使用原始 HTML 验证选择器在真实环境下的有效性）
                    logger.info(f"🧪 开始沙盒测试: {url}")

                    sample_data = self._test_rule_with_beautifulsoup(
                        raw_html_for_test, rule_schema, max_items=3
                    )
                    logger.info(f"🧪 沙盒测试结果: 提取到 {len(sample_data)} 条数据")

                    # 🌟 ========== 自我反思触发条件 ==========
                    # 如果提取到有效数据，跳出循环
                    if sample_data and len(sample_data) > 0:
                        # 检查数据质量：至少有一个字段非空
                        has_valid_data = any(
                            any(v and v.strip() for v in item.values())
                            for item in sample_data
                        )
                        if has_valid_data:
                            logger.info(f"✅ 提取成功，跳出反思循环")
                            break

                    # 🌟 ========== AI 分析师反思 ==========
                    if attempt <= MAX_RETRIES:
                        logger.warning(f"⚠️ 第 {attempt} 次提取失败，触发 AI 自我反思...")

                        # 调用 AI 分析师分析失败原因
                        ai_hint = self._ai_reflect_on_failure(
                            url=url,
                            rule_schema=rule_schema,
                            sample_data=sample_data,
                            target_fields=target_fields,
                        )
                        logger.info(f"🧠 AI 分析师建议: {ai_hint}")
                    else:
                        logger.warning(f"⚠️ 已达到最大重试次数 {MAX_RETRIES}，停止反思")

                # 🌟 ========== 最终结果处理 ==========
                # 检查是否有有效规则
                if rule_schema is None:
                    # 🌟 ========== 多模型回退机制 ==========
                    secondary_configs = self._get_secondary_llm_configs()
                    if secondary_configs:
                        logger.info(f"🔄 主模型失败，尝试 {len(secondary_configs)} 个备选模型...")

                        for i, sec_config in enumerate(secondary_configs):
                            logger.info(f"🔄 尝试备选模型 {i+1}/{len(secondary_configs)}: {sec_config['model_name']}")

                            try:
                                sec_graph_config = self._build_scrapegraph_config(sec_config)
                                prompt = self._build_enhanced_prompt(target_fields, website_type, website_strategy)
                                if template_prompt_hint:
                                    prompt = f"{prompt}\n\n{template_prompt_hint}"

                                sec_scraper = SmartScraperGraph(
                                    prompt=prompt,
                                    source=pruned_html,
                                    config=sec_graph_config,
                                    schema=SpiderRuleSchema,
                                )

                                sec_result = sec_scraper.run()

                                if isinstance(sec_result, dict):
                                    sec_schema = SpiderRuleSchema(**sec_result)
                                    sec_sample = self._test_rule_with_beautifulsoup(
                                        raw_html_for_test, sec_schema, max_items=3
                                    )

                                    if sec_sample and len(sec_sample) > 0:
                                        has_valid = any(
                                            any(v and v.strip() for v in item.values())
                                            for item in sec_sample
                                        )
                                        if has_valid:
                                            logger.info(f"✅ 备选模型 {sec_config['model_name']} 成功！")
                                            rule_schema = sec_schema
                                            sample_data = sec_sample
                                            break

                            except Exception as sec_e:
                                logger.warning(f"备选模型 {sec_config['model_name']} 失败: {sec_e}")
                                continue

                    # 如果所有模型都失败
                    if rule_schema is None:
                        return RuleGenerationResult(
                            success=False,
                            error_message=last_error or "规则生成失败，请检查目标网页是否可访问",
                        )

                # 生成完整规则
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
                    enable_ai_summary=require_ai_summary,
                    custom_summary_prompt=custom_summary_prompt,
                    summary_prompt=custom_summary_prompt,
                    max_items=max_items,
                    detail_strategy=normalized_detail_strategy,
                    body_field=body_field,
                    skip_detail=skip_detail,
                    fetch_strategy=normalized_fetch_strategy,
                    request_method=normalized_request_method,
                    request_body=normalized_request_body,
                    request_headers=normalize_request_headers(request_headers),
                    cookie_string=normalize_cookie_string(cookie_string),
                    created_at=now,
                    updated_at=now,
                    enabled=True,
                )

                # 如果最终仍然没有数据，添加警告
                if not sample_data or len(sample_data) == 0:
                    logger.warning("⚠️ 规则生成成功，但沙盒测试未提取到数据，可能需要人工验证")

                # 🌟 计算选择器稳定性评分
                avg_score = 0.0
                scores = []
                for field_name, field_selector in rule_schema.field_selectors.items():
                    field_score = score_selector_stability(field_selector)
                    scores.append((field_name, field_score))
                    logger.info(f"📊 字段 '{field_name}' 稳定性: {field_score:.1f}分 - {get_stability_rating(field_score)}")

                # 计算列表容器和列表项的评分
                container_score = score_selector_stability(rule_schema.list_container)
                item_score = score_selector_stability(rule_schema.item_selector)

                # 综合评分：字段平均分 * 0.6 + 容器分 * 0.2 + 列表项分 * 0.2
                if scores:
                    field_avg = sum(s[1] for s in scores) / len(scores)
                    avg_score = field_avg * 0.6 + container_score * 0.2 + item_score * 0.2

                stability_rating = get_stability_rating(avg_score)
                logger.info(f"🎯 综合稳定性评分: {avg_score:.1f}分 - {stability_rating}")

                (
                    detail_samples,
                    detail_preview_required,
                    detail_preview_passed,
                    detail_preview_message,
                ) = self._build_html_detail_preview(
                    rule_output,
                    max_items=2,
                )

                return build_success_result(
                    rule_output=rule_output,
                    sample_rows=sample_data,
                    detail_rows=detail_samples,
                    detail_required=detail_preview_required,
                    detail_passed=detail_preview_passed,
                    detail_message=detail_preview_message,
                    stability_score=avg_score,
                    stability_rating=stability_rating,
                )

            except Exception as e:
                logger.error(f"规则生成过程发生异常: {e}", exc_info=True)
                return RuleGenerationResult(
                    success=False, error_message=f"规则生成过程发生异常: {str(e)}"
                )

    def _ai_reflect_on_failure(
        self,
        url: str,
        rule_schema: "SpiderRuleSchema",
        sample_data: List[Dict[str, str]],
        target_fields: List[str],
    ) -> str:
        """
        AI 分析师：反思提取失败的原因并给出修复建议

        Args:
            url: 目标网页 URL
            rule_schema: 当前生成的规则
            sample_data: 沙盒测试提取的数据（可能为空）
            target_fields: 用户想要提取的字段列表

        Returns:
            一句话修复建议
        """
        try:
            llm_config = self._get_llm_config()
            if not llm_config["api_key"]:
                return "无法分析：未配置 API Key"

            # 构建反思 Prompt
            reflection_prompt = f"""你是一个网页爬虫专家。刚才的爬虫规则提取数据失败，请分析原因并给出修复建议。

【目标网站】
{url}

【当前规则】
- 列表容器: {rule_schema.list_container}
- 列表项选择器: {rule_schema.item_selector}
- 字段选择器: {json.dumps(rule_schema.field_selectors, ensure_ascii=False)}

【目标字段】
{', '.join(target_fields)}

【提取结果】
{f'提取到 {len(sample_data)} 条数据，但可能字段值为空' if sample_data else '未提取到任何数据'}

请推测失败原因（如：使用了动态生成的 class、选择器嵌套层级错误、列表容器选择器不正确等），并给出**一条简洁的修复建议**。

要求：
1. 只输出一句话建议，不要啰嗦
2. 建议要具体，比如"改用 article 标签作为列表项选择器"而不是"检查选择器"
3. 如果怀疑是动态 class 问题，建议改用语义化标签或属性选择器"""

            # 调用 LLM
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(
                model=llm_config["model_name"],
                api_key=llm_config["api_key"],
                base_url=llm_config["base_url"],
                temperature=0.3,
            )

            response = llm.invoke(reflection_prompt)
            hint = response.content.strip() if hasattr(response, 'content') else str(response)

            # 限制建议长度
            if len(hint) > 200:
                hint = hint[:200] + "..."

            return hint

        except Exception as e:
            logger.error(f"AI 反思失败: {e}")
            return f"自动分析失败，建议检查网页结构是否支持静态抓取"

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
            html_content = self._fetch_html_content(
                rule.url,
                fetch_strategy=str(rule.fetch_strategy or "requests_first"),
                request_method=normalize_request_method(
                    getattr(rule, "request_method", "get")
                ),
                request_body=normalize_request_body(
                    getattr(rule, "request_body", "")
                ),
                request_headers=normalize_request_headers(
                    getattr(rule, "request_headers", {})
                ),
                cookie_string=normalize_cookie_string(
                    getattr(rule, "cookie_string", "")
                ),
            )
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

    def _detail_preview_has_primary_content(
        self, detail: Optional[Dict[str, Any]]
    ) -> bool:
        if not isinstance(detail, dict):
            return False
        return any(
            [
                str(detail.get("body_text") or "").strip(),
                str(detail.get("raw_markdown") or "").strip(),
                str(detail.get("body_html") or "").strip(),
                bool(detail.get("content_blocks")),
            ]
        )

    def _merge_preview_asset_dicts(
        self,
        primary: Optional[List[Dict[str, Any]]],
        fallback: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen_keys: set[str] = set()
        for bucket in (primary or [], fallback or []):
            if not isinstance(bucket, list):
                continue
            for item in bucket:
                if not isinstance(item, dict):
                    continue
                item_url = str(item.get("url") or "").strip()
                dedupe_key = item_url or json.dumps(
                    item,
                    sort_keys=True,
                    ensure_ascii=False,
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                merged.append(dict(item))
        return merged

    def _merge_preview_image_urls(
        self,
        primary: Optional[List[str]],
        fallback: Optional[List[str]],
        image_assets: Optional[List[Dict[str, Any]]] = None,
    ) -> List[str]:
        merged: List[str] = []
        seen: set[str] = set()

        for bucket in (primary or [], fallback or []):
            if not isinstance(bucket, list):
                continue
            for value in bucket:
                clean_value = str(value or "").strip()
                if not clean_value or clean_value in seen:
                    continue
                seen.add(clean_value)
                merged.append(clean_value)

        if isinstance(image_assets, list):
            for item in image_assets:
                if not isinstance(item, dict):
                    continue
                clean_value = str(item.get("url") or "").strip()
                if not clean_value or clean_value in seen:
                    continue
                seen.add(clean_value)
                merged.append(clean_value)

        return merged

    def _build_list_fallback_detail_preview(
        self, article: Dict[str, Any]
    ) -> Dict[str, Any]:
        image_assets = (
            article.get("image_assets")
            if isinstance(article.get("image_assets"), list)
            else []
        )
        images = self._merge_preview_image_urls(
            article.get("images") if isinstance(article.get("images"), list) else [],
            [
                item.get("url")
                for item in image_assets
                if isinstance(item, dict) and item.get("url")
            ],
            image_assets,
        )
        raw_markdown = str(article.get("raw_markdown") or "").strip()
        body_text = str(article.get("body_text") or "").strip()
        return {
            "title": str(article.get("title") or "").strip(),
            "url": str(article.get("url") or "").strip(),
            "body_text": body_text,
            "raw_markdown": raw_markdown or body_text,
            "body_html": str(article.get("body_html") or "").strip(),
            "attachments": (
                article.get("attachments")
                if isinstance(article.get("attachments"), list)
                else []
            ),
            "image_assets": image_assets,
            "images": images,
            "content_blocks": (
                article.get("content_blocks")
                if isinstance(article.get("content_blocks"), list)
                else []
            ),
            "exact_time": str(article.get("date") or "").strip(),
        }

    def _merge_detail_preview_with_list_fallback(
        self,
        detail: Optional[Dict[str, Any]],
        article: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(detail or {})
        fallback = self._build_list_fallback_detail_preview(article)

        if not self._detail_preview_has_primary_content(merged) and self._detail_preview_has_primary_content(fallback):
            merged["body_text"] = fallback.get("body_text", "")
            merged["body_html"] = fallback.get("body_html", "")
            merged["raw_markdown"] = fallback.get("raw_markdown", "")
            if fallback.get("content_blocks"):
                merged["content_blocks"] = fallback.get("content_blocks", [])
            if fallback.get("image_assets"):
                merged["image_assets"] = fallback.get("image_assets", [])
            if fallback.get("images"):
                merged["images"] = fallback.get("images", [])

        merged["title"] = str(merged.get("title") or article.get("title") or "").strip()
        merged["url"] = str(merged.get("url") or article.get("url") or "").strip()
        merged["exact_time"] = str(
            merged.get("exact_time") or article.get("date") or ""
        ).strip()
        merged["attachments"] = self._merge_preview_asset_dicts(
            merged.get("attachments"),
            fallback.get("attachments"),
        )
        merged["image_assets"] = self._merge_preview_asset_dicts(
            merged.get("image_assets"),
            fallback.get("image_assets"),
        )
        merged["images"] = self._merge_preview_image_urls(
            merged.get("images"),
            fallback.get("images"),
            merged.get("image_assets"),
        )
        if not merged.get("content_blocks") and fallback.get("content_blocks"):
            merged["content_blocks"] = fallback.get("content_blocks", [])
        return merged

    def _build_detail_preview_sample(
        self,
        spider: Any,
        article: Dict[str, Any],
        detail_strategy: str,
    ) -> Dict[str, Any]:
        list_fallback = self._build_list_fallback_detail_preview(article)
        list_body_ok = self._detail_preview_has_primary_content(list_fallback)

        if detail_strategy == "list_only":
            preview_payload = list_fallback
            return {
                "title": preview_payload.get("title", ""),
                "url": preview_payload.get("url", ""),
                "list_date": str(article.get("date") or "").strip(),
                "status": "passed" if list_body_ok else "failed",
                "status_label": "列表正文可用" if list_body_ok else "列表正文为空",
                "status_reason": (
                    "当前策略为列表页足够，无需详情抓取。"
                    if list_body_ok
                    else "列表页正文为空，请补充正文来源字段或切换详情策略。"
                ),
                "body_source": "list_only",
                "body_ok": list_body_ok,
                "exact_time": str(preview_payload.get("exact_time") or "").strip(),
                "attachment_count": len(preview_payload.get("attachments") or []),
                "image_count": len(preview_payload.get("images") or []),
                "attachments": (preview_payload.get("attachments") or [])[:3],
                "images": (preview_payload.get("images") or [])[:6],
                "raw_markdown": str(
                    preview_payload.get("raw_markdown")
                    or preview_payload.get("body_text")
                    or ""
                ).strip(),
            }

        detail_error = ""
        detail_payload: Optional[Dict[str, Any]] = None
        try:
            detail_payload = spider.fetch_detail(str(article.get("url") or "").strip())
        except Exception as e:
            detail_error = str(e)

        detail_body_ok = self._detail_preview_has_primary_content(detail_payload)
        preview_payload = self._merge_detail_preview_with_list_fallback(
            detail_payload,
            article,
        )

        if detail_body_ok:
            status = "passed"
            status_label = "详情正文命中"
            status_reason = "详情页正文已成功提取。"
            body_source = "detail"
        elif list_body_ok:
            status = "warning"
            status_label = "仅回退列表正文"
            status_reason = (
                detail_error
                or "详情页正文未命中，当前仅能回退到列表页正文。"
            )
            body_source = "list_fallback"
        else:
            status = "failed"
            status_label = "详情正文缺失"
            status_reason = detail_error or "详情页与列表页均未提取到可用正文。"
            body_source = "missing"

        return {
            "title": str(preview_payload.get("title") or "").strip(),
            "url": str(preview_payload.get("url") or "").strip(),
            "list_date": str(article.get("date") or "").strip(),
            "status": status,
            "status_label": status_label,
            "status_reason": status_reason,
            "body_source": body_source,
            "body_ok": detail_body_ok,
            "exact_time": str(preview_payload.get("exact_time") or "").strip(),
            "attachment_count": len(preview_payload.get("attachments") or []),
            "image_count": len(preview_payload.get("images") or []),
            "attachments": (preview_payload.get("attachments") or [])[:3],
            "images": (preview_payload.get("images") or [])[:6],
            "raw_markdown": str(
                preview_payload.get("raw_markdown")
                or preview_payload.get("body_text")
                or ""
            ).strip(),
        }

    def _build_html_detail_preview(
        self,
        rule: SpiderRuleOutput,
        max_items: int = 2,
    ) -> tuple[List[Dict[str, Any]], bool, bool, str]:
        detail_strategy = normalize_detail_strategy(
            getattr(rule, "detail_strategy", ""),
            skip_detail=bool(getattr(rule, "skip_detail", False)),
        )
        detail_preview_required = detail_strategy != "list_only"

        if str(getattr(rule, "source_type", "html") or "html").lower() != "html":
            return [], False, True, ""

        try:
            from src.spiders.dynamic_spider import create_dynamic_spider_from_rule

            spider = create_dynamic_spider_from_rule(rule.model_dump())
            if not spider:
                return (
                    [],
                    detail_preview_required,
                    False,
                    "无法根据当前规则创建详情预览爬虫。",
                )

            articles = spider.fetch_list(limit=max(max_items, 1))
        except Exception as e:
            return (
                [],
                detail_preview_required,
                False,
                f"详情预览初始化失败：{e}",
            )

        if not articles:
            message = "未提取到列表样本，无法生成详情预览。"
            return [], detail_preview_required, False, message

        detail_samples = [
            self._build_detail_preview_sample(spider, article, detail_strategy)
            for article in list(articles)[: max(max_items, 1)]
        ]

        if not detail_preview_required:
            return (
                detail_samples,
                False,
                True,
                "当前策略为列表页足够，无需详情预览。",
            )

        passed_count = sum(
            1 for item in detail_samples if str(item.get("status") or "") == "passed"
        )
        warning_count = sum(
            1 for item in detail_samples if str(item.get("status") or "") == "warning"
        )
        failed_count = sum(
            1 for item in detail_samples if str(item.get("status") or "") == "failed"
        )
        passed = bool(detail_samples) and warning_count == 0 and failed_count == 0
        if passed:
            message = f"详情预览通过，{passed_count} 条样本正文已成功命中详情页。"
        else:
            message = (
                f"详情预览未通过：{warning_count + failed_count} 条样本未稳定命中详情正文。"
                "请调整详情规则，或切换为“列表页足够”。"
            )
        return detail_samples, True, passed, message

    def build_rule_preview_bundle(
        self,
        rule: SpiderRuleOutput,
        max_items: int = 3,
    ) -> Dict[str, Any]:
        sample_data = self.test_existing_rule(rule, max_items=max_items)
        detail_samples, detail_preview_required, detail_preview_passed, detail_preview_message = (
            self._build_html_detail_preview(rule, max_items=min(max_items, 2))
        )
        page_summary = None
        try:
            raw_html = self._fetch_html_content(
                rule.url,
                fetch_strategy=str(rule.fetch_strategy or "requests_first"),
                request_method=normalize_request_method(
                    getattr(rule, "request_method", "get")
                ),
                request_body=normalize_request_body(
                    getattr(rule, "request_body", "")
                ),
                request_headers=normalize_request_headers(
                    getattr(rule, "request_headers", {})
                ),
                cookie_string=normalize_cookie_string(
                    getattr(rule, "cookie_string", "")
                ),
            )
            if raw_html:
                pruned_html = self._prune_html(raw_html)
                website_type, _ = self._identify_website_type(rule.url, raw_html)
                content_region = self._extract_main_content_region(raw_html)
                site_profile = build_site_profile(
                    rule.url,
                    raw_html,
                    target_fields=list((rule.field_selectors or {}).keys()),
                )
                template_candidates = match_template_candidates(
                    rule.url,
                    raw_html,
                    target_fields=list((rule.field_selectors or {}).keys()),
                    limit=3,
                )
                page_summary = self._build_page_summary(
                    url=rule.url,
                    raw_html=raw_html,
                    pruned_html=pruned_html,
                    website_type=website_type,
                    fetch_strategy=str(rule.fetch_strategy or "requests_first"),
                    request_method=normalize_request_method(
                        getattr(rule, "request_method", "get")
                    ),
                    content_region=content_region,
                    site_profile=site_profile,
                    template_candidates=template_candidates,
                )
        except Exception as e:
            logger.debug("构建规则页面摘要失败: %s", e)

        return {
            "sample_data": sample_data,
            "detail_samples": detail_samples,
            "detail_preview_required": detail_preview_required,
            "detail_preview_passed": detail_preview_passed,
            "detail_preview_message": detail_preview_message,
            "page_summary": page_summary,
            "test_snapshot": self._build_test_snapshot(
                sample_data=sample_data,
                detail_samples=detail_samples,
                detail_preview_required=detail_preview_required,
                detail_preview_passed=detail_preview_passed,
                detail_preview_message=detail_preview_message,
            ),
        }
