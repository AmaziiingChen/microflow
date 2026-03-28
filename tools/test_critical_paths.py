#!/usr/bin/env python3
"""
MicroFlow 关键路径测试脚本

测试核心功能模块的关键路径：
- 数据库操作
- 配置服务
- LLM 服务
- 规则生成器
- 爬虫基础功能
"""

import sys
import os
import logging
import time
import tempfile
import shutil
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class TestResult:
    """测试结果记录"""
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def record_pass(self, name: str):
        self.passed += 1
        logger.info(f"✅ PASS: {name}")

    def record_fail(self, name: str, error: str):
        self.failed += 1
        self.errors.append((name, error))
        logger.error(f"❌ FAIL: {name} - {error}")

    def summary(self):
        total = self.passed + self.failed
        print("\n" + "="*60)
        print(f"测试汇总: {self.passed}/{total} 通过")
        print("="*60)
        if self.errors:
            print("\n失败的测试:")
            for name, error in self.errors:
                print(f"  - {name}: {error}")
        return self.failed == 0


def test_database(result: TestResult):
    """测试数据库核心操作"""
    print("\n📦 测试数据库模块...")

    try:
        from src.database import db

        # 测试 1: 获取统计信息
        stats = db.get_stats()
        if 'write_queue_size' in stats and 'write_stats' in stats:
            result.record_pass("数据库统计信息")
        else:
            result.record_fail("数据库统计信息", f"返回缺少必要字段: {list(stats.keys())}")

        # 测试 2: 检查队列状态（使用公开方法）
        queue_size = db.get_stats().get('write_queue_size', 0)
        if queue_size >= 0:
            result.record_pass("写队列状态检查")
        else:
            result.record_fail("写队列状态检查", "队列大小为负数")

    except Exception as e:
        result.record_fail("数据库模块", str(e))


def test_config_service(result: TestResult):
    """测试配置服务"""
    print("\n⚙️ 测试配置服务...")

    try:
        from src.services.config_service import ConfigService
        from src.core.paths import CONFIG_PATH

        config = ConfigService(str(CONFIG_PATH))

        # 测试 1: 读取配置
        api_key = config.get("apiKey", "")
        result.record_pass("配置服务读取")

        # 测试 2: 获取所有配置
        all_config = config.to_dict()
        if isinstance(all_config, dict):
            result.record_pass("配置服务获取全部")
        else:
            result.record_fail("配置服务获取全部", "返回类型错误")

    except Exception as e:
        result.record_fail("配置服务", str(e))


def test_llm_service(result: TestResult):
    """测试 LLM 服务"""
    print("\n🤖 测试 LLM 服务...")

    try:
        from src.llm_service import LLMService

        llm = LLMService()

        # 测试 1: 取消机制
        llm.clear_cancel()
        if not llm.is_cancelled():
            result.record_pass("LLM取消机制-清除")
        else:
            result.record_fail("LLM取消机制-清除", "状态错误")

        llm.request_cancel()
        if llm.is_cancelled():
            result.record_pass("LLM取消机制-设置")
        else:
            result.record_fail("LLM取消机制-设置", "状态错误")

        llm.clear_cancel()

        # 测试 2: 配置更新
        llm.update_config(None, "test-model", None, "https://api.test.com/v1")
        result.record_pass("LLM配置更新")

    except Exception as e:
        result.record_fail("LLM服务", str(e))


def test_rule_generator(result: TestResult):
    """测试规则生成器服务"""
    print("\n🕷️ 测试规则生成器...")

    try:
        from src.services.rule_generator import RuleGeneratorService, score_selector_stability
        from src.services.config_service import ConfigService
        from src.core.paths import CONFIG_PATH

        config = ConfigService(str(CONFIG_PATH))
        generator = RuleGeneratorService(config)

        # 测试 1: 选择器稳定性评分
        score1 = score_selector_stability("#main-content .article-list")
        if 0 <= score1 <= 100:
            result.record_pass("选择器稳定性评分-正常范围")
        else:
            result.record_fail("选择器稳定性评分-正常范围", f"评分超出范围: {score1}")

        score2 = score_selector_stability("div.css-abc123")
        if score2 < 50:  # 动态class应该低分
            result.record_pass("选择器稳定性评分-动态class检测")
        else:
            result.record_fail("选择器稳定性评分-动态class检测", f"应该检测到动态class: {score2}")

        # 测试 2: 网站类型识别
        site_type, strategy = generator._identify_website_type("https://www.pku.edu.cn/news", "")
        if site_type == "edu_gov":
            result.record_pass("网站类型识别-高校网站")
        else:
            result.record_fail("网站类型识别-高校网站", f"识别错误: {site_type}")

    except Exception as e:
        result.record_fail("规则生成器", str(e))


