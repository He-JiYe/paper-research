"""论文 CRUD 原语（PaperDB 组合之一）：批量插入、查询、标记更新。

状态值统一用 ``db.enums`` 的枚举常量，SQLite 存 TEXT。
"""

import json

from src.config.settings import now_str, today_str
from src.db.connection import ConnectionMixin
from src.db.enums import PENDING_WHERE, UserMark, fetch_date_clause


def _to_json(value) -> str:
    """把 dict/str 统一转成 JSON 字符串。"""
    if isinstance(value, str):
        return value
    return json.dumps(value or {}, ensure_ascii=False)


class PaperCrudMixin(ConnectionMixin):
    """论文表 CRUD。"""

    # ─── 批量插入 ────────────────────────────────────────────

    def _add_papers_batch(self, papers: list[dict]) -> int:
        """批量插入论文（内部调用，无锁）。"""
        conn = self._get_conn()
        try:
            new_count = 0
            today = today_str()
            for p in papers:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO papers
                    (source, source_id, title, authors, abstract, url, pdf_url,
                     categories, published, updated, keyword_match, raw_data,
                     llm_summary, llm_remark, llm_reason, llm_score, score_source,
                     fetch_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p.get("source", ""),
                        p.get("source_id", ""),
                        p.get("title", ""),
                        p.get("authors", ""),
                        p.get("abstract", ""),
                        p.get("url", ""),
                        p.get("pdf_url", ""),
                        p.get("categories", ""),
                        p.get("published", ""),
                        p.get("updated", ""),
                        p.get("keyword_match", ""),
                        _to_json(p.get("raw_data", {})),
                        p.get("llm_summary", ""),
                        p.get("llm_remark", ""),
                        p.get("llm_reason", ""),
                        p.get("llm_score", 0.0),
                        p.get("score_source", ""),
                        p.get("fetch_date", today),
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

    # ─── 查询 ────────────────────────────────────────────────

    def get_paper(self, source: str, source_id: str) -> dict | None:
        """获取单篇论文。"""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT * FROM papers WHERE source = ? AND source_id = ?",
                (source, source_id),
            )
            return self._row_to_dict(cur.fetchone())
        finally:
            conn.close()

    def get_existing_ids(self, source: str) -> set[str]:
        """获取某数据源已有 source_id 集合（用于抓取 skip_ids）。

        source 必填：调用方须显式传数据源名，漏传即报错而非静默当 arxiv。
        """
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT source_id FROM papers WHERE source = ?", (source,))
            return {row["source_id"] for row in cur.fetchall()}
        finally:
            conn.close()

    def _query(self, where: str, order_by: str, params: list) -> list[dict]:
        """按 WHERE/ORDER 查询论文表（get_pending/get_marked/get_lurk 共用）。

        契约：``where``/``order_by`` 必须是模块内硬编码 SQL 片段，**禁止插入任何
        运行时值**（用户可控值一律走 ``params`` 占位符），否则构成注入面。
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(f"SELECT * FROM papers WHERE {where}{order_by}", params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_pending(self, fetch_date_from: str | None = None) -> list[dict]:
        """待审阅论文：已评分且未标记，按抓取日期降序（可选按 fetch_date 起始日过滤）。"""
        clause, params = fetch_date_clause(fetch_date_from)
        return self._query(
            PENDING_WHERE + clause,
            " ORDER BY fetch_date DESC, llm_score DESC",
            params,
        )

    def get_marked(self, fetch_date_from: str | None = None) -> list[dict]:
        """已处理的论文（已忽略 + 已导入 Zotero），按标记时间降序（可选按 fetch_date 过滤）。"""
        clause, params = fetch_date_clause(fetch_date_from)
        return self._query(
            f"user_mark IN (?, ?){clause}",
            " ORDER BY marked_date DESC, rowid DESC",
            [UserMark.IGNORE.value, UserMark.IMPORTED.value, *params],
        )

    def get_lurk(self, fetch_date_from: str | None = None) -> list[dict]:
        """延后处理的论文，按标记时间降序（可选按 fetch_date 过滤）。"""
        clause, params = fetch_date_clause(fetch_date_from)
        return self._query(
            f"user_mark = ?{clause}",
            " ORDER BY marked_date DESC, rowid DESC",
            [UserMark.LURK.value, *params],
        )

    # ─── 标记操作 ────────────────────────────────────────────

    def update_mark(
        self,
        source: str,
        source_id: str,
        mark_type: str,
        short_title: str = "",
        zotero_key: str = "",
    ):
        """更新论文标记（ignore/lurk/pending/imported，值取 UserMark 枚举）。

        幂等语义：论文只存在两种字段形态——
        - imported：携带 short_title / zotero_key（Zotero 关联）；
        - 其余任何非 imported 标记（ignore/lurk/pending）：恢复刚入库时的形态，
          即 short_title / zotero_key 一律清空（不残留旧导入关联）。

        论文状态由 user_mark 单一推导：NULL=待审阅（get_pending），
        ignore/lurk/imported 分别为已忽略/延后/已导入（get_marked/get_lurk）。
        """
        now = (
            now_str()
        )  # 与 created_at/updated_at 默认、fetch_logs.run_time 同格式（%Y-%m-%d %H:%M:%S）

        if mark_type == UserMark.IMPORTED.value:
            user_mark, marked_date, st, zk = mark_type, now, short_title, zotero_key
        elif mark_type in (UserMark.LURK.value, UserMark.IGNORE.value):
            user_mark, marked_date, st, zk = mark_type, now, "", ""
        elif mark_type == UserMark.PENDING.value:
            user_mark, marked_date, st, zk = None, None, "", ""
        else:
            raise ValueError(f"未知 mark_type: {mark_type!r}")

        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """UPDATE papers SET
                        user_mark = ?,
                        short_title = ?, zotero_key = ?,
                        marked_date = ?, updated_at = ?
                    WHERE source = ? AND source_id = ?""",
                    (user_mark, st, zk, marked_date, now, source, source_id),
                )
                conn.commit()
            finally:
                conn.close()
