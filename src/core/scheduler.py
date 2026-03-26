"""爬虫调度器 - 负责爬虫的初始化、管理和调度执行"""

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable, Type, TYPE_CHECKING

from src.spiders import (
    BaseSpider,
    GwtSpider,
    NmneSpider,
    AiSpider,
    SgimSpider,
    UtlSpider,
    HseeSpider,
    CepSpider,
    CopSpider,
    DesignSpider,
    BusinessSpider,
    IcocSpider,
    FutureTechSpider,
    SflSpider,
)
from src.spiders.dynamic_spider import DynamicSpider, create_dynamic_spider_from_rule
from src.database import db
from src.core.article_processor import ArticleProcessor, ArticleContext
from src.core.network_utils import (
    check_network_status,
    NetworkStatus,
    get_network_description,
)
from src.services.custom_spider_rules_manager import get_rules_manager

if TYPE_CHECKING:
    from src.services.config_service import ConfigService

logger = logging.getLogger(__name__)


def _parse_date_safe(date_str: str) -> Optional[datetime]:
    """
    健壮的日期字符串标准化解析器（线程安全，纯函数）

    支持格式：
    - "2026-03-24"、"2026/03/24"（日期）
    - "2026-03-24 10:30:00"、"2026/03/24 10:30:00"（日期时间）

    Returns:
        datetime 对象，解析失败返回 None
    """
    if not date_str:
        return None
    # 统一分隔符，截取前 10 位日期部分
    normalized = date_str.strip().replace("/", "-")
    date_part = normalized[:10]
    try:
        return datetime.strptime(date_part, "%Y-%m-%d")
    except ValueError:
        return None


