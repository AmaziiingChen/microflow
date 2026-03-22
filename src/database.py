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

import sqlite3
import hashlib
import logging
import threading
import queue
import re
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

    def __init__(self, db_path: str, pool_size: int = 3):
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
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._task_queue: queue.Queue[WriteTask] = queue.Queue(maxsize=100)
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

    def submit(self, operation: Callable[[sqlite3.Cursor], Any], wait: bool = True, timeout: float = 10.0) -> Any:
        """
        提交写操作

        Args:
            operation: 接收 cursor 的操作函数
            wait: 是否等待结果
            timeout: 等待超时时间

        Returns:
            操作结果（如果 wait=True）
        """
        if self._stop_event.is_set():
            raise RuntimeError("写入线程已停止")

        task = WriteTask(
            operation=operation,
            result_event=threading.Event()
        )

        try:
            self._task_queue.put(task, block=False)
        except queue.Full:
            with self._stats_lock:
                self._stats['queue_full'] += 1
            raise RuntimeError("写队列已满，请稍后重试")

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
                    raw_hash TEXT NOT NULL,
                    is_read INTEGER DEFAULT 0,
                    source_name TEXT DEFAULT '公文通',
                    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
                )
            ''')
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

            # 🌟 新增：is_favorite 字段迁移
            if 'is_favorite' not in columns:
                logger.info("正在执行 is_favorite 字段迁移...")
                cursor.execute("ALTER TABLE articles ADD COLUMN is_favorite INTEGER DEFAULT 0")
                logger.info("is_favorite 字段迁移完成")

            return True

        try:
            self._write_worker.submit(migrate, wait=True, timeout=30.0)
        except Exception as e:
            logger.error(f"数据库迁移失败: {e}")

    def generate_hash(self, text: str) -> str:
        """生成文本的 MD5 哈希值"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    # ==================== 读操作 ====================

    def check_if_url_exists(self, url: str) -> bool:
        """检查 URL 是否已存在"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,))
            return cursor.fetchone() is not None

    def check_if_new_or_updated(self, url: str, raw_content: str) -> tuple:
        """检查文章是否为新或有更新"""
        current_hash = self.generate_hash(raw_content)

        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_hash FROM articles WHERE url = ?", (url,))
            row = cursor.fetchone()

            if row is None:
                return True, "new"

            if row['raw_hash'] != current_hash:
                return True, "updated"

            return False, "unchanged"

    def get_articles_paged(self, limit: int = 20, offset: int = 0, source_name: Optional[str] = None, source_names: Optional[List[str]] = None, favorites_only: bool = False) -> List[Dict]:
        """分页获取文章

        Args:
            limit: 返回数量限制
            offset: 偏移量
            source_name: 单个来源筛选
            source_names: 多个来源筛选列表（当 source_name 为 None 时生效）
            favorites_only: 仅返回收藏的文章
        """
        with self._get_read_connection() as conn:
            cursor = conn.cursor()

            # 构建基础查询
            base_query = "SELECT * FROM articles"
            conditions = []
            params = []

            # 收藏筛选
            if favorites_only:
                conditions.append("is_favorite = 1")

            # 来源筛选
            if source_name:
                conditions.append("source_name = ?")
                params.append(source_name)
            elif source_names and len(source_names) > 0:
                placeholders = ','.join('?' * len(source_names))
                conditions.append(f"source_name IN ({placeholders})")
                params.extend(source_names)

            # 组装 WHERE 子句
            if conditions:
                base_query += " WHERE " + " AND ".join(conditions)

            base_query += " ORDER BY date DESC, exact_time DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(base_query, tuple(params))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_sources(self) -> List[str]:
        """获取所有数据来源"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT source_name FROM articles ORDER BY source_name")
            return [row[0] for row in cursor.fetchall() if row[0]]

    def get_article_by_id(self, article_id: int) -> Optional[Dict[str, Any]]:
        """根据 ID 获取文章"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def search_articles(self, keyword: str, limit: int = 50, source_name: Optional[str] = None, favorites_only: bool = False) -> List[Dict]:
        """搜索文章"""
        with self._get_read_connection() as conn:
            cursor = conn.cursor()

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
                    # 🌟 核心修改：在 SQL 条件中加入 attachments 字段
                    # 这样搜索文件名（如 "实验报告"）或后缀（如 ".zip"）都能匹配到
                    term_cond = ("(title LIKE ? OR date LIKE ? OR raw_text LIKE ? OR summary LIKE ? "
                                 "OR department LIKE ? OR category LIKE ? OR source_name LIKE ? "
                                 "OR attachments LIKE ?)")
                    and_conditions.append(term_cond)

                    # 🌟 注意：这里必须改成 * 8，因为括号内现在有 8 个问号
                    params.extend([like_term] * 8)

                sql_or_conditions.append("(" + " AND ".join(and_conditions) + ")")

            if not sql_or_conditions:
                base_condition = "1=1"
            else:
                base_condition = "(" + " OR ".join(sql_or_conditions) + ")"

            # 🌟 构建额外条件
            extra_conditions = []
            if source_name:
                extra_conditions.append("source_name = ?")
                params.append(source_name)
            if favorites_only:
                extra_conditions.append("is_favorite = 1")

            where_clause = base_condition
            if extra_conditions:
                where_clause += " AND " + " AND ".join(extra_conditions)

            query = f"""
            SELECT * FROM articles
            WHERE {where_clause}
            ORDER BY exact_time DESC, date DESC
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

                missed_in_ui = [
                    t for t in terms
                    if t.lower() not in summary.lower() and t.lower() not in title.lower()
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
                        item['summary'] = summary + "\n\n" + html_block

                results.append(item)

            return results

    # ==================== 写操作 ====================

    def insert_or_update_article(self, title: str, url: str, date: str, exact_time: str,
                                  category: str, department: str, attachments: str,
                                  summary: str, raw_content: str, source_name: str = '公文通'):
        """插入或更新文章（异步写入）"""
        current_hash = self.generate_hash(raw_content)

        def do_insert(cursor: sqlite3.Cursor):
            cursor.execute('''
                REPLACE INTO articles
                (title, url, date, exact_time, category, department, attachments, summary, raw_text, raw_hash, is_read, source_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ''', (title, url, date, exact_time, category, department, attachments, summary, raw_content, current_hash, source_name))
            return True

        # 提交到写队列，不等待结果（异步写入）
        try:
            self._write_worker.submit(do_insert, wait=False)
        except Exception as e:
            logger.error(f"提交写入任务失败: {e}")
            raise

    def insert_or_update_article_sync(self, title: str, url: str, date: str, exact_time: str,
                                        category: str, department: str, attachments: str,
                                        summary: str, raw_content: str, source_name: str = '公文通') -> bool:
        """插入或更新文章（同步版本，等待写入完成）"""
        current_hash = self.generate_hash(raw_content)

        def do_insert(cursor: sqlite3.Cursor):
            cursor.execute('''
                REPLACE INTO articles
                (title, url, date, exact_time, category, department, attachments, summary, raw_text, raw_hash, is_read, source_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            ''', (title, url, date, exact_time, category, department, attachments, summary, raw_content, current_hash, source_name))
            return True

        try:
            self._write_worker.submit(do_insert, wait=True, timeout=15.0)
            return True
        except Exception as e:
            logger.error(f"同步写入失败: {e}")
            return False

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
        def do_update(cursor: sqlite3.Cursor):
            cursor.execute(
                "UPDATE articles SET summary = ? WHERE id = ?",
                (new_summary, article_id)
            )
            return cursor.rowcount > 0

        try:
            return self._write_worker.submit(do_update, wait=True, timeout=10.0)
        except Exception as e:
            logger.error(f"更新摘要失败: {e}")
            return False

    # ==================== 管理 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'write_stats': self._write_worker.get_stats(),
            'write_queue_size': self._write_worker.get_queue_size()
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
    def __getattr__(self, name):
        return getattr(get_db(), name)

    def __repr__(self):
        return repr(get_db())


db = _DBProxy()
