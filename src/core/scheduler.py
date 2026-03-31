"""爬虫调度器 - 负责爬虫的初始化、管理和调度执行"""

import json
import logging
import threading
import requests
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
    MusicSpider,
    RssSpider,
)
from src.spiders.dynamic_spider import DynamicSpider, create_dynamic_spider_from_rule
from src.spiders.rss_spider import RssSpider, create_rss_spider_from_rule
from src.database import db
from src.core.article_processor import ArticleProcessor, ArticleContext
from src.core.network_utils import (
    check_network_status,
    NetworkStatus,
    get_network_description,
)
from src.services.custom_spider_rules_manager import get_rules_manager
from src.utils.date_utils import parse_date_safe

if TYPE_CHECKING:
    from src.services.config_service import ConfigService

logger = logging.getLogger(__name__)


# 🌟 使用公共模块中的增强日期解析函数（兼容旧名称）
_parse_date_safe = parse_date_safe


# 爬虫注册表：(爬虫类, 板块数量, 描述, 是否需要校园网)
# 说明：这里的静态注册源均属于校内信息源，公网环境下一律跳过
# 顺序：公文通 -> 中德智能制造 -> 人工智能 -> 新材料与新能源 -> 城市交通与物流 -> 健康与环境工程 -> 工程物理 -> 药学院 -> 集成电路与光电芯片 -> 未来技术 -> 创意设计 -> 商学院 -> 外国语 -> 音乐学院
SPIDER_REGISTRY: List[Tuple[Type[BaseSpider], int, str, bool]] = [
    (GwtSpider, 1, "公文通", True),  # 公文通需要校园网
    (SgimSpider, 2, "中德智能制造学院", True),
    (AiSpider, 2, "人工智能学院", True),
    (NmneSpider, 6, "新材料与新能源学院", True),
    (UtlSpider, 2, "城市交通与物流学院", True),
    (HseeSpider, 2, "健康与环境工程学院", True),
    (CepSpider, 2, "工程物理学院", True),
    (CopSpider, 2, "药学院", True),
    (IcocSpider, 3, "集成电路与光电芯片学院", True),
    (FutureTechSpider, 6, "未来技术学院", True),
    (DesignSpider, 6, "创意设计学院", True),
    (BusinessSpider, 4, "商学院", True),
    (SflSpider, 2, "外国语学院", True),
    (MusicSpider, 3, "音乐学院", True),
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
    _WEAK_DATE_UNDATED_GRACE = 3  # 弱日期站点在连续追踪时，允许每板块前 N 条无时间文章通过
    _INCREMENTAL_REPAIR_WINDOW_HTML = 10  # 连续追踪时，HTML 源每板块顶部回补窗口
    _INCREMENTAL_REPAIR_WINDOW_RSS = 20  # 连续追踪时，RSS 源每板块顶部回补窗口
    _MANUAL_REPAIR_SCAN_LIMIT_HTML = 50  # 手动更新时，HTML 源每板块深补扫上限
    _MANUAL_REPAIR_SCAN_LIMIT_RSS = 100  # 手动更新时，RSS 源每板块深补扫上限
    _SPIDER_WATCHDOG_POLL_SECONDS = 0.5
    _SPIDER_TIMEOUT_BASE_SECONDS = 18
    _SPIDER_TIMEOUT_PER_SECTION_SECONDS = 14

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

        # 🌟 热重载优化：缓存规则文件修改时间
        self._rules_last_mtime: float = 0
        self._rules_manager = get_rules_manager()

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
        根据 source_type 选择创建 RssSpider 或 DynamicSpider。
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

                # 🌟 根据 source_type 路由到不同的爬虫
                source_type = rule_dict.get("source_type", "html")

                if source_type == "rss":
                    # RSS 订阅：使用 RssSpider
                    spider = create_rss_spider_from_rule(rule_dict)
                else:
                    # HTML 网页：使用 DynamicSpider
                    spider = create_dynamic_spider_from_rule(rule_dict)

                if spider:
                    # 标记为动态爬虫（不需要校园网）
                    setattr(spider, "_requires_intranet", False)
                    setattr(spider, "_is_dynamic", True)
                    self.active_spiders.append(spider)
                    dynamic_count += 1
                    logger.info(f"🕷️ 加载动态爬虫: {spider.SOURCE_NAME} ({source_type})")

            if dynamic_count > 0:
                logger.info(f"🕷️ 共加载 {dynamic_count} 个动态爬虫")

        except Exception as e:
            logger.error(f"加载动态爬虫规则失败: {e}")

    def _update_dynamic_rule_health(self, spider: BaseSpider) -> None:
        """将自定义动态规则的抓取结果回写到规则健康状态。"""
        if not getattr(spider, "_is_dynamic", False):
            return

        rule_id = str(
            getattr(spider, "rule_id", "") or getattr(spider, "_rule_id", "")
        ).strip()
        if not rule_id:
            return

        status = str(getattr(spider, "last_fetch_status", "") or "").strip() or "error"
        error_message = str(getattr(spider, "last_fetch_error", "") or "").strip()
        fetched_count = getattr(spider, "last_fetched_count", None)
        field_hit_stats = getattr(spider, "last_field_hit_stats", None)

        try:
            get_rules_manager().update_rule_health(
                rule_id,
                status=status,
                error_message=error_message,
                fetched_count=fetched_count if isinstance(fetched_count, int) else None,
                field_hit_stats=field_hit_stats if isinstance(field_hit_stats, dict) else None,
            )
        except Exception as e:
            logger.debug(f"回写 RSS 健康状态失败: {e}")

    def reload_dynamic_spiders(self) -> None:
        """
        重新加载动态爬虫（每次轮询前调用，实现热重载）

        🌟 优化：通过检查规则文件修改时间，仅在文件变化时执行重载，
        避免不必要的爬虫实例创建和规则解析。
        """
        import os

        # 获取规则文件路径
        rules_path = self._rules_manager._rules_path

        # 文件不存在，视为需要加载（可能首次运行）
        if not rules_path.exists():
            logger.debug("规则文件不存在，执行动态爬虫加载")
            self._do_reload_dynamic_spiders()
            return

        # 获取当前文件修改时间
        try:
            current_mtime = rules_path.stat().st_mtime
        except Exception as e:
            logger.warning(f"无法读取规则文件修改时间: {e}")
            self._do_reload_dynamic_spiders()
            return

        # 如果文件未修改，跳过重载
        if current_mtime == self._rules_last_mtime:
            logger.debug("规则文件未变化，跳过动态爬虫重载")
            return

        # 文件有变化，执行重载
        logger.info("📋 检测到规则文件变化，重新加载动态爬虫")
        self._do_reload_dynamic_spiders()
        self._rules_last_mtime = current_mtime

    def _do_reload_dynamic_spiders(self) -> None:
        """
        实际执行动态爬虫重载（清除现有动态爬虫并重新加载）
        """
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
        sections_dict = getattr(spider, "SECTIONS", None)
        if isinstance(sections_dict, dict) and sections_dict:
            return list(sections_dict.keys())

        sections_dict = getattr(spider, "sections", None)
        if isinstance(sections_dict, dict) and sections_dict:
            return list(sections_dict.keys())

        return [None]

    def _push_progress(self, completed: int, total: int, current_title: str = ""):
        """推送进度更新（通过回调）"""
        if self.progress_callback:
            try:
                self.progress_callback(completed, total, current_title)
            except Exception as e:
                logger.debug(f"进度回调执行失败: {e}")

    def _resolve_fetch_limit(self, spider: BaseSpider) -> int:
        """确定单板块本次抓取上限。"""
        if hasattr(spider, "max_items") and getattr(spider, "max_items") is not None:
            return int(getattr(spider, "max_items"))
        if getattr(spider, "_source_type", "html") == "rss":
            return 50
        return self._FALLBACK_LIMIT

    def _parse_time_cursor(self, value: Optional[str]) -> Optional[datetime]:
        """解析时间游标，尽量保留时分秒精度。"""
        if not value or not str(value).strip():
            return None

        text = (
            str(value)
            .strip()
            .replace("年", "-")
            .replace("月", "-")
            .replace("日", "")
            .replace("/", "-")
            .replace("T", " ")
        )
        text = " ".join(text.split())

        try:
            from dateutil import parser

            return parser.parse(text, fuzzy=False)
        except ImportError:
            pass
        except Exception:
            pass

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y-%m-%d %H:%M:%S.%f",
        ):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue

        return _parse_date_safe(text)

    def _resolve_dynamic_page_budget(
        self,
        spider: BaseSpider,
        is_cold_start: bool,
        *,
        is_manual: bool = False,
    ) -> Optional[int]:
        if (
            getattr(spider, "_source_type", "html") != "html"
            or not getattr(spider, "_is_dynamic", False)
        ):
            return None

        pagination_mode = str(
            getattr(spider, "pagination_mode", "single") or "single"
        ).strip().lower()
        if pagination_mode == "single":
            return 1

        max_pages = max(int(getattr(spider, "max_pages", 1) or 1), 1)
        if is_cold_start or is_manual:
            return max_pages

        incremental_max_pages = max(
            int(getattr(spider, "incremental_max_pages", 1) or 1),
            1,
        )
        return min(max_pages, incremental_max_pages)

    def _resolve_section_scan_limit(
        self,
        spider: BaseSpider,
        *,
        mode: str,
        is_cold_start: bool,
        is_manual: bool,
    ) -> int:
        """确定单板块本次实际扫描深度。"""
        base_limit = self._resolve_fetch_limit(spider)
        if is_cold_start or mode != "continuous" or not is_manual:
            return base_limit

        source_type = getattr(spider, "_source_type", "html")
        deep_limit = (
            self._MANUAL_REPAIR_SCAN_LIMIT_RSS
            if source_type == "rss"
            else self._MANUAL_REPAIR_SCAN_LIMIT_HTML
        )
        return max(base_limit, deep_limit)

    def _resolve_incremental_repair_window(
        self,
        spider: BaseSpider,
        fetch_limit: int,
    ) -> int:
        """连续追踪时，为物理删除/漏抓留出的顶部回补窗口。"""
        source_type = getattr(spider, "_source_type", "html")
        base_window = (
            self._INCREMENTAL_REPAIR_WINDOW_RSS
            if source_type == "rss"
            else self._INCREMENTAL_REPAIR_WINDOW_HTML
        )
        return max(0, min(max(fetch_limit, 0), base_window))

    @staticmethod
    def _normalize_section_key(section_name: Optional[str]) -> str:
        return str(section_name or "").strip()

    def _record_cold_start_frontiers(
        self, selected_candidates: List[Dict[str, Any]]
    ) -> None:
        """记录冷启动首批入库文章的板块边界，避免后续手动更新倒灌旧文。"""
        frontier_map: Dict[Tuple[str, str], ArticleContext] = {}
        for candidate in selected_candidates:
            ctx = candidate.get("ctx")
            if not isinstance(ctx, ArticleContext) or not ctx.url:
                continue
            source_name = str(ctx.source_name or "").strip()
            if not source_name:
                continue
            section_key = self._normalize_section_key(candidate.get("section_name"))
            frontier_map[(source_name, section_key)] = ctx

        for (source_name, section_key), ctx in frontier_map.items():
            db.upsert_crawl_frontier(
                source_name=source_name,
                section_name=section_key,
                frontier_url=ctx.url,
                frontier_cursor=str(ctx.date or "").strip(),
            )

    def _fetch_section_articles(
        self,
        spider: BaseSpider,
        section_name: Optional[str],
        fetch_limit: int,
        *,
        page_budget: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """抓取单个板块的候选文章列表。"""
        if section_name:
            return spider.fetch_list(
                page_num=1,
                section_name=section_name,
                limit=fetch_limit,
                page_budget=page_budget,
                cancel_event=self._cancel_event,
            )
        return spider.fetch_list(
            page_num=1,
            limit=fetch_limit,
            page_budget=page_budget,
            cancel_event=self._cancel_event,
        )

    def _prefetch_detail_cursor(
        self, spider: BaseSpider, ctx: ArticleContext
    ) -> Optional[datetime]:
        """为时间裁剪预取详情页时间，并复用详情避免重复抓取。"""
        if ctx.detail is None:
            try:
                ctx.detail = spider.fetch_detail(ctx.url)
            except Exception as e:
                logger.debug(f"[{ctx.source_name}] 预取详情时间失败 ({ctx.title}): {e}")
                ctx.detail = None

        if not ctx.detail:
            return None

        detail = ctx.detail
        detail_cursor = (
            str(detail.get("exact_time") or "").strip()
            or str(detail.get("date") or "").strip()
        )
        parsed = self._parse_time_cursor(detail_cursor)
        if parsed is not None and not ctx.date:
            ctx.date = parsed.strftime("%Y-%m-%d")
        return parsed

    def _submit_context(
        self,
        spider: BaseSpider,
        ctx: ArticleContext,
        mode: str,
        today_str: str,
        is_manual: bool,
    ) -> bool:
        """提交文章到处理队列，并同步进度。"""
        with self._progress_lock:
            self._current_scanned += 1
            current_scanned = self._current_scanned
        self._push_progress(current_scanned, 0, ctx.title)

        return self.article_processor.submit(
            spider=spider,
            ctx=ctx,
            mode=mode,
            today_str=today_str,
            is_manual=is_manual,
        )

    def _collect_cold_start_candidates(
        self,
        spider: BaseSpider,
        sections: List[Optional[str]],
        fetch_limit: int,
        page_budget: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """冷启动时先跨板块收集候选，再按全来源最新程度裁剪。"""
        source_name = spider.SOURCE_NAME
        candidates: List[Dict[str, Any]] = []
        error_list: List[str] = []
        seen_urls: set[str] = set()
        encounter_order = 0

        for section_name in sections:
            if self._cancel_event.is_set():
                break

            try:
                articles = self._fetch_section_articles(
                    spider,
                    section_name,
                    fetch_limit,
                    page_budget=page_budget,
                )
                self._update_dynamic_rule_health(spider)

                for article in articles:
                    ctx = self.article_processor.create_context(
                        article=article,
                        source_name=source_name,
                        section_name=section_name,
                    )

                    should_skip, _ = self.article_processor.should_skip_by_title(
                        ctx.title
                    )
                    if should_skip or not ctx.url or ctx.url in seen_urls:
                        continue

                    seen_urls.add(ctx.url)
                    candidates.append(
                        {
                            "ctx": ctx,
                            "section_name": section_name,
                            "article_dt": self._parse_time_cursor(ctx.date),
                            "order": encounter_order,
                        }
                    )
                    encounter_order += 1
            except (
                requests.RequestException,
                ConnectionError,
                TimeoutError,
                ValueError,
            ) as e:
                section_label = f"板块 '{section_name}'" if section_name else "默认板块"
                error_msg = f"[{source_name}] {section_label} 抓取异常: {e}"
                logger.warning(error_msg)
                error_list.append(error_msg)

        candidates.sort(key=lambda item: item["order"])
        candidates.sort(
            key=lambda item: item["article_dt"] or datetime.min,
            reverse=True,
        )
        return candidates, error_list

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
            executor = ThreadPoolExecutor(max_workers=5)
            try:
                futures = {}
                future_deadlines = {}
                pending = set()

                for idx, spider in enumerate(spiders_to_run):
                    future = executor.submit(
                        self._process_spider,
                        spider=spider,
                        mode=mode,
                        today_str=today_str,
                        is_manual=is_manual,
                    )
                    futures[future] = (spider.SOURCE_NAME, idx, spider)
                    future_deadlines[future] = (
                        time.monotonic() + self._resolve_spider_timeout(spider)
                    )
                    pending.add(future)

                completed_count = 0
                while pending:
                    done, still_pending = wait(
                        pending,
                        timeout=self._SPIDER_WATCHDOG_POLL_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )

                    if not done:
                        now = time.monotonic()
                        timed_out = [
                            future
                            for future in list(still_pending)
                            if future_deadlines.get(future, float("inf")) <= now
                        ]

                        if not timed_out:
                            pending = still_pending
                            continue

                        for future in timed_out:
                            source_name, idx, spider = futures[future]
                            pending.discard(future)
                            completed_count += 1

                            if spider_progress_callback:
                                try:
                                    spider_progress_callback(
                                        completed_count, total_spiders, source_name
                                    )
                                except Exception as e:
                                    logger.debug(f"爬虫进度回调失败: {e}")

                            timeout_seconds = int(
                                round(self._resolve_spider_timeout(spider))
                            )
                            future.cancel()
                            error_msg = (
                                f"[{source_name}] 抓取耗时超过 {timeout_seconds} 秒，"
                                "已跳过该来源"
                            )
                            logger.warning(error_msg)
                            errors.append(error_msg)

                        continue

                    for future in done:
                        pending.discard(future)
                        source_name, idx, spider = futures[future]
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
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

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
        source_type = getattr(spider, "_source_type", "html")
        error_list: List[str] = []

        # ── 冷启动状态推演 ──────────────────────────────────────────
        existing_count = db.get_article_count_by_source(source_name)
        is_cold_start = existing_count == 0

        if is_cold_start:
            if source_type == 'rss':
                # RSS 订阅：不设配额限制，抓取所有条目
                quota = None
                logger.info(f"📡 [{source_name}] RSS 订阅冷启动，不限配额")
            else:
                # HTML 爬虫：使用原有配额
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
                # 持续追踪：优先使用精确时间游标，避免同日历史内容回灌
                latest_cursor_str = db.get_latest_article_cursor_by_source(source_name)
                if latest_cursor_str:
                    time_cutoff = self._parse_time_cursor(latest_cursor_str)

        # 获取板块列表
        sections = self._get_sections(spider)
        fetch_limit = self._resolve_fetch_limit(spider)
        default_page_budget = self._resolve_dynamic_page_budget(spider, is_cold_start)
        deep_scan_limit = self._resolve_section_scan_limit(
            spider,
            mode=mode,
            is_cold_start=is_cold_start,
            is_manual=is_manual,
        )

        if is_cold_start and quota is not None:
            candidates, error_list = self._collect_cold_start_candidates(
                spider=spider,
                sections=sections,
                fetch_limit=fetch_limit,
                page_budget=default_page_budget,
            )
            selected_candidates = candidates[:quota]

            if selected_candidates:
                logger.info(
                    f"❄️ [{source_name}] 冷启动候选 {len(candidates)} 条，按全来源最新排序后入库 {len(selected_candidates)} 条"
                )
                self._record_cold_start_frontiers(selected_candidates)

            for candidate in selected_candidates:
                if self._cancel_event.is_set():
                    break

                ctx: ArticleContext = candidate["ctx"]
                if self._submit_context(
                    spider=spider,
                    ctx=ctx,
                    mode=mode,
                    today_str=today_str,
                    is_manual=is_manual,
                ):
                    submitted_count += 1

            return (source_name, submitted_count, error_list)

        seen_urls_in_run: set[str] = set()

        for section_name in sections:
            if self._cancel_event.is_set():
                break

            section_key = self._normalize_section_key(section_name)
            manual_frontier_url = (
                db.get_crawl_frontier_url(source_name, section_key)
                if (is_manual and not is_cold_start and mode == "continuous")
                else ""
            )
            allow_manual_deep_repair = bool(manual_frontier_url)
            section_scan_limit = (
                deep_scan_limit if allow_manual_deep_repair else fetch_limit
            )
            section_page_budget = default_page_budget
            if getattr(spider, "_is_dynamic", False):
                section_page_budget = self._resolve_dynamic_page_budget(
                    spider,
                    is_cold_start,
                    is_manual=allow_manual_deep_repair,
                )

            repair_window = (
                self._resolve_incremental_repair_window(spider, section_scan_limit)
                if (
                    not is_cold_start
                    and mode == "continuous"
                    and not allow_manual_deep_repair
                )
                else 0
            )
            section_unique_index = 0
            manual_frontier_reached = False
            undated_grace_remaining = (
                self._WEAK_DATE_UNDATED_GRACE
                if (not is_cold_start and mode == "continuous" and time_cutoff is not None)
                else 0
            )

            try:
                articles = self._fetch_section_articles(
                    spider,
                    section_name,
                    section_scan_limit,
                    page_budget=section_page_budget,
                )
                self._update_dynamic_rule_health(spider)

                for article in articles:
                    if self._cancel_event.is_set():
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

                    if not ctx.url or ctx.url in seen_urls_in_run:
                        continue
                    seen_urls_in_run.add(ctx.url)
                    section_unique_index += 1
                    auto_repair_window = (
                        not is_cold_start
                        and mode == "continuous"
                        and repair_window > 0
                        and section_unique_index <= repair_window
                    )
                    is_manual_frontier_article = (
                        allow_manual_deep_repair and ctx.url == manual_frontier_url
                    )
                    in_manual_repair_range = (
                        allow_manual_deep_repair and not manual_frontier_reached
                    )
                    in_repair_window = auto_repair_window or in_manual_repair_range

                    # 持续追踪模式：
                    # - 顶部回补窗口内：继续检查是否存在物理删除/漏抓的缺口文章
                    # - 回补窗口外：命中已存在 URL 视为触达当前板块的增量边界
                    if (
                        not is_cold_start
                        and mode == "continuous"
                        and self.article_processor.should_skip_by_url(ctx.url, is_manual)
                    ):
                        if is_manual_frontier_article:
                            logger.debug(
                                "[%s] 命中手动修复边界，结束当前板块缺口检查: %s",
                                source_name,
                                ctx.url,
                            )
                            manual_frontier_reached = True
                            break
                        if in_repair_window:
                            logger.debug(
                                "[%s] 命中已存在 URL，但仍处于顶部回补窗口，继续检查缺口: %s",
                                source_name,
                                ctx.url,
                            )
                            continue
                        logger.debug(
                            f"[{source_name}] 命中已存在 URL，终止当前板块继续回溯: {ctx.url}"
                        )
                        break

                    # ── 双轨时间拦截断言 ────────────────────────────
                    if time_cutoff is not None:
                        article_dt = self._parse_time_cursor(ctx.date)
                        has_list_date = bool(ctx.date)
                        needs_detail_cursor = article_dt is None

                        if (
                            not needs_detail_cursor
                            and article_dt is not None
                            and article_dt.date() == time_cutoff.date()
                            and article_dt.time() == datetime.min.time()
                            and time_cutoff.time() != datetime.min.time()
                        ):
                            needs_detail_cursor = True

                        if needs_detail_cursor:
                            detail_dt = self._prefetch_detail_cursor(spider, ctx)
                            if detail_dt is not None:
                                article_dt = detail_dt

                        if (
                            article_dt is not None
                            and article_dt <= time_cutoff
                            and not in_repair_window
                        ):
                            # 有列表日期时可视作当前板块已进入旧内容区，直接停止继续扫描
                            if has_list_date:
                                logger.debug(
                                    f"[{source_name}] 时间拦截：{ctx.date} <= {time_cutoff}，终止当前板块"
                                )
                                break
                            logger.debug(
                                f"[{source_name}] 详情时间拦截：{ctx.title} <= {time_cutoff}，跳过文章"
                            )
                            continue

                        if article_dt is None and not is_cold_start:
                            if undated_grace_remaining > 0:
                                undated_grace_remaining -= 1
                                logger.debug(
                                    "[%s] 弱日期站点回退：未解析到时间，允许继续提交 (%s)，本板块剩余额度=%d",
                                    source_name,
                                    ctx.title,
                                    undated_grace_remaining,
                                )
                            else:
                                logger.debug(
                                    f"[{source_name}] 无法解析时间游标，保守跳过: {ctx.title}"
                                )
                                continue
                    elif not is_cold_start and time_cutoff is None:
                        # 非冷启动且无时间游标时，走旧的 today 模式过滤兜底
                        if self.article_processor.should_skip_by_date(
                            ctx.date, mode, today_str
                        ):
                            continue

                    if self._submit_context(
                        spider=spider,
                        ctx=ctx,
                        mode=mode,
                        today_str=today_str,
                        is_manual=is_manual,
                    ):
                        submitted_count += 1
                    if is_manual_frontier_article:
                        manual_frontier_reached = True
                        break

            except (requests.RequestException, ConnectionError, TimeoutError, ValueError) as e:
                if getattr(spider, "_is_dynamic", False):
                    try:
                        get_rules_manager().update_rule_health(
                            str(
                                getattr(spider, "rule_id", "")
                                or getattr(spider, "_rule_id", "")
                            ).strip(),
                            status="error",
                            error_message=str(e),
                            fetched_count=0,
                            field_hit_stats=None,
                        )
                    except Exception:
                        pass
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

    def _resolve_spider_timeout(self, spider: BaseSpider) -> float:
        """根据板块数量估算单来源的最长允许执行时间。"""
        section_count = max(1, len(self._get_sections(spider)))
        return float(
            self._SPIDER_TIMEOUT_BASE_SECONDS
            + section_count * self._SPIDER_TIMEOUT_PER_SECTION_SECONDS
        )

    def request_cancel(self):
        """🌟 外部调用：紧急终止所有爬虫任务"""
        logger.info("【3】调度器 request_cancel() 被调用，_cancel_event 即将 set")
        self._cancel_event.set()
        logger.info("【3.1】调度器 _cancel_event 已 set，所有爬虫线程应该能检测到")

    def is_cancelled(self) -> bool:
        """🌟 供外部调用的线程安全检查方法"""
        return self._cancel_event.is_set()
