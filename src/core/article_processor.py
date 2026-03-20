"""文章处理管道 - 异步生产者-消费者架构"""

import json
import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.spiders import BaseSpider

logger = logging.getLogger(__name__)


@dataclass
class ArticleContext:
    """文章处理上下文"""
    url: str
    title: str
    date: str
    source_name: str
    section_name: Optional[str] = None
    raw_text: str = ""
    detail: Optional[Dict[str, Any]] = None
    category: str = ""
    department: str = ""


@dataclass
class ProcessingTask:
    """处理任务"""
    spider: 'BaseSpider'
    ctx: ArticleContext
    mode: str
    today_str: str
    is_manual: bool


# 回调类型定义
OnArticleProcessedCallback = Callable[[Dict[str, Any]], None]  # (article_data)


class ArticleProcessor:
    """
    文章处理管道 - 异步生产者-消费者架构

    架构设计：
    ┌─────────────┐      ┌──────────────┐      ┌─────────────┐
    │  Producer   │ ───▶ │  Task Queue  │ ───▶ │   Workers   │
    │ (scheduler) │      │  (threadsafe)│      │  (4 线程)   │
    └─────────────┘      └──────────────┘      └─────────────┘
                                                      │
                                                      ▼
                                               ┌─────────────┐
                                               │  Database   │
                                               │ (异步写队列) │
                                               └─────────────┘

    数据库架构（详见 src/database.py）：
    - 读操作：ConnectionPool (3 连接并发读)
    - 写操作：WriteQueue (单线程串行写，WAL 模式)
    """

    # 标题黑名单：过滤导航噪音
    TITLE_BLACKLIST = [
    "EN", "English", "学院首页", "返回顶部", "网站地图",
    "领导团队", "师资队伍", "教研团队", "实践团队", "客座教授",
    "学院简介", "学院概况", "现任领导", "组织机构", "规章制度", "联系我们"
]

    # 最小内容长度（字符数）
    MIN_CONTENT_LENGTH = 10

    # AI 失败标识
    AI_FAILURE_PREFIXES = ("❌", "⏳", "⚠️")

    # Worker 配置
    WORKER_COUNT = 4

    def __init__(
        self,
        llm_service,
        database,
        on_task_complete: Optional[Callable[[bool, str, Optional[Dict]], None]] = None,
        on_article_processed: Optional[OnArticleProcessedCallback] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None  # 🌟 新增：AI 进度回调
    ):
        """
        初始化文章处理器

        Args:
            llm_service: LLM 服务实例
            database: 数据库管理器实例
            on_task_complete: 任务完成回调 (success, reason, article_data)
            on_article_processed: 单篇文章处理成功回调 (title, summary_preview, source_name)
            on_progress: AI 进度回调 (completed, total, current_title)
        """
        self.llm = llm_service
        self.db = database
        self.on_task_complete = on_task_complete
        self.on_article_processed = on_article_processed
        self.on_progress = on_progress  # 🌟 新增

        # 任务队列（线程安全）
        self._task_queue: queue.Queue[Optional[ProcessingTask]] = queue.Queue()

        # Worker 线程池
        self._executor = ThreadPoolExecutor(
            max_workers=self.WORKER_COUNT,
            thread_name_prefix="ArticleWorker"
        )

        # 控制标志
        self._shutdown_event = threading.Event()

        # 统计信息（线程安全）
        self._stats_lock = threading.Lock()
        self._stats = {
            'submitted': 0,
            'processed': 0,
            'success': 0,
            'failed': 0
        }

        # 启动 Workers
        self._start_workers()

        logger.info(f"🚀 ArticleProcessor 已启动，Worker 数量: {self.WORKER_COUNT}")

    def _start_workers(self):
        """启动 Worker 线程"""
        for i in range(self.WORKER_COUNT):
            self._executor.submit(self._worker_loop, i)

    def _worker_loop(self, worker_id: int):
        """
        Worker 主循环：从队列获取任务并处理

        Args:
            worker_id: Worker 编号（用于日志）
        """
        logger.debug(f"Worker #{worker_id} 已启动")

        while not self._shutdown_event.is_set():
            try:
                # 从队列获取任务（带超时，便于检查 shutdown 信号）
                try:
                    task = self._task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # None 是哨兵值，表示退出
                if task is None:
                    break

                # 处理任务
                try:
                    success, reason, article_data = self._process_task(task)

                    # 更新统计
                    with self._stats_lock:
                        self._stats['processed'] += 1
                        if success:
                            self._stats['success'] += 1
                        else:
                            self._stats['failed'] += 1
                        # 🌟 获取当前进度用于回调
                        current_processed = self._stats['processed']
                        total_submitted = self._stats['submitted']

                    # 🌟 推送 AI 进度（每处理完一篇文章）
                    if self.on_progress and total_submitted > 0:
                        try:
                            title = article_data.get('title', '') if article_data else ''
                            self.on_progress(current_processed, total_submitted, title)
                        except Exception as e:
                            logger.warning(f"进度回调执行失败: {e}")

                    # 回调通知
                    if self.on_task_complete:
                        try:
                            self.on_task_complete(success, reason, article_data)
                        except Exception as e:
                            logger.warning(f"任务完成回调执行失败: {e}")

                except Exception as e:
                    logger.error(f"Worker #{worker_id} 处理任务异常: {e}")
                    with self._stats_lock:
                        self._stats['processed'] += 1
                        self._stats['failed'] += 1

                finally:
                    self._task_queue.task_done()

            except Exception as e:
                logger.error(f"Worker #{worker_id} 循环异常: {e}")

        logger.debug(f"Worker #{worker_id} 已退出")

    def _process_task(self, task: ProcessingTask) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        处理单个任务（Worker 线程内执行）

        Args:
            task: 处理任务

        Returns:
            (success, reason, article_data)
        """
        spider = task.spider
        ctx = task.ctx

        # 1. 获取详情
        try:
            detail = spider.fetch_detail(ctx.url)
            if not detail:
                logger.debug(f"[{ctx.source_name}] 详情获取失败: {ctx.title}")
                return False, "detail_failed", None
            ctx.detail = detail
        except Exception as e:
            logger.warning(f"[{ctx.source_name}] 详情获取异常 ({ctx.title}): {e}")
            return False, "detail_error", None

        # 2. 提取正文
        raw_text = detail.get('body_text', '')
        if not raw_text:
            raw_text = detail.get('body_html', '')
        ctx.raw_text = raw_text

        # 3. 内容长度校验
        if not self.validate_content_length(raw_text):
            logger.debug(f"跳过疑似噪音内容（长度不足）: {ctx.title}")
            return False, "content_too_short", None

        # 4. 检查是否新/更新
        is_changed, reason = self.db.check_if_new_or_updated(ctx.url, raw_text)
        if not is_changed:
            return False, "unchanged", None

        # 5. AI 生成摘要（此处已有指数退避重试）
        logger.info(f"[{ctx.source_name}] 发现变动 ({reason}) -> {ctx.title}")
        try:
            summary = self.llm.summarize_article(ctx.title, raw_text)
        except Exception as e:
            logger.warning(f"AI 摘要生成异常 ({ctx.title}): {e}")
            return False, "ai_error", None

        # 6. 核心防线：拦截 AI 失败的情况
        if summary.startswith(self.AI_FAILURE_PREFIXES):
            logger.warning(f"AI 分析失败，本次不入库: {ctx.title}")
            return False, "ai_failed", None

        # 7. 添加时间戳（已禁用）
        # timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # summary = summary + f"\n\n---\n**🤖 AI 生成时间: {timestamp}**"

        # 8. 处理附件（强制初始化为空列表）
        attachments_data = detail.get('attachments') or []
        if not isinstance(attachments_data, list):
            attachments_data = []
        attachments_json = json.dumps(attachments_data, ensure_ascii=False)

        # 9. 入库（🌟 异步写入：通过写队列串行化，不阻塞当前 Worker）
        self.db.insert_or_update_article(
            title=ctx.title,
            url=ctx.url,
            date=ctx.date,
            exact_time=detail.get('exact_time', ''),
            category=ctx.category or ctx.section_name or '未知类别',
            department=ctx.department or detail.get('department', ''),
            attachments=attachments_json,
            summary=summary,
            raw_content=raw_text,
            source_name=ctx.source_name
        )

        logger.info(f"✅ [{ctx.source_name}] 文章入库成功: {ctx.title}")

        # 10. 构建完整的文章数据并触发回调
        article_data = {
            'title': ctx.title,
            'url': ctx.url,
            'date': ctx.date,
            'source_name': ctx.source_name,
            'category': ctx.category or ctx.section_name or '未知类别',
            'department': ctx.department or detail.get('department', ''),
            'exact_time': detail.get('exact_time', ''),
            'attachments': attachments_data if isinstance(attachments_data, list) else [],
            'summary': summary,
            'raw_content': raw_text,
            'is_read': 0
        }

        if self.on_article_processed:
            try:
                self.on_article_processed(article_data)
            except Exception as e:
                logger.warning(f"文章处理回调执行失败: {e}")

        # 11. 返回成功信息
        return True, reason, article_data

    def submit(
        self,
        spider: 'BaseSpider',
        ctx: ArticleContext,
        mode: str = 'continuous',
        today_str: str = "",
        is_manual: bool = False
    ) -> bool:
        """
        提交文章处理任务（异步，立即返回）

        Args:
            spider: 爬虫实例
            ctx: 文章上下文
            mode: 追踪模式
            today_str: 今日日期字符串
            is_manual: 是否手动触发

        Returns:
            是否成功提交到队列
        """
        if self._shutdown_event.is_set():
            logger.warning("ArticleProcessor 已关闭，拒绝新任务")
            return False

        task = ProcessingTask(
            spider=spider,
            ctx=ctx,
            mode=mode,
            today_str=today_str,
            is_manual=is_manual
        )

        try:
            self._task_queue.put(task, block=False)
            with self._stats_lock:
                self._stats['submitted'] += 1
            return True
        except queue.Full:
            logger.warning(f"任务队列已满，拒绝任务: {ctx.title}")
            return False

    def process(
        self,
        spider: 'BaseSpider',
        ctx: ArticleContext,
        mode: str,
        today_str: str,
        is_manual: bool
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        同步处理单篇文章（保留向后兼容，但标记为已废弃）

        ⚠️ 此方法现在会阻塞等待处理完成，建议使用 submit() 进行异步处理

        Args:
            spider: 爬虫实例
            ctx: 文章上下文
            mode: 追踪模式
            today_str: 今日日期字符串
            is_manual: 是否手动触发

        Returns:
            (success: bool, reason: str, article_data: Optional[dict])
        """
        # 为了向后兼容，仍然提供同步处理能力
        return self._process_task(ProcessingTask(
            spider=spider,
            ctx=ctx,
            mode=mode,
            today_str=today_str,
            is_manual=is_manual
        ))

    def should_skip_by_title(self, title: str) -> Tuple[bool, str]:
        """根据标题判断是否应该跳过"""
        if title.strip() in self.TITLE_BLACKLIST:
            return True, "blacklisted"
        return False, ""

    def should_skip_by_date(self, date: str, mode: str, today_str: str) -> bool:
        """根据日期判断是否应该跳过（当日追踪模式）"""
        if mode == 'today' and date:
            normalized_date = date.replace('/', '-').split()[0] if date else ''
            if normalized_date != today_str:
                return True
        return False

    def should_skip_by_url(self, url: str, is_manual: bool) -> bool:
        """根据 URL 判断是否应该跳过（严格依据本地数据库差异比对）"""
        if self.db.check_if_url_exists(url):
            return True
        return False

    def validate_content_length(self, raw_text: str) -> bool:
        """校验内容长度"""
        cleaned = raw_text.replace(' ', '').replace('\n', '')
        return len(cleaned) >= self.MIN_CONTENT_LENGTH

    def create_context(
        self,
        article: Dict[str, Any],
        source_name: str,
        section_name: Optional[str] = None
    ) -> ArticleContext:
        """从文章字典创建处理上下文"""
        # 部门逻辑重构：
        # - 公文通：保持原有的发文单位（部门）提取逻辑
        # - 学院/中心：强制 department = source_name
        department = article.get('department', '')
        if source_name != "公文通" and ("学院" in source_name or "中心" in source_name):
            department = source_name

        return ArticleContext(
            url=article.get('url', ''),
            title=article.get('title', '未知标题'),
            date=article.get('date', ''),
            source_name=source_name,
            section_name=section_name,
            category=article.get('category', ''),
            department=department
        )

    def get_stats(self) -> Dict[str, int]:
        """获取处理统计信息"""
        with self._stats_lock:
            return dict(self._stats)

    def get_queue_size(self) -> int:
        """获取当前队列大小"""
        return self._task_queue.qsize()

    def wait_completion(self, timeout: Optional[float] = None) -> bool:
        """
        等待所有任务处理完成

        Args:
            timeout: 超时时间（秒），None 表示无限等待

        Returns:
            是否在超时前完成所有任务
        """
        try:
            self._task_queue.join()
            return True
        except Exception:
            return False

    def shutdown(self, wait: bool = True):
        """
        关闭处理器

        Args:
            wait: 是否等待所有任务完成
        """
        logger.info("正在关闭 ArticleProcessor...")
        self._shutdown_event.set()

        # 发送哨兵值通知所有 Worker 退出
        for _ in range(self.WORKER_COUNT):
            self._task_queue.put(None)

        if wait:
            self._executor.shutdown(wait=True)
        else:
            self._executor.shutdown(wait=False)

        logger.info(f"ArticleProcessor 已关闭，最终统计: {self.get_stats()}")