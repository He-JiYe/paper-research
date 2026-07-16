"""测试共享 fixtures 和 mock 数据"""

import os
import sqlite3
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# ── 路径处理 ──────────────────────────────────────────────
# 确保从项目根可导入 src
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def temp_dir() -> Generator[Path]:
    """临时目录 fixture，自动清理"""
    with tempfile.TemporaryDirectory() as d:
        old_cwd = Path.cwd()
        os.chdir(PROJECT_ROOT)  # 始终在项目根运行
        yield Path(d)
        os.chdir(old_cwd)


@pytest.fixture
def db_path(temp_dir: Path) -> str:
    """临时数据库路径"""
    return str(temp_dir / "test.db")


def _create_tables(conn: sqlite3.Connection):
    """在连接上创建所有测试需要的表"""
    conn.executescript("""
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
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_arxiv_id ON papers(arxiv_id);
        CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
        CREATE INDEX IF NOT EXISTS idx_papers_user_mark ON papers(user_mark);
        CREATE INDEX IF NOT EXISTS idx_papers_fetch_date ON papers(fetch_date);

        CREATE TABLE IF NOT EXISTS note_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS note_category_map (
            arxiv_id TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            PRIMARY KEY (arxiv_id, category_id),
            FOREIGN KEY (category_id) REFERENCES note_categories(id)
        );
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
        );
    """)
    conn.commit()


@pytest.fixture
def conn(db_path: str) -> Generator[sqlite3.Connection]:
    """SQLite 连接 + 初始化表（WAL + Row factory）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _create_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def raw_conn(db_path: str) -> Generator[sqlite3.Connection]:
    """SQLite 连接，不初始化表（用于测试 init_db 本身）"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    yield conn
    conn.close()


@pytest.fixture
def sample_paper() -> dict:
    """单篇样本论文数据"""
    return {
        "arxiv_id": "2401.00001",
        "version": 1,
        "title": "Test-Time Adaptation: A Survey",
        "authors": "Alice Zhang, Bob Li",
        "abstract": "This paper surveys test-time adaptation methods for deep neural networks. We review recent approaches and propose a new taxonomy.",
        "url": "https://arxiv.org/abs/2401.00001",
        "primary_category": "cs.CV",
        "categories": "cs.CV, cs.LG",
        "published": "2024-01-01",
        "arxiv_updated": "2024-01-05T00:00:00Z",
        "keyword_match": "test-time adaptation",
        "fetch_date": "2024-07-01",
    }


@pytest.fixture
def sample_papers(conn, sample_paper) -> list[dict]:
    """插入多篇样本论文到数据库"""
    papers = [
        sample_paper,
        {
            "arxiv_id": "2401.00002",
            "version": 1,
            "title": "Out-of-Distribution Detection via Energy-Based Models",
            "authors": "Chen Wang, Dan Li",
            "abstract": "We propose energy-based out-of-distribution detection for neural networks.",
            "url": "https://arxiv.org/abs/2401.00002",
            "primary_category": "cs.LG",
            "categories": "cs.LG, cs.CV",
            "published": "2024-01-02",
            "arxiv_updated": "2024-01-06T00:00:00Z",
            "keyword_match": "out-of-distribution detection",
            "fetch_date": "2024-07-01",
        },
        {
            "arxiv_id": "2401.00003",
            "version": 2,
            "title": "Domain Generalization: A Systematic Review",
            "authors": "Eve Fan",
            "abstract": "Domain generalization aims to learn models that generalize to unseen domains.",
            "url": "https://arxiv.org/abs/2401.00003",
            "primary_category": "cs.CV",
            "categories": "cs.CV",
            "published": "2024-01-03",
            "arxiv_updated": "2024-01-10T00:00:00Z",
            "keyword_match": "domain generalization",
            "fetch_date": "2024-07-01",
        },
    ]

    from src.db import insert_paper

    for p in papers:
        insert_paper(conn, p)

    # 给第一篇加评分和标记
    from src.db import mark_paper, update_paper_summary

    update_paper_summary(
        conn, "2401.00001", "Good survey", "important", "Comprehensive review", 0.85
    )
    update_paper_summary(
        conn, "2401.00002", "Interesting method", "useful", "Novel approach", 0.72
    )
    mark_paper(conn, "2401.00001", "deep_read")

    return papers


@pytest.fixture
def mock_settings():
    """Mock AppConfig 对象"""
    from src.config import (AppConfig, ArxivConfig, EmailConfig, LLMConfig,
                            NotificationConfig, ScoringConfig, ServerConfig)

    config = AppConfig(
        llm=LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            api_key="",
            temperature=0.3,
            max_tokens=2000,
        ),
        arxiv=ArxivConfig(
            delay_between_requests=1.0,
            max_concurrent_requests=10,
            lookback_days=90,
            user_agent="PaperResearch/1.0",
            target_new_per_keyword=25,
        ),
        server=ServerConfig(host="127.0.0.1", port=8899),
        notification=NotificationConfig(
            windows_toast=True,
            email=EmailConfig(
                enabled=False,
                smtp_host="",
                smtp_port=465,
                username="",
                password="",
                from_addr="",
                to_addr="",
            ),
        ),
        scoring=ScoringConfig(min_relevance_score=0.3),
    )
    return config


SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Test-Time Adaptation: A Survey</title>
    <summary>This paper surveys test-time adaptation methods for deep neural networks.</summary>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
    <published>2024-01-01T00:00:00Z</published>
    <updated>2024-01-05T00:00:00Z</updated>
    <arxiv:primary_category term="cs.CV"/>
    <category term="cs.CV"/>
    <category term="cs.LG"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v2</id>
    <title>Out-of-Distribution Detection via Energy</title>
    <summary>We propose energy-based out-of-distribution detection.</summary>
    <author><name>Chen Wang</name></author>
    <published>2024-01-02T00:00:00Z</published>
    <updated>2024-01-06T00:00:00Z</updated>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <category term="cs.CV"/>
  </entry>
</feed>"""


SAMPLE_EMPTY_ATOM_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
</feed>"""


SAMPLE_KEYWORDS_JSON = [
    {
        "keyword": "test-time adaptation",
        "arxiv_cats": ["cs.CV", "cs.LG"],
        "active": True,
    },
    {
        "keyword": "out-of-distribution detection",
        "arxiv_cats": ["cs.LG", "cs.CV"],
        "active": True,
    },
    {
        "keyword": "domain generalization",
        "arxiv_cats": ["cs.CV"],
        "active": False,
    },
]

SAMPLE_SETTINGS_JSON = {
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "api_base": "https://api.deepseek.com",
        "api_key": "",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "arxiv": {
        "delay_between_requests": 1.0,
        "max_concurrent_requests": 10,
        "lookback_days": 90,
        "user_agent": "PaperResearch/1.0",
        "target_new_per_keyword": 25,
    },
    "server": {"host": "127.0.0.1", "port": 8899},
    "notification": {
        "windows_toast": True,
        "email": {
            "enabled": False,
            "smtp_host": "",
            "smtp_port": 465,
            "username": "",
            "password": "",
            "from": "",
            "to": "",
        },
    },
    "scoring": {"min_relevance_score": 0.3},
}
