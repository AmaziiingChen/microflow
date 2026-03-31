"""
动态爬虫规则数据模型 - 定义 ScrapeGraphAI 输出的结构化 Schema

用于约束 AI 生成的 CSS 选择器规则，确保输出格式统一且可验证。
"""

from typing import Any, Dict, List, Optional
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

    fetch_strategy: Optional[str] = Field(
        default="requests_first",
        description="HTML 抓取策略：requests_first、browser_first、requests_only、browser_only"
    )

    request_method: Optional[str] = Field(
        default="get",
        description="HTML 列表页请求方法：get 或 post"
    )

    request_body: Optional[str] = Field(
        default="",
        description="HTML 列表页的原始请求体，仅 request_method=post 时生效"
    )

    request_headers: Optional[Dict[str, str]] = Field(
        default_factory=dict,
        description="HTML 抓取时附带的自定义请求头，键值对形式"
    )

    cookie_string: Optional[str] = Field(
        default="",
        description="HTML 抓取时附带的 Cookie 原始字符串，可用于登录态或特定会话"
    )

    pagination_mode: Optional[str] = Field(
        default="single",
        description="HTML 列表分页模式：single（单页）、next_link（下一页链接）、url_template（页码模板）、load_more（加载更多）"
    )

    next_page_selector: Optional[str] = Field(
        default="",
        description="下一页链接选择器（仅 pagination_mode=next_link 时使用，支持 ::attr(href)）"
    )

    page_url_template: Optional[str] = Field(
        default="",
        description="分页 URL 模板（仅 pagination_mode=url_template 时使用），例如 /list_{page}.htm 或 https://example.com/list?page={page}"
    )

    page_start: Optional[int] = Field(
        default=2,
        description="页码模板的起始页号，默认从第 2 页开始"
    )

    max_pages: Optional[int] = Field(
        default=None,
        description="分页抓取的最大页数（包含目标 URL 首屏），留空时分页模式默认最多抓取 3 页"
    )

    incremental_max_pages: Optional[int] = Field(
        default=1,
        description="持续追踪时的最大翻页数（包含首屏），用于限制常规轮询回溯深度"
    )

    load_more_selector: Optional[str] = Field(
        default="",
        description="加载更多按钮选择器（仅 pagination_mode=load_more 时使用，支持常见 href/data-url/data-next/hx-get）"
    )

    # AI 摘要开关
    require_ai_summary: bool = Field(
        default=False,
        description="兼容旧字段：是否启用 AI 摘要/增强"
    )

    enable_ai_formatting: bool = Field(
        default=False,
        description="是否启用 AI 排版增强（主要用于 RSS）"
    )

    enable_ai_summary: bool = Field(
        default=False,
        description="是否启用 AI 摘要与标签"
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

    detail_strategy: Optional[str] = Field(
        default="detail_preferred",
        description="HTML 正文抓取策略：list_only（仅列表页）、detail_preferred（详情页优先）、hybrid（详情与列表混合）"
    )

    detail_body_selector: Optional[str] = Field(
        default="",
        description="详情页正文容器选择器（仅 HTML 爬虫有效）。填写后优先按该选择器提取正文"
    )

    detail_time_selector: Optional[str] = Field(
        default="",
        description="详情页时间选择器（仅 HTML 爬虫有效）。支持 ::text / ::attr() 语法"
    )

    detail_attachment_selector: Optional[str] = Field(
        default="",
        description="详情页附件区域选择器（仅 HTML 爬虫有效）。可填写附件容器或附件链接选择器"
    )

    detail_image_selector: Optional[str] = Field(
        default="",
        description="详情页图片区选择器（仅 HTML 爬虫有效）。可填写图片容器或 img 选择器"
    )

    # 🌟 专属 AI 提示词
    custom_summary_prompt: Optional[str] = Field(
        default="",
        description="兼容旧字段：该数据源专属的 AI 提示词"
    )

    formatting_prompt: Optional[str] = Field(
        default="",
        description="AI 排版增强专属提示词（主要用于 RSS）"
    )

    summary_prompt: Optional[str] = Field(
        default="",
        description="AI 摘要与标签专属提示词"
    )

    source_profile: Optional[str] = Field(
        default="",
        description="RSS 源级策略档位，如 news / longform / visual"
    )

    source_profile_source: Optional[str] = Field(
        default="",
        description="RSS 源级策略来源，如 manual / inferred"
    )

    source_template_id: Optional[str] = Field(
        default="",
        description="RSS 策略模板 ID"
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

    detail_samples: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="详情页预览样本（展示正文、时间、附件、图片命中情况）"
    )

    detail_preview_required: bool = Field(
        default=False,
        description="当前规则是否要求通过详情预览"
    )

    detail_preview_passed: bool = Field(
        default=False,
        description="当前规则的详情预览是否通过"
    )

    detail_preview_message: Optional[str] = Field(
        default=None,
        description="详情预览结果提示"
    )

    recovery_applied: bool = Field(
        default=False,
        description="本次生成是否使用了历史健康快照/恢复上下文"
    )

    recovery_message: Optional[str] = Field(
        default=None,
        description="规则恢复提示信息"
    )

    page_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="页面摘要信息（标题、结构信号、主区域候选等）"
    )

    test_snapshot: Optional[Dict[str, Any]] = Field(
        default=None,
        description="最近一次测试快照（样本、详情预览、评分等）"
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
