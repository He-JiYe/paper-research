"""数据库连接管理：连接 + 线程锁 + 表初始化（PaperDB 组合基类之一）。"""

import sqlite3
import threading
from pathlib import Path

from src.db.schema import FETCH_LOGS_SCHEMA_SQL, PAPERS_SCHEMA_SQL
from src.paths import DATA_DIR

DB_PATH = DATA_DIR / "papers.db"

_SQLITE_TIMEOUT = 10  # SQLite 连接/写锁等待超时（秒）

# 全局写锁：调度器 / Web 请求 / 导入任务各自新建 PaperDB 实例，若每实例各持一把锁，
# 跨实例并发写只能靠 SQLite WAL + timeout 兜底（可能撞 database is locked）。
# 统一用模块级锁，跨实例串行写。
_WRITE_LOCK = threading.Lock()


class ConnectionMixin:
    """连接管理 + 表结构初始化（WAL + 每次操作独立连接 + 模块级写锁）。"""

    def __init__(self, db_path: str | Path | None = None):
        self._path = Path(db_path) if db_path else DB_PATH
        self._lock = _WRITE_LOCK
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=_SQLITE_TIMEOUT)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _init_db(self):
        """创建表结构（幂等；持模块级写锁，避免并发首次构造时的 DDL 写竞争）。"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executescript(PAPERS_SCHEMA_SQL + "\n" + FETCH_LOGS_SCHEMA_SQL)
            finally:
                conn.close()
