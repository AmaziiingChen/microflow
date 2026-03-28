"""快照生成服务 - 使用 Playwright 同步 API 将公文摘要渲染为图片"""

import json
import logging
import os
import sys
import tempfile
import time
from typing import Dict, Any, List, Optional
from datetime import datetime
import mistune
from src.utils.text_cleaner import strip_emoji

logger = logging.getLogger(__name__)

# 模板文件路径
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "snapshot_template.html")

# 默认星星图标 SVG（当找不到 AI 品牌图标时使用）
DEFAULT_AI_ICON_SVG = """<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
    <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="#FFB800"/>
</svg>"""


def _get_ai_icon_svg(model_name: str) -> str:
    """
    获取 AI 品牌 SVG 图标 - 复用 api.py 的核心逻辑

    Args:
        model_name: 模型名称，如 'deepseek-chat', 'gpt-4', 'claude-3'

    Returns:
        SVG 字符串
    """
    if not model_name:
        return DEFAULT_AI_ICON_SVG

    # 1. 提取品牌关键词
    brand_key = model_name.lower().split("-")[0]

    # 2. 别名映射
    brand_map = {"gpt": "openai", "claude": "anthropic", "gemini": "google"}
    slug = brand_map.get(brand_key, brand_key)

    # 3. 🚀 打包兼容性路径处理
    if getattr(sys, "frozen", False):
        base_path = getattr(sys, "_MEIPASS", "")
    else:
        base_path = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    # 确保指向 /data/icons 文件夹
    icons_dir = os.path.join(base_path, "data", "icons")

    # 4. 检索逻辑：优先精准匹配，次选模糊匹配
    svg_content = None

    try:
        if os.path.exists(icons_dir):
            files = os.listdir(icons_dir)

            # 策略 A: 精准匹配
            target_exact = [f"{slug}.svg", f"icon_{slug}.svg", f"{slug}-color.svg"]
            for f in target_exact:
                if f in files:
                    with open(os.path.join(icons_dir, f), "r", encoding="utf-8") as fs:
                        svg_content = fs.read()
                    break

            # 策略 B: 模糊匹配
            if not svg_content:
                for f in files:
                    if f.endswith(".svg") and slug in f.lower():
                        with open(
                            os.path.join(icons_dir, f), "r", encoding="utf-8"
                        ) as fs:
                            svg_content = fs.read()
                        break

        # 5. 尺寸加固：确保截图必现
        if svg_content and "width=" not in svg_content:
            svg_content = svg_content.replace("<svg", '<svg width="100%" height="100%"')

        return svg_content or DEFAULT_AI_ICON_SVG

    except Exception as e:
        logger.warning(f"获取 AI 图标失败: {e}")
        return DEFAULT_AI_ICON_SVG


def _get_ai_brand_title(model_name: str) -> str:
    """
    生成 AI 品牌标题，如 'DEEPSEEK INTELLIGENCE'

    Args:
        model_name: 模型名称

    Returns:
        品牌标题字符串
    """
    if not model_name:
        return "AI INTELLIGENCE"

    # 提取第一个连字符前的部分并大写
    brand = model_name.split("-")[0].upper()

    # 特殊处理：如果品牌名比较短或没有连字符，直接大写全名
    if len(brand) < 2:
        brand = model_name.upper()

    return f"{brand} INTELLIGENCE"


def _get_attachment_icon_type(filename: str) -> str:
    """
    获取附件类型标识 - Python 版本的 getAttachmentIcon

    Args:
        filename: 文件名

    Returns:
        类型标识: "pdf", "doc", "ppt", "xls", "pic", "zip", "txt", "file"
    """
    if not filename or "." not in filename:
        return "file"

    ext = filename.split(".")[-1].lower()

    if ext == "pdf":
        return "pdf"
    if ext in ["doc", "docx", "wps"]:
        return "doc"
    if ext in ["ppt", "pptx"]:
        return "ppt"
    if ext in ["xls", "xlsx", "csv"]:
        return "xls"
    if ext in ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "ico", "tiff", "tif"]:
        return "pic"
    if ext in ["zip", "7z", "rar", "tar", "gz", "bz2", "xz"]:
        return "zip"
    if ext in ["txt", "text", "log", "md", "markdown"]:
        return "txt"

    return "file"


