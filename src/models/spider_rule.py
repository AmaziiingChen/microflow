"""
动态爬虫规则数据模型 - 定义 ScrapeGraphAI 输出的结构化 Schema

用于约束 AI 生成的 CSS 选择器规则，确保输出格式统一且可验证。
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class SpiderRuleSchema(BaseModel):
    """
    动态爬虫规则 Schema

    用于约束 ScrapeGraphAI 输出的 CSS 选择器规则结构。
    包含列表容器、单项选择器和字段映射。

    Attributes:
        list_container: 包含所有列表项的父级 CSS 选择器
        item_selector: 单个列表项的 CSS 选择器（相对于 list_container）
        field_selectors: 字段选择器映射，键为用户定义的字段名，值为相对 CSS 选择器
    """

    list_container: str = Field(
        ...,
        description="包含所有列表项的父级 CSS 选择器，例如 'ul.news-list' 或 'div.article-list'"
    )

    item_selector: str = Field(
        ...,
        description="单个列表项的 CSS 选择器（相对于 list_container），例如 'li' 或 'div.item'"
    )

    field_selectors: Dict[str, str] = Field(
        ...,
        description="""字段选择器映射。键为字段名，值为合法的 BeautifulSoup4 CSS 选择器。

【格式铁律】
1. 必须返回合法的相对 CSS 选择器，绝对禁止返回 JS 属性名（如 'textContent', 'innerText', 'href', 'src'）。

2. 提取文本：在选择器末尾加 ::text
   - 从子节点提取：'span.date::text' 或 'a.title::text'
   - 从当前 item 节点本身提取：'::text'（前面无标签）

3. 提取属性：使用 ::attr(属性名)
   - 提取链接：'a::attr(href)' 或 '::attr(href)'（如果 item 本身就是 a 标签）
   - 提取图片：'img::attr(src)'

【示例】
✅ 正确：{'title': 'a.title::text', 'url': 'a::attr(href)', 'date': 'span::text'}
✅ 正确：{'title': '::text', 'url': '::attr(href)'}  # 从 item 本身提取
❌ 错误：{'title': 'textContent', 'url': 'href'}      # JS 属性名，非法！
❌ 错误：{'title': 'innerText', 'url': 'a.href'}      # JS 属性名，非法！"""
    )


class SpiderRuleOutput(BaseModel):
    """
    完整的爬虫规则输出（包含元数据）

    用于持久化存储和 API 返回。
    """

    # 规则 ID（用于唯一标识）
    rule_id: str = Field(
        ...,
        description="规则的唯一标识符"
    )

    # 任务信息
    task_id: str = Field(
        ...,
        description="关联的任务 ID"
    )
    task_name: str = Field(
        ...,
        description="任务名称（映射到数据库的 department 字段）"
    )
    task_purpose: Optional[str] = Field(
        default="",
        description="任务目的/类别（映射到数据库的 category 字段）"
    )

    # 🌟 数据源类型（HTML 或 RSS）
    source_type: str = Field(
        default="html",
        description="数据源类型：html（网页 CSS 提取）或 rss（RSS/Atom 订阅）"
    )

    # 目标 URL
    url: str = Field(
        ...,
        description="目标网页 URL 或 RSS 订阅地址"
    )

    # CSS 选择器规则（仅 source_type=html 时需要）
    list_container: Optional[str] = Field(
        default="",
        description="包含所有列表项的父级 CSS 选择器（RSS 源无需填写）"
    )
    item_selector: Optional[str] = Field(
        default="",
        description="单个列表项的 CSS 选择器（RSS 源无需填写）"
    )
    field_selectors: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="字段选择器映射（RSS 源无需填写）"
    )

    # AI 摘要开关
    require_ai_summary: bool = Field(
        default=False,
        description="是否需要对抓取内容进行 AI 摘要"
    )

    # 🌟 跳过详情页抓取（仅 HTML 爬虫有效）
    skip_detail: bool = Field(
        default=False,
        description="是否跳过详情页抓取（仅 HTML 爬虫有效）。当列表页已包含所需全部字段时，可设为 True 以提升抓取速度"
    )

    # 🌟 正文来源字段（仅 HTML 爬虫有效）
    body_field: Optional[str] = Field(
        default=None,
        description="指定作为正文的字段名（仅 HTML 爬虫有效）。不设则使用所有字段拼接的文本。适用于列表页已包含摘要/描述的场景"
    )

    # 🌟 专属 AI 提示词
    custom_summary_prompt: Optional[str] = Field(
        default="",
        description="该数据源专属的 AI 摘要提示词，用于定制提取和 Markdown 排版逻辑"
    )

    # 🌟 单次抓取最大条目数
    max_items: Optional[int] = Field(
        default=None,
        description="单次抓取的最大条目数，不设则使用默认值（HTML:10, RSS:50）"
    )

    # 时间戳
    created_at: Optional[str] = Field(
        default=None,
        description="规则创建时间（ISO 格式）"
    )
    updated_at: Optional[str] = Field(
        default=None,
        description="规则最后更新时间（ISO 格式）"
    )

    # 启用状态
    enabled: bool = Field(
        default=True,
        description="规则是否启用"
    )


class RuleGenerationResult(BaseModel):
    """
    规则生成结果（包含沙盒测试数据）

    用于 API 返回给前端预览。
    """

    # 生成状态
    success: bool = Field(
        ...,
        description="规则生成是否成功"
    )

    # 错误信息（如果失败）
    error_message: Optional[str] = Field(
        default=None,
        description="错误信息"
    )

    # 生成的规则
    rule: Optional[SpiderRuleOutput] = Field(
        default=None,
        description="生成的完整规则"
    )

    # 沙盒测试数据（前 3 条）
    sample_data: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="沙盒测试提取的前 3 条数据样本"
    )

    # 🌟 选择器稳定性评分
    stability_score: Optional[float] = Field(
        default=None,
        description="选择器稳定性评分 (0-100)"
    )

    stability_rating: Optional[str] = Field(
        default=None,
        description="稳定性评级（优秀/良好/一般/风险）"
    )


# 创建 __init__.py 导出模型
__all__ = [
    'SpiderRuleSchema',
    'SpiderRuleOutput',
    'RuleGenerationResult'
]
