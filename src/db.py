"""数据库操作模块：SQLite 建表、CRUD、状态管理"""

import datetime
import sqlite3
from typing import Optional


def get_connection(db_path: str) -> sqlite3.Connection:
    """获取数据库连接，启用 WAL 模式和行工厂"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str):
    """初始化数据库表（幂等操作）"""
    conn = get_connection(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            title TEXT NOT NULL DEFAULT '',
            authors TEXT NOT NULL DEFAULT '',
            abstract TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            primary_category TEXT NOT NULL DEFAULT '',
            categories TEXT NOT NULL DEFAULT '',
            published TEXT NOT NULL DEFAULT '',
            arxiv_updated TEXT NOT NULL DEFAULT '',
            keyword_match TEXT NOT NULL DEFAULT '',
            llm_summary TEXT NOT NULL DEFAULT '',
            llm_remark TEXT NOT NULL DEFAULT '',
            llm_reason TEXT NOT NULL DEFAULT '',
            llm_score REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'new',
            user_mark TEXT,
            is_updated INTEGER NOT NULL DEFAULT 0,
            old_version INTEGER,
            fetch_date TEXT NOT NULL DEFAULT '',
            last_fetch_date TEXT NOT NULL DEFAULT '',
            note_short_title TEXT NOT NULL DEFAULT '',
            note_description TEXT NOT NULL DEFAULT ''
        )
    """)

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_arxiv_id
        ON papers(arxiv_id)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_status
        ON papers(status)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_user_mark
        ON papers(user_mark)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_papers_fetch_date
        ON papers(fetch_date)
    """)

    # 笔记自定义分类
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS note_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS note_category_map (
            arxiv_id TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            PRIMARY KEY (arxiv_id, category_id),
            FOREIGN KEY (category_id) REFERENCES note_categories(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT NOT NULL DEFAULT '',
            keywords_used INTEGER NOT NULL DEFAULT 0,
            papers_fetched INTEGER NOT NULL DEFAULT 0,
            papers_new INTEGER NOT NULL DEFAULT 0,
            papers_updated INTEGER NOT NULL DEFAULT 0,
            papers_summarized INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'success',
            error_msg TEXT
        )
    """)

    conn.commit()
    conn.close()


# ─── Paper CRUD ────────────────────────────────────────────