def test_date_utils(result: TestResult):
    """测试日期解析工具"""
    print("\n📅 测试日期解析工具...")

    try:
        from src.utils.date_utils import parse_date_safe, format_date

        # 测试 1: ISO 格式
        dt = parse_date_safe("2024-03-15")
        if dt and dt.year == 2024:
            result.record_pass("日期解析-ISO格式")
        else:
            result.record_fail("日期解析-ISO格式", f"解析错误: {dt}")

        # 测试 2: 中文格式
        dt = parse_date_safe("2024年3月15日")
        if dt and dt.month == 3:
            result.record_pass("日期解析-中文格式")
        else:
            result.record_fail("日期解析-中文格式", f"解析错误: {dt}")

        # 测试 3: 格式化输出
        formatted = format_date("2024/03/15")
        if formatted == "2024-03-15":
            result.record_pass("日期格式化")
        else:
            result.record_fail("日期格式化", f"格式化错误: {formatted}")

        # 测试 4: 空值处理
        dt = parse_date_safe("")
        if dt is None:
            result.record_pass("日期解析-空值处理")
        else:
            result.record_fail("日期解析-空值处理", f"应该返回None: {dt}")

    except Exception as e:
        result.record_fail("日期解析工具", str(e))


def test_spider_base(result: TestResult):
    """测试爬虫基类"""
    print("\n🔍 测试爬虫基类...")

    try:
        from src.spiders.base_spider import BaseSpider, ArticleData
        from typing import List, Optional

        # 创建具体实现类来测试抽象基类
        class TestSpider(BaseSpider):
            SOURCE_NAME = "test_spider"
            BASE_URL = "https://example.com"

            def fetch_list(self, page_num: int = 1, section_name: Optional[str] = None, limit: Optional[int] = None, **kwargs) -> List[ArticleData]:
                return []

        spider = TestSpider()

        # 测试 1: 基本属性
        if hasattr(spider, 'SOURCE_NAME') and hasattr(spider, 'BASE_URL'):
            result.record_pass("爬虫基类-基本属性")
        else:
            result.record_fail("爬虫基类-基本属性", "缺少必要属性")

        # 测试 2: session 存在
        if hasattr(spider, 'session'):
            result.record_pass("爬虫基类-HTTP会话")
        else:
            result.record_fail("爬虫基类-HTTP会话", "缺少session")

        # 测试 3: _safe_get 方法存在
        if hasattr(spider, '_safe_get') and callable(getattr(spider, '_safe_get')):
            result.record_pass("爬虫基类-安全请求方法")
        else:
            result.record_fail("爬虫基类-安全请求方法", "缺少_safe_get方法")

        spider.close()

    except Exception as e:
        result.record_fail("爬虫基类", str(e))


def test_spider_rule_model(result: TestResult):
    """测试爬虫规则数据模型"""
    print("\n📋 测试爬虫规则数据模型...")

    try:
        from src.models.spider_rule import (
            SpiderRuleSchema,
            SpiderRuleOutput,
            RuleGenerationResult
        )

        # 测试 1: SpiderRuleSchema 验证
        schema = SpiderRuleSchema(
            list_container="ul.news-list",
            item_selector="li",
            field_selectors={"title": "a::text", "url": "a::attr(href)"}
        )
        result.record_pass("规则Schema创建")

        # 测试 2: SpiderRuleOutput 创建
        import time
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        output = SpiderRuleOutput(
            rule_id="rule_test001",
            task_id="task_001",
            task_name="测试任务",
            url="https://example.com",
            list_container="ul.news-list",
            item_selector="li",
            field_selectors={"title": "a::text"},
            max_items=20,
            body_field="content",
            skip_detail=True,
            created_at=now,
            updated_at=now,
        )

        if output.max_items == 20 and output.skip_detail == True:
            result.record_pass("规则Output-新字段")
        else:
            result.record_fail("规则Output-新字段", "字段值不正确")

    except Exception as e:
        result.record_fail("爬虫规则数据模型", str(e))


