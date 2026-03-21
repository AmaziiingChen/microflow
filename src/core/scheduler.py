"""爬虫调度器 - 负责爬虫的初始化、管理和调度执行"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable, Type, TYPE_CHECKING

from src.spiders import (
    BaseSpider, GwtSpider, NmneSpider, AiSpider,
    SgimSpider, UtlSpider, HseeSpider, CepSpider, CopSpider, DesignSpider,
    BusinessSpider, IcocSpider, FutureTechSpider, SflSpider
)
from src.database import db
from src.core.article_processor import ArticleProcessor, ArticleContext
from src.core.network_utils import check_network_status, NetworkStatus, get_network_description

if TYPE_CHECKING:
    from src.services.config_service import ConfigService

logger = logging.getLogger(__name__)


# 爬虫注册表：(爬虫类, 板块数量, 描述, 是否需要校园网)
# 顺序：公文通 -> 中德智能制造 -> 人工智能 -> 新材料与新能源 -> 城市交通与物流 -> 健康与环境工程 -> 工程物理 -> 药学院 -> 集成电路与光电芯片 -> 未来技术 -> 创意设计 -> 商学院
SPIDER_REGISTRY: List[Tuple[Type[BaseSpider], int, str, bool]] = [
    (GwtSpider, 1, "公文通", True),                    # 公文通需要校园网
    (SgimSpider, 2, "中德智能制造学院", False),
    (AiSpider, 2, "人工智能学院", False),
    (NmneSpider, 6, "新材料与新能源学院", False),
    (UtlSpider, 2, "城市交通与物流学院", False),
    (HseeSpider, 2, "健康与环境工程学院", False),
    (CepSpider, 2, "工程物理学院", False),
    (CopSpider, 2, "药学院", False),
    (IcocSpider, 3, "集成电路与光电芯片学院", False),
    (FutureTechSpider, 6, "未来技术学院", False),
    (DesignSpider, 6, "创意设计学院", False),
    (BusinessSpider, 4, "商学院", False),
    (SflSpider, 2, "外国语学院", False),
]


class SpiderScheduler:
    """
    爬虫调度器 - 单一职责：管理爬虫生命周期与调度

    架构说明：
    ┌─────────────┐      ┌──────────────┐      ┌─────────────┐
    │  Scheduler  │ ───▶ │ArticleProc.  │ ───▶ │   Workers   │
    │  (生产者)   │      │ Task Queue   │      │  (消费AI)   │
    └─────────────┘      └──────────────┘      └─────────────┘
           │                                           │
           │                                           ▼
           │                                    ┌─────────────┐
           └───────────────────────────────────▶│  Database   │
                                                │ (RLock保护) │
                                                └─────────────┘

    职责：
    1. 初始化和管理爬虫实例
    2. 遍历爬虫，抓取文章列表
    3. 将需要处理的文章提交到异步队列
    4. 推送进度到前端
    """

    # 🌟 V3 升级：移除硬编码常量，改为从配置服务动态读取
    DEFAULT_ARTICLES_PER_SECTION_LIMIT = 10

    def __init__(
        self,
        article_processor: ArticleProcessor,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        config_service: Optional["ConfigService"] = None
    ):
        """
        初始化调度器

        Args:
            article_processor: 文章处理器实例
            progress_callback: 进度回调函数 (completed, total, current_title)
            config_service: 配置服务实例（用于动态读取配置）
        """
        self.article_processor = article_processor
        self.progress_callback = progress_callback
        self.config_service = config_service
        self.active_spiders: List[BaseSpider] = []
        self._update_lock = threading.Lock()
        # 线程安全进度条支持
        self._progress_lock = threading.Lock()
        self._current_scanned = 0

        # 初始化爬虫
        self._init_spiders()

    @property
    def articles_per_section_limit(self) -> int:
        """
        动态获取每个板块处理的文章上限（支持热重载）

        Returns:
            每个板块处理的文章上限
        """
        if self.config_service:
            return self.config_service.get('articlesPerSectionLimit', self.DEFAULT_ARTICLES_PER_SECTION_LIMIT)
        return self.DEFAULT_ARTICLES_PER_SECTION_LIMIT

    def _init_spiders(self) -> None:
        """初始化所有爬虫实例"""
        for spider_cls, section_count, description, requires_intranet in SPIDER_REGISTRY:
            try:
                spider = spider_cls()
                # 使用 setattr 动态添加属性（避免 Pylance 警告）
                setattr(spider, '_requires_intranet', requires_intranet)
                self.active_spiders.append(spider)
                section_info = f" ({section_count} 个板块)" if section_count > 1 else ""
                network_req = "校园网" if requires_intranet else "公网"
                logger.info(f"✅ 爬虫已加载: {spider.SOURCE_NAME}{section_info} [{network_req}]")
            except Exception as e:
                logger.error(f"❌ {spider_cls.__name__} 初始化失败: {e}")

    def estimate_total_tasks(self) -> int:
        """预估总任务数（用于进度条显示，动态读取配置）"""
        total = 0
        limit = self.articles_per_section_limit
        for spider in self.active_spiders:
            section_count = self._get_section_count(spider)
            total += section_count * limit
        return max(total, 1)

    def _get_section_count(self, spider: BaseSpider) -> int:
        """获取爬虫的板块数量"""
        for spider_cls, count, _, _ in SPIDER_REGISTRY:
            if isinstance(spider, spider_cls):
                return count
        return 1

    def _get_sections(self, spider: BaseSpider) -> List[Optional[str]]:
        """获取爬虫的板块列表"""
        if isinstance(spider, NmneSpider):
            return list(spider.SECTIONS.keys())
        elif isinstance(spider, AiSpider):
            return list(spider.SECTIONS.keys())
        elif isinstance(spider, (SgimSpider, UtlSpider, HseeSpider, CepSpider, CopSpider, DesignSpider)):
            return list(spider.sections.keys())
        else:
            return [None]

    def _push_progress(self, completed: int, total: int, current_title: str = ""):
        """推送进度更新（通过回调）"""
        if self.progress_callback:
            try:
                self.progress_callback(completed, total, current_title)
            except Exception as e:
                logger.debug(f"进度回调执行失败: {e}")

    def run_all_spiders(
        self,
        mode: str = 'continuous',
        is_manual: bool = False,
        wait_for_completion: bool = False,
        skip_network_check: bool = False,
        enabled_sources: Optional[List[str]] = None,
        spider_progress_callback: Optional[Callable[[int, int, str], None]] = None   # 🌟 新增
    ) -> Dict[str, Any]:
        """
        执行所有爬虫的抓取任务（异步提交到处理队列）

        Args:
            mode: 追踪模式 ('today' 或 'continuous')
            is_manual: 是否手动触发
            wait_for_completion: 是否等待所有任务处理完成（阻塞）
            skip_network_check: 是否跳过网络检测（用于补偿抓取）
            enabled_sources: 启用的来源列表，如果为 None 则执行所有爬虫

        Returns:
            {
                "status": "success/error",
                "submitted_count": int,    # 提交到队列的文章数
                "queue_size": int,         # 当前队列积压数
                "stats": dict,             # 处理器统计
                "errors": list,
                "network_status": str      # 网络状态
            }
        """
        # 1. 尝试获取锁（防止并发调度）
        if not self._update_lock.acquire(blocking=False):
            logger.warning("⚠️ 拦截到并发请求：当前已有更新任务在运行")
            if is_manual:
                return {
                    "status": "error",
                    "message": "后台正在处理数据，请勿频繁点击哦",
                    "submitted_count": 0,
                    "errors": [],
                }
            return {
                "status": "success",
                "submitted_count": 0,
                "queue_size": 0,
                "stats": {},
                "errors": [],
            }

        try:
            # 2. 网络环境检测（智能路由）
            if not skip_network_check:
                network_status = check_network_status()
                network_desc = get_network_description(network_status)
                logger.info(f"🌐 网络环境检测: {network_desc}")

                # 无网络：直接返回错误
                if network_status == NetworkStatus.NO_NETWORK:
                    return {
                        "status": "error",
                        "message": "无网络连接，请检查网络后重试",
                        "submitted_count": 0,
                        "errors": ["无网络连接"],
                        "network_status": network_status.value,
                    }
            else:
                network_status = None
                network_desc = "未知（已跳过检测）"

            today_str = datetime.now().strftime('%Y-%m-%d')
            submitted_count = 0
            submitted_sources: List[str] = []  # 来源溯源列表
            errors: List[str] = []
            skipped_spiders: List[str] = []

            # 3. 重置进度计数器并推送初始进度
            self._current_scanned = 0
            # 🌟 不再发送预估值，total 设为 0，前端只显示已扫描数量
            self._push_progress(0, 0, "正在扫描数据源...")

            # 4. 筛选需要执行的爬虫（智能过滤）
            spiders_to_run: List[BaseSpider] = []
            for spider in self.active_spiders:
                # 订阅过滤：如果指定了 enabled_sources，且当前爬虫不在列表中，则跳过
                if enabled_sources is not None and spider.SOURCE_NAME not in enabled_sources:
                    logger.debug(f"⏭️ [{spider.SOURCE_NAME}] 跳过（未在订阅列表中）")
                    continue

                requires_intranet = getattr(spider, '_requires_intranet', False)

                # 智能路由：公网环境下跳过需要校园网的爬虫
                if network_status == NetworkStatus.PUBLIC_ONLY and requires_intranet:
                    skip_msg = f"[{spider.SOURCE_NAME}] 跳过（需要校园网，当前仅公网）"
                    logger.info(f"⏭️ {skip_msg}")
                    skipped_spiders.append(spider.SOURCE_NAME)
                    continue

                spiders_to_run.append(spider)

            # 5. 发送爬虫总数通知（前端初始化进度条）
            total_spiders = len(spiders_to_run)
            if spider_progress_callback and total_spiders > 0:
                try:
                    # 发送初始通知：0/total，表示开始
                    spider_progress_callback(0, total_spiders, "正在启动...")
                except Exception as e:
                    logger.debug(f"爬虫进度回调失败: {e}")

            # 6. 使用线程池并发执行爬虫
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {}
                for idx, spider in enumerate(spiders_to_run):
                    future = executor.submit(
                        self._process_spider,
                        spider=spider,
                        mode=mode,
                        today_str=today_str,
                        is_manual=is_manual
                    )
                    futures[future] = (spider.SOURCE_NAME, idx)  # 🌟 保存索引用于进度更新

                # 使用 as_completed 收集结果，并在每个完成时更新进度
                completed_count = 0
                for future in as_completed(futures):
                    source_name, idx = futures[future]
                    completed_count += 1

                    # 🌟 每个爬虫完成时推送进度
                    if spider_progress_callback:
                        try:
                            spider_progress_callback(completed_count, total_spiders, source_name)
                        except Exception as e:
                            logger.debug(f"爬虫进度回调失败: {e}")

                    try:
                        result_name, count, error_list = future.result()
                        submitted_count += count
                        if error_list:
                            errors.extend(error_list)
                        if count > 0:
                            submitted_sources.append(f"{result_name}({count})")
                        logger.info(f"📊 [{result_name}] 已提交 {count} 篇文章到处理队列")
                    except Exception as e:
                        error_msg = f"[{source_name}] 爬虫执行崩溃: {e}"
                        logger.error(error_msg, exc_info=True)
                        errors.append(error_msg)

            # 5. 如果需要等待完成
            if wait_for_completion:
                logger.info("等待所有任务处理完成...")
                self.article_processor.wait_completion()

            # 6. 获取处理器统计
            stats = self.article_processor.get_stats()
            queue_size = self.article_processor.get_queue_size()

            result = {
                "status": "success",
                "submitted_count": submitted_count,
                "queue_size": queue_size,
                "stats": stats,
                "errors": errors,
                "warnings": errors if is_manual and errors else [],
                "network_status": network_status.value if network_status else None,
            }

            # 添加跳过的爬虫信息
            if skipped_spiders:
                result["skipped_spiders"] = skipped_spiders
                result["skip_reason"] = "需要校园网访问"

            # 来源溯源日志
            if submitted_sources:
                logger.info(f"✅ 抓取完毕！提交数量: {submitted_count} (来源: {submitted_sources})")
            else:
                logger.info(f"✅ 抓取完毕！提交数量: 0 (无新文章)")

            return result

        except Exception as e:
            logger.error(f"调度执行失败: {e}")
            return {
                "status": "error",
                "message": str(e),
                "submitted_count": 0,
                "errors": [str(e)],
            }
        finally:
            self._update_lock.release()

    def _process_spider(
        self,
        spider: BaseSpider,
        mode: str,
        today_str: str,
        is_manual: bool
    ) -> Tuple[str, int, List[str]]:
        """
        处理单个爬虫的所有板块（异步提交，线程安全）

        Args:
            spider: 爬虫实例
            mode: 追踪模式
            today_str: 今日日期字符串
            is_manual: 是否手动触发

        Returns:
            (来源名称, 提交数量, 错误列表) 元组
        """
        submitted_count = 0
        source_name = spider.SOURCE_NAME
        error_list: List[str] = []

        # 获取板块列表
        sections = self._get_sections(spider)

        for section_name in sections:
            try:
                # 获取文章列表（传入 limit，让爬虫层控制抓取数量）
                limit = self.articles_per_section_limit
                if section_name:
                    articles = spider.fetch_list(page_num=1, section_name=section_name, limit=limit)
                else:
                    articles = spider.fetch_list(page_num=1, limit=limit)

                # limit 已在爬虫层生效，无需再次截断

                for article in articles:
                    # 创建文章上下文
                    ctx = self.article_processor.create_context(
                        article=article,
                        source_name=source_name,
                        section_name=section_name
                    )

                    # 标题黑名单过滤
                    should_skip, _ = self.article_processor.should_skip_by_title(ctx.title)
                    if should_skip:
                        logger.debug(f"跳过导航噪音（黑名单标题）: {ctx.title}")
                        continue

                    # 当日追踪模式：跳过非今日文章
                    if self.article_processor.should_skip_by_date(ctx.date, mode, today_str):
                        continue

                    # 持续追踪模式：快速跳过已存在的 URL
                    if self.article_processor.should_skip_by_url(ctx.url, is_manual):
                        continue

                    # 线程安全地更新进度
                    with self._progress_lock:
                        self._current_scanned += 1
                        current_scanned = self._current_scanned
                    # 🌟 不再发送预估值，total 设为 0，前端只显示已扫描数量
                    self._push_progress(current_scanned, 0, ctx.title)

                    # 🌟 异步提交到处理队列（立即返回，不阻塞）
                    if self.article_processor.submit(
                        spider=spider,
                        ctx=ctx,
                        mode=mode,
                        today_str=today_str,
                        is_manual=is_manual
                    ):
                        submitted_count += 1

            except Exception as e:
                section_label = f"板块 '{section_name}'" if section_name else "默认板块"
                error_msg = f"[{source_name}] {section_label} 抓取异常: {e}"
                logger.warning(error_msg)
                error_list.append(error_msg)
                continue

        return (source_name, submitted_count, error_list)

    @property
    def is_locked(self) -> bool:
        """调度器是否被锁定（正在执行任务）"""
        return self._update_lock.locked()

    def get_processor_stats(self) -> Dict[str, Any]:
        """获取处理器的统计信息"""
        return {
            "stats": self.article_processor.get_stats(),
            "queue_size": self.article_processor.get_queue_size()
        }