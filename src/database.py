"""
SQLite 连接池 - 高并发优化的数据库管理器

架构设计：
┌──────────────────────────────────────────────────────────────┐
│                    DatabaseManager (单例)                     │
├──────────────────────────────────────────────────────────────┤
│  读操作 (并发)                │  写操作 (串行化)               │
│  ┌─────────────────┐         │  ┌─────────────────┐         │
│  │  Read Pool (3)  │         │  │  Write Queue    │         │
│  │  Connection 1   │         │  │  ┌───┐┌───┐┌───┐│         │
│  │  Connection 2   │         │  │  │ T ││ T ││ T ││         │
│  │  Connection 3   │         │  │  └───┘└───┘└───┘│         │
│  └─────────────────┘         │  └────────┬────────┘         │
│                              │           ▼                  │
│                              │  ┌─────────────────┐         │
│                              │  │ Write Thread    │         │
│                              │  │ (Single Conn)   │         │
│                              │  └─────────────────┘         │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │   SQLite WAL    │
                    │  (Readers ⊥ Writer) │
                    └─────────────────┘

核心特性：
1. WAL 模式：允许多读单写并发
2. 读连接池：复用连接，避免频繁创建销毁
3. 写队列：串行化写入，杜绝 database is locked
4. 超时保护：防止无限等待
"""

import json
import sqlite3
import hashlib
import logging
import threading
import queue
import re
import time
from src.utils.text_cleaner import strip_emoji
from src.utils.article_identity import canonicalize_article_url
from src.utils.ai_markdown import compose_tagged_markdown, extract_leading_tags, serialize_tags
from typing import Optional, Dict, Any, List, Callable
from contextlib import contextmanager
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _get_db_path() -> str:
    """延迟获取数据库路径，避免循环导入"""
    from src.core.paths import DB_PATH
    return str(DB_PATH)


def _ensure_data_dir():
    """延迟确保数据目录存在"""
    from src.core.paths import ensure_data_dir_exists
    ensure_data_dir_exists()


@dataclass
class WriteTask:
    """写任务封装"""
    operation: Callable[[sqlite3.Cursor], Any]
    result_event: threading.Event
    result: Any = None
    error: Optional[Exception] = None


class ConnectionPool:
    """
    轻量级读连接池

    特性：
    - 固定大小的连接池（默认 3 个）
    - 线程安全的 get/put
    - 自动重连机制
    """

    def __init__(self, db_path: str, pool_size: int = 2):  # 🌟 性能优化：从3降至2
        self._db_path = db_path
        self._pool_size = pool_size
        self._pool: queue.Queue[sqlite3.Connection] = queue.Queue(maxsize=pool_size)
        self._lock = threading.Lock()
        self._initialized = False

        # 初始化连接池
        self._init_pool()

    def _create_connection(self) -> sqlite3.Connection:
        """创建新的数据库连接"""
        conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            timeout=10.0
        )
        # 开启 WAL 模式（读连接也需要的设置）
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
        conn.execute('PRAGMA busy_timeout=5000;')
        conn.execute('PRAGMA read_uncommitted=ON;')  # WAL 模式下允许脏读，提高并发
        conn.row_factory = sqlite3.Row
        return conn

    def _init_pool(self):
        """初始化连接池"""
        with self._lock:
            if self._initialized:
                return
            for _ in range(self._pool_size):
                try:
                    conn = self._create_connection()
                    self._pool.put(conn, block=False)
                except Exception as e:
                    logger.warning(f"创建读连接失败: {e}")
            self._initialized = True
            logger.info(f"📖 读连接池已初始化，大小: {self._pool_size}")

    @contextmanager
    def get(self):
        """
        获取连接（上下文管理器）

        用法：
            with pool.get() as conn:
                cursor = conn.cursor()
                cursor.execute(...)
        """
        conn = None
        try:
            conn = self._pool.get(timeout=5.0)
            yield conn
        except queue.Empty:
            # 池中无可用连接，创建临时连接
            logger.debug("读连接池耗尽，创建临时连接")
            temp_conn = None
            try:
                temp_conn = self._create_connection()
                yield temp_conn
            finally:
                # 临时连接用完即关，不回池
                if temp_conn:
                    try:
                        temp_conn.close()
                    except Exception:
                        pass
            return
        except Exception as e:
            logger.error(f"获取读连接失败: {e}")
            raise
        else:
            # 正常归还连接
            if conn:
                try:
                    self._pool.put(conn, block=False)
                except queue.Full:
                    # 池已满，直接关闭
                    try:
                        conn.close()
                    except Exception:
                        pass

    def close_all(self):
        """关闭所有连接"""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Exception:
                pass
        logger.info("读连接池已关闭")


