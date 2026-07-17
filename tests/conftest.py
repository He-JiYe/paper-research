"""共享测试 fixtures"""
import json
import sqlite3
from pathlib import Path
from typing import Generator

import pytest

from src.config import (
    OUTPUT_DIR,
    ROOT_DIR,
    AppConfig,
    FetchConfig,
    LLMConfig,
    ScoringConfig,
    ServerConfig,
)
from src.db import get_connection, init_db


@pytest.fixture
def sample_papers() -> list[dict]:
    """样本论文数据"""
    return [
        {
            "arxiv_id": "2401.00001",
            "version": 1,
            "title": "Test-Time Adaptation with Transformers",
            "authors": "Alice Zhang, Bob Li",
            "abstract": "We propose a novel method for test-time adaptation using transformer architectures.",
            "url": "https://arxiv.org/abs/2401.00001",
            "primary_category": "cs.LG",
            "categories": "cs.LG, cs.CV",
            "published": "2024-01-01",
            "arxiv_updated": "2024-01-05T12:00:00Z",
            "keyword_match": "test-time adaptation",
        },
        {
            "arxiv_id": "2401.00002",
            "version": 1,
            "title": "Out-of-Distribution Detection via Energy Scoring",
            "authors": "Chen Wang, Dan Liu",
            "abstract": "Energy-based methods for out-of-distribution detection in deep neural networks.",
            "url": "https://arxiv.org/abs/2401.00002",
            "primary_category": "cs.LG",
            "categories": "cs.LG",
            "published": "2024-01-02",
            "arxiv_updated": "2024-01-06T12:00:00Z",
            "keyword_match": "out-of-distribution detection",
        },
        {
            "arxiv_id": "2401.00003",
            "version": 2,
            "title": "Domain Generalization: A Survey",
            "authors": "Eve Zhao",
            "abstract": "A comprehensive survey of domain generalization methods.",
            "url": "https://arxiv.org/abs/2401.00003",
            "primary_category": "cs.CV",
            "categories": "cs.CV, cs.LG",
            "published": "2024-01-03",
            "arxiv_updated": "2024-01-10T12:00:00Z",
            "keyword_match": "domain generalization",
        },
    ]


@pytest.fixture
def sample_atom_xml() -> str:
    """样本 Arxiv Atom XML 响应"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom"'
        '      xmlns:arxiv="http://arxiv.org/schemas/atom"'
        '      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">'
        "  <entry>"
        '    <id>http://arxiv.org/abs/2401.00001v1</id>'
        "    <title>Test-Time Adaptation with Transformers</title>"
        "    <summary>We propose a novel method for test-time adaptation.</summary>"
        "    <author><name>Alice Zhang</name></author>"
        "    <author><name>Bob Li</name></author>"
        "    <published>2024-01-01T00:00:00Z</published>"
        "    <updated>2024-01-05T12:00:00Z</updated>"
        '    <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.CV"/>'
        "  </entry>"
        "  <entry>"
        '    <id>http://arxiv.org/abs/2401.00002v1</id>'
        "    <title>Out-of-Distribution Detection via Energy Scoring</title>"
        "    <summary>Energy-based methods for OOD detection.</summary>"
        "    <author><name>Chen Wang</name></author>"
        "    <published>2024-01-02T00:00:00Z</published>"
        "    <updated>2024-01-06T12:00:00Z</updated>"
        '    <arxiv:primary_category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        '    <category scheme="http://arxiv.org/schemas/atom" term="cs.LG"/>'
        "  </entry>"
        "</feed>"
    )


@pytest.fixture
def mock_settings() -> AppConfig:
    """Mock AppConfig 对象"""
    return AppConfig(
        source="arxiv",
        llm=LLMConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_base="https://api.deepseek.com",
            api_key="",
            temperature=0.3,
            max_tokens=2000,
        ),
        fetch=FetchConfig(
            delay_between_requests=0.3,
            max_concurrent_requests=3,
            lookback_days=7,
            user_agent="PaperResearch/1.0",
            target_new_per_keyword=25,
            max_results=50,
        ),
        server=ServerConfig(host="127.0.0.1", port=8899),
        scoring=ScoringConfig(min_relevance_score=0.3),
    )


@pytest.fixture
def active_keywords() -> list[dict]:
    """活跃关键词列表"""
    return [
        {"keyword": "test-time adaptation", "arxiv_cats": ["cs.CV", "cs.LG"], "active": True},
        {"keyword": "out-of-distribution detection", "arxiv_cats": ["cs.LG"], "active": True},
    ]


@pytest.fixture
def db_conn(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """临时 SQLite 数据库连接"""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
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
    conn.execute("""
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
    yield conn
    conn.close()


# ─── 已有测试文件需要的 fixtures ─────────────────────────


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """数据库文件路径（用于 test_db.py 等）"""
    return str(tmp_path / "test.db")


@pytest.fixture
def conn(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """初始化的数据库连接（调用 init_db）"""
    init_db(db_path)
    conn = get_connection(db_path)
    yield conn
    conn.close()


@pytest.fixture
def sample_paper() -> dict:
    """单篇样本论文（test_db.py 用）"""
    return {
        "arxiv_id": "2401.00001",
        "version": 1,
        "title": "Test-Time Adaptation: A Survey",
        "authors": "Alice Zhang, Bob Li",
        "abstract": "A comprehensive survey of test-time adaptation methods.",
        "url": "https://arxiv.org/abs/2401.00001",
        "primary_category": "cs.LG",
        "categories": "cs.LG, cs.CV",
        "published": "2024-01-01",
        "arxiv_updated": "2024-01-05T12:00:00Z",
        "keyword_match": "test-time adaptation",
    }


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """临时目录（test_config.py, test_renderer.py 用）"""
    return tmp_path


@pytest.fixture
def setup_sample_papers(conn, sample_papers) -> list[dict]:
    """将 sample_papers 插入数据库并标记，供查询类测试使用"""
    from src.db import insert_paper, mark_paper, update_paper_summary

    for p in sample_papers:
        insert_paper(conn, p)
    # 为 sample_papers[0] 添加 LLM 摘要和标记
    update_paper_summary(conn, "2401.00001", "Summary 1", "important", "Reason 1", 0.95)
    update_paper_summary(conn, "2401.00002", "Summary 2", "useful", "Reason 2", 0.75)
    update_paper_summary(conn, "2401.00003", "Summary 3", "browse", "Reason 3", 0.50)
    mark_paper(conn, "2401.00001", "deep_read")
    return sample_papers
