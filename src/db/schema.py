"""数据库表结构 SQL 常量（唯一事实来源，供 _init_db 使用）。"""

PAPERS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
    source         TEXT NOT NULL DEFAULT '',
    source_id      TEXT NOT NULL DEFAULT '',
    title          TEXT NOT NULL DEFAULT '',
    authors        TEXT DEFAULT '',
    abstract       TEXT DEFAULT '',
    url            TEXT DEFAULT '',
    pdf_url        TEXT DEFAULT '',
    categories     TEXT DEFAULT '',
    published      TEXT DEFAULT '',
    updated        TEXT DEFAULT '',
    keyword_match  TEXT DEFAULT '',
    raw_data       TEXT DEFAULT '{}',

    llm_summary     TEXT DEFAULT '',
    llm_remark      TEXT DEFAULT '',
    llm_reason      TEXT DEFAULT '',
    llm_score       REAL DEFAULT 0.0,
    score_source    TEXT DEFAULT '',

    user_mark       TEXT DEFAULT NULL,
    short_title     TEXT DEFAULT '',
    zotero_key      TEXT DEFAULT '',

    fetch_date      TEXT DEFAULT '',
    marked_date     TEXT DEFAULT NULL,

    created_at      TEXT DEFAULT (datetime('now','localtime')),
    updated_at      TEXT DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_papers_user_mark
    ON papers(user_mark);
CREATE INDEX IF NOT EXISTS idx_papers_keyword
    ON papers(keyword_match);
CREATE INDEX IF NOT EXISTS idx_papers_fetch_date
    ON papers(fetch_date);
"""

FETCH_LOGS_SCHEMA_SQL = """
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
"""
