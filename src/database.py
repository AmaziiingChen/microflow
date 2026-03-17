import sqlite3
import os
import hashlib
import logging
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# 动态获取项目根目录，确保 PyInstaller 打包后也能找到 data 文件夹
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, 'data')
DB_PATH = os.path.join(DATA_DIR, 'tongwen.db')


class DatabaseManager:
    def __init__(self):
        # 确保 data 文件夹存在
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR)

        # 🌟 数据库级别的全局写锁（使用可重入锁，支持同一线程多次获取）
        self._write_lock = threading.RLock()
        

        self.init_db()
        self._migrate_source_name()

    def get_connection(self):
        """获取数据库连接的工厂方法"""
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        # --- 核心改进：开启 WAL 模式 ---
        conn.execute('PRAGMA journal_mode=WAL;')
        # 增加同步频率，让数据写入更安全（可选）
        conn.execute('PRAGMA synchronous=NORMAL;')
        # 🌟 新增：设置繁忙等待超时（5秒），避免立即报 database is locked 错误
        conn.execute('PRAGMA busy_timeout=5000;')
        # 将返回结果转化为类似字典的对象，方便后续通过列名获取数据 (如 row['title'])
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """初始化数据库表结构"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 创建公文表
            # raw_hash: 用于比对文章内容是否发生了暗中修改（标题没变，但正文变了）
            # is_read: 0 表示未读（显示红点），1 表示已读
            # source_name: 数据来源标识（如 '公文通', '新能源学院' 等）
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
            conn.commit()

    def _migrate_source_name(self):
        """
        V2 版本迁移：无损添加 source_name 字段
        如果表已存在但缺少该字段，执行 ALTER TABLE 补充
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # 1. 检查 source_name 字段是否存在
                cursor.execute("PRAGMA table_info(articles)")
                columns = [row[1] for row in cursor.fetchall()]

                if 'source_name' not in columns:
                    logger.info("检测到旧版数据库结构，正在执行 source_name 字段迁移...")

                    # 2. 添加字段
                    cursor.execute("ALTER TABLE articles ADD COLUMN source_name TEXT DEFAULT '公文通'")

                    # 3. 将所有历史数据更新为 '公文通'
                    cursor.execute("UPDATE articles SET source_name = '公文通' WHERE source_name IS NULL")

                    conn.commit()
                    logger.info("source_name 字段迁移完成，历史数据已标记为 '公文通'")

        except Exception as e:
            logger.error(f"数据库迁移失败: {e}")

    def generate_hash(self, text):
        """生成文本的 MD5 哈希值，用于内容变动比对"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()

    def check_if_url_exists(self, url: str) -> bool:
        """
        极轻量级的查重：仅判断 URL 是否已在数据库中。
        用于在"持续追更"模式下，快速跳过已抓取的历史公文，无需发起网络请求。
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 使用 EXISTS 语句是 SQLite 中性能最高的查重方式
            cursor.execute("SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,))
            return cursor.fetchone() is not None

    def check_if_new_or_updated(self, url, raw_content):
        """
        核心业务逻辑：判断公文是否是全新的，或者是内容被暗中修改过的。
        返回 (is_new_or_updated, reason)
        """
        current_hash = self.generate_hash(raw_content)

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT raw_hash FROM articles WHERE url = ?", (url,))
            row = cursor.fetchone()

            if row is None:
                return True, "new"  # 数据库里没有这个URL，完全是新的

            if row['raw_hash'] != current_hash:
                return True, "updated"  # 有这个URL，但哈希值变了，说明教务老师悄悄改了内容

            return False, "unchanged"  # 啥也没变，忽略

    def insert_or_update_article(self, title, url, date, exact_time, category, department, attachments, summary, raw_content, source_name='公文通'):
        """插入或更新文章 - 受写锁保护，防止并发写入冲突"""
        current_hash = self.generate_hash(raw_content)
        with self._write_lock:  # 🌟 加锁保护写入操作
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    REPLACE INTO articles
                    (title, url, date, exact_time, category, department, attachments, summary, raw_text, raw_hash, is_read, source_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ''', (title, url, date, exact_time, category, department, attachments, summary, raw_content, current_hash, source_name))
                conn.commit()

    def get_articles_paged(self, limit=20, offset=0, source_name: str = None):# type: ignore
        """分页获取公文，支持按来源筛选"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if source_name:
                cursor.execute(
                    "SELECT * FROM articles WHERE source_name = ? ORDER BY date DESC, exact_time DESC LIMIT ? OFFSET ?",
                    (source_name, limit, offset)
                )
            else:
                cursor.execute(
                    "SELECT * FROM articles ORDER BY date DESC, exact_time DESC LIMIT ? OFFSET ?",
                    (limit, offset)
                )
            return [dict(row) for row in cursor.fetchall()]

    def mark_as_read(self, url):
        """标记某篇公文为已读（消除红点）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE articles SET is_read = 1 WHERE url = ?", (url,))
            conn.commit()

    def search_articles(self, keyword: str, limit: int = 50) -> list:
        """
        全局搜索接口：支持在标题、日期、正文和摘要中进行模糊匹配
        """
        with self.get_connection() as conn:
            # 确保返回字典格式
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 使用 LIKE 语法进行全字段模糊匹配
            query = """
            SELECT * FROM articles
            WHERE title LIKE ? OR date LIKE ? OR raw_text LIKE ? OR summary LIKE ?
            ORDER BY exact_time DESC, date DESC
            LIMIT ?
            """

            # SQLite 的 LIKE 需要包裹 % 符号
            like_keyword = f"%{keyword}%"
            cursor.execute(query, (like_keyword, like_keyword, like_keyword, like_keyword, limit))

            # 将 Row 对象转换为标准的 dict 列表返回
            return [dict(row) for row in cursor.fetchall()]

    def get_all_sources(self) -> list:
        """获取所有数据来源列表"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT source_name FROM articles ORDER BY source_name")
            return [row[0] for row in cursor.fetchall() if row[0]]

    def get_article_by_id(self, article_id: int) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取文章详情

        Args:
            article_id: 文章 ID

        Returns:
            文章字典，不存在则返回 None
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_summary(self, article_id: int, new_summary: str) -> bool:
        """
        更新文章的 AI 总结

        Args:
            article_id: 文章 ID
            new_summary: 新的总结内容

        Returns:
            是否更新成功
        """
        with self._write_lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE articles SET summary = ? WHERE id = ?",
                    (new_summary, article_id)
                )
                conn.commit()
                return cursor.rowcount > 0


# 实例化一个单例供其他模块直接引入使用
db = DatabaseManager()
