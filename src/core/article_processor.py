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
    require_ai_summary: bool = True  # 🌟 是否需要 AI 摘要（动态爬虫可设置为 False）
    custom_summary_prompt: str = ""  # 🌟 专属 AI 提示词（用于定制摘要输出格式）


@dataclass
class ProcessingTask:
    """处理任务"""

    spider: "BaseSpider"
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
        "EN",
        "English",
        "学院首页",
        "返回顶部",
        "网站地图",
        "领导团队",
        "师资队伍",
        "教研团队",
        "实践团队",
        "客座教授",
        "学院简介",
        "学院概况",
        "现任领导",
        "组织机构",
        "规章制度",
        "联系我们",
    ]

    # 最小内容长度（字符数）
    MIN_CONTENT_LENGTH = 10

    # AI 失败标识
    AI_FAILURE_PREFIXES = ("❌", "⏳", "⚠️")

    # 纯图片内容默认摘要
    PURE_IMAGE_SUMMARY = ""

    # Worker 配置
    WORKER_COUNT = 1

    def __init__(
        self,
        llm_service,
        database,
        on_task_complete: Optional[Callable[[bool, str, Optional[Dict]], None]] = None,
        on_article_processed: Optional[OnArticleProcessedCallback] = None,
        on_progress: Optional[
            Callable[[int, int, str], None]
        ] = None,  # 🌟 新增：AI 进度回调
        config_service=None,  # 📧 邮件推送配置服务
    ):
        """
        初始化文章处理器

        Args:
            llm_service: LLM 服务实例
            database: 数据库管理器实例
            on_task_complete: 任务完成回调 (success, reason, article_data)
            on_article_processed: 单篇文章处理成功回调 (title, summary_preview, source_name)
            on_progress: AI 进度回调 (completed, total, current_title)
            config_service: 配置服务实例（用于邮件推送）
        """
        self.llm = llm_service
        self.db = database
        self.on_task_complete = on_task_complete
        self.on_article_processed = on_article_processed
        self.on_progress = on_progress  # 🌟 新增
        self.config_service = config_service  # 📧 邮件推送配置服务

        # 任务队列（线程安全）
        self._task_queue: queue.Queue[Optional[ProcessingTask]] = queue.Queue()

        # Worker 线程池
        self._executor = ThreadPoolExecutor(
            max_workers=self.WORKER_COUNT, thread_name_prefix="ArticleWorker"
        )

        # 控制标志
        self._shutdown_event = threading.Event()

        # 🌟 取消标志（用于用户主动终止 AI 任务）
        self._cancel_requested = False
        self._cancel_lock = threading.Lock()

        # 统计信息（线程安全）
        self._stats_lock = threading.Lock()
        self._stats = {
            "submitted": 0,  # 提交到队列的任务数
            "processed": 0,  # 已处理完成的任务数
            "success": 0,  # 成功入库的任务数
            "failed": 0,  # 失败的任务数
            "ai_total": 0,  # 🌟 新增：真正需要调用 AI 的任务数
            "ai_completed": 0,  # 🌟 新增：AI 调用完成的任务数
        }

        # 启动 Workers
        self._start_workers()

        logger.info(f"🚀 ArticleProcessor 已启动，Worker 数量: {self.WORKER_COUNT}")

    # ==================== 取消控制 ====================

    def request_cancel(self) -> None:
        """请求取消所有待处理的AI任务"""
        with self._cancel_lock:
            self._cancel_requested = True
        logger.info("🛑 已请求取消所有 AI 任务")

    def clear_cancel(self) -> None:
        """清除取消标志（用于新的一轮任务）"""
        with self._cancel_lock:
            self._cancel_requested = False

    def is_cancel_requested(self) -> bool:
        """检查是否请求了取消"""
        with self._cancel_lock:
            return self._cancel_requested

    # ==================== Worker 管理 ====================

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
            # ❌ 已删除：最开头的 if self.is_cancel_requested(): break
            # 绝不能在这里 break，否则会导致线程死亡

            try:
                # 从队列获取任务（带超时，这是线程保持监听的核心）
                try:
                    task = self._task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # None 是哨兵值，表示程序真的要彻底关闭了（走 shutdown 逻辑）
                if task is None:
                    break

                # 🌟 核心修复：检查取消状态。如果用户点击了取消，充当"黑洞"瞬间清空队列
                if self.is_cancel_requested():
                    logger.info(
                        f"Worker #{worker_id} 处于取消状态，快速丢弃任务: {task.ctx.title if task else 'None'}"
                    )
                    self._task_queue.task_done()
                    continue  # ✅ 关键：使用 continue 而不是 break！丢弃任务后回去继续监听队列！

                # 处理任务（正常流程）
                try:
                    success, reason, article_data = self._process_task(task)

                    # 更新统计
                    with self._stats_lock:
                        self._stats["processed"] += 1
                        if success:
                            self._stats["success"] += 1
                        else:
                            self._stats["failed"] += 1

                    # 回调通知
                    if self.on_task_complete:
                        try:
                            self.on_task_complete(success, reason, article_data)
                        except Exception as e:
                            logger.warning(f"任务完成回调执行失败: {e}")

                except Exception as e:
                    logger.error(f"Worker #{worker_id} 处理任务异常: {e}")
                    with self._stats_lock:
                        self._stats["processed"] += 1
                        self._stats["failed"] += 1

                finally:
                    # 无论成功失败，必须标记任务完成，防止队列阻塞
                    self._task_queue.task_done()

            except Exception as e:
                logger.error(f"Worker #{worker_id} 循环异常: {e}")

        logger.debug(f"Worker #{worker_id} 已安全退出")

    def _process_task(
        self, task: ProcessingTask
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        处理单个任务（Worker 线程内执行）

        Args:
            task: 处理任务

        Returns:
            (success, reason, article_data)
        """
        spider = task.spider
        ctx = task.ctx

        # 🌟 获取数据源类型（用于分流处理）
        source_type = getattr(spider, '_source_type', 'html')
        # 🌟 获取跳过详情配置（仅 HTML 爬虫有效）
        skip_detail = getattr(spider, 'skip_detail', False)

        # 1. 获取详情
        # 🌟 根据数据源类型和配置决定是否抓取详情
        if source_type == 'rss':
            # RSS 订阅：列表中已包含完整内容，无需抓取详情
            detail = {
                'title': ctx.title,
                'url': ctx.url,
                'body_text': ctx.raw_text,
                'body_html': '',
                'images': [],
                'attachments': [],
                'exact_time': '',
                'dynamic_fields': {}
            }
            logger.debug(f"[{ctx.source_name}] RSS 订阅，跳过详情抓取: {ctx.title[:30]}...")
        elif source_type == 'html' and skip_detail:
            # HTML 动态爬虫且配置了 skip_detail：使用列表页内容，跳过详情抓取
            detail = {
                'title': ctx.title,
                'url': ctx.url,
                'body_text': ctx.raw_text,
                'body_html': '',
                'images': [],
                'attachments': [],
                'exact_time': '',
                'dynamic_fields': getattr(spider, 'field_selectors', {})
            }
            logger.debug(f"[{ctx.source_name}] HTML 动态爬虫配置了 skip_detail，跳过详情抓取: {ctx.title[:30]}...")
        elif ctx.raw_text and len(ctx.raw_text.strip()) >= 10:
            # HTML 动态爬虫：列表中可能已包含内容
            detail = {
                'title': ctx.title,
                'url': ctx.url,
                'body_text': ctx.raw_text,
                'body_html': '',
                'images': [],
                'attachments': [],
                'exact_time': ''
            }
            logger.debug(f"[{ctx.source_name}] 列表中已包含内容，跳过详情抓取: {ctx.title[:30]}...")
        else:
            # 正常抓取详情
            try:
                detail = spider.fetch_detail(ctx.url)
                if not detail:
                    logger.debug(f"[{ctx.source_name}] 详情获取失败: {ctx.title}")
                    return False, "detail_failed", None
            except Exception as e:
                logger.warning(f"[{ctx.source_name}] 详情获取异常 ({ctx.title}): {e}")
                return False, "detail_error", None

        ctx.detail = detail

        # 2. 提取正文
        raw_text = detail.get("body_text", "")
        body_html = detail.get("body_html", "")
        images = detail.get("images", [])  # 🌟 新增：获取图片链接列表

        # 🌟 纯图片内容检测标记
        is_pure_image = False
        pure_image_html = ""  # 🌟 纯图片内容的 HTML

        # 🌟 纯图片内容防御：如果 body_text 为空，检查 HTML 中是否有实际文字
        if not raw_text or len(raw_text.strip()) < 10:
            # 尝试从 HTML 中提取纯文本
            if body_html:
                from bs4 import BeautifulSoup

                try:
                    soup = BeautifulSoup(body_html, 'lxml')
                    extracted_text = soup.get_text(strip=True, separator=" ")
                    # 检查是否包含图片标签
                    has_images = bool(soup.find("img"))

                    # 如果 HTML 中提取的文本也不够长
                    if len(extracted_text) < 10:
                        if has_images:
                            # 纯图片内容：提取图片链接，生成图片 HTML
                            is_pure_image = True
                            raw_text = "[纯图片内容]"

                            # 🌟 从 HTML 中提取所有图片链接
                            if not images:
                                for img in soup.find_all("img"):
                                    img_url = img.get("data-src") or img.get("src")
                                    if img_url and img_url.startswith(  # type:ignore
                                        "http"
                                    ):  # type:ignore
                                        images.append(img_url)

                            # 生成图片 HTML（添加 referrerpolicy 绕过微信防盗链）
                            if images:
                                img_tags = "".join(
                                    [
                                        f'<img src="{img_url}" referrerpolicy="no-referrer" alt="文章图片" style="max-width: 100%; height: auto; margin: 8px 0; border-radius: 8px;" />'
                                        for img_url in images
                                    ]
                                )
                                # 🌟 生成带标签的 summary，便于搜索
                                # 检测是否来自微信公众号
                                is_wechat = 'mp.weixin.qq.com' in ctx.url
                                if is_wechat:
                                    tags_line = "【图文】【微信文章】\n"
                                else:
                                    tags_line = "【图文】\n"
                                pure_image_html = f'{tags_line}<div class="pure-image-content" style="text-align: center;">{img_tags}</div>'
                                logger.info(
                                    f"检测到纯图片内容，已提取 {len(images)} 张图片: {ctx.title}"
                                )
                            else:
                                # 无图片，只添加标签
                                is_wechat = 'mp.weixin.qq.com' in ctx.url
                                if is_wechat:
                                    pure_image_html = "【图文】【微信文章】"
                                else:
                                    pure_image_html = "【图文】"
                                logger.info(
                                    f"检测到纯图片内容（未能提取图片）: {ctx.title}"
                                )
                        else:
                            logger.info(f"跳过内容过短且无图片的文章: {ctx.title}")
                            return False, "content_too_short", None
                    else:
                        raw_text = extracted_text
                except Exception as e:
                    logger.warning(f"HTML 解析失败 ({ctx.title}): {e}")
                    return False, "parse_error", None
            else:
                return False, "content_too_short", None

        ctx.raw_text = raw_text

        # 3. 内容长度校验（纯图片内容跳过此校验）
        if not is_pure_image and not self.validate_content_length(raw_text):
            logger.debug(f"跳过疑似噪音内容（长度不足）: {ctx.title}")
            return False, "content_too_short", None

        # 4. 检查是否新/更新
        is_changed, reason = self.db.check_if_new_or_updated(ctx.url, raw_text)
        if not is_changed:
            return False, "unchanged", None

        # 5. 🌟 在调用 AI 之前，检查是否已取消
        if self.is_cancel_requested():
            logger.info(f"[{ctx.source_name}] 任务已取消，跳过 AI 调用: {ctx.title}")
            return False, "cancelled", None

        # 6. 🌟 检查是否需要 AI 摘要（动态爬虫可设置 require_ai_summary=False）
        skip_ai_summary = not ctx.require_ai_summary

        # 7. 🌟 统计任务总数（包括纯图片内容和跳过 AI 的动态内容），并通知前端进度
        with self._stats_lock:
            self._stats["ai_total"] += 1
            ai_completed = self._stats["ai_completed"]
            ai_total = self._stats["ai_total"]

        # 8. 🌟 纯图片内容：跳过 AI 调用，使用图片 HTML 作为摘要
        if is_pure_image:
            summary = pure_image_html  # 使用图片 HTML
            logger.info(f"[{ctx.source_name}] 纯图片内容，已生成图片摘要: {ctx.title}")
            # 纯图片内容也计入完成进度
            with self._stats_lock:
                self._stats["ai_completed"] += 1
                ai_completed = self._stats["ai_completed"]
                ai_total = self._stats["ai_total"]
            logger.info(f"📊 纯图片进度: ai_completed={ai_completed}, ai_total={ai_total}")
            if self.on_progress and ai_total > 0:
                try:
                    self.on_progress(ai_completed, ai_total, f"[纯图片] {ctx.title}")
                    logger.info(f"✅ 纯图片进度回调已发送")
                except Exception as e:
                    logger.warning(f"纯图片进度回调失败: {e}")
        elif skip_ai_summary:
            # 🌟 跳过 AI 调用：根据数据源类型分流处理
            if source_type == 'html':
                # HTML 动态爬虫：使用格式化的动态字段作为摘要
                summary = self._format_dynamic_summary(detail, raw_text)
                logger.info(f"[{ctx.source_name}] HTML 动态爬虫跳过 AI，使用字段摘要: {ctx.title}")
            elif source_type == 'rss':
                # RSS 订阅：直接使用完整原始内容作为摘要
                summary = raw_text if raw_text else "【无内容】"
                logger.info(f"[{ctx.source_name}] RSS 订阅跳过 AI，使用原始内容: {ctx.title[:30]}...")
            else:
                # 其他类型：兜底处理
                summary = raw_text if raw_text else "【无内容】"
                logger.info(f"[{ctx.source_name}] 跳过 AI，使用原始内容: {ctx.title[:30]}...")
            with self._stats_lock:
                self._stats["ai_completed"] += 1
                ai_completed = self._stats["ai_completed"]
                ai_total = self._stats["ai_total"]
            if self.on_progress and ai_total > 0:
                try:
                    self.on_progress(ai_completed, ai_total, f"[动态] {ctx.title}")
                except Exception:
                    pass
        else:
            # 8. 通知前端开始 AI 处理
            if self.on_progress and ai_total > 0:
                try:
                    # 回调参数：(已完成AI调用数量, 真实AI任务总数, 当前正在调用AI的标题)
                    self.on_progress(ai_completed, ai_total, ctx.title)
                except Exception as e:
                    logger.warning(f"进度回调执行失败: {e}")

            # 9. AI 生成摘要（此处已有指数退避重试）
            logger.info(f"[{ctx.source_name}] 发现变动 ({reason}) -> {ctx.title}")
            try:
                # 🌟 传递专属 AI 提示词
                summary = self.llm.summarize_article(
                    ctx.title, raw_text, custom_prompt=ctx.custom_summary_prompt
                )
            except Exception as e:
                logger.warning(f"AI 摘要生成异常 ({ctx.title}): {e}")
                with self._stats_lock:
                    self._stats["ai_completed"] += 1
                    ai_completed = self._stats["ai_completed"]
                    ai_total = self._stats["ai_total"]
                if self.on_progress and ai_total > 0:
                    try:
                        self.on_progress(ai_completed, ai_total, ctx.title)
                    except Exception:
                        pass
                return False, "ai_error", None

            # 🌟 AI 调用完成，推送最新进度（含完成信号）
            with self._stats_lock:
                self._stats["ai_completed"] += 1
                ai_completed = self._stats["ai_completed"]
                ai_total = self._stats["ai_total"]
            if self.on_progress and ai_total > 0:
                try:
                    self.on_progress(ai_completed, ai_total, ctx.title)
                except Exception:
                    pass

            # 10. 核心防线：拦截 AI 失败的情况
            if summary.startswith(self.AI_FAILURE_PREFIXES):
                logger.warning(f"AI 分析失败，本次不入库: {ctx.title}")
                return False, "ai_failed", None

        # 7. 添加时间戳（已禁用）
        # timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        # summary = summary + f"\n\n---\n**🤖 AI 生成时间: {timestamp}**"

        # 8. 处理附件（强制初始化为空列表）
        attachments_data = detail.get("attachments") or []
        if not isinstance(attachments_data, list):
            attachments_data = []
        attachments_json = json.dumps(attachments_data, ensure_ascii=False)

        # 9. 入库（🌟 异步写入：通过写队列串行化，不阻塞当前 Worker）
        self.db.insert_or_update_article(
            title=ctx.title,
            url=ctx.url,
            date=ctx.date,
            exact_time=detail.get("exact_time", ""),
            category=ctx.category or ctx.section_name or "未知类别",
            department=ctx.department or detail.get("department", ""),
            attachments=attachments_json,
            summary=summary,
            raw_content=raw_text,
            source_name=ctx.source_name,
        )

        logger.info(f"✅ [{ctx.source_name}] 文章入库成功: {ctx.title}")

        # 10. 构建完整的文章数据并触发回调
        article_data = {
            "title": ctx.title,
            "url": ctx.url,
            "date": ctx.date,
            "source_name": ctx.source_name,
            "category": ctx.category or ctx.section_name or "未知类别",
            "department": ctx.department or detail.get("department", ""),
            "exact_time": detail.get("exact_time", ""),
            "attachments": (
                attachments_data if isinstance(attachments_data, list) else []
            ),
            "summary": summary,
            "raw_content": raw_text,
            "is_read": 0,
            "model_name": self.config_service.get('modelName', 'AI'),  # 🌟 传递模型名称用于快照
        }
        logger.info(f"📸 article_data.model_name = {article_data.get('model_name')}")

        if self.on_article_processed:
            try:
                self.on_article_processed(article_data)
            except Exception as e:
                logger.warning(f"文章处理回调执行失败: {e}")

        # 11. 📧 邮件推送（异步，不阻塞主流程）
        if self._should_send_email():
            import threading
            threading.Thread(
                target=self._send_email_notification,
                args=(article_data,),
                daemon=True
            ).start()

        # 12. 返回成功信息
        return True, reason, article_data

    def submit(
        self,
        spider: "BaseSpider",
        ctx: ArticleContext,
        mode: str = "continuous",
        today_str: str = "",
        is_manual: bool = False,
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

        # 🌟 检查取消请求
        if self.is_cancel_requested():
            logger.debug(f"AI 任务已取消，跳过提交: {ctx.title}")
            return False

        task = ProcessingTask(
            spider=spider, ctx=ctx, mode=mode, today_str=today_str, is_manual=is_manual
        )

        try:
            self._task_queue.put(task, block=False)
            with self._stats_lock:
                self._stats["submitted"] += 1
            return True
        except queue.Full:
            logger.warning(f"任务队列已满，拒绝任务: {ctx.title}")
            return False

    def process(
        self,
        spider: "BaseSpider",
        ctx: ArticleContext,
        mode: str,
        today_str: str,
        is_manual: bool,
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
        return self._process_task(
            ProcessingTask(
                spider=spider,
                ctx=ctx,
                mode=mode,
                today_str=today_str,
                is_manual=is_manual,
            )
        )

    def should_skip_by_title(self, title: str) -> Tuple[bool, str]:
        """根据标题判断是否应该跳过"""
        if title.strip() in self.TITLE_BLACKLIST:
            return True, "blacklisted"
        return False, ""

    def should_skip_by_date(self, date: str, mode: str, today_str: str) -> bool:
        """根据日期判断是否应该跳过（当日追踪模式）"""
        if mode == "today" and date:
            normalized_date = date.replace("/", "-").split()[0] if date else ""
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
        cleaned = raw_text.replace(" ", "").replace("\n", "")
        return len(cleaned) >= self.MIN_CONTENT_LENGTH

    def create_context(
        self,
        article: Dict[str, Any],
        source_name: str,
        section_name: Optional[str] = None,
    ) -> ArticleContext:
        """从文章字典创建处理上下文"""
        # 部门逻辑重构：
        # - 公文通：保持原有的发文单位（部门）提取逻辑
        # - 学院/中心：强制 department = source_name
        department = article.get("department", "")
        if source_name != "公文通" and ("学院" in source_name or "中心" in source_name):
            department = source_name

        return ArticleContext(
            url=article.get("url", ""),
            title=article.get("title", "未知标题"),
            date=article.get("date", ""),
            source_name=source_name,
            section_name=section_name,
            raw_text=article.get("body_text", ""),  # 🌟 传递列表中已获取的内容（RSS 等场景）
            category=article.get("category", ""),
            department=department,
            require_ai_summary=article.get("require_ai_summary", True),
            custom_summary_prompt=article.get("custom_summary_prompt", ""),
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

    def _format_dynamic_summary(self, detail: Dict[str, Any], raw_text: str) -> str:
        """
        格式化动态爬虫字段为摘要（跳过 AI 调用时使用）

        将动态字段格式化为易读的文本摘要。

        Args:
            detail: 详情字典（包含 dynamic_fields）
            raw_text: 原始文本（作为备选）

        Returns:
            格式化后的摘要字符串
        """
        dynamic_fields = detail.get("dynamic_fields", {})
        if not dynamic_fields:
            # 如果没有动态字段，返回原始文本（截取前 500 字符）
            return raw_text[:500] if raw_text else "【动态数据】"

        # 构建易读的摘要
        lines = ["【动态数据】"]
        for key, value in dynamic_fields.items():
            # 跳过空值
            if not value or (isinstance(value, str) and not value.strip()):
                continue
            # 截取过长的值
            if len(str(value)) > 200:
                value = str(value)[:200] + "..."
            # 添加到摘要中
            lines.append(f"- **{key}**: {value}")

        return "\n".join(lines)

    # ==================== 📧 邮件推送功能 ====================

    def _should_send_email(self) -> bool:
        """
        检查是否应该发送邮件通知

        Returns:
            是否启用邮件推送
        """
        if not self.config_service:
            logger.info("📧 邮件检查: config_service 为空，跳过")
            return False

        try:
            # 检查是否启用邮件通知
            enabled = self.config_service.get('emailNotifyEnabled', False)
            logger.info(f"📧 邮件检查: emailNotifyEnabled = {enabled}")
            if not enabled:
                logger.info("📧 邮件检查: 邮件通知未启用，跳过")
                return False

            # 检查是否有订阅者
            subscriber_list = self.config_service.get('subscriberList', [])
            logger.info(f"📧 邮件检查: subscriberList = {subscriber_list}")
            if not subscriber_list or len(subscriber_list) == 0:
                logger.info("📧 邮件检查: 订阅者列表为空，跳过")
                return False

            # 检查 SMTP 配置是否完整
            smtp_host = self.config_service.get('smtpHost', '')
            smtp_user = self.config_service.get('smtpUser', '')
            smtp_password = self.config_service.get('smtpPassword', '')

            logger.info(f"📧 邮件检查: smtpHost={smtp_host}, smtpUser={smtp_user}, hasPassword={bool(smtp_password)}")

            if not smtp_host or not smtp_user or not smtp_password:
                logger.info("📧 邮件检查: SMTP 配置不完整，跳过")
                return False

            logger.info("📧 邮件检查: 所有条件满足，将发送邮件通知")
            return True

        except Exception as e:
            logger.warning(f"📧 检查邮件配置失败: {e}")
            return False

    def _send_email_notification(self, article_data: Dict[str, Any]) -> None:
        """
        发送邮件通知（后台线程执行）

        Args:
            article_data: 文章数据
        """
        import tempfile
        import os

        logger.info(f"📧 开始发送邮件通知: {article_data.get('title', '')[:30]}...")

        try:
            from src.services.snapshot_service import render_article_snapshot
            from src.services.email_service import EmailService

            # 1. 🌟 生成快照图片（带重试机制）
            logger.info(f"📧 步骤1: 正在生成快照图片...")
            snapshot_path = None
            max_retries = 3

            for attempt in range(max_retries):
                try:
                    snapshot_path = render_article_snapshot(article_data)
                    if snapshot_path:
                        break  # 成功，退出重试
                    logger.warning(f"📧 快照生成返回空，第 {attempt+1}/{max_retries} 次重试")
                except Exception as e:
                    logger.warning(f"📧 快照生成异常 (尝试 {attempt+1}/{max_retries}): {e}")

                if attempt < max_retries - 1:
                    import time
                    time.sleep(1)  # 等待 1 秒后重试

            if not snapshot_path:
                logger.error("📧 快照生成最终失败，跳过邮件推送")
                # 🌟 通知用户快照生成失败
                self._notify_email_failure("快照生成失败，请检查网络或重试")
                return

            logger.info(f"📧 快照生成成功: {snapshot_path}")

            # 2. 获取邮件配置
            logger.info(f"📧 步骤2: 获取邮件配置...")
            smtp_host = self.config_service.get('smtpHost', '')
            smtp_port = self.config_service.get('smtpPort', 465)
            smtp_user = self.config_service.get('smtpUser', '')
            smtp_password = self.config_service.get('smtpPassword', '')
            subscriber_list = self.config_service.get('subscriberList', [])

            logger.info(f"📧 配置: host={smtp_host}, port={smtp_port}, user={smtp_user}, subscribers={subscriber_list}")

            # 3. 创建邮件服务并发送
            logger.info(f"📧 步骤3: 发送邮件...")
            email_service = EmailService(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password
            )

            result = email_service.send_article_notification(
                to_addrs=subscriber_list,
                article_data=article_data,
                image_path=snapshot_path
            )

            # 4. 清理临时文件
            try:
                if snapshot_path and os.path.exists(snapshot_path):
                    os.remove(snapshot_path)
                    logger.info(f"📧 已清理临时文件: {snapshot_path}")
            except Exception as e:
                logger.warning(f"📧 清理临时文件失败: {e}")

            # 5. 🌟 处理发送结果，失败时通知用户
            if result.get('success'):
                logger.info(f"📧 ✅ 邮件推送成功: {result.get('sent_count', 0)} 封")
            else:
                failed_list = result.get('failed', [])
                error_msg = result.get('message', '未知错误')
                logger.error(f"📧 ❌ 邮件推送失败: {error_msg}")

                # 通知用户邮件发送失败
                if failed_list:
                    failed_emails = [f.get('email', '未知') for f in failed_list]
                    self._notify_email_failure(f"邮件推送失败：{', '.join(failed_emails[:3])}")
                else:
                    self._notify_email_failure(error_msg)

        except ImportError as e:
            logger.error(f"📧 邮件服务模块导入失败: {e}")
            self._notify_email_failure("邮件服务模块加载失败")
        except Exception as e:
            logger.error(f"📧 邮件发送异常: {e}", exc_info=True)
            self._notify_email_failure(f"邮件发送异常: {str(e)[:50]}")

    def _notify_email_failure(self, message: str) -> None:
        """
        通知用户邮件推送失败

        Args:
            message: 失败原因
        """
        try:
            from src.notifier import send_notification
            send_notification("邮件推送失败", message)
            logger.info(f"📧 已发送邮件失败通知: {message}")
        except Exception as e:
            logger.warning(f"📧 发送失败通知时出错: {e}")

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