def exists(conn: sqlite3.Connection, arxiv_id: str) -> bool:
    """检查论文是否已存在"""
    cursor = conn.execute("SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    return cursor.fetchone() is not None


def get_paper(conn: sqlite3.Connection, arxiv_id: str) -> Optional[dict]:
    """获取单篇论文数据"""
    cursor = conn.execute("SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    row = cursor.fetchone()
    return dict(row) if row else None


def insert_paper(conn: sqlite3.Connection, paper: dict):
    """插入新论文"""
    conn.execute(
        """
        INSERT INTO papers (
            arxiv_id, version, title, authors, abstract, url,
            primary_category, categories, published, arxiv_updated,
            keyword_match, status, fetch_date, last_fetch_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
    """,
        (
            paper["arxiv_id"],
            paper.get("version", 1),
            paper.get("title", ""),
            paper.get("authors", ""),
            paper.get("abstract", ""),
            paper.get("url", ""),
            paper.get("primary_category", ""),
            paper.get("categories", ""),
            paper.get("published", ""),
            paper.get("arxiv_updated", ""),
            paper.get("keyword_match", ""),
            paper.get("fetch_date", ""),
            paper.get("fetch_date", ""),
        ),
    )
    conn.commit()


def update_paper_summary(
    conn: sqlite3.Connection,
    arxiv_id: str,
    summary: str,
    remark: str,
    reason: str,
    score: float,
):
    """更新 LLM 摘要结果"""
    conn.execute(
        """
        UPDATE papers
        SET llm_summary = ?, llm_remark = ?, llm_reason = ?, llm_score = ?,
            status = 'summarized'
        WHERE arxiv_id = ?
    """,
        (summary, remark, reason, score, arxiv_id),
    )
    conn.commit()


def update_paper_version(
    conn: sqlite3.Connection,
    arxiv_id: str,
    new_version: int,
    arxiv_updated: str,
    fetch_date: str,
):
    """标记论文版本更新"""
    paper = get_paper(conn, arxiv_id)
    if paper:
        conn.execute(
            """
            UPDATE papers
            SET version = ?, arxiv_updated = ?, is_updated = 1,
                old_version = ?, last_fetch_date = ?,
                status = 'new'
            WHERE arxiv_id = ?
        """,
            (new_version, arxiv_updated, paper["version"], fetch_date, arxiv_id),
        )
        conn.commit()


def touch_paper(conn: sqlite3.Connection, arxiv_id: str, fetch_date: str):
    """更新最近检测日期（无变更时仅更新 last_fetch_date）"""
    conn.execute(
        "UPDATE papers SET last_fetch_date = ? WHERE arxiv_id = ?",
        (fetch_date, arxiv_id),
    )
    conn.commit()


# ─── User Mark ────────────────────────────────────────────


def mark_paper(
    conn: sqlite3.Connection,
    arxiv_id: str,
    mark_type: str,
):
    """用户标记论文（'pending' 清空标记，放回待审核）"""
    if mark_type == "pending":
        conn.execute(
            "UPDATE papers SET user_mark = NULL, status = 'summarized' WHERE arxiv_id = ?",
            (arxiv_id,),
        )
    else:
        conn.execute(
            "UPDATE papers SET user_mark = ?, status = 'marked' WHERE arxiv_id = ?",
            (mark_type, arxiv_id),
        )
    conn.commit()


def get_paper_status(conn: sqlite3.Connection, arxiv_id: str) -> Optional[str]:
    """获取论文状态"""
    cursor = conn.execute("SELECT status FROM papers WHERE arxiv_id = ?", (arxiv_id,))
    row = cursor.fetchone()
    return row["status"] if row else None


# ─── Note Categories ──────────────────────────────────────


def get_all_categories(conn: sqlite3.Connection) -> list[dict]:
    """获取所有分类"""
    cursor = conn.execute("SELECT * FROM note_categories ORDER BY id")
    return [dict(r) for r in cursor.fetchall()]


def add_category(conn: sqlite3.Connection, name: str) -> int:
    """添加自定义分类"""
    cursor = conn.execute("INSERT INTO note_categories (name) VALUES (?)", (name,))
    conn.commit()
    return cursor.lastrowid


def delete_category(conn: sqlite3.Connection, category_id: int) -> bool:
    """删除自定义分类"""
    cat = conn.execute(
        "SELECT id FROM note_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if not cat:
        return False
    conn.execute("DELETE FROM note_category_map WHERE category_id = ?", (category_id,))
    conn.execute("DELETE FROM note_categories WHERE id = ?", (category_id,))
    conn.commit()
    return True


def set_note_categories(
    conn: sqlite3.Connection, arxiv_id: str, category_ids: list[int]
):
    """设置笔记的多分类（全量替换）"""
    conn.execute("DELETE FROM note_category_map WHERE arxiv_id = ?", (arxiv_id,))
    for cid in category_ids:
        conn.execute(
            "INSERT INTO note_category_map (arxiv_id, category_id) VALUES (?, ?)",
            (arxiv_id, cid),
        )
    conn.commit()


def get_note_categories(conn: sqlite3.Connection, arxiv_id: str) -> list[dict]:
    """获取笔记的所有分类（返回列表）"""
    cursor = conn.execute(
        """
        SELECT c.id, c.name FROM note_categories c
        JOIN note_category_map m ON c.id = m.category_id
        WHERE m.arxiv_id = ? ORDER BY c.id
    """,
        (arxiv_id,),
    )
    return [dict(r) for r in cursor.fetchall()]


def get_note_short_title(conn: sqlite3.Connection, arxiv_id: str) -> str:
    """获取笔记短标题"""
    cursor = conn.execute(
        "SELECT note_short_title FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    )
    row = cursor.fetchone()
    return row["note_short_title"] if row else ""


def update_note_short_info(
    conn: sqlite3.Connection, arxiv_id: str, short_title: str, description: str
):
    """更新笔记短标题和简要说明"""
    conn.execute(
        "UPDATE papers SET note_short_title = ?, note_description = ? WHERE arxiv_id = ?",
        (short_title, description, arxiv_id),
    )
    conn.commit()


# ─── Queries ──────────────────────────────────────────────


def get_papers_for_summary(conn: sqlite3.Connection) -> dict:
    """
    获取摘要页面所需的所有论文，按状态分组。

    Returns:
        dict with keys:
        - unmarked: 未标记论文（主列表）
        - marked: 已处理论文（skim/deep_read，底部可折叠）
        - lurk: 延后处理论文（底部折叠）
    """
    all_papers = conn.execute("""
        SELECT * FROM papers
        WHERE user_mark IS NULL OR user_mark NOT IN ('ignore')
        ORDER BY
            CASE llm_remark
                WHEN 'important' THEN 1
                WHEN 'useful' THEN 2
                WHEN 'browse' THEN 3
                WHEN 'skip' THEN 4
                ELSE 5
            END,
            llm_score DESC,
            published DESC
    """)

    result = {"unmarked": [], "marked": [], "lurk": []}
    for row in all_papers:
        p = dict(row)
        mark = p.get("user_mark")
        if mark == "lurk":
            result["lurk"].append(p)
        elif mark in ("skim", "deep_read"):
            result["marked"].append(p)
        else:
            result["unmarked"].append(p)

    return result


def get_pending_keywords(conn: sqlite3.Connection) -> list[dict]:
    """获取待审阅论文的关键词及篇数，按篇数降序"""
    cursor = conn.execute("""
        SELECT keyword_match AS keyword, COUNT(*) AS count
        FROM papers
        WHERE status = 'summarized' AND user_mark IS NULL
          AND keyword_match != ''
        GROUP BY keyword_match
        ORDER BY count DESC, keyword_match
    """)
    return [dict(r) for r in cursor.fetchall()]


def get_papers_by_mark(conn: sqlite3.Connection, mark_type: str) -> list[dict]:
    """按用户标记类型查询论文"""
    cursor = conn.execute(
        "SELECT * FROM papers WHERE user_mark = ? ORDER BY published DESC",
        (mark_type,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_papers_by_keyword(conn: sqlite3.Connection, keyword: str) -> list[dict]:
    """按关键词查询论文"""
    cursor = conn.execute(
        "SELECT * FROM papers WHERE keyword_match LIKE ? ORDER BY published DESC",
        (f"%{keyword}%",),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_keyword_paper_count(conn: sqlite3.Connection, keyword: str) -> int:
    """获取某个关键词已抓取的论文数量"""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE keyword_match LIKE ?",
        (f"%{keyword}%",),
    )
    return cursor.fetchone()[0]


def get_all_papers(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """获取所有论文，可选状态过滤"""
    if status:
        cursor = conn.execute(
            "SELECT * FROM papers WHERE status = ? ORDER BY published DESC LIMIT ?",
            (status, limit),
        )
    else:
        cursor = conn.execute(
            "SELECT * FROM papers ORDER BY published DESC LIMIT ?",
            (limit,),
        )
    return [dict(row) for row in cursor.fetchall()]


def get_stats(conn: sqlite3.Connection) -> dict:
    """获取统计信息"""
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    new = conn.execute("SELECT COUNT(*) FROM papers WHERE status = 'new'").fetchone()[0]
    summarized = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE status = 'summarized' AND user_mark IS NULL"
    ).fetchone()[0]
    skim = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE user_mark = 'skim'"
    ).fetchone()[0]
    deep_read = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE user_mark = 'deep_read'"
    ).fetchone()[0]
    ignored = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE user_mark = 'ignore'"
    ).fetchone()[0]
    lurk = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE user_mark = 'lurk'"
    ).fetchone()[0]
    updated = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE is_updated = 1"
    ).fetchone()[0]

    # 各评级计数
    important = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE llm_remark = 'important'"
    ).fetchone()[0]
    useful = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE llm_remark = 'useful'"
    ).fetchone()[0]
    browse = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE llm_remark = 'browse'"
    ).fetchone()[0]
    skip = conn.execute(
        "SELECT COUNT(*) FROM papers WHERE llm_remark = 'skip'"
    ).fetchone()[0]

    return {
        "total": total,
        "new": new,
        "summarized_pending": summarized,
        "skim": skim,
        "deep_read": deep_read,
        "ignored": ignored,
        "lurk": lurk,
        "updated": updated,
        "important": important,
        "useful": useful,
        "browse": browse,
        "skip": skip,
    }


