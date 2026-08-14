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

# 本进程已完成建表的 DB 路径集合：PaperDB 是每请求/每操作热路径构造，
# 同一路径第二次起跳过全量 DDL（SQLite DDL 幂等，跨进程首建由 CREATE IF NOT EXISTS 兜底）。
_INITIALIZED_PATHS: set[str] = set()


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
        try:
            # WAL 已持久化于 DB 文件，此处冗余执行仅兜底旧库/损坏重建；PRAGMA 抛错时关闭连接防泄漏
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def _init_db(self):
        """创建表结构（幂等；持模块级写锁，避免并发首次构造时的 DDL 写竞争）。

        同一路径第二次构造起跳过（_INITIALIZED_PATHS 缓存），避免热路径每次
        实例化都全量跑一遍 DDL 并占用全局写锁。
        """
        key = str(self._path.resolve())
        if key in _INITIALIZED_PATHS:
            return
        with self._lock:
            if key in _INITIALIZED_PATHS:  # 等锁期间可能已被其他实例初始化
                return
            conn = self._get_conn()
            try:
                conn.executescript(PAPERS_SCHEMA_SQL + "\n" + FETCH_LOGS_SCHEMA_SQL)
            finally:
                conn.close()
            _INITIALIZED_PATHS.add(key)
