"""
来源排序配置 - 定义信息来源的显示顺序

用于：
1. 前端筛选栏的来源排序
2. 设置页面的来源列表展示
3. 爬虫调度器的执行顺序
"""

# 来源显示顺序（按优先级排列）
SOURCE_ORDER = [
    "公文通",
    "中德智能制造学院",
    "人工智能学院",
    "新材料与新能源学院",
    "城市交通与物流学院",
    "健康与环境工程学院",
    "工程物理学院",
    "药学院",
    "集成电路与光电芯片学院",
    "未来技术学院",
    "创意设计学院",
    "商学院"
]


def sort_sources(sources: list) -> list:
    """
    根据预定义顺序对来源列表进行排序

    Args:
        sources: 原始来源列表

    Returns:
        排序后的来源列表（预定义顺序 + 字母序排列的其他来源）
    """
    # 预定义顺序的来源
    ordered = [s for s in SOURCE_ORDER if s in sources]
    # 未在预定义列表中的来源，按字母序排列
    others = sorted([s for s in sources if s not in SOURCE_ORDER])
    return ordered + others