def test_dynamic_spider_fields(result: TestResult):
    """测试动态爬虫新字段"""
    print("\n🌐 测试动态爬虫新字段...")

    try:
        from src.spiders.dynamic_spider import DynamicSpider

        rule_dict = {
            "rule_id": "rule_test",
            "task_id": "task_001",
            "task_name": "测试爬虫",
            "url": "https://example.com",
            "list_container": "ul.list",
            "item_selector": "li",
            "field_selectors": {"title": "a::text", "content": "p::text"},
            "max_items": 15,
            "body_field": "content",
            "skip_detail": True,
        }

        spider = DynamicSpider(rule_dict)

        # 测试 1: max_items
        if spider.max_items == 15:
            result.record_pass("动态爬虫-max_items")
        else:
            result.record_fail("动态爬虫-max_items", f"值错误: {spider.max_items}")

        # 测试 2: body_field
        if spider.body_field == "content":
            result.record_pass("动态爬虫-body_field")
        else:
            result.record_fail("动态爬虫-body_field", f"值错误: {spider.body_field}")

        # 测试 3: skip_detail
        if spider.skip_detail == True:
            result.record_pass("动态爬虫-skip_detail")
        else:
            result.record_fail("动态爬虫-skip_detail", f"值错误: {spider.skip_detail}")

        spider.close()

    except Exception as e:
        result.record_fail("动态爬虫新字段", str(e))


def test_rss_spider_fields(result: TestResult):
    """测试 RSS 爬虫新字段"""
    print("\n📡 测试 RSS 爬虫新字段...")

    try:
        from src.spiders.rss_spider import RssSpider

        rule_dict = {
            "rule_id": "rule_rss_test",
            "task_id": "task_001",
            "task_name": "测试RSS",
            "url": "https://example.com/feed.xml",
            "source_type": "rss",
            "max_items": 30,
        }

        spider = RssSpider(rule_dict)

        # 测试 1: max_items
        if spider.max_items == 30:
            result.record_pass("RSS爬虫-max_items")
        else:
            result.record_fail("RSS爬虫-max_items", f"值错误: {spider.max_items}")

        # 测试 2: source_type
        if spider._source_type == "rss":
            result.record_pass("RSS爬虫-source_type")
        else:
            result.record_fail("RSS爬虫-source_type", f"值错误: {spider._source_type}")

        spider.close()

    except Exception as e:
        result.record_fail("RSS爬虫新字段", str(e))


def test_api_queue(result: TestResult):
    """测试 API JS 队列"""
    print("\n🔄 测试 API JS 队列...")

    try:
        # 检查 api.py 中是否有 JS 队列定义
        import src.api as api_module

        source = open(api_module.__file__, 'r').read()

        # 测试 1: JS 队列存在
        if '_js_queue' in source and 'queue.Queue' in source:
            result.record_pass("API-JS队列定义")
        else:
            result.record_fail("API-JS队列定义", "未找到队列定义")

        # 测试 2: 队列处理函数存在
        if '_process_js_queue' in source:
            result.record_pass("API-JS队列处理函数")
        else:
            result.record_fail("API-JS队列处理函数", "未找到处理函数")

        # 测试 3: 入队函数存在
        if '_enqueue_js' in source:
            result.record_pass("API-JS入队函数")
        else:
            result.record_fail("API-JS入队函数", "未找到入队函数")

    except Exception as e:
        result.record_fail("API JS 队列", str(e))


def main():
    """运行所有关键路径测试"""
    print("="*60)
    print("🧪 MicroFlow 关键路径测试")
    print("="*60)

    result = TestResult()

    # 运行测试
    test_database(result)
    test_config_service(result)
    test_llm_service(result)
    test_rule_generator(result)
    test_date_utils(result)
    test_spider_base(result)
    test_spider_rule_model(result)
    test_dynamic_spider_fields(result)
    test_rss_spider_fields(result)
    test_api_queue(result)

    # 打印汇总
    success = result.summary()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