class WriteWorker:
    """
    写入工作线程 - 串行化所有写操作

    特性：
    - 单连接，避免 SQLite 写锁冲突
    - 异步提交，不阻塞调用线程
    - 同步等待结果（可选）
    - 🌟 队列满时自动重试（默认 3 次）
    """

    def __init__(self, db_path: str, queue_size: int = 300):  # 🌟 平衡优化：300缓冲
        self._db_path = db_path
        self._task_queue: queue.Queue[WriteTask] = queue.Queue(maxsize=queue_size)
        self._queue_size = queue_size
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[sqlite3.Connection] = None

        # 统计
        self._stats = {
            'writes': 0,
            'errors': 0,
            'queue_full': 0
        }
        self._stats_lock = threading.Lock()

    def start(self):
        """启动写入线程"""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._worker_loop, daemon=True, name="DBWriteWorker")
        self._thread.start()
        logger.info("✍️ 写入线程已启动")

    def _worker_loop(self):
        """写入线程主循环"""
        # 创建专用的写入连接
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            timeout=30.0
        )
        self._conn.execute('PRAGMA journal_mode=WAL;')
        self._conn.execute('PRAGMA synchronous=NORMAL;')
        self._conn.execute('PRAGMA busy_timeout=10000;')  # 写入等待更久
        self._conn.row_factory = sqlite3.Row

        logger.debug("写入连接已创建")

        while not self._stop_event.is_set():
            try:
                # 获取任务（带超时，便于检查停止信号）
                try:
                    task = self._task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # 执行写入操作
                try:
                    cursor = self._conn.cursor()
                    task.result = task.operation(cursor)
                    self._conn.commit()

                    with self._stats_lock:
                        self._stats['writes'] += 1

                except Exception as e:
                    task.error = e
                    with self._stats_lock:
                        self._stats['errors'] += 1

                    # 尝试回滚
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass

                    logger.error(f"写入操作失败: {e}")

                finally:
                    # 通知调用线程结果已就绪
                    task.result_event.set()
                    self._task_queue.task_done()

            except Exception as e:
                logger.error(f"写入线程异常: {e}")

        # 关闭连接
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        logger.info("写入线程已停止")

    def submit(self, operation: Callable[[sqlite3.Cursor], Any], wait: bool = True, timeout: float = 10.0, retries: int = 3) -> Any:
        """
        提交写操作

        Args:
            operation: 接收 cursor 的操作函数
            wait: 是否等待结果
            timeout: 等待超时时间
            retries: 队列满时的重试次数（每次间隔 0.1 秒）

        Returns:
            操作结果（如果 wait=True）
        """
        if self._stop_event.is_set():
            raise RuntimeError("写入线程已停止")

        task = WriteTask(
            operation=operation,
            result_event=threading.Event()
        )

        # 🌟 重试机制：队列满时自动重试
        for attempt in range(retries):
            try:
                self._task_queue.put(task, block=False)
                break  # 成功入队，退出重试循环
            except queue.Full:
                with self._stats_lock:
                    self._stats['queue_full'] += 1
                if attempt == retries - 1:
                    # 最后一次重试仍失败，抛出异常
                    logger.error(f"写队列已满，重试 {retries} 次后仍失败")
                    raise RuntimeError("写队列已满，请稍后重试")
                # 等待 0.1 秒后重试
                import time
                time.sleep(0.1)

        if wait:
            # 等待结果
            if not task.result_event.wait(timeout=timeout):
                raise TimeoutError(f"写入操作超时 ({timeout}s)")

            if task.error:
                raise task.error

            return task.result

        return None

    def stop(self):
        """停止写入线程"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)

    def get_stats(self) -> Dict[str, int]:
        """获取统计信息"""
        with self._stats_lock:
            return dict(self._stats)

    def get_queue_size(self) -> int:
        """获取当前队列大小"""
        return self._task_queue.qsize()


class DatabaseManager:
    """
    数据库管理器 - 单例模式

    架构：
    - 读操作：通过连接池并发执行
    - 写操作：通过写队列串行化执行
    """

    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # 防止重复初始化
        if hasattr(self, '_initialized') and self._initialized:
            return

        _ensure_data_dir()
        db_path = _get_db_path()

        # 读连接池（3 个并发读连接）
        self._read_pool = ConnectionPool(db_path, pool_size=3)

        # 写工作线程（单连接串行写入）
        self._write_worker = WriteWorker(db_path)
        self._write_worker.start()

        # 兼容旧代码的写锁（现在主要用于保护 generate_hash）
        self._write_lock = threading.RLock()

        # 初始化数据库
        self._init_db()
        self._migrate_source_name()
        self._backfill_exact_time_on_startup()

        self._initialized = True
        logger.info("🗄️ DatabaseManager 初始化完成 (连接池模式)")

    @contextmanager
    def _get_read_connection(self):
        """获取读连接（上下文管理器）"""
        with self._read_pool.get() as conn:
            yield conn

    def _init_db(self):
        """初始化数据库表结构"""
        def init_schema(cursor: sqlite3.Cursor):
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT UNIQUE NOT NULL,
                    date TEXT,
                    exact_time TEXT,
                    category TEXT,
                    department TEXT,
                    attachments TEXT,
                    summary TEXT,
                    raw_text TEXT,
                    raw_markdown TEXT DEFAULT '',
                    enhanced_markdown TEXT DEFAULT '',
                    ai_summary TEXT DEFAULT '',
                    ai_tags TEXT DEFAULT '',
                    raw_hash TEXT NOT NULL,
                    is_read INTEGER DEFAULT 0,
                    is_favorite INTEGER DEFAULT 0,
                    is_deleted INTEGER DEFAULT 0,
                    source_name TEXT DEFAULT '公文通',
                    source_type TEXT DEFAULT 'html',
                    rule_id TEXT DEFAULT '',
                    custom_summary_prompt TEXT DEFAULT '',
                    formatting_prompt TEXT DEFAULT '',
                    summary_prompt TEXT DEFAULT '',
                    enable_ai_formatting INTEGER DEFAULT 0,
                    enable_ai_summary INTEGER DEFAULT 0,
                    content_blocks TEXT DEFAULT '[]',
                    image_assets TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ai_result_cache (
                    cache_key TEXT PRIMARY KEY,
                    cache_scope TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    model_name TEXT DEFAULT '',
                    base_url TEXT DEFAULT '',
                    result_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_ai_result_cache_scope_updated
                ON ai_result_cache(cache_scope, updated_at DESC)
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS article_annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article_id INTEGER NOT NULL,
                    view_mode TEXT NOT NULL DEFAULT 'summary',
                    anchor_text TEXT NOT NULL DEFAULT '',
                    anchor_prefix TEXT DEFAULT '',
                    anchor_suffix TEXT DEFAULT '',
                    start_offset INTEGER NOT NULL DEFAULT 0,
                    end_offset INTEGER NOT NULL DEFAULT 0,
                    style_payload TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_article_annotations_article_view
                ON article_annotations(article_id, view_mode, start_offset, end_offset)
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS crawl_frontiers (
                    source_name TEXT NOT NULL,
                    section_name TEXT NOT NULL DEFAULT '',
                    frontier_url TEXT NOT NULL DEFAULT '',
                    frontier_cursor TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
                    PRIMARY KEY(source_name, section_name)
                )
            ''')
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    event_name TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                    next_retry_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
                    last_error TEXT DEFAULT '',
                    sent_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telemetry_events_status_retry
                ON telemetry_events(status, next_retry_at, id)
                """
            )
            return True

        # 初始化时使用写线程同步执行
        self._write_worker.submit(init_schema, wait=True, timeout=30.0)

    def _migrate_source_name(self):
        """V2 版本迁移：添加 source_name 字段"""
        def migrate(cursor: sqlite3.Cursor):
            cursor.execute("PRAGMA table_info(articles)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'source_name' not in columns:
                logger.info("检测到旧版数据库结构，正在执行 source_name 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN source_name TEXT DEFAULT '公文通'")
                cursor.execute("UPDATE articles SET source_name = '公文通' WHERE source_name IS NULL")
                logger.info("source_name 字段迁移完成")

            if 'source_type' not in columns:
                logger.info("正在执行 source_type 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN source_type TEXT DEFAULT 'html'")
                cursor.execute("UPDATE articles SET source_type = 'html' WHERE source_type IS NULL")
                logger.info("source_type 字段迁移完成")

            # 🌟 新增：is_favorite 字段迁移
            if 'is_favorite' not in columns:
                logger.info("正在执行 is_favorite 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN is_favorite INTEGER DEFAULT 0")
                logger.info("is_favorite 字段迁移完成")

            # 🌟 新增：is_deleted 字段迁移（软删除标记）
            if 'is_deleted' not in columns:
                logger.info("正在执行 is_deleted 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN is_deleted INTEGER DEFAULT 0")
                logger.info("is_deleted 字段迁移完成")

            if 'rule_id' not in columns:
                logger.info("正在执行 rule_id 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN rule_id TEXT DEFAULT ''")
                logger.info("rule_id 字段迁移完成")

            if 'custom_summary_prompt' not in columns:
                logger.info("正在执行 custom_summary_prompt 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN custom_summary_prompt TEXT DEFAULT ''")
                logger.info("custom_summary_prompt 字段迁移完成")

            if 'formatting_prompt' not in columns:
                logger.info("正在执行 formatting_prompt 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN formatting_prompt TEXT DEFAULT ''")
                logger.info("formatting_prompt 字段迁移完成")

            if 'summary_prompt' not in columns:
                logger.info("正在执行 summary_prompt 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN summary_prompt TEXT DEFAULT ''")
                logger.info("summary_prompt 字段迁移完成")

            if 'enable_ai_formatting' not in columns:
                logger.info("正在执行 enable_ai_formatting 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN enable_ai_formatting INTEGER DEFAULT 0")
                logger.info("enable_ai_formatting 字段迁移完成")

            if 'enable_ai_summary' not in columns:
                logger.info("正在执行 enable_ai_summary 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN enable_ai_summary INTEGER DEFAULT 0")
                logger.info("enable_ai_summary 字段迁移完成")

            if 'content_blocks' not in columns:
                logger.info("正在执行 content_blocks 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN content_blocks TEXT DEFAULT '[]'")
                logger.info("content_blocks 字段迁移完成")

            if 'image_assets' not in columns:
                logger.info("正在执行 image_assets 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN image_assets TEXT DEFAULT '[]'")
                logger.info("image_assets 字段迁移完成")

            if 'raw_markdown' not in columns:
                logger.info("正在执行 raw_markdown 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN raw_markdown TEXT DEFAULT ''")
                logger.info("raw_markdown 字段迁移完成")

            if 'enhanced_markdown' not in columns:
                logger.info("正在执行 enhanced_markdown 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN enhanced_markdown TEXT DEFAULT ''")
                logger.info("enhanced_markdown 字段迁移完成")

            if 'ai_summary' not in columns:
                logger.info("正在执行 ai_summary 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN ai_summary TEXT DEFAULT ''")
                logger.info("ai_summary 字段迁移完成")

            if 'ai_tags' not in columns:
                logger.info("正在执行 ai_tags 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN ai_tags TEXT DEFAULT ''")
                logger.info("ai_tags 字段迁移完成")

            # 为历史 RSS 数据补默认值，保证新旧前端都能回退读取
            cursor.execute("""
                UPDATE articles
                SET raw_markdown = raw_text
                WHERE source_type = 'rss'
                  AND (raw_markdown IS NULL OR raw_markdown = '')
                  AND raw_text IS NOT NULL
                  AND raw_text != ''
            """)
            cursor.execute("""
                UPDATE articles
                SET enhanced_markdown = CASE
                    WHEN summary IS NOT NULL AND summary != '' THEN summary
                    ELSE raw_text
                END
                WHERE source_type = 'rss'
                  AND (enhanced_markdown IS NULL OR enhanced_markdown = '')
            """)

            cursor.execute("""
                UPDATE articles
                SET summary_prompt = custom_summary_prompt
                WHERE source_type = 'rss'
                  AND (summary_prompt IS NULL OR summary_prompt = '')
                  AND custom_summary_prompt IS NOT NULL
                  AND custom_summary_prompt != ''
            """)
            cursor.execute("""
                UPDATE articles
                SET enable_ai_summary = CASE
                    WHEN summary_prompt IS NOT NULL AND summary_prompt != '' THEN 1
                    WHEN ai_summary IS NOT NULL AND ai_summary != '' THEN 1
                    ELSE enable_ai_summary
                END
                WHERE source_type = 'rss'
            """)
            cursor.execute("""
                UPDATE articles
                SET enable_ai_formatting = CASE
                    WHEN raw_markdown IS NOT NULL
                         AND raw_markdown != ''
                         AND enhanced_markdown IS NOT NULL
                         AND enhanced_markdown != ''
                         AND enhanced_markdown != raw_markdown THEN 1
                    ELSE enable_ai_formatting
                END
                WHERE source_type = 'rss'
            """)

            return True

        try:
            self._write_worker.submit(migrate, wait=True, timeout=30.0)
        except Exception as e:
            logger.error(f"数据库迁移失败: {e}")

    def _backfill_exact_time_on_startup(self):
        """启动时清洗并填充时间数据"""
        def backfill(cursor: sqlite3.Cursor):
            # 1. 🌟 标准化 date 字段格式
            # 处理中文日期
            cursor.execute('''
                UPDATE articles
                SET date = REPLACE(REPLACE(REPLACE(date, '年', '-'), '月', '-'), '日', '')
                WHERE date LIKE '%年%' OR date LIKE '%月%' OR date LIKE '%日%'
            ''')
            date_cn_count = cursor.rowcount

            # 处理斜杠日期
            cursor.execute('''
                UPDATE articles
                SET date = REPLACE(date, '/', '-')
                WHERE date LIKE '%/%'
            ''')
            date_slash_count = cursor.rowcount

            # 2. 为 exact_time 为空但有 date 的记录填充默认时间
            cursor.execute('''
                UPDATE articles
                SET exact_time = date || ' 00:00:00'
                WHERE (exact_time IS NULL OR exact_time = '')
                  AND date IS NOT NULL
                  AND date != ''
            ''')
            fill_count = cursor.rowcount

            # 3. 🌟 清洗 exact_time 格式：将中文日期标准化
            # 处理 "年月日" 格式
            cursor.execute('''
                UPDATE articles
                SET exact_time = REPLACE(REPLACE(REPLACE(exact_time, '年', '-'), '月', '-'), '日', ' ')
                WHERE exact_time LIKE '%年%' OR exact_time LIKE '%月%' OR exact_time LIKE '%日%'
            ''')
            cn_count = cursor.rowcount

            # 处理斜杠格式
            cursor.execute('''
                UPDATE articles
                SET exact_time = REPLACE(exact_time, '/', '-')
                WHERE exact_time LIKE '%/%'
            ''')
            slash_count = cursor.rowcount

            # 4. 🌟 确保时间部分完整：如果只有日期没有时间，补充 00:00:00
            cursor.execute('''
                UPDATE articles
                SET exact_time = exact_time || ' 00:00:00'
                WHERE exact_time IS NOT NULL
                  AND exact_time != ''
                  AND length(exact_time) = 10
                  AND exact_time LIKE '____-__-__'
            ''')
            time_fill_count = cursor.rowcount

            total = date_cn_count + date_slash_count + fill_count + cn_count + slash_count + time_fill_count
            if total > 0:
                logger.info(f"时间数据清洗完成: 日期格式 {date_cn_count + date_slash_count} 条, 填充空值 {fill_count} 条, 时间格式 {cn_count + slash_count + time_fill_count} 条")
            return total

        try:
            self._write_worker.submit(backfill, wait=True, timeout=30.0)
        except Exception as e:
            logger.error(f"时间数据清洗失败: {e}")

    def generate_hash(self, text: str) -> str:
        """生成文本的 MD5 哈希值"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def get_ai_result_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """读取 AI 结果缓存。"""
        cache_key = str(cache_key or "").strip()
        if not cache_key:
            return None

        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT cache_key, cache_scope, content_hash, prompt_hash,
                       model_name, base_url, result_text, created_at, updated_at
                FROM ai_result_cache
                WHERE cache_key = ?
                LIMIT 1
                """,
                (cache_key,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def upsert_ai_result_cache(
        self,
        *,
        cache_key: str,
        cache_scope: str,
        content_hash: str,
        prompt_hash: str,
        model_name: str,
        base_url: str,
        result_text: str,
    ) -> bool:
        """写入或更新 AI 结果缓存。"""
        cache_key = str(cache_key or "").strip()
        if not cache_key:
            return False

        cache_scope = str(cache_scope or "").strip()
        content_hash = str(content_hash or "").strip()
        prompt_hash = str(prompt_hash or "").strip()
        model_name = strip_emoji(str(model_name or "").strip())
        base_url = strip_emoji(str(base_url or "").strip())
        result_text = strip_emoji(str(result_text or "").strip())

        def do_upsert(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                INSERT INTO ai_result_cache (
                    cache_key, cache_scope, content_hash, prompt_hash,
                    model_name, base_url, result_text, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
                ON CONFLICT(cache_key) DO UPDATE SET
                    cache_scope = excluded.cache_scope,
                    content_hash = excluded.content_hash,
                    prompt_hash = excluded.prompt_hash,
                    model_name = excluded.model_name,
                    base_url = excluded.base_url,
                    result_text = excluded.result_text,
                    updated_at = datetime('now', 'localtime')
                """,
                (
                    cache_key,
                    cache_scope,
                    content_hash,
                    prompt_hash,
                    model_name,
                    base_url,
                    result_text,
                ),
            )
            return True

        try:
            return bool(self._write_worker.submit(do_upsert, wait=True, timeout=10.0))
        except Exception as e:
            logger.error(f"写入 AI 缓存失败: {e}")
            return False

    # ==================== 读操作 ====================

    def check_if_url_exists(self, url: str) -> bool:
        """检查 URL 是否已存在"""
        normalized_url = canonicalize_article_url(url)
        if not normalized_url:
            return False
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (normalized_url,))
            return cursor.fetchone() is not None

    def check_if_new_or_updated(self, url: str, raw_content: str) -> tuple:
        """检查文章是否为新或有更新"""
        normalized_url = canonicalize_article_url(url)
        if not normalized_url:
            return False, "invalid_url"
        current_hash = self.generate_hash(raw_content)

        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_hash FROM articles WHERE url = ?", (normalized_url,))
            row = cursor.fetchone()

            if row is None:
                return True, "new"

            if row['raw_hash'] != current_hash:
                return True, "updated"

            return False, "unchanged"

    def get_article_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """根据 URL 获取文章。"""
        normalized_url = canonicalize_article_url(url)
        if not normalized_url:
            return None
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM articles WHERE url = ? LIMIT 1", (normalized_url,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_articles_by_rule_prefix(self, rule_prefix: str) -> List[Dict[str, Any]]:
        """根据 rule_id 前缀读取文章，主要用于系统内容等内置数据。"""
        prefix = str(rule_prefix or "").strip()
        if not prefix:
            return []
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM articles
                WHERE rule_id LIKE ? AND is_deleted = 0
                ORDER BY created_at DESC
                """,
                (f"{prefix}%",),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_articles_by_rule_id(self, rule_id: str) -> int:
        """按 rule_id 删除文章。"""
        normalized_rule_id = str(rule_id or "").strip()
        if not normalized_rule_id:
            return 0

        def do_delete(cursor: sqlite3.Cursor):
            cursor.execute(
                "DELETE FROM articles WHERE rule_id = ?",
                (normalized_rule_id,),
            )
            return int(cursor.rowcount or 0)

        try:
            deleted = self._write_worker.submit(do_delete, wait=True, timeout=10.0)
            return int(deleted or 0)
        except Exception as e:
            logger.error(f"按 rule_id 删除文章失败: {e}")
            return 0

    def get_articles_paged(
        self,
        limit: int = 20,
        offset: int = 0,
        source_name: Optional[str] = None,
        source_names: Optional[List[str]] = None,
        favorites_only: bool = False,
        include_content: bool = True,
    ) -> List[Dict]:
        """分页获取文章

        Args:
            limit: 返回数量限制
            offset: 偏移量
            source_name: 单个来源筛选
            source_names: 多个来源筛选列表（当 source_name 为 None 时生效）
            favorites_only: 仅返回收藏的文章
            include_content: 是否返回正文/Markdown/结构化图片等重字段
        """
        article_order_sql = (
            "COALESCE(NULLIF(exact_time, ''), "
            "CASE WHEN NULLIF(date, '') IS NOT NULL THEN date || ' 00:00:00' END, "
            "created_at) DESC, created_at DESC"
        )
        with self._get_read_connection() as conn:
            cursor = conn.cursor()

            # 构建基础查询
            if include_content:
                base_query = "SELECT * FROM articles"
            else:
                base_query = """
                    SELECT
                        id,
                        title,
                        url,
                        date,
                        exact_time,
                        category,
                        department,
                        attachments,
                        summary,
                        ai_summary,
                        ai_tags,
                        is_read,
                        is_favorite,
                        is_deleted,
                        source_name,
                        source_type,
                        rule_id,
                        custom_summary_prompt,
                        formatting_prompt,
                        summary_prompt,
                        enable_ai_formatting,
                        enable_ai_summary,
                        created_at
                    FROM articles
                """
            conditions = []
            params = []

            # 🌟 软删除过滤：只显示未删除的文章
            conditions.append("is_deleted = 0")

            # 收藏筛选
            if favorites_only:
                conditions.append("is_favorite = 1")

            # 来源筛选
            logger.debug(
                "get_articles_paged - source_name=%s, source_names=%s",
                source_name,
                source_names,
            )

            if source_name:
                conditions.append("source_name = ?")
                params.append(source_name)
            elif source_names is not None:
                # 🌟 区分 None（不过滤）和空列表（不显示任何内容）
                if len(source_names) > 0:
                    placeholders = ','.join('?' * len(source_names))
                    conditions.append(f"source_name IN ({placeholders})")
                    params.extend(source_names)
                    logger.debug(
                        "SQL条件: source_name IN (%s), params=%s",
                        placeholders,
                        source_names,
                    )
                else:
                    # 空列表：添加一个永远为假的条件
                    conditions.append("1 = 0")
                    logger.debug("空列表，添加 1=0 条件")

            # 组装 WHERE 子句
            if conditions:
                base_query += " WHERE " + " AND ".join(conditions)

            # 所有聚合与分页查询统一按“有效发布时间”倒序，
            # 避免后端分页顺序与前端展示顺序不一致，导致首屏来源分布失真。
            base_query += f" ORDER BY {article_order_sql}"
            base_query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(base_query, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_sources(self) -> List[str]:
        """获取所有数据来源"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT source_name FROM articles ORDER BY source_name")
            return [row[0] for row in cursor.fetchall() if row[0]]

    def get_unread_count(self, source_names: Optional[List[str]] = None) -> int:
        """获取未读文章数量

        Args:
            source_names: 来源筛选列表
                - None: 不过滤（统计所有未读）
                - 空列表: 返回 0（不显示任何内容）
                - 有内容: 统计指定来源的未读

        Returns:
            未读文章数量
        """
        with self._get_read_connection() as conn:
            cursor = conn.cursor()

            if source_names is None:
                # None: 不过滤
                cursor.execute("SELECT COUNT(*) FROM articles WHERE is_read = 0 AND is_deleted = 0")
            elif len(source_names) == 0:
                # 空列表: 返回 0
                return 0
            else:
                # 有内容: 统计指定来源
                placeholders = ','.join('?' * len(source_names))
                query = f"SELECT COUNT(*) FROM articles WHERE is_read = 0 AND is_deleted = 0 AND source_name IN ({placeholders})"
                cursor.execute(query, tuple(source_names))

            row = cursor.fetchone()
            return row[0] if row else 0

    def get_first_unread(self, source_names: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """获取第一个未读文章（按时间降序，最新的未读优先）

        Args:
            source_names: 来源筛选列表
                - None: 不过滤
                - 空列表: 返回 None（不显示任何内容）
                - 有内容: 查询指定来源

        Returns:
            未读文章字典，如果没有则返回 None
        """
        # 🌟 空列表: 直接返回 None
        if source_names is not None and len(source_names) == 0:
            return None

        with self._get_read_connection() as conn:
            cursor = conn.cursor()

            if source_names is None:
                # None: 不过滤
                cursor.execute("""
                    SELECT * FROM articles
                    WHERE is_read = 0 AND is_deleted = 0
                    ORDER BY COALESCE(NULLIF(exact_time, ''), date || ' 00:00:00') DESC
                    LIMIT 1
                """)
            else:
                # 有内容: 查询指定来源
                placeholders = ','.join('?' * len(source_names))
                query = f"""
                    SELECT * FROM articles
                    WHERE is_read = 0 AND is_deleted = 0 AND source_name IN ({placeholders})
                    ORDER BY COALESCE(NULLIF(exact_time, ''), date || ' 00:00:00') DESC
                    LIMIT 1
                """
                cursor.execute(query, tuple(source_names))

            row = cursor.fetchone()
            if row:
                columns = [desc[0] for desc in cursor.description]
                return dict(zip(columns, row))
            return None

    def get_article_count_by_source(self, source_name: str) -> int:
        """查询指定来源的在库文章数（用于冷启动判断）"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM articles WHERE source_name = ?", (source_name,))
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_latest_article_date_by_source(self, source_name: str) -> Optional[str]:
        """查询指定来源最新一篇文章的发布日期（用于持续追踪时间游标）"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT date FROM articles WHERE source_name = ? ORDER BY COALESCE(NULLIF(exact_time, ''), date || ' 00:00:00') DESC LIMIT 1",
                (source_name,)
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def get_latest_article_cursor_by_source(self, source_name: str) -> Optional[str]:
        """查询指定来源最新一篇文章的精确时间游标（优先 exact_time，其次 date）。"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(NULLIF(exact_time, ''), date)
                FROM articles
                WHERE source_name = ?
                ORDER BY COALESCE(NULLIF(exact_time, ''), date || ' 00:00:00') DESC
                LIMIT 1
                """,
                (source_name,),
            )
            row = cursor.fetchone()
            return row[0] if row else None

    def get_crawl_frontier_url(
        self, source_name: str, section_name: Optional[str] = None
    ) -> str:
        """读取来源/板块的本地覆盖边界 URL。"""
        normalized_section = str(section_name or "").strip()
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT frontier_url
                FROM crawl_frontiers
                WHERE source_name = ? AND section_name = ?
                LIMIT 1
                """,
                (source_name, normalized_section),
            )
            row = cursor.fetchone()
            return str(row[0] or "").strip() if row else ""

    def upsert_crawl_frontier(
        self,
        source_name: str,
        section_name: Optional[str],
        frontier_url: str,
        frontier_cursor: str = "",
    ) -> bool:
        """写入来源/板块的本地覆盖边界。"""
        normalized_section = str(section_name or "").strip()
        normalized_url = canonicalize_article_url(frontier_url)
        normalized_cursor = str(frontier_cursor or "").strip()
        if not source_name or not normalized_url:
            return False

        def do_upsert(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                INSERT INTO crawl_frontiers (
                    source_name,
                    section_name,
                    frontier_url,
                    frontier_cursor,
                    updated_at
                )
                VALUES (?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(source_name, section_name) DO UPDATE SET
                    frontier_url = excluded.frontier_url,
                    frontier_cursor = excluded.frontier_cursor,
                    updated_at = datetime('now', 'localtime')
                """,
                (
                    source_name,
                    normalized_section,
                    normalized_url,
                    normalized_cursor,
                ),
            )
            return True

        try:
            self._write_worker.submit(do_upsert, wait=True, timeout=5.0)
            return True
        except Exception as e:
            logger.warning(
                "写入 crawl frontier 失败: source=%s section=%s error=%s",
                source_name,
                normalized_section,
                e,
            )
            return False

    def get_article_by_id(self, article_id: int) -> Optional[Dict[str, Any]]:
        """根据 ID 获取文章"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_article_annotations(
        self, article_id: int, view_mode: str = "summary"
    ) -> List[Dict[str, Any]]:
        """读取指定文章和阅读模式下的批注列表。"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM article_annotations
                WHERE article_id = ? AND view_mode = ?
                ORDER BY start_offset ASC, end_offset ASC, id ASC
                """,
                (article_id, str(view_mode or "summary").strip() or "summary"),
            )
            return [dict(row) for row in cursor.fetchall()]

    def search_articles(
        self,
        keyword: str,
        limit: int = 50,
        source_name: Optional[str] = None,
        source_names: Optional[List[str]] = None,
        favorites_only: bool = False,
        include_content: bool = True,
    ) -> List[Dict]:
        """搜索文章（支持搜索AI总结标签）

        Args:
            keyword: 搜索关键词
            limit: 返回数量限制
            source_name: 单个来源筛选
            source_names: 多个来源筛选列表（优先级高于 source_name）
            favorites_only: 仅返回收藏的文章
            include_content: 是否返回正文/Markdown/结构化图片等重字段
        """
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            article_order_sql = (
                "COALESCE(NULLIF(exact_time, ''), "
                "CASE WHEN NULLIF(date, '') IS NOT NULL THEN date || ' 00:00:00' END, "
                "created_at) DESC, created_at DESC"
            )

            # 解析布尔搜索语法
            or_groups = [g.strip() for g in re.split(r'\s+or\s+', keyword, flags=re.IGNORECASE)]

            sql_or_conditions = []
            params = []

            for group in or_groups:
                group = re.sub(r'\s+and\s+', ' ', group, flags=re.IGNORECASE)
                and_terms = [t.strip() for t in group.split() if t.strip()]

                if not and_terms:
                    continue

                and_conditions = []
                for term in and_terms:
                    like_term = f"%{term}%"
                    # 🌟 核心修改：添加标签搜索支持
                    # 标签格式：【标签名】，存储在 summary 字段开头
                    # 使用 SQLite 的 substr 和 instr 函数提取标签进行匹配
                    term_cond = ("(title LIKE ? OR date LIKE ? OR raw_text LIKE ? OR summary LIKE ? "
                                 "OR raw_markdown LIKE ? OR enhanced_markdown LIKE ? OR ai_summary LIKE ? OR ai_tags LIKE ? "
                                 "OR department LIKE ? OR category LIKE ? OR source_name LIKE ? "
                                 "OR attachments LIKE ? "
                                 # 🌟 新增：专门搜索标签部分（summary 中【】内的内容）
                                 f"OR (summary LIKE '%【%{term}%】%' OR summary LIKE '%【{term}%') "
                                 f"OR (ai_tags LIKE '%{term}%'))")
                    and_conditions.append(term_cond)

                    params.extend([like_term] * 12)

                sql_or_conditions.append("(" + " AND ".join(and_conditions) + ")")

            if not sql_or_conditions:
                base_condition = "1=1"
            else:
                base_condition = "(" + " OR ".join(sql_or_conditions) + ")"

            # 🌟 构建额外条件
            extra_conditions = ["is_deleted = 0"]  # 🌟 软删除过滤
            if source_name:
                extra_conditions.append("source_name = ?")
                params.append(source_name)
            elif source_names is not None:
                # 🌟 区分 None（不过滤）和空列表（不显示任何内容）
                if len(source_names) > 0:
                    placeholders = ','.join('?' * len(source_names))
                    extra_conditions.append(f"source_name IN ({placeholders})")
                    params.extend(source_names)
                else:
                    # 空列表：添加一个永远为假的条件
                    extra_conditions.append("1 = 0")
            if favorites_only:
                extra_conditions.append("is_favorite = 1")

            where_clause = base_condition
            if extra_conditions:
                where_clause += " AND " + " AND ".join(extra_conditions)

            if include_content:
                select_clause = "SELECT *"
            else:
                select_clause = """
                SELECT
                    id,
                    title,
                    url,
                    date,
                    exact_time,
                    category,
                    department,
                    attachments,
                    summary,
                    ai_summary,
                    ai_tags,
                    raw_text,
                    enhanced_markdown,
                    is_read,
                    is_favorite,
                    is_deleted,
                    source_name,
                    source_type,
                    rule_id,
                    custom_summary_prompt,
                    formatting_prompt,
                    summary_prompt,
                    enable_ai_formatting,
                    enable_ai_summary,
                    created_at
                """

            query = f"""
            {select_clause} FROM articles
            WHERE {where_clause}
            ORDER BY {article_order_sql}
            LIMIT ?
            """

            params.append(limit)
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

            # 结果后处理：上下文摘录
            results = []
            raw_terms = re.split(r'\s+', keyword)
            terms = [t for t in raw_terms if t.lower() not in ('and', 'or') and t.strip()]

            for row in rows:
                item = dict(row)
                summary = item.get('summary') or ""
                title = item.get('title') or ""
                raw_text = item.get('raw_text') or ""
                enhanced_markdown = item.get('enhanced_markdown') or ""
                ai_summary = item.get('ai_summary') or ""

                missed_in_ui = [
                    t for t in terms
                    if t.lower() not in summary.lower()
                    and t.lower() not in title.lower()
                    and t.lower() not in enhanced_markdown.lower()
                    and t.lower() not in ai_summary.lower()
                ]

                if missed_in_ui and raw_text:
                    snippets = []
                    for t in missed_in_ui:
                        idx = raw_text.lower().find(t.lower())
                        if idx != -1:
                            start = max(0, idx - 20)
                            end = min(len(raw_text), idx + len(t) + 30)
                            prefix = "..." if start > 0 else ""
                            suffix = "..." if end < len(raw_text) else ""

                            snippet_text = raw_text[start:end].replace('\n', ' ')
                            snippet_text = re.sub(f"({re.escape(t)})", r"<strong>\1</strong>", snippet_text, flags=re.IGNORECASE)
                            snippets.append(f"{prefix}{snippet_text}{suffix}")

                    if snippets:
                        snippets_html = "<br><br>".join(snippets)
                        html_block = f"""
<div class="search-snippet-box">
    <div class="snippet-header">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        原文内容
    </div>
    <div class="snippet-content">{snippets_html}</div>
</div>"""
                        item['search_snippet_html'] = html_block

                if not include_content:
                    item["has_full_content"] = False
                    item.pop("raw_text", None)
                    item.pop("enhanced_markdown", None)

                results.append(item)

            return results

    # ==================== 写操作 ====================

    def _normalize_datetime(self, datetime_str: str, date_str: str = "") -> str:
        """
        标准化日期时间格式为 YYYY-MM-DD HH:MM:SS

        Args:
            datetime_str: 原始日期时间字符串（可能包含各种格式）
            date_str: 备用日期字符串（仅日期部分）

        Returns:
            标准化的日期时间字符串，格式为 "YYYY-MM-DD HH:MM:SS"
        """
        import re
        from datetime import datetime

        if not datetime_str and not date_str:
            return ""

        # 优先使用 datetime_str
        source = datetime_str if datetime_str else date_str

        # 统一分隔符：将中文和斜杠替换为横杠
        source = source.replace('年', '-').replace('月', '-').replace('日', ' ').replace('/', '-')

        # 尝试提取日期时间部分
        # 支持格式：2026-03-25 14:30:00, 2026-03-25 14:30, 2026-03-25
        match = re.match(
            r'(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?',
            source.strip()
        )

        if match:
            year, month, day = match.group(1), match.group(2), match.group(3)
            hour = match.group(4) or "00"
            minute = match.group(5) or "00"
            second = match.group(6) or "00"

            # 补零对齐
            month = month.zfill(2)
            day = day.zfill(2)
            hour = hour.zfill(2)
            minute = minute.zfill(2)
            second = second.zfill(2)

            return f"{year}-{month}-{day} {hour}:{minute}:{second}"

        # 无法解析，返回原始字符串
        return datetime_str

    def _normalize_date(self, date_str: str) -> str:
        """
        标准化日期格式为 YYYY-MM-DD

        Args:
            date_str: 原始日期字符串

        Returns:
            标准化的日期字符串，格式为 "YYYY-MM-DD"
        """
        import re

        if not date_str:
            return ""

        # 统一分隔符
        source = date_str.replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')

        # 提取日期部分
        match = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', source.strip())

        if match:
            year, month, day = match.group(1), match.group(2), match.group(3)
            return f"{year}-{month.zfill(2)}-{day.zfill(2)}"

        return date_str

    def insert_or_update_article(self, title: str, url: str, date: str, exact_time: str,
                                  category: str, department: str, attachments: str,
                                  summary: str, raw_content: str, source_name: str = '公文通',
                                  rule_id: str = '', custom_summary_prompt: str = '',
                                  source_type: str = 'html', raw_markdown: str = '',
                                  enhanced_markdown: str = '', ai_summary: str = '',
                                  ai_tags: Optional[List[str]] = None,
                                  formatting_prompt: str = '',
                                  summary_prompt: str = '',
                                  enable_ai_formatting: bool = False,
                                  enable_ai_summary: bool = False,
                                  content_blocks: Optional[List[Dict[str, Any]]] = None,
                                  image_assets: Optional[List[Dict[str, Any]]] = None):
        """插入或更新文章（异步写入）"""
        url = canonicalize_article_url(url)
        current_hash = self.generate_hash(raw_content)
        summary = strip_emoji(summary)
        custom_summary_prompt = strip_emoji(custom_summary_prompt)
        raw_markdown = strip_emoji(raw_markdown)
        enhanced_markdown = strip_emoji(enhanced_markdown)
        ai_summary = strip_emoji(ai_summary)
        formatting_prompt = strip_emoji(formatting_prompt)
        summary_prompt = strip_emoji(summary_prompt)
        ai_tags_json = serialize_tags(ai_tags or [])
        content_blocks_json = json.dumps(content_blocks or [], ensure_ascii=False)
        image_assets_json = json.dumps(image_assets or [], ensure_ascii=False)

        # 🌟 标准化日期和时间格式
        date = self._normalize_date(date)
        exact_time = self._normalize_datetime(exact_time, date)

        def do_insert(cursor: sqlite3.Cursor):
            cursor.execute('''
                REPLACE INTO articles
                (title, url, date, exact_time, category, department, attachments, summary, raw_text, raw_markdown, enhanced_markdown, ai_summary, ai_tags, raw_hash, is_read, source_name, source_type, rule_id, custom_summary_prompt, formatting_prompt, summary_prompt, enable_ai_formatting, enable_ai_summary, content_blocks, image_assets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                title, url, date, exact_time, category, department, attachments,
                summary, raw_content, raw_markdown, enhanced_markdown, ai_summary,
                ai_tags_json, current_hash, source_name, source_type, rule_id,
                custom_summary_prompt, formatting_prompt, summary_prompt,
                int(enable_ai_formatting), int(enable_ai_summary),
                content_blocks_json, image_assets_json
            ))
            return True

        # 提交到写队列，不等待结果（异步写入）
        try:
            self._write_worker.submit(do_insert, wait=False)
        except Exception as e:
            logger.error(f"提交写入任务失败: {e}")
            raise

    def insert_or_update_article_sync(self, title: str, url: str, date: str, exact_time: str,
                                        category: str, department: str, attachments: str,
                                        summary: str, raw_content: str, source_name: str = '公文通',
                                        rule_id: str = '', custom_summary_prompt: str = '',
                                        source_type: str = 'html', raw_markdown: str = '',
                                        enhanced_markdown: str = '', ai_summary: str = '',
                                        ai_tags: Optional[List[str]] = None,
                                        formatting_prompt: str = '',
                                        summary_prompt: str = '',
                                        enable_ai_formatting: bool = False,
                                        enable_ai_summary: bool = False,
                                        content_blocks: Optional[List[Dict[str, Any]]] = None,
                                        image_assets: Optional[List[Dict[str, Any]]] = None) -> bool:
        """插入或更新文章（同步版本，等待写入完成）"""
        url = canonicalize_article_url(url)
        current_hash = self.generate_hash(raw_content)
        summary = strip_emoji(summary)
        custom_summary_prompt = strip_emoji(custom_summary_prompt)
        raw_markdown = strip_emoji(raw_markdown)
        enhanced_markdown = strip_emoji(enhanced_markdown)
        ai_summary = strip_emoji(ai_summary)
        formatting_prompt = strip_emoji(formatting_prompt)
        summary_prompt = strip_emoji(summary_prompt)
        ai_tags_json = serialize_tags(ai_tags or [])
        content_blocks_json = json.dumps(content_blocks or [], ensure_ascii=False)
        image_assets_json = json.dumps(image_assets or [], ensure_ascii=False)

        # 🌟 标准化日期和时间格式
        date = self._normalize_date(date)
        exact_time = self._normalize_datetime(exact_time, date)

        def do_insert(cursor: sqlite3.Cursor):
            cursor.execute('''
                REPLACE INTO articles
                (title, url, date, exact_time, category, department, attachments, summary, raw_text, raw_markdown, enhanced_markdown, ai_summary, ai_tags, raw_hash, is_read, source_name, source_type, rule_id, custom_summary_prompt, formatting_prompt, summary_prompt, enable_ai_formatting, enable_ai_summary, content_blocks, image_assets)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                title, url, date, exact_time, category, department, attachments,
                summary, raw_content, raw_markdown, enhanced_markdown, ai_summary,
                ai_tags_json, current_hash, source_name, source_type, rule_id,
                custom_summary_prompt, formatting_prompt, summary_prompt,
                int(enable_ai_formatting), int(enable_ai_summary),
                content_blocks_json, image_assets_json
            ))
            return True

        try:
            self._write_worker.submit(do_insert, wait=True, timeout=15.0)
            return True
        except Exception as e:
            logger.error(f"同步写入失败: {e}")
            return False

    def backfill_exact_time(self) -> int:
        """
        为 exact_time 为空但有 date 的文章填充默认时间

        Returns:
            更新的记录数
        """
        def do_backfill(cursor: sqlite3.Cursor):
            # 查找 exact_time 为空但有 date 的记录
            cursor.execute('''
                UPDATE articles
                SET exact_time = date || ' 00:00:00'
                WHERE (exact_time IS NULL OR exact_time = '')
                  AND date IS NOT NULL
                  AND date != ''
            ''')
            return cursor.rowcount

        try:
            result = self._write_worker.submit(do_backfill, wait=True, timeout=30.0)
            count = result.result() if hasattr(result, 'result') else result
            if count and count > 0:
                logger.info(f"已为 {count} 篇文章填充默认时间")
            return count or 0
        except Exception as e:
            logger.error(f"填充默认时间失败: {e}")
            return 0

    def mark_as_read(self, url: str):
        """标记文章为已读"""
        def do_mark(cursor: sqlite3.Cursor):
            cursor.execute("UPDATE articles SET is_read = 1 WHERE url = ?", (url,))
            return True

        try:
            self._write_worker.submit(do_mark, wait=False)
        except Exception as e:
            logger.error(f"标记已读失败: {e}")

    def toggle_favorite(self, url: str) -> bool:
        """
        切换文章收藏状态

        Args:
            url: 文章 URL（唯一标识）

        Returns:
            切换后的收藏状态（True=已收藏，False=未收藏）
        """
        def do_toggle(cursor: sqlite3.Cursor):
            # 先查询当前状态
            cursor.execute("SELECT is_favorite FROM articles WHERE url = ?", (url,))
            row = cursor.fetchone()
            if row is None:
                return False

            current_status = row['is_favorite'] if row['is_favorite'] else 0
            new_status = 0 if current_status else 1

            cursor.execute(
                "UPDATE articles SET is_favorite = ? WHERE url = ?",
                (new_status, url)
            )
            return bool(new_status)

        try:
            return self._write_worker.submit(do_toggle, wait=True, timeout=5.0)
        except Exception as e:
            logger.error(f"切换收藏状态失败: {e}")
            return False

    def update_summary(self, article_id: int, new_summary: str) -> bool:
        """更新文章摘要（同步）"""
        new_summary = strip_emoji(new_summary)
        tags, body = extract_leading_tags(new_summary)
        compatibility_summary = compose_tagged_markdown(tags, body)

        def do_update(cursor: sqlite3.Cursor):
            cursor.execute(
                "UPDATE articles SET summary = ?, ai_summary = ?, ai_tags = ? WHERE id = ?",
                (compatibility_summary, body, serialize_tags(tags), article_id)
            )
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_update, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"更新摘要失败: {e}")
            return False

    def update_rss_ai_content(
        self,
        article_id: int,
        enhanced_markdown: str,
        ai_summary: str,
        ai_tags: Optional[List[str]] = None,
    ) -> bool:
        """更新 RSS 增强正文、摘要与标签。"""
        enhanced_markdown = strip_emoji(enhanced_markdown)
        ai_summary = strip_emoji(ai_summary)
        compatibility_summary = compose_tagged_markdown(ai_tags or [], ai_summary)

        def do_update(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                UPDATE articles
                SET enhanced_markdown = ?, ai_summary = ?, ai_tags = ?, summary = ?
                WHERE id = ?
                """,
                (
                    enhanced_markdown,
                    ai_summary,
                    serialize_tags(ai_tags or []),
                    compatibility_summary,
                    article_id,
                ),
            )
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_update, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"更新 RSS AI 内容失败: {e}")
            return False

    def update_rss_detail_content(
        self,
        article_id: int,
        *,
        raw_text: Optional[str] = None,
        raw_markdown: Optional[str] = None,
        enhanced_markdown: Optional[str] = None,
        ai_summary: Optional[str] = None,
        ai_tags: Optional[List[str]] = None,
        summary: Optional[str] = None,
    ) -> bool:
        """按需更新 RSS 正文/增强正文/摘要相关字段。"""
        assignments = []
        values: List[Any] = []

        def push_text(field_name: str, field_value: Optional[str]) -> None:
            if field_value is None:
                return
            assignments.append(f"{field_name} = ?")
            values.append(strip_emoji(field_value))

        push_text("raw_text", raw_text)
        push_text("raw_markdown", raw_markdown)
        push_text("enhanced_markdown", enhanced_markdown)
        push_text("ai_summary", ai_summary)
        push_text("summary", summary)

        next_raw_hash_source = None
        if raw_text is not None:
            next_raw_hash_source = raw_text
        elif raw_markdown is not None:
            next_raw_hash_source = raw_markdown
        if next_raw_hash_source is not None:
            assignments.append("raw_hash = ?")
            values.append(self.generate_hash(next_raw_hash_source))

        if ai_tags is not None:
            assignments.append("ai_tags = ?")
            values.append(serialize_tags(ai_tags))

        if not assignments:
            return True

        def do_update(cursor: sqlite3.Cursor):
            cursor.execute(
                f"UPDATE articles SET {', '.join(assignments)} WHERE id = ?",
                (*values, article_id),
            )
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_update, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"更新 RSS 正文失败: {e}")
            return False

    def upsert_article_annotation(
        self,
        *,
        article_id: int,
        view_mode: str = "summary",
        anchor_text: str = "",
        anchor_prefix: str = "",
        anchor_suffix: str = "",
        start_offset: int = 0,
        end_offset: int = 0,
        style_payload: Optional[Dict[str, Any]] = None,
        annotation_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """新增或更新文章批注。"""
        safe_view_mode = str(view_mode or "summary").strip() or "summary"
        safe_anchor_text = strip_emoji(str(anchor_text or ""))
        safe_anchor_prefix = strip_emoji(str(anchor_prefix or ""))
        safe_anchor_suffix = strip_emoji(str(anchor_suffix or ""))
        safe_start_offset = max(int(start_offset or 0), 0)
        safe_end_offset = max(int(end_offset or 0), safe_start_offset)
        safe_style_payload = json.dumps(
            style_payload or {},
            ensure_ascii=False,
            sort_keys=True,
        )
        safe_annotation_id = (
            int(annotation_id) if annotation_id is not None else None
        )

        def do_upsert(cursor: sqlite3.Cursor):
            if safe_annotation_id is not None:
                cursor.execute(
                    """
                    UPDATE article_annotations
                    SET view_mode = ?,
                        anchor_text = ?,
                        anchor_prefix = ?,
                        anchor_suffix = ?,
                        start_offset = ?,
                        end_offset = ?,
                        style_payload = ?,
                        updated_at = datetime('now', 'localtime')
                    WHERE id = ? AND article_id = ?
                    """,
                    (
                        safe_view_mode,
                        safe_anchor_text,
                        safe_anchor_prefix,
                        safe_anchor_suffix,
                        safe_start_offset,
                        safe_end_offset,
                        safe_style_payload,
                        safe_annotation_id,
                        article_id,
                    ),
                )
                if cursor.rowcount <= 0:
                    return None
                target_id = safe_annotation_id
            else:
                cursor.execute(
                    """
                    INSERT INTO article_annotations (
                        article_id,
                        view_mode,
                        anchor_text,
                        anchor_prefix,
                        anchor_suffix,
                        start_offset,
                        end_offset,
                        style_payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        safe_view_mode,
                        safe_anchor_text,
                        safe_anchor_prefix,
                        safe_anchor_suffix,
                        safe_start_offset,
                        safe_end_offset,
                        safe_style_payload,
                    ),
                )
                target_id = int(cursor.lastrowid or 0)

            if target_id <= 0:
                return None

            cursor.execute(
                "SELECT * FROM article_annotations WHERE id = ?",
                (target_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

        try:
            return self._write_worker.submit(do_upsert, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"保存文章批注失败: {e}")
            return None

    def delete_article_annotation(
        self,
        annotation_id: int,
        *,
        article_id: Optional[int] = None,
    ) -> bool:
        """删除单条文章批注。"""

        def do_delete(cursor: sqlite3.Cursor):
            if article_id is None:
                cursor.execute(
                    "DELETE FROM article_annotations WHERE id = ?",
                    (annotation_id,),
                )
            else:
                cursor.execute(
                    "DELETE FROM article_annotations WHERE id = ? AND article_id = ?",
                    (annotation_id, article_id),
                )
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_delete, wait=True, timeout=5.0)
        except Exception as e:
            logger.error(f"删除文章批注失败: {e}")
            return False

    def delete_article_annotations(
        self,
        article_id: int,
        annotation_ids: List[int],
    ) -> int:
        """批量删除指定文章的批注。"""
        normalized_ids = [
            int(annotation_id)
            for annotation_id in annotation_ids
            if str(annotation_id).strip()
        ]
        if not normalized_ids:
            return 0

        def do_delete(cursor: sqlite3.Cursor):
            placeholders = ",".join("?" for _ in normalized_ids)
            cursor.execute(
                f"""
                DELETE FROM article_annotations
                WHERE article_id = ?
                  AND id IN ({placeholders})
                """,
                (article_id, *normalized_ids),
            )
            return int(cursor.rowcount or 0)

        try:
            return self._write_worker.submit(do_delete, wait=True, timeout=5.0)
        except Exception as e:
            logger.error(f"批量删除文章批注失败: {e}")
            return 0

    def delete_article_annotations_by_view_modes(
        self,
        article_id: int,
        view_modes: List[str],
    ) -> int:
        """按阅读模式批量删除指定文章的批注。"""
        normalized_modes = list(
            dict.fromkeys(
                str(view_mode or "").strip()
                for view_mode in (view_modes or [])
                if str(view_mode or "").strip()
            )
        )
        if article_id <= 0 or not normalized_modes:
            return 0

        def do_delete(cursor: sqlite3.Cursor):
            placeholders = ",".join("?" for _ in normalized_modes)
            cursor.execute(
                f"""
                DELETE FROM article_annotations
                WHERE article_id = ?
                  AND view_mode IN ({placeholders})
                """,
                (article_id, *normalized_modes),
            )
            return int(cursor.rowcount or 0)

        try:
            return self._write_worker.submit(do_delete, wait=True, timeout=5.0)
        except Exception as e:
            logger.error(f"按阅读模式删除文章批注失败: {e}")
            return 0

    def enqueue_telemetry_event(
        self,
        *,
        event_id: str,
        event_name: str,
        payload_json: str,
        created_at: Optional[int] = None,
        next_retry_at: Optional[int] = None,
    ) -> bool:
        """写入一条待上报遥测事件。"""
        safe_created_at = int(created_at or 0) or int(time.time())
        safe_next_retry_at = int(next_retry_at or 0) or safe_created_at

        def do_insert(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                INSERT OR IGNORE INTO telemetry_events (
                    event_id,
                    event_name,
                    payload_json,
                    status,
                    retry_count,
                    created_at,
                    next_retry_at,
                    last_error,
                    sent_at
                )
                VALUES (?, ?, ?, 'pending', 0, ?, ?, '', 0)
                """,
                (
                    str(event_id or "").strip(),
                    str(event_name or "").strip(),
                    str(payload_json or "{}"),
                    safe_created_at,
                    safe_next_retry_at,
                ),
            )
            return cursor.rowcount > 0

        try:
            return bool(self._write_worker.submit(do_insert, wait=True, timeout=5.0))
        except Exception as e:
            logger.error(f"写入遥测事件失败: {e}")
            return False

    def get_pending_telemetry_events(
        self,
        *,
        limit: int = 20,
        now_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """读取待上报或失败后可重试的遥测事件。"""
        safe_limit = max(int(limit or 20), 1)
        safe_now = int(now_ts or 0) or int(time.time())
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM telemetry_events
                WHERE status IN ('pending', 'failed')
                  AND next_retry_at <= ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (safe_now, safe_limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def mark_telemetry_events_sent(
        self,
        event_ids: List[str],
        *,
        sent_at: Optional[int] = None,
    ) -> int:
        """批量标记遥测事件已成功发送。"""
        normalized_ids = [
            str(event_id or "").strip()
            for event_id in (event_ids or [])
            if str(event_id or "").strip()
        ]
        if not normalized_ids:
            return 0
        safe_sent_at = int(sent_at or 0) or int(time.time())

        def do_update(cursor: sqlite3.Cursor):
            placeholders = ",".join("?" for _ in normalized_ids)
            cursor.execute(
                f"""
                UPDATE telemetry_events
                SET status = 'sent',
                    sent_at = ?,
                    next_retry_at = 0,
                    last_error = ''
                WHERE event_id IN ({placeholders})
                """,
                (safe_sent_at, *normalized_ids),
            )
            return int(cursor.rowcount or 0)

        try:
            return int(self._write_worker.submit(do_update, wait=True, timeout=5.0))
        except Exception as e:
            logger.error(f"标记遥测事件已发送失败: {e}")
            return 0

    def mark_telemetry_events_failed(
        self,
        failure_items: List[Dict[str, Any]],
    ) -> int:
        """批量更新遥测事件失败状态与下次重试时间。"""
        normalized_items = []
        for item in failure_items or []:
            event_id = str((item or {}).get("event_id") or "").strip()
            if not event_id:
                continue
            normalized_items.append(
                {
                    "event_id": event_id,
                    "retry_count": max(int((item or {}).get("retry_count") or 0), 0),
                    "next_retry_at": max(
                        int((item or {}).get("next_retry_at") or 0),
                        int(time.time()),
                    ),
                    "last_error": str((item or {}).get("last_error") or "").strip(),
                }
            )
        if not normalized_items:
            return 0

        def do_update(cursor: sqlite3.Cursor):
            updated = 0
            for item in normalized_items:
                cursor.execute(
                    """
                    UPDATE telemetry_events
                    SET status = 'failed',
                        retry_count = ?,
                        next_retry_at = ?,
                        last_error = ?
                    WHERE event_id = ?
                    """,
                    (
                        item["retry_count"],
                        item["next_retry_at"],
                        item["last_error"],
                        item["event_id"],
                    ),
                )
                updated += int(cursor.rowcount or 0)
            return updated

        try:
            return int(self._write_worker.submit(do_update, wait=True, timeout=5.0))
        except Exception as e:
            logger.error(f"更新遥测事件失败状态失败: {e}")
            return 0

    def clear_telemetry_events(self, statuses: Optional[List[str]] = None) -> int:
        """清空遥测事件队列，可按状态过滤。"""
        normalized_statuses = [
            str(status or "").strip()
            for status in (statuses or [])
            if str(status or "").strip()
        ]

        def do_delete(cursor: sqlite3.Cursor):
            if normalized_statuses:
                placeholders = ",".join("?" for _ in normalized_statuses)
                cursor.execute(
                    f"DELETE FROM telemetry_events WHERE status IN ({placeholders})",
                    tuple(normalized_statuses),
                )
            else:
                cursor.execute("DELETE FROM telemetry_events")
            return int(cursor.rowcount or 0)

        try:
            return int(self._write_worker.submit(do_delete, wait=True, timeout=5.0))
        except Exception as e:
            logger.error(f"清空遥测事件失败: {e}")
            return 0

    def prune_sent_telemetry_events(self, older_than_ts: int) -> int:
        """清理过旧的已发送遥测事件。"""

        def do_delete(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                DELETE FROM telemetry_events
                WHERE status = 'sent'
                  AND sent_at > 0
                  AND sent_at < ?
                """,
                (int(older_than_ts or 0),),
            )
            return int(cursor.rowcount or 0)

        try:
            return int(self._write_worker.submit(do_delete, wait=True, timeout=5.0))
        except Exception as e:
            logger.error(f"清理已发送遥测事件失败: {e}")
            return 0

    def get_telemetry_queue_stats(self, now_ts: Optional[int] = None) -> Dict[str, int]:
        """返回遥测队列统计。"""
        safe_now = int(now_ts or 0) or int(time.time())
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                    SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent_count,
                    SUM(
                        CASE
                            WHEN status IN ('pending', 'failed')
                                 AND next_retry_at <= ?
                            THEN 1 ELSE 0
                        END
                    ) AS ready_count
                FROM telemetry_events
                """,
                (safe_now,),
            )
            row = cursor.fetchone()
            if not row:
                return {
                    "total_count": 0,
                    "pending_count": 0,
                    "failed_count": 0,
                    "sent_count": 0,
                    "ready_count": 0,
                }
            return {
                "total_count": int(row["total_count"] or 0),
                "pending_count": int(row["pending_count"] or 0),
                "failed_count": int(row["failed_count"] or 0),
                "sent_count": int(row["sent_count"] or 0),
                "ready_count": int(row["ready_count"] or 0),
            }

    def sync_rss_rule_ai_config(
        self,
        rule_id: str,
        *,
        formatting_prompt: str = "",
        summary_prompt: str = "",
        custom_summary_prompt: str = "",
        enable_ai_formatting: bool = False,
        enable_ai_summary: bool = False,
    ) -> int:
        """将 RSS 规则的 AI 配置同步到已入库文章。"""
        clean_rule_id = str(rule_id or "").strip()
        if not clean_rule_id:
            return 0

        formatting_prompt = strip_emoji(formatting_prompt)
        summary_prompt = strip_emoji(summary_prompt)
        custom_summary_prompt = strip_emoji(custom_summary_prompt)

        def do_update(cursor: sqlite3.Cursor):
            cursor.execute(
                """
                UPDATE articles
                SET formatting_prompt = ?,
                    summary_prompt = ?,
                    custom_summary_prompt = ?,
                    enable_ai_formatting = ?,
                    enable_ai_summary = ?
                WHERE rule_id = ? AND source_type = 'rss'
                """,
                (
                    formatting_prompt,
                    summary_prompt,
                    custom_summary_prompt,
                    int(enable_ai_formatting),
                    int(enable_ai_summary),
                    clean_rule_id,
                ),
            )
            return int(cursor.rowcount or 0)

        try:
            return self._write_worker.submit(do_update, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"同步 RSS 规则 AI 配置失败: {e}")
            return 0

    def delete_articles_by_rule_id(
        self, rule_id: str, hard_delete: bool = True
    ) -> int:
        """按规则 ID 删除对应的文章。"""
        clean_rule_id = str(rule_id or "").strip()
        if not clean_rule_id:
            return 0

        def do_delete(cursor: sqlite3.Cursor):
            if hard_delete:
                cursor.execute(
                    """
                    DELETE FROM article_annotations
                    WHERE article_id IN (
                        SELECT id FROM articles WHERE rule_id = ?
                    )
                    """,
                    (clean_rule_id,),
                )
                cursor.execute(
                    "DELETE FROM articles WHERE rule_id = ?",
                    (clean_rule_id,),
                )
            else:
                cursor.execute(
                    "UPDATE articles SET is_deleted = 1 WHERE rule_id = ?",
                    (clean_rule_id,),
                )
            return int(cursor.rowcount or 0)

        try:
            return self._write_worker.submit(do_delete, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"按规则删除文章失败: {e}")
            return 0

    def delete_article(self, article_id: int, hard_delete: bool = False) -> bool:
        """
        删除文章

        Args:
            article_id: 文章 ID
            hard_delete: True 为物理删除（清除记录，允许重新抓取），
                        False 为软删除（屏蔽，不再抓取）

        Returns:
            是否删除成功
        """
        def do_delete(cursor: sqlite3.Cursor):
            if hard_delete:
                cursor.execute(
                    "DELETE FROM article_annotations WHERE article_id = ?",
                    (article_id,),
                )
                cursor.execute("DELETE FROM articles WHERE id = ?", (article_id,))
                logger.info(f"硬删除文章: id={article_id}")
            else:
                cursor.execute("UPDATE articles SET is_deleted = 1 WHERE id = ?", (article_id,))
                logger.info(f"软删除文章: id={article_id}")
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_delete, wait=True, timeout=5.0)
        except Exception as e:
            logger.error(f"删除文章失败: {e}")
            return False

    # ==================== 管理 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        write_stats = self._write_worker.get_stats()
        return {
            'write_stats': write_stats,
            'write_queue_size': self._write_worker.get_queue_size(),
            'write_queue_max_size': self._write_worker._queue_size,
            'queue_full_count': write_stats.get('queue_full', 0)
        }

    def close(self):
        """关闭所有资源"""
        logger.info("正在关闭数据库管理器...")
        self._write_worker.stop()
        self._read_pool.close_all()
        logger.info("数据库管理器已关闭")

    # ==================== 兼容旧 API ====================

    def get_connection(self):
        """兼容旧 API：获取读连接"""
        return self._read_pool.get()


# ==================== 延迟初始化单例 ====================

_db_instance: Optional[DatabaseManager] = None
_db_lock = threading.Lock()


def get_db() -> DatabaseManager:
    """
    获取数据库管理器单例（延迟初始化）

    解决循环导入问题：
    - 模块加载时不创建实例
    - 首次调用时才初始化
    """
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = DatabaseManager()
    return _db_instance


# 兼容旧代码：db 属性访问（延迟初始化）
class _DBProxy:
    """数据库代理类，支持延迟初始化和属性透明转发"""

    __func__ = None  # 兼容 unittest.mock/inspect 的异步对象探测

    def __getattr__(self, name):
        if name == "__func__":
            raise AttributeError(name)
        return getattr(get_db(), name)

    def __repr__(self):
        return repr(get_db())


db = _DBProxy()
