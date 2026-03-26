"""数据模型模块 - 定义数据结构和 Schema"""

from .spider_rule import (
    SpiderRuleSchema,
    SpiderRuleOutput,
    RuleGenerationResult
)

__all__ = [
    'SpiderRuleSchema',
    'SpiderRuleOutput',
    'RuleGenerationResult'
]