# ─── Fetch Log ────────────────────────────────────────────


def insert_fetch_log(
    conn: sqlite3.Connection,
    keywords_used: int,
    papers_fetched: int,
    papers_new: int,
    papers_updated: int,
    papers_summarized: int,
    status: str = "success",
    error_msg: Optional[str] = None,
):
    """插入抓取日志"""
    run_time = datetime.datetime.now().isoformat()
    conn.execute(
        """
        INSERT INTO fetch_log (
            run_time, keywords_used, papers_fetched, papers_new,
            papers_updated, papers_summarized, status, error_msg
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            run_time,
            keywords_used,
            papers_fetched,
            papers_new,
            papers_updated,
            papers_summarized,
            status,
            error_msg,
        ),
    )
    conn.commit()


def get_recent_logs(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    """获取最近的抓取日志"""
    cursor = conn.execute(
        "SELECT * FROM fetch_log ORDER BY run_time DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_most_recent_fetch_log(conn: sqlite3.Connection) -> Optional[dict]:
    """获取最近一次成功的抓取日志。"""
    cursor = conn.execute(
        "SELECT * FROM fetch_log WHERE status = 'success' ORDER BY run_time DESC LIMIT 1"
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def get_earliest_paper_date_for_keyword(
    conn: sqlite3.Connection, keyword: str
) -> Optional[str]:
    """获取某个关键词匹配论文的最早发表日期。"""
    cursor = conn.execute(
        "SELECT MIN(published) FROM papers WHERE keyword_match LIKE ? AND published != ''",
        (f"%{keyword}%",),
    )
    return cursor.fetchone()[0]