def _get_attachment_icon_svg(icon_type: str) -> str:
    """
    返回附件图标的内联 SVG（从 index.html 复刻）

    Args:
        icon_type: 图标类型

    Returns:
        SVG 字符串
    """
    # 从 index.html 复制的正确 SVG 图标
    icons = {
        "pdf": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#FFEEEF"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#FFB8BE"></path>
            <path d="M302.592 792.576c-10.24 0-19.456-4.096-23.04-5.632-17.92-7.68-28.672-23.552-28.672-43.008 0-12.288 0-50.688 140.8-111.616 31.744-57.856 56.32-115.712 73.216-172.032-15.872-33.792-54.272-118.784-27.136-164.352 7.68-15.872 26.624-25.6 48.128-25.6 15.872 0 30.72 7.68 40.448 19.968 20.48 28.672 17.92 86.016-6.144 161.28 20.48 37.888 48.64 74.752 83.968 110.08l4.096-0.512c28.672-4.608 55.296-9.216 84.48-9.216 34.304 1.536 57.856 9.728 70.656 25.088 9.728 11.776 10.24 24.576 9.216 31.232 1.536 15.36-1.536 27.648-9.728 36.352-15.36 16.896-44.032 16.896-62.464 16.896-38.4-2.56-75.264-17.408-107.52-43.008-53.76 11.776-106.496 28.16-165.376 52.224-45.056 80.896-87.04 121.856-124.928 121.856z m33.28-75.264c-6.144 4.096-11.264 8.704-16.896 13.824 6.656-3.584 11.776-8.192 16.896-13.824z m159.744-182.272c-9.728 25.088-22.016 51.712-36.352 80.384 26.624-8.704 53.76-16.384 81.408-23.04l-5.12-5.12h6.144c-14.336-16.896-29.184-35.328-43.52-53.76l-2.56 1.536z m185.344 70.656c6.656 1.536 13.824 2.56 22.016 2.56h1.024c2.56 0.512 5.12 1.024 7.68 1.024s4.608 0 7.168-0.512c-4.608-1.536-11.776-3.072-24.064-3.072H680.96z m-195.584-281.6c-3.584 16.896-2.56 34.304 2.048 51.2 3.072-16.896 2.56-34.304-0.512-51.2h-1.536z" fill="#FF5462"></path>
        </svg>""",
        "doc": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.04389 985.695761H247.95611c-84.269327 0-153.216958-68.947631-153.216958-153.727681V192.03192c0-84.269327 68.947631-153.727681 153.216958-153.727681h377.935162c40.857855 0 79.162095 18.896758 104.187531 50.561596L900.149626 305.412469c18.386035 23.493267 28.600499 52.604489 28.600499 82.226434v443.818454c0.510723 84.78005-68.436908 154.238404-152.706235 154.238404z" fill="#E6F1FF"></path>
            <path d="M776.04389 1024H247.95611c-104.187531 0-188.967581-85.290773-188.967581-190.499751V190.499751C58.988529 85.290773 143.768579 0 247.95611 0h377.935162c52.093766 0 100.101746 23.493267 132.277307 64.861845l170.070822 217.56808c23.493267 29.621945 36.261347 66.904738 36.261347 105.208978v446.37207c0.510723 104.698254-84.269327 189.989027-188.456858 189.989027zM247.95611 72.01197c-64.861845 0-117.466334 53.115212-117.466334 118.487781v643.000498c0 65.372569 52.604489 118.487781 117.466334 118.487781h527.577057c64.861845 0 117.466334-53.115212 117.466334-118.487781V387.638903c0-21.450374-7.150125-43.411471-20.939651-60.77606l-170.070823-217.56808c-18.386035-23.493267-46.47581-37.282793-76.097755-37.282793h-377.935162z" fill="#96C6FF"></path>
            <path d="M292.389027 387.12818h76.608479l48.00798 247.190024h3.064339l51.07232-215.014463h84.78005l48.00798 215.014463h3.064339l49.029426-247.190024H731.610973l-76.608479 334.012967h-91.419452L512 509.191022h-3.064339l-52.604489 211.950125H364.911721L292.389027 387.12818z" fill="#0075FF"></path>
        </svg>""",
        "ppt": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#FFF0EB"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#FFC1AC"></path>
            <path d="M359.424 768V337.92h145.408c123.904 0 167.936 37.888 167.936 144.384 0 110.592-44.032 149.504-167.936 149.504H445.44V768H359.424zM445.44 565.248h46.08c69.12 0 92.672-20.48 92.672-80.384 0-58.88-25.088-80.384-92.672-80.384h-46.08v160.768z" fill="#FF6A38"></path>
        </svg>""",
        "xls": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 985.6H247.296c-84.48 0-153.6-69.12-153.6-154.112V189.952C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 303.616c18.432 23.552 28.672 52.736 28.672 82.432v444.928c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#E9F6F3"></path>
            <path d="M776.704 1021.44H247.296c-104.448 0-189.44-84.992-189.44-189.952V189.952C57.856 84.992 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 64.512L929.28 281.6c23.552 29.696 36.352 66.56 36.352 104.96v444.928c0.512 104.96-84.48 189.952-188.928 189.952zM247.296 71.68c-65.024 0-117.76 52.736-117.76 118.272v641.536c0 65.024 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-52.736 117.76-118.272V386.56c0-21.504-7.168-43.008-20.992-60.416l-170.496-217.088c-18.432-23.552-46.592-36.864-76.288-36.864 0-0.512-378.88-0.512-378.88-0.512z" fill="#A3DBCC"></path>
            <path d="M595.456 749.056l-86.528-128-86.528 128H314.368l140.8-195.072-130.56-188.928h107.52l76.8 123.904 76.288-123.904h107.52l-130.56 188.928 140.8 195.072z" fill="#20A884"></path>
        </svg>""",
        "pic": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#E6FCFD"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#92EFEF"></path>
            <path d="M439.808 392.192m-71.68 0a71.68 71.68 0 1 0 143.36 0 71.68 71.68 0 1 0 -143.36 0Z" fill="#4EEAE2"></path>
            <path d="M398.848 495.104L261.12 747.52h275.456zM581.12 402.432l-97.792 132.608 117.76 212.48H762.88z" fill="#4EEAE2"></path>
        </svg>""",
        "zip": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#F5ECEC"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#DBBDBD"></path>
            <path d="M332.8 744.96v-52.224L576.512 414.72H350.72V355.84h326.144v52.736L433.664 686.08H691.2v58.88z" fill="#BC8585"></path>
        </svg>""",
        "txt": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#E6F5FC"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#96D6F4"></path>
            <path d="M708.608 427.52h-148.992v339.456H467.968V427.52H318.976V347.136h389.632v80.384z" fill="#009DE6"></path>
        </svg>""",
        "file": """<svg viewBox="0 0 1024 1024" width="24" height="24">
            <path d="M776.704 988.16H247.296c-84.48 0-153.6-69.632-153.6-154.624V190.464C93.696 105.472 162.816 35.84 247.296 35.84h378.88c40.96 0 79.36 18.944 104.448 50.688L901.12 304.64c18.432 23.552 28.672 53.248 28.672 82.944v445.952c0.512 84.992-68.608 154.624-153.088 154.624z" fill="#E6F5FC"></path>
            <path d="M776.704 1024H247.296c-104.448 0-189.44-85.504-189.44-190.464V190.464C57.856 85.504 142.848 0 247.296 0h378.88c52.224 0 100.352 23.552 132.608 65.024l170.496 217.6c23.552 29.696 36.352 67.072 36.352 104.96v445.952c0.512 104.96-84.48 190.464-188.928 190.464zM247.296 72.192c-65.024 0-117.76 53.248-117.76 118.272v643.072c0 65.536 52.736 118.272 117.76 118.272h528.896c65.024 0 117.76-53.248 117.76-118.272V387.584c0-21.504-7.168-43.008-20.992-60.928l-170.496-217.6c-18.432-23.552-46.592-36.864-76.288-36.864h-378.88z" fill="#96D6F4"></path>
            <path d="M708.608 427.52h-148.992v339.456H467.968V427.52H318.976V347.136h389.632v80.384z" fill="#009DE6"></path>
        </svg>""",
    }

    return icons.get(icon_type, icons["file"])


def _truncate_middle(filename: str, max_display_len: int = 25) -> dict:
    """
    文件名中间省略处理（截图专用：固定宽度，直接截断）

    Args:
        filename: 文件名
        max_display_len: 主文件名最大显示长度（默认22字符，基于最小GUI界面宽度）

    Returns:
        dict: {"start": str, "end": str, "suffix": str, "short": bool}
    """
    if not filename:
        return {"start": "", "end": "", "suffix": "", "short": True}

    # 查找最后一个点号（文件扩展名）
    last_dot_index = filename.rfind(".")
    main_part = filename
    suffix = ""

    if last_dot_index != -1 and last_dot_index != 0:
        suffix = filename[last_dot_index:]  # 包含点号，如 ".pdf"
        main_part = filename[:last_dot_index]

    # 短文件名（主文件名 <= max_display_len 字符）：直接显示，不分割
    if len(main_part) <= max_display_len:
        return {"start": main_part, "end": "", "suffix": suffix, "short": True}

    # 长文件名：前半部分限制长度，后半部分固定显示最后4字符
    end_len = 6
    start_len = max_display_len - end_len - 1  # -1 是省略号的位置
    start = main_part[:start_len]
    end = main_part[-end_len:]

    return {"start": start, "end": end, "suffix": suffix, "short": False}


def _render_markdown(text: str) -> str:
    """
    使用 mistune 渲染 Markdown，并处理自定义标签

    复刻前端 renderMarkdown 函数逻辑
    """
    if not text:
        return "无总结内容"

    # 使用 mistune 渲染 Markdown
    html = mistune.html(text)

    # 调试：打印渲染后的 HTML（可选）
    # logger.debug(f"📸 Markdown 渲染结果: {html[:500]}...")

    # 替换自定义标签为带样式的 span
    # <date>...</date> -> <span class="md-date">...</span>
    html = html.replace("&lt;date&gt;", '<span class="md-date">')
    html = html.replace("&lt;/date&gt;", "</span>")
    html = html.replace("<date>", '<span class="md-date">')
    html = html.replace("</date>", "</span>")

    # <loc>...</loc> -> <span class="md-loc">...</span>
    html = html.replace("&lt;loc&gt;", '<span class="md-loc">')
    html = html.replace("&lt;/loc&gt;", "</span>")
    html = html.replace("<loc>", '<span class="md-loc">')
    html = html.replace("</loc>", "</span>")

    # <contact>...</contact> -> <span class="md-contact">...</span>
    html = html.replace("&lt;contact&gt;", '<span class="md-contact">')
    html = html.replace("&lt;/contact&gt;", "</span>")
    html = html.replace("<contact>", '<span class="md-contact">')
    html = html.replace("</contact>", "</span>")

    return html


def _generate_html_template(article_data: Dict[str, Any]) -> str:
    """
    生成快照 HTML 模板 - 使用模板文件 + mistune 渲染
    """
    title = article_data.get("title", "未知标题")
    source_name = article_data.get("source_name", "")
    category = article_data.get("category", "")
    department = article_data.get("department", "")
    date = article_data.get("date", "")
    exact_time = article_data.get("exact_time", "")
    summary = strip_emoji(article_data.get("summary", ""))
    url = article_data.get("url", "")
    model_name = article_data.get("model_name", "AI")
    attachments_raw = article_data.get("attachments", [])

    logger.info(f"📸 snapshot_service - 接收到 model_name: {model_name}")

    # 格式化日期 - 🌟 如果时间是 00:00，则不显示时间部分
    if exact_time:
        # 检查是否是 "00:00:00" 或 "00:00" 结尾
        import re
        time_match = re.search(r'(\d{1,2}):(\d{1,2})(?::\d{1,2})?$', exact_time)
        if time_match:
            hour, minute = int(time_match.group(1)), int(time_match.group(2))
            if hour == 0 and minute == 0:
                # 时间是 00:00，只显示日期部分
                date_display = re.sub(r'\s+\d{1,2}:\d{1,2}(:\d{1,2})?$', '', exact_time)
            else:
                date_display = exact_time
        else:
            date_display = exact_time
    else:
        date_display = date

    # 🌟 AI 品牌图标和标题
    ai_icon_svg = _get_ai_icon_svg(model_name)
    ai_brand_title = _get_ai_brand_title(model_name)
    logger.info(f"📸 snapshot_service - 生成 ai_brand_title: {ai_brand_title}")

    # 🌟 解析附件（兼容 JSON 字符串和数组）
    attachments = []
    if attachments_raw:
        if isinstance(attachments_raw, str):
            try:
                attachments = json.loads(attachments_raw)
            except (json.JSONDecodeError, TypeError):
                attachments = []
        elif isinstance(attachments_raw, list):
            attachments = attachments_raw

    # 🌟 提取彩色标签（复刻前端逻辑：从 summary 第一行提取【】标签）
    parsed_tags = []
    parsed_body = summary

    if summary and "【" in summary:
        lines = summary.split("\n")
        first_line = lines[0].strip() if lines else ""
        if first_line.startswith("【"):
            import re

            matches = re.findall(r"【(.*?)】", first_line)
            if matches:
                parsed_tags = matches
            # 移除第一行标签，保留正文
            parsed_body = "\n".join(lines[1:]).strip()

    # 使用 mistune 渲染 Markdown
    content_html = _render_markdown(parsed_body)

    # 读取模板文件
    try:
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
            template = f.read()
    except Exception as e:
        logger.error(f"读取模板文件失败: {e}")
        # 回退到内联模板
        template = _get_fallback_template()

    # 替换占位符
    html = template.replace("{{TITLE}}", title)
    html = html.replace("{{SOURCE_NAME}}", source_name)
    html = html.replace("{{AI_ICON}}", ai_icon_svg)
    html = html.replace("{{AI_BRAND}}", ai_brand_title)
    html = html.replace("{{DATE}}", date_display)
    html = html.replace("{{DEPARTMENT}}", department if department else source_name)
    html = html.replace("{{CATEGORY}}", category)
    html = html.replace("{{CONTENT}}", content_html)
    html = html.replace("{{URL}}", url)
    html = html.replace("{{TIMESTAMP}}", datetime.now().strftime("%Y-%m-%d %H:%M"))

    # 处理标签容器（彩色标签）
    tags_container = ""
    if parsed_tags:
        tags_html = ""
        tag_colors = ["tag-blue", "tag-green", "tag-purple", "tag-rose", "tag-amber"]
        for i, tag in enumerate(parsed_tags[:5]):
            color = tag_colors[i % len(tag_colors)]
            tags_html += f'<span class="tag {color}">{tag}</span>'
        tags_container = f'<div class="tags-container">{tags_html}</div>'
    html = html.replace("{{TAGS_CONTAINER}}", tags_container)

    # 🌟 处理附件图标和数量（右上角）
    attachment_badge = ""
    if attachments:
        attachment_count = len(attachments)
        attachment_badge = f"""<span class="badge-attachment">
            <svg class="icon-svg" viewBox="0 0 17.3523 21.2951">
                <path d="M3.28199 21.2915L13.709 21.2915C15.8578 21.2915 16.991 20.1419 16.991 17.9864L16.991 9.33974L9.51384 9.33974C8.31353 9.33974 7.72093 8.74714 7.72093 7.54372L7.72093 0L3.28199 0C1.14295 0 0 1.15627 0 3.31173L0 17.9864C0 20.1486 1.13629 21.2915 3.28199 21.2915ZM9.79778 7.84347L16.8924 7.84347C16.8462 7.37787 16.4916 6.93135 15.9545 6.38451L10.6606 1.0209C10.1458 0.50246 9.68946 0.150938 9.2172 0.0950003L9.2172 7.26955C9.2172 7.65216 9.40851 7.84347 9.79778 7.84347Z" fill="currentColor"/>
            </svg>
            {attachment_count}
        </span>"""
    html = html.replace("{{ATTACHMENT_BADGE}}", attachment_badge)

    # 🌟 处理底部附件区域（带文件名省略）
    attachments_section = ""
    if attachments:
        attachments_html = ""
        for att in attachments:
            att_name = att.get("name", "未知文件")
            icon_type = _get_attachment_icon_type(att_name)
            icon_svg = _get_attachment_icon_svg(icon_type)

            # 文件名省略处理
            truncated = _truncate_middle(att_name)
            if truncated["short"]:
                # 短文件名直接显示
                name_html = f'<span class="att-name">{att_name}</span>'
            else:
                # 长文件名：start + … + end + suffix
                name_html = f"""<span class="att-name" title="{att_name}">
                    <span class="att-name-prefix">{truncated["start"]}</span>
                    <span class="att-name-ellipsis">…</span>
                    <span class="att-name-middle">{truncated["end"]}</span>
                    <span class="att-name-suffix">{truncated["suffix"]}</span>
                </span>"""

            attachments_html += f"""<div class="attachment-card">
                <div class="att-icon">{icon_svg}</div>
                <div class="att-info">{name_html}</div>
            </div>"""

        attachments_section = f"""<div class="attachments-section">
            <div class="attachments-divider"></div>
            <div class="attachments-list">{attachments_html}</div>
        </div>"""
    html = html.replace("{{ATTACHMENTS_SECTION}}", attachments_section)

    return html


def _get_fallback_template() -> str:
    """回退模板（当模板文件读取失败时使用）"""
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=480, initial-scale=1.0">
    <title>{{TITLE}}</title>
    <style>
      :root { --accent-color: #408ff7; --border-color: #e5e7eb; }
      * { box-sizing: border-box; margin: 0; padding: 0; }
      body { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background: #f6f6f8; width: 480px; padding: 20px; }
      .detail-view { background: #fff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08); }
      .detail-header { padding: 16px 24px 12px; border-bottom: 1px solid var(--border-color); }
      .provider-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
      .provider-info { font-size: 12px; color: #9ca3af; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; display: inline-flex; align-items: center; }
      .inline-ai-icon { display: inline-flex; align-items: center; justify-content: center; width: 14px; height: 14px; margin-right: 4px; }
      .inline-ai-icon svg { width: 100%; height: 100%; display: block; }
      .top-actions { display: flex; align-items: center; gap: 6px; }
      .badge-attachment { color: #8395ff; background: #eef1ff; border: 1px solid #d6deff; display: inline-flex; align-items: center; gap: 4px; height: 28px; min-width: 28px; padding: 0 6px; border-radius: 7px; font-size: 11px; font-weight: 600; }
      .detail-title { font-size: 18px; font-weight: 600; color: #111; margin: 0 0 16px 0; line-height: 1.4; }
      .tags-container { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 6px; }
      .tag { font-size: 12px; padding: 3px 8px; border-radius: 6px; font-weight: 600; border: 1px solid transparent; }
      .tag-blue { background-color: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
      .tag-green { background-color: #ecfdf5; border-color: #a7f3d0; color: #065f46; }
      .tag-purple { background-color: #f5f3ff; border-color: #ddd6fe; color: #5b21b6; }
      .tag-rose { background-color: #fff1f2; border-color: #fecdd3; color: #be123c; }
      .tag-amber { background-color: #fffbeb; border-color: #fde68a; color: #92400e; }
      .meta-badges { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .meta-badge { background: #f3f4f6; color: #808080; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; border: 1px solid #e5e7eb; }
      .detail-content { padding: 20px 24px; font-size: 15px; color: #374151; line-height: 1.6; }
      .markdown-body h3 { font-size: 16px; font-weight: 600; color: #111; margin: 10px 0; display: flex; align-items: center; }
      .markdown-body h3::before { content: ""; width: 5px; height: 18px; background: var(--accent-color); margin-right: 10px; border-radius: 2px; flex-shrink: 0; }
      .markdown-body p { margin-top: 0; margin-bottom: 6px; }
      .markdown-body ul { padding-left: 24px; margin: 10px 0; color: #4b5563; list-style-type: disc; list-style-position: outside; }
      .markdown-body ul ul { padding-left: 24px; margin: 4px 0; list-style-type: circle; list-style-position: outside; }
      .markdown-body ul ul ul { padding-left: 24px; margin: 4px 0; list-style-type: square; list-style-position: outside; }
      .markdown-body ul ul ul ul { padding-left: 24px; margin: 4px 0; list-style-type: disc; list-style-position: outside; }
      .markdown-body li { margin-bottom: 2px; padding-left: 4px; }
      .markdown-body li > ul { margin-top: 4px; margin-bottom: 4px; }
      .md-date { color: #e11d48; font-weight: 600; background: #fff1f2; padding: 0 4px; border-radius: 3px; }
      .md-loc { color: #8b5cf6; font-weight: 600; background: rgba(139, 92, 246, 0.08); padding: 0 4px; border-radius: 4px; }
      .md-contact { color: #10b981; font-weight: 600; background: #ecfdf5; padding: 0 4px; border-radius: 3px; }
      .attachments-section { margin-top: 32px; padding: 0 24px 10px; }
      .attachments-divider { height: 1px; background: var(--border-color); margin-bottom: 12px; }
      .attachments-list { display: flex; flex-direction: column; gap: 10px; }
      .attachment-card { display: flex; align-items: center; gap: 12px; padding: 12px 16px; background: #f9fafb; border: 1px solid var(--border-color); border-radius: 7px; }
      .att-icon { font-size: 24px; line-height: 1; flex-shrink: 0; }
      .att-info { flex: 1; min-width: 0; overflow: hidden; }
      .att-name { font-size: 13px; color: #374151; font-weight: 500; white-space: nowrap; display: flex; align-items: center; }
      .att-name-prefix { white-space: nowrap; overflow: hidden; text-overflow: clip; }
      .att-name-ellipsis { flex-shrink: 0; }
      .att-name-middle { white-space: nowrap; flex-shrink: 0; }
      .att-name-suffix { white-space: nowrap; flex-shrink: 0; }
      .detail-footer { padding: 12px 24px; background: #fafafa; border-top: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; }
      .disclaimer { font-size: 11px; color: #aeb3bd; line-height: 1.6; display: flex; flex-direction: column; }
      .disclaimer span { display: flex; align-items: center; gap: 6px; }
      .disclaimer-icon { flex-shrink: 0; width: 14px; height: 14px; }
      .btn-group { display: flex; gap: 10px; }
      .action-btn { width: 60px; height: 38px; border: none; border-radius: 19px; color: white; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1); flex-shrink: 0; }
      .action-btn svg { width: 18px; height: 18px; }
      .btn-copy { background: #65c466; }
      .btn-view { background: #f2ca44; }
      .btn-close { background: #ec6765; }
      .card-footer { padding: 12px 24px 16px; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; }
      .brand-text { font-size: 11px; color: #9ca3af; }
      .timestamp-text { font-size: 11px; color: #9ca3af; }
    </style>
</head>
<body>
    <div class="detail-view">
        <div class="detail-header">
            <div class="provider-row">
                <span class="provider-info" style="display: inline-flex; align-items: center;">
                    <span class="inline-ai-icon">{{AI_ICON}}</span>
                    {{AI_BRAND}}
                </span>
                <div class="top-actions">{{ATTACHMENT_BADGE}}</div>
            </div>
            <h2 class="detail-title">{{TITLE}}</h2>
            {{TAGS_CONTAINER}}
            <div class="meta-badges">
                <span class="meta-badge">{{DEPARTMENT}}</span>
                <span class="meta-badge">{{CATEGORY}}</span>
                <span class="meta-badge">{{DATE}}</span>
            </div>
        </div>
        <div class="detail-content markdown-body">{{CONTENT}}</div>
        {{ATTACHMENTS_SECTION}}
        <div class="detail-footer">
            <div class="disclaimer">
                <span>
                    <svg class="disclaimer-icon" opacity="0.5" viewBox="0 0 1024 1024" width="14" height="14"><path d="M576.4 203.3c46.7 90.9 118.6 145.5 215.7 163.9 97.1 18.4 111.5 64.9 43.3 139.5s-95.6 162.9-82.3 265.2c13.2 102.3-24.6 131-113.4 86.2s-177.7-44.8-266.6 0-126.6 16-113.4-86.2c13.2-102.3-14.2-190.7-82.4-265.2-68.2-74.6-53.7-121.1 43.3-139.5 97.1-18.4 169-73 215.7-163.9 46.6-90.9 93.4-90.9 140.1 0z" fill="currentColor"></path></svg>
                    本文由 AI 生成 仅供参考
                </span>
                <span>
                    <svg class="disclaimer-icon" opacity="0.5" viewBox="0 0 1024 1024" width="14" height="14"><path d="M505.4 878.6c-196.7 0-358-150.9-374.9-343.1 1-18.6 16.1-33.4 34.9-33.4 10.8 0 20.5 4.8 26.9 12.4 0.2 0.3 0.5 0.1 0.5-0.7 41.6 44.2 100.5 71.9 166.1 71.9 127.1 0 230.1-103 230.1-230.1 0-66.1-28-125.1-72.6-166.8 0.1-0.1 0.5-0.1 0.3-0.3-7-6.5-11.4-15.7-11.4-26.1 0-19 14.9-34.1 33.7-35.3 192.1 17.1 342.9 178.3 342.9 375 0 208-168.5 376.5-376.5 376.5z" fill="currentColor"></path></svg>
                    具体内容请查看信息原文
                </span>
            </div>
            <div class="btn-group">
                <span class="action-btn btn-copy"><svg viewBox="0 0 24.4531 28.4863"><path d="M15.293 7.46582C15.293 8.41309 15.9619 9.07715 16.9092 9.07715L22.5098 9.07715L22.5098 18.2031C22.5098 20.4198 21.3043 21.6448 19.0967 21.6499L19.0967 16.1035C19.1064 14.3604 18.623 13.0664 17.5537 11.9873L12.5391 6.91895C11.3965 5.76172 10.1416 5.24902 8.39355 5.24902L6.58203 5.2438L6.58203 4.98047C6.58203 2.82227 7.79785 1.57715 10.0098 1.57715L15.293 1.57715ZM17.876 2.68066L21.5381 6.46973C21.9629 6.9043 22.2559 7.24121 22.2852 7.6123L17.2949 7.6123C16.9287 7.6123 16.7676 7.44629 16.7676 7.0752L16.7627 1.95801C17.1387 1.9873 17.4902 2.27051 17.876 2.68066Z" fill="currentColor"/><path d="M10.6201 14.5215L17.3242 14.5117C17.1973 14.043 16.8945 13.5742 16.4307 13.1006L11.416 8.03223C10.9424 7.55371 10.5273 7.23145 10.0488 7.08008L10.0488 13.9307C10.0488 14.3115 10.2539 14.5215 10.6201 14.5215ZM5.00977 26.8994L14.082 26.8994C16.2988 26.8994 17.5098 25.6738 17.5098 23.4521L17.5098 16.1084L10.3418 16.1084C9.10645 16.1084 8.46191 15.459 8.46191 14.2285L8.46191 6.82617L5.00977 6.82617C2.79785 6.82617 1.58203 8.04199 1.58203 10.2734L1.58203 23.4521C1.58203 25.6836 2.79785 26.8994 5.00977 26.8994Z" fill="currentColor"/></svg></span>
                <span class="action-btn btn-view"><svg viewBox="0 0 21.5635 19.9456"><path d="M1.78027 11.1251L8.70898 11.1398C8.80664 11.1398 8.8457 11.1788 8.8457 11.2765L8.85546 18.1759C8.85546 20.1974 11.4629 20.6369 12.3418 18.7081L19.5 3.11244C20.4424 1.04213 18.8652-0.422718 16.8242 0.505016L1.18456 7.6681C-0.656255 8.50306-0.270513 11.1154 1.78027 11.1251Z" fill="currentColor"/></svg></span>
                <span class="action-btn btn-close"><svg viewBox="0 0 16.8712 16.5346"><path d="M13.4642 0.515675L0.484912 13.5024C-0.16047 14.1403-0.170235 15.3409 0.507294 16.0235C1.19228 16.6988 2.40777 16.6764 3.03823 16.0459L16.0175 3.06669C16.6852 2.39893 16.6801 1.23566 15.9876 0.545518C15.2952-0.146932 14.1417-0.156698 13.4642 0.515675ZM16.0175 13.4875L3.03823 0.508214C2.40031-0.12455 1.19744-0.156698 0.507294 0.538057C-0.175391 1.23051-0.153009 2.42131 0.484912 3.05923L13.4642 16.0385C14.1319 16.7062 15.2975 16.7011 15.9876 16.0161C16.6778 15.3236 16.6852 14.1627 16.0175 13.4875Z" fill="currentColor"/></svg></span>
            </div>
        </div>
        <div class="card-footer">
            <span class="brand-text">本文由 MicroFlow 推送</span>
            <span class="timestamp-text">{{TIMESTAMP}}</span>
        </div>
    </div>
</body>
</html>"""


def render_article_snapshot(
    article_data: Dict[str, Any], max_retries: int = 3
) -> Optional[str]:
    """
    渲染文章快照（同步接口，使用 Playwright 同步 API）

    Args:
        article_data: 文章数据字典，包含 title, source_name, category, date, summary 等字段
        max_retries: 最大重试次数

    Returns:
        生成的图片文件路径，失败返回 None
    """
    from playwright.sync_api import sync_playwright

    title = article_data.get("title", "snapshot")[:30]
    logger.info(f"📸 开始渲染快照: {title}...")

    for attempt in range(1, max_retries + 1):
        playwright = None
        browser = None
        page = None

        try:
            # 每次创建新的 Playwright 实例，避免事件循环冲突
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                ],
            )

            # Retina 高清截图
            page = browser.new_page(device_scale_factor=2)
            page.set_viewport_size({"width": 480, "height": 800})

            # 生成 HTML
            html_content = _generate_html_template(article_data)
            logger.info(f"📸 HTML 模板已生成，长度: {len(html_content)}")

            # 加载 HTML
            page.set_content(html_content, wait_until="domcontentloaded")

            # 等待字体加载
            page.wait_for_timeout(500)

            # 创建临时文件
            temp_dir = tempfile.gettempdir()
            safe_title = "".join(
                c
                for c in article_data.get("title", "snapshot")
                if c.isalnum() or c in " -_"
            )[:50]
            timestamp = int(datetime.now().timestamp() * 1000)
            temp_path = os.path.join(
                temp_dir, f"microflow_email_{safe_title}_{timestamp}.png"
            )

            # 截图
            page.screenshot(path=temp_path, full_page=True, type="png")

            logger.info(f"快照生成成功: {temp_path}")
            return temp_path

        except Exception as e:
            logger.warning(f"📸 ⚠️ 快照生成失败 (尝试 {attempt}/{max_retries}): {e}")

            if attempt < max_retries:
                # 等待后重试
                time.sleep(1)
            else:
                logger.error(f"快照生成最终失败: {e}", exc_info=True)
                return None

        finally:
            # 确保资源正确释放
            try:
                if page:
                    page.close()
            except Exception:
                pass

            try:
                if browser:
                    browser.close()
            except Exception:
                pass

            try:
                if playwright:
                    playwright.stop()
            except Exception:
                pass

    return None


def cleanup_snapshot(path: str) -> None:
    """
    清理临时快照文件

    Args:
        path: 快照文件路径
    """
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.debug(f"已清理临时快照: {path}")
    except Exception as e:
        logger.warning(f"清理临时快照失败: {e}")
