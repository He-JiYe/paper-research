"""SQLite 数据库层：论文及抓取日志持久化

替代 TempStore JSON 文件存储，提供线程安全的数据库操作。
"""

import datetime
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "papers.db"
TEMP_STORE_LEGACY_PATH = DATA_DIR / "pending_review.json"


class PaperDB:
    """SQLite 数据库操作封装。

    线程安全：写操作使用 threading.Lock。
    WAL 模式允许并发读取。
    """

    def __init__(self, db_path: str | Path | None = None):
        self._path = Path(db_path) if db_path else DB_PATH
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_legacy()

    # ─── 连接管理 ────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ─── 初始化 ───────────────────────────────────────────────────

    def _init_db(self):
        """创建表结构（幂等）。"""
        conn = self._get_conn()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS papers (
                    arxiv_id        TEXT PRIMARY KEY,
                    title           TEXT NOT NULL DEFAULT '',
                    authors         TEXT DEFAULT '',
                    abstract        TEXT DEFAULT '',
                    url             TEXT DEFAULT '',
                    categories      TEXT DEFAULT '',
                    primary_category TEXT DEFAULT '',
                    published       TEXT DEFAULT '',
                    arxiv_updated   TEXT DEFAULT '',
                    keyword_match   TEXT DEFAULT '',
                    version         INTEGER DEFAULT 1,

                    llm_summary     TEXT DEFAULT '',
                    llm_remark      TEXT DEFAULT '',
                    llm_reason      TEXT DEFAULT '',
                    llm_score       REAL DEFAULT 0.0,

                    user_mark       TEXT DEFAULT NULL,
                    short_title     TEXT DEFAULT '',
                    zotero_key      TEXT DEFAULT '',

                    status          TEXT DEFAULT 'new',
                    fetch_date      TEXT DEFAULT '',
                    marked_date     TEXT DEFAULT NULL,

                    created_at      TEXT DEFAULT (datetime('now','localtime')),
                    updated_at      TEXT DEFAULT (datetime('now','localtime'))
                );

                CREATE TABLE IF NOT EXISTS fetch_logs (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_time         TEXT DEFAULT (datetime('now','localtime')),
                    keywords_used    INTEGER DEFAULT 0,
                    papers_fetched   INTEGER DEFAULT 0,
                    papers_new       INTEGER DEFAULT 0,
                    papers_summarized INTEGER DEFAULT 0,
                    status           TEXT DEFAULT 'success',
                    error_msg        TEXT DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_papers_status
                    ON papers(status);
                CREATE INDEX IF NOT EXISTS idx_papers_user_mark
                    ON papers(user_mark);
                CREATE INDEX IF NOT EXISTS idx_papers_keyword
                    ON papers(keyword_match);
            """)
            conn.commit()
        finally:
            conn.close()

    def _migrate_legacy(self):
        """从旧 TempStore JSON 文件迁移数据（一次性）。"""
        legacy = Path(TEMP_STORE_LEGACY_PATH)
        if not legacy.exists():
            return
        try:
            raw = legacy.read_text(encoding="utf-8")
            papers = json.loads(raw) if raw.strip() else []
        except (json.JSONDecodeError, OSError):
            return

        if not papers:
            legacy.unlink(missing_ok=True)
            return

        # 只迁移还未在 DB 中的论文
        existing = self.get_existing_arxiv_ids()
        to_add = [p for p in papers if p.get("arxiv_id") not in existing]
        if to_add:
            for p in to_add:
                p.setdefault("status", "new")
                p.setdefault("fetch_date", p.get("_pending_date", ""))
            added = self._add_papers_batch(to_add)
            logger.info("从 TempStore 迁移 %d 篇论文到 DB", added)

        # 迁移完成后重命名旧文件（不直接删除，以防万一）
        legacy.rename(legacy.with_suffix(".json.bak"))

    # ─── 论文 CRUD ───────────────────────────────────────────────

    def _add_papers_batch(self, papers: list[dict]) -> int:
        """批量插入论文（内部调用，无锁）。"""
        conn = self._get_conn()
        try:
            new_count = 0
            now = datetime.datetime.now().isoformat()
            for p in papers:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO papers
                    (arxiv_id, title, authors, abstract, url,
                     categories, primary_category, published, arxiv_updated,
                     keyword_match, version,
                     llm_summary, llm_remark, llm_reason, llm_score,
                     status, fetch_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p["arxiv_id"],
                        p.get("title", ""),
                        p.get("authors", ""),
                        p.get("abstract", ""),
                        p.get("url", ""),
                        p.get("categories", ""),
                        p.get("primary_category", ""),
                        p.get("published", ""),
                        p.get("arxiv_updated", ""),
                        p.get("keyword_match", ""),
                        p.get("version", 1),
                        p.get("llm_summary", ""),
                        p.get("llm_remark", ""),
                        p.get("llm_reason", ""),
                        p.get("llm_score", 0.0),
                        p.get("status", "summarized"),
                        p.get("fetch_date", now),
                    ),
                )
                if cur.rowcount > 0:
                    new_count += 1
            conn.commit()
            return new_count
        finally:
            conn.close()

    def add_papers(self, papers: list[dict]) -> int:
        """批量添加论文（线程安全），返回新增数量。"""
        with self._lock:
            return self._add_papers_batch(papers)

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return dict(row)

    def get_paper(self, arxiv_id: str) -> dict | None:
        """获取单篇论文。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
            )
            return self._row_to_dict(cur.fetchone())
        finally:
            conn.close()

    def get_existing_arxiv_ids(self) -> set[str]:
        """获取所有已有 arxiv_id 集合（用于抓取 skip_ids）。"""
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT arxiv_id FROM papers")
            return {row["arxiv_id"] for row in cur.fetchall()}
        finally:
            conn.close()

    def get_pending(self) -> list[dict]:
        """获取待审阅论文。

        条件：已 LLM 评分 且 未标记（或标记为 pending）。
        按抓取日期降序排列。
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """SELECT * FROM papers
                WHERE status IN ('summarized','new')
                  AND (user_mark IS NULL OR user_mark = '')
                ORDER BY fetch_date DESC""",
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_all(self) -> list[dict]:
        """获取所有论文，按 fetch_date 降序。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT * FROM papers ORDER BY fetch_date DESC"
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_marked(self) -> list[dict]:
        """获取已处理的论文（已忽略 + 已导入 Zotero）。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """SELECT * FROM papers
                WHERE user_mark IN ('ignore', 'imported')
                ORDER BY marked_date DESC""",
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_lurk(self) -> list[dict]:
        """获取延后处理的论文。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                """SELECT * FROM papers
                WHERE user_mark = 'lurk'
                ORDER BY marked_date DESC""",
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ─── 标记操作 ────────────────────────────────────────────────

    def update_mark(
        self,
        arxiv_id: str,
        mark_type: str,
        short_title: str = "",
        zotero_key: str = "",
    ):
        """更新论文标记。

        Args:
            arxiv_id: Arxiv ID
            mark_type: ignore/lurk/pending/imported
            short_title: Zotero 短标题
            zotero_key: Zotero item key
        """
        now = datetime.datetime.now().isoformat()
        with self._lock:
            conn = self._get_conn()
            try:
                if mark_type == "ignore":
                    conn.execute(
                        """UPDATE papers SET
                            user_mark = ?, status = 'ignored',
                            short_title = ?,
                            marked_date = ?, updated_at = ?
                        WHERE arxiv_id = ?""",
                        (mark_type, short_title, now, now, arxiv_id),
                    )
                elif mark_type == "imported":
                    # 导入 Zotero 成功：标记已处理，记录 item key
                    conn.execute(
                        """UPDATE papers SET
                            user_mark = ?, status = 'reviewed',
                            short_title = ?, zotero_key = ?,
                            marked_date = ?, updated_at = ?
                        WHERE arxiv_id = ?""",
                        (mark_type, short_title, zotero_key, now, now, arxiv_id),
                    )
                elif mark_type == "lurk":
                    conn.execute(
                        """UPDATE papers SET
                            user_mark = ?, status = 'reviewed',
                            short_title = ?,
                            marked_date = ?, updated_at = ?
                        WHERE arxiv_id = ?""",
                        (mark_type, short_title, now, now, arxiv_id),
                    )
                elif mark_type == "pending":
                    # 取消标记，放回待审阅
                    conn.execute(
                        """UPDATE papers SET
                            user_mark = NULL, status = 'summarized',
                            short_title = '',
                            zotero_key = '',
                            marked_date = NULL, updated_at = ?
                        WHERE arxiv_id = ?""",
                        (now, arxiv_id),
                    )
                conn.commit()
            finally:
                conn.close()

    def set_zotero_key(self, arxiv_id: str, zotero_key: str):
        """设置论文的 Zotero item key。"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """UPDATE papers SET zotero_key = ?, updated_at = ?
                    WHERE arxiv_id = ?""",
                    (zotero_key, datetime.datetime.now().isoformat(), arxiv_id),
                )
                conn.commit()
            finally:
                conn.close()

    # ─── 统计 ────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息。"""
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) as c FROM papers").fetchone()["c"]

            # 按标记分布
            mark_dist = {}
            for row in conn.execute(
                "SELECT user_mark, COUNT(*) as c FROM papers WHERE user_mark IS NOT NULL AND user_mark != '' GROUP BY user_mark"
            ):
                mark_dist[row["user_mark"]] = row["c"]

            # 按评级分布
            remark_dist = {}
            for row in conn.execute(
                "SELECT llm_remark, COUNT(*) as c FROM papers WHERE llm_remark != '' GROUP BY llm_remark"
            ):
                remark_dist[row["llm_remark"]] = row["c"]

            # 按关键词分布
            kw_dist = {}
            for row in conn.execute(
                "SELECT keyword_match, COUNT(*) as c FROM papers WHERE keyword_match != '' GROUP BY keyword_match"
            ):
                kw_dist[row["keyword_match"]] = row["c"]

            pending = conn.execute(
                """SELECT COUNT(*) as c FROM papers
                WHERE status IN ('summarized','new')
                  AND (user_mark IS NULL OR user_mark = '')"""
            ).fetchone()["c"]

            return {
                "total": total,
                "pending": pending,
                "by_mark": mark_dist,
                "by_remark": remark_dist,
                "by_keyword": kw_dist,
            }
        finally:
            conn.close()

    # ─── 抓取日志 ────────────────────────────────────────────────

    def add_fetch_log(
        self,
        keywords_used: int = 0,
        papers_fetched: int = 0,
        papers_new: int = 0,
        papers_summarized: int = 0,
        status: str = "success",
        error_msg: str = "",
    ):
        """记录一条抓取日志。"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO fetch_logs
                    (keywords_used, papers_fetched, papers_new, papers_summarized, status, error_msg)
                    VALUES (?,?,?,?,?,?)""",
                    (keywords_used, papers_fetched, papers_new, papers_summarized, status, error_msg),
                )
                conn.commit()
            finally:
                conn.close()

    def get_recent_logs(self, limit: int = 10) -> list[dict]:
        """获取最近的抓取日志。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT * FROM fetch_logs ORDER BY run_time DESC LIMIT ?",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    # ─── 关闭 ────────────────────────────────────────────────────

    def close(self):
        """显式关闭（可选）。"""
        pass  # 每次操作自动创建和关闭连接