# 爬虫注册表：(爬虫类, 板块数量, 描述, 是否需要校园网)
# 顺序：公文通 -> 中德智能制造 -> 人工智能 -> 新材料与新能源 -> 城市交通与物流 -> 健康与环境工程 -> 工程物理 -> 药学院 -> 集成电路与光电芯片 -> 未来技术 -> 创意设计 -> 商学院
SPIDER_REGISTRY: List[Tuple[Type[BaseSpider], int, str, bool]] = [
    (GwtSpider, 1, "公文通", True),  # 公文通需要校园网
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

    # 🔐 后端硬编码安全边界（不对外暴露，不可被用户配置覆盖）
    _FALLBACK_LIMIT = 10  # 每板块最大抓取条数
    _COLD_START_QUOTA_GWT = 10  # 公文通冷启动配额
    _COLD_START_QUOTA_COLLEGE = 1  # 学院冷启动配额（跨板块累计）

    def __init__(
        self,
        article_processor: ArticleProcessor,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        config_service: Optional["ConfigService"] = None,
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
        # 🌟 使用 threading.Event 确保线程可见性
        self._cancel_event = threading.Event()  # 全局终止信号

        # 初始化爬虫
        self._init_spiders()

    def _init_spiders(self) -> None:
        """初始化所有爬虫实例（包括静态注册的学院爬虫和动态爬虫）"""
        # 1. 初始化静态注册的学院爬虫
        for (
            spider_cls,
            section_count,
            description,
            requires_intranet,
        ) in SPIDER_REGISTRY:
            try:
                spider = spider_cls()
                # 使用 setattr 动态添加属性（避免 Pylance 警告）
                setattr(spider, "_requires_intranet", requires_intranet)
                self.active_spiders.append(spider)
            except Exception as e:
                logger.error(f"{spider_cls.__name__} 初始化失败: {e}")

        # 2. 🌟 加载动态爬虫规则
        self._load_dynamic_spiders()

    def _load_dynamic_spiders(self) -> None:
        """
        加载所有启用的动态爬虫规则

        从 CustomSpiderRulesManager 读取启用的规则，
        为每条规则创建 DynamicSpider 实例并加入爬虫列表。
        """
        try:
            rules_manager = get_rules_manager()
            rules = rules_manager.load_custom_rules()

            if not rules:
                logger.info("🕷️ 无动态爬虫规则")
                return

            dynamic_count = 0
            for rule_dict in rules:
                # 跳过未启用的规则
                if not rule_dict.get("enabled", True):
                    logger.debug(
                        f"跳过已禁用的规则: {rule_dict.get('task_name', 'unknown')}"
                    )
                    continue

                # 创建动态爬虫实例
                spider = create_dynamic_spider_from_rule(rule_dict)
                if spider:
                    # 标记为动态爬虫（不需要校园网）
                    setattr(spider, "_requires_intranet", False)
                    setattr(spider, "_is_dynamic", True)
                    self.active_spiders.append(spider)
                    dynamic_count += 1
                    logger.info(f"🕷️ 加载动态爬虫: {spider.SOURCE_NAME}")

            if dynamic_count > 0:
                logger.info(f"🕷️ 共加载 {dynamic_count} 个动态爬虫")

        except Exception as e:
            logger.error(f"加载动态爬虫规则失败: {e}")

    def reload_dynamic_spiders(self) -> None:
        """重新加载动态爬虫（每次轮询前调用，实现热重载）"""
        # 清除现有的动态爬虫
        self.active_spiders = [s for s in self.active_spiders if not getattr(s, '_is_dynamic', False)]
        self._load_dynamic_spiders()

    def estimate_total_tasks(self) -> int:
        """预估总任务数（用于进度条显示）"""
        total = 0
        for spider in self.active_spiders:
            section_count = self._get_section_count(spider)
            total += section_count * self._FALLBACK_LIMIT
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
        elif isinstance(
            spider,
            (SgimSpider, UtlSpider, HseeSpider, CepSpider, CopSpider, DesignSpider),
        ):
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
        mode: str = "continuous",
        is_manual: bool = False,
        wait_for_completion: bool = False,
        skip_network_check: bool = False,
        enabled_sources: Optional[List[str]] = None,
        spider_progress_callback: Optional[
            Callable[[int, int, str], None]
        ] = None,  # 🌟 新增
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
        self._cancel_event.clear()  # 🌟 每次重新启动时，重置刹车标志

        # 1. 尝试获取锁（防止并发调度）
        if not self._update_lock.acquire(blocking=False):
            logger.warning("拦截到并发请求：当前已有更新任务在运行")
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

            today_str = datetime.now().strftime("%Y-%m-%d")
            submitted_count = 0
            submitted_sources: List[str] = []  # 来源溯源列表
            errors: List[str] = []
            skipped_spiders: List[str] = []

            # 3. 重置进度计数器并推送初始进度
            self._current_scanned = 0
            self._push_progress(0, 0, "正在扫描数据源...")
            # 🌟 热重载动态爬虫
            self.reload_dynamic_spiders()

            # 4. 筛选需要执行的爬虫（智能过滤）
            spiders_to_run: List[BaseSpider] = []
            for spider in self.active_spiders:
                # 订阅过滤：如果指定了 enabled_sources，且当前爬虫不在列表中，则跳过
                if (
                    enabled_sources is not None
                    and spider.SOURCE_NAME not in enabled_sources
                ):
                    continue

                requires_intranet = getattr(spider, "_requires_intranet", False)

                # 智能路由：公网环境下跳过需要校园网的爬虫
                if network_status == NetworkStatus.PUBLIC_ONLY and requires_intranet:
                    skipped_spiders.append(spider.SOURCE_NAME)
                    continue

                spiders_to_run.append(spider)

            # 5. 发送爬虫总数通知（前端初始化进度条）
            total_spiders = len(spiders_to_run)
            if spider_progress_callback and total_spiders > 0:
                try:
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
                        is_manual=is_manual,
                    )
                    futures[future] = (spider.SOURCE_NAME, idx)

                # 使用 as_completed 收集结果，并在每个完成时更新进度
                completed_count = 0
                for future in as_completed(futures):
                    source_name, idx = futures[future]
                    completed_count += 1

                    # 每个爬虫完成时推送进度
                    if spider_progress_callback:
                        try:
                            spider_progress_callback(
                                completed_count, total_spiders, source_name
                            )
                        except Exception as e:
                            logger.debug(f"爬虫进度回调失败: {e}")

                    try:
                        result_name, count, error_list = future.result()
                        submitted_count += count
                        if error_list:
                            errors.extend(error_list)
                        if count > 0:
                            submitted_sources.append(f"{result_name}({count})")
                    except Exception as e:
                        error_msg = f"[{source_name}] 爬虫执行崩溃: {e}"
                        logger.error(error_msg, exc_info=True)
                        errors.append(error_msg)

            # 5. 如果需要等待完成
            if wait_for_completion:
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
        self, spider: BaseSpider, mode: str, today_str: str, is_manual: bool
    ) -> Tuple[str, int, List[str]]:
        """
        处理单个爬虫的所有板块（异步提交，线程安全）

        包含：
        - 冷启动配额熔断（Cold Start Quota Circuit Breaker）
        - 双轨时间拦截器（Dual-Track Time Horizon Interceptor）

        Returns:
            (来源名称, 提交数量, 错误列表) 元组
        """
        submitted_count = 0
        source_name = spider.SOURCE_NAME
        error_list: List[str] = []

        # ── 冷启动状态推演 ──────────────────────────────────────────
        existing_count = db.get_article_count_by_source(source_name)
        is_cold_start = existing_count == 0

        if is_cold_start:
            # 公文通配额 10，其余学院跨板块累计 1
            quota = (
                self._COLD_START_QUOTA_GWT
                if source_name == "公文通"
                else self._COLD_START_QUOTA_COLLEGE
            )
            logger.info(f"❄️ [{source_name}] 冷启动模式，配额上限: {quota} 条")
        else:
            quota = None  # 非冷启动，不限配额

        # ── 双轨时间游标计算 ────────────────────────────────────────
        time_cutoff: Optional[datetime] = None
        if not is_cold_start:
            if mode == "today":
                # 当日追踪：游标为今日 00:00，宽容 2 小时防午夜断层
                today_dt = datetime.now().replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                time_cutoff = today_dt - __import__("datetime").timedelta(hours=2)
            elif mode == "continuous":
                # 持续追踪：游标为该来源最新文章日期
                latest_date_str = db.get_latest_article_date_by_source(source_name)
                if latest_date_str:
                    time_cutoff = _parse_date_safe(latest_date_str)

        # 获取板块列表
        sections = self._get_sections(spider)

        # 冷启动跨板块计数器
        cold_yield_count = 0

        for section_name in sections:
            if self._cancel_event.is_set():
                break

            # 冷启动配额短路：跨板块累计已达上限，终止所有剩余板块
            if is_cold_start and quota is not None and cold_yield_count >= quota:
                logger.info(
                    f"❄️ [{source_name}] 冷启动配额已满（{cold_yield_count}/{quota}），终止剩余板块"
                )
                break

            try:
                if section_name:
                    articles = spider.fetch_list(
                        page_num=1,
                        section_name=section_name,
                        limit=self._FALLBACK_LIMIT,
                    )
                else:
                    articles = spider.fetch_list(page_num=1, limit=self._FALLBACK_LIMIT)

                for article in articles:
                    if self._cancel_event.is_set():
                        break

                    # 冷启动配额短路（文章粒度）
                    if (
                        is_cold_start
                        and quota is not None
                        and cold_yield_count >= quota
                    ):
                        break

                    ctx = self.article_processor.create_context(
                        article=article,
                        source_name=source_name,
                        section_name=section_name,
                    )

                    # 标题黑名单过滤
                    should_skip, _ = self.article_processor.should_skip_by_title(
                        ctx.title
                    )
                    if should_skip:
                        logger.debug(f"跳过导航噪音（黑名单标题）: {ctx.title}")
                        continue

                    # ── 双轨时间拦截断言 ────────────────────────────
                    if time_cutoff is not None and ctx.date:
                        article_dt = _parse_date_safe(ctx.date)
                        if article_dt is not None and article_dt < time_cutoff:
                            # 列表通常按时间倒序，遇到过期文章可直接 break 当前板块
                            logger.debug(
                                f"[{source_name}] 时间拦截：{ctx.date} < {time_cutoff.date()}，终止当前板块"
                            )
                            break
                    elif not is_cold_start:
                        # 非冷启动且无时间游标时，走旧的 today 模式过滤兜底
                        if self.article_processor.should_skip_by_date(
                            ctx.date, mode, today_str
                        ):
                            continue

                    # 持续追踪模式：快速跳过已存在的 URL
                    if self.article_processor.should_skip_by_url(ctx.url, is_manual):
                        continue

                    # 线程安全地更新进度
                    with self._progress_lock:
                        self._current_scanned += 1
                        current_scanned = self._current_scanned
                    self._push_progress(current_scanned, 0, ctx.title)

                    if self.article_processor.submit(
                        spider=spider,
                        ctx=ctx,
                        mode=mode,
                        today_str=today_str,
                        is_manual=is_manual,
                    ):
                        submitted_count += 1
                        if is_cold_start:
                            cold_yield_count += 1

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
            "queue_size": self.article_processor.get_queue_size(),
        }

    def request_cancel(self):
        """🌟 外部调用：紧急终止所有爬虫任务"""
        logger.info("【3】调度器 request_cancel() 被调用，_cancel_event 即将 set")
        self._cancel_event.set()
        logger.info("【3.1】调度器 _cancel_event 已 set，所有爬虫线程应该能检测到")

    def is_cancelled(self) -> bool:
        """🌟 供外部调用的线程安全检查方法"""
        return self._cancel_event.is_set()

    def reload_dynamic_spiders(self) -> None:
        """重新加载动态爬虫（每次轮询前调用，实现热重载）"""
        # 清除现有的动态爬虫
        self.active_spiders = [
            s for s in self.active_spiders if not getattr(s, "_is_dynamic", False)
        ]
        self._load_dynamic_spiders()
