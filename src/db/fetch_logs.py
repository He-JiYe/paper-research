"""统计 + 抓取日志（PaperDB 组合之一）。"""

import datetime
from typing import Any

from src.db.connection import ConnectionMixin
from src.db.enums import PENDING_WHERE, FetchLogStatus, fetch_date_clause


class LogCrudMixin(ConnectionMixin):
    """统计与抓取日志。"""

    def get_stats(self, fetch_date_from: str | None = None) -> dict[str, Any]:
        """获取统计信息（总数/待审阅/标记/评级/关键词分布，可选按 fetch_date 范围过滤）。

        邮件与 Web 都经 ``fetch_date_from`` 反映各自的时间维度（今日 / 快捷范围）。
        """
        clause, params = fetch_date_clause(fetch_date_from)
        conn = self._get_conn()
        try:
            total = conn.execute(
                f"SELECT COUNT(*) as c FROM papers WHERE 1=1{clause}", params
            ).fetchone()["c"]

            mark_dist = {}
            for row in conn.execute(
                f"SELECT user_mark, COUNT(*) as c FROM papers"
                f" WHERE user_mark IS NOT NULL AND user_mark != ''{clause} GROUP BY user_mark",
                params,
            ):
                mark_dist[row["user_mark"]] = row["c"]

            remark_dist = {}
            for row in conn.execute(
                f"SELECT llm_remark, COUNT(*) as c FROM papers"
                f" WHERE llm_remark != ''{clause} GROUP BY llm_remark",
                params,
            ):
                remark_dist[row["llm_remark"]] = row["c"]

            kw_dist = {}
            for row in conn.execute(
                f"SELECT keyword_match, COUNT(*) as c FROM papers"
                f" WHERE keyword_match != ''{clause} GROUP BY keyword_match",
                params,
            ):
                kw_dist[row["keyword_match"]] = row["c"]

            pending = conn.execute(
                f"SELECT COUNT(*) as c FROM papers WHERE {PENDING_WHERE}{clause}", params
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

    def has_successful_since(self, since: str) -> bool:
        """``since``（YYYY-MM-DD HH:MM:SS）之后是否已有成功抓取记录（补抓判定）。

        直接按 ``run_time`` 字符串比较，无需日期上界——since 取「今日 fetch_time」时，
        早于 fetch_time 的记录（含昨天）自然 ``run_time < since``。
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT 1 FROM fetch_logs WHERE run_time >= ? AND status = ? LIMIT 1",
                (since, FetchLogStatus.SUCCESS.value),
            )
            return cur.fetchone() is not None
        finally:
            conn.close()

    def get_last_success(self, date: str) -> dict | None:
        """今天（YYYY-MM-DD）最近一次成功的抓取日志；无则返回 None。

        范围查询替代 LIKE 前缀，避免日期前缀过宽（如 "2024-8" 误配 "2024-8-xx"）。
        """
        next_date = (datetime.date.fromisoformat(date) + datetime.timedelta(days=1)).isoformat()
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT * FROM fetch_logs WHERE run_time >= ? AND run_time < ? AND status = ?"
                " ORDER BY run_time DESC LIMIT 1",
                (date, next_date, FetchLogStatus.SUCCESS.value),
            )
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def add_fetch_log(
        self,
        keywords_used: int = 0,
        papers_fetched: int = 0,
        papers_new: int = 0,
        papers_summarized: int = 0,
        status: str = FetchLogStatus.SUCCESS.value,
        error_msg: str = "",
        run_time: str = "",
    ):
        """记录一条抓取日志。

        Args:
            run_time: 系统本地时间戳（YYYY-MM-DD HH:MM:SS），保证
                ``has_successful_since`` 的补抓判定与存储口径一致；为空用 SQLite 本地默认。
        """
        cols = "(keywords_used, papers_fetched, papers_new, papers_summarized, status, error_msg)"
        vals = (keywords_used, papers_fetched, papers_new, papers_summarized, status, error_msg)
        if run_time:
            cols = f"(run_time, {cols.lstrip('(')}"
            vals = (run_time, *vals)
        placeholders = ", ".join("?" * len(vals))
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(f"INSERT INTO fetch_logs {cols} VALUES ({placeholders})", vals)
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
